import asyncio
import json
import os
import signal
import subprocess

from app.core.config import settings


def python_path():
    return settings.crawler_python or settings.crawler_root / (
        ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    )


def environment():
    # The Selenium child gets neither DATABASE_URL nor LLM/provider credentials.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "DISPLAY",
        "XAUTHORITY",
        "LANG",
        "LC_ALL",
        "SE_CACHE_PATH",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMSPEC",
        "PATHEXT",
        "SYSTEMDRIVE",
        "HOMEDRIVE",
        "HOMEPATH",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result["PYTHONUTF8"] = "1"
    result.setdefault("SE_CACHE_PATH", str(settings.crawler_root / "output" / ".selenium"))
    return result


def probe():
    result = subprocess.run(
        [str(python_path()), "-m", "src.crawler.worker_entry", "--capabilities"],
        cwd=settings.crawler_root,
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=True,
    )
    data = json.loads(result.stdout)
    if (
        not isinstance(data, list)
        or not data
        or any(not isinstance(c, dict) or "platform" not in c for c in data)
    ):
        raise RuntimeError("Invalid engine capability manifest")
    return data


class Engine:
    def __init__(self):
        self.process = None
        self.job = None

    async def start(self):
        kwargs = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        self.process = await asyncio.create_subprocess_exec(
            str(python_path()),
            "-m",
            "src.crawler.worker_entry",
            cwd=settings.crawler_root,
            env=environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1024 * 1024,
            **kwargs,
        )
        if os.name == "nt":
            from app.crawler.windows_job import WindowsJob

            self.job = WindowsJob(self.process.pid)
        return self.process.pid

    async def send(self, config):
        self.process.stdin.write((json.dumps(config) + "\n").encode())
        await self.process.stdin.drain()
        self.process.stdin.close()

    async def events(self):
        finished = False
        while line := await self.process.stdout.readline():
            event = json.loads(line)
            if not isinstance(event, dict):
                raise TypeError("Invalid engine event")
            if event.get("type") == "finished":
                finished = True
            else:
                yield event
        code = await self.process.wait()
        if code or not finished:
            raise RuntimeError("Selenium engine stopped without a complete outcome")

    async def stop(self):
        if not self.process:
            return
        if os.name == "nt":
            if self.job:
                self.job.close()
            elif self.process.returncode is None:
                stop = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(self.process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(stop.wait(), timeout=15)
        else:
            # Also terminate children left behind after an abnormal engine exit.
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                os.killpg(self.process.pid, signal.SIGKILL)
        await asyncio.wait_for(self.process.wait(), timeout=15)
