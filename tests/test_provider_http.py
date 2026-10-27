import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from app.core.config import settings
from app.integrations.llm import LLMProvider, ProviderUnavailable


def test_provider_uses_real_http_and_retries_only_transient_responses(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, body))
            code = 503 if len(received) == 1 else 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"choices": [{"message": {"content": "Bounded local response"}}]}
                ).encode()
            )

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "llm_base_url", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setattr(settings, "llm_api_key", "local-test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    try:
        response = asyncio.run(
            LLMProvider().complete([{"role": "user", "content": "Local fixture only"}], [], 5)
        )
        assert response["content"] == "Bounded local response"
        assert len(received) == 2
        assert received[0][0] == "/v1/chat/completions"
        assert received[0][1]["model"] == "test-model"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_missing_provider_config_does_not_make_network_request(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", None)
    with pytest.raises(ProviderUnavailable):
        asyncio.run(LLMProvider().complete([], [], 5))
