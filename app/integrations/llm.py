import asyncio
import json

import httpx

from app.core.config import settings


class ProviderUnavailable(Exception):
    pass


class LLMProvider:
    async def complete(self, messages, tools, remaining_seconds):
        if not all((settings.llm_base_url, settings.llm_api_key, settings.llm_model)):
            raise ProviderUnavailable("LLM provider is not configured")
        if not settings.llm_base_url.startswith("https://") and settings.app_env not in {
            "development",
            "test",
        }:
            raise ProviderUnavailable("HTTPS provider endpoint required")
        timeout = min(20, max(0.1, remaining_seconds))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for attempt in range(2):
                try:
                    async with client.stream(
                        "POST",
                        settings.llm_base_url.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + settings.llm_api_key},
                        json={
                            "model": settings.llm_model,
                            "messages": messages,
                            "tools": tools,
                            "tool_choice": "auto",
                        },
                    ) as response:
                        if response.status_code in {429, 502, 503, 504} and attempt == 0:
                            await asyncio.sleep(min(0.25, remaining_seconds / 10))
                            continue
                        response.raise_for_status()
                        chunks = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > 256000:
                                raise ProviderUnavailable("Provider response exceeds limit")
                            chunks.append(chunk)
                        data = json.loads(b"".join(chunks))
                        message = data["choices"][0]["message"]
                        if not isinstance(message, dict):
                            raise TypeError()
                        return message
                except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
                    if attempt == 0 and isinstance(exc, httpx.TransportError):
                        continue
                    raise ProviderUnavailable("Provider request failed") from None
