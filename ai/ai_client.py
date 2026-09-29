"""Async client for OpenAI-compatible chat-completions services."""
from __future__ import annotations
import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(slots=True)
class AIClient:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 45.0

    def _post(self, payload: dict) -> dict:
        endpoint = self.base_url.strip().rstrip("/")
        if not endpoint:
            raise ValueError("请填写 AI API 地址")
        if not endpoint.endswith("/chat/completions"):
            if not endpoint.casefold().endswith("/v1"):
                endpoint += "/v1"
            endpoint += "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read(1000).decode("utf-8", "replace")
            raise RuntimeError(f"AI 服务返回 HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"无法连接 AI 服务：{error.reason}") from error

    async def complete(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model, "messages": messages, "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        try:
            result = await asyncio.to_thread(self._post, payload)
        except RuntimeError as error:
            # Several OpenAI-compatible local servers do not implement the
            # optional JSON response-format parameter; the parser validates it.
            if "HTTP 400" not in str(error) and "response_format" not in str(error):
                raise
            payload.pop("response_format", None)
            result = await asyncio.to_thread(self._post, payload)
        try:
            return str(result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("AI 服务响应格式不正确") from error

    async def test_connection(self) -> str:
        result = await asyncio.to_thread(self._post, {
            "model": self.model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 8,
        })
        return str(result["choices"][0]["message"]["content"])
