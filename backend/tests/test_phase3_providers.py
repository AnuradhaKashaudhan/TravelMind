"""Mocked REST tests for the LLM provider adapters."""

from __future__ import annotations

import json

import httpx
from pydantic import SecretStr

from app.llm.providers.gemini import GeminiClient
from app.llm.providers.openai_compatible import OpenAICompatibleClient


async def test_openai_compatible_chat_json_mode_embedding_and_stream() -> None:
    """OpenAI-compatible REST responses normalize chat and embedding payloads."""
    streamed = (
        'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer test-key"
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            if body.get("stream"):
                return httpx.Response(
                    200,
                    content=streamed,
                    headers={"Content-Type": "text/event-stream"},
                )
            assert body["response_format"] == {"type": "json_object"}
            return httpx.Response(
                200,
                json={
                    "model": "test-chat",
                    "choices": [
                        {
                            "message": {"content": '{"ok": true}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    },
                },
            )
        assert request.url.path.endswith("/embeddings")
        body = json.loads(request.content)
        assert body["dimensions"] == 768
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.25] * 768},
                    {"index": 1, "embedding": [0.5] * 768},
                ]
            },
        )

    http = httpx.AsyncClient(
        base_url="https://compatible.test/v1",
        transport=httpx.MockTransport(handler),
    )
    client = OpenAICompatibleClient(
        base_url="https://compatible.test/v1",
        api_key=SecretStr("test-key"),
        model="test-chat",
        embedding_model="test-embedding",
        http_client=http,
    )
    response = await client.generate(
        [{"role": "user", "content": "hello"}],
        json_mode=True,
    )
    assert response.text == '{"ok": true}'
    assert response.usage.total_tokens == 5
    assert [chunk async for chunk in client.stream(
        [{"role": "user", "content": "hello"}]
    )] == ["hello"]
    vectors = await client.embed(["first", "second"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 768
    await http.aclose()


async def test_gemini_generation_json_mode_and_embedding() -> None:
    """Gemini API fields map to normalized results and fixed-size vectors."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-goog-api-key") == "test-key"
        if request.url.path.endswith(":generateContent"):
            body = json.loads(request.content)
            assert body["generationConfig"]["responseMimeType"] == "application/json"
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {"parts": [{"text": '{"ok": true}'}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 4,
                        "candidatesTokenCount": 3,
                        "totalTokenCount": 7,
                    },
                },
            )
        assert request.url.path.endswith(":embedContent")
        return httpx.Response(
            200,
            json={"embedding": {"values": [0.125] * 768}},
        )

    http = httpx.AsyncClient(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        transport=httpx.MockTransport(handler),
    )
    client = GeminiClient(
        SecretStr("test-key"),
        model="test-gemini",
        embedding_model="test-embedding",
        http_client=http,
    )
    response = await client.generate(
        [{"role": "user", "content": "hello"}],
        json_mode=True,
    )
    assert response.provider == "gemini"
    assert response.usage.prompt_tokens == 4
    vectors = await client.embed(["hello"])
    assert len(vectors[0]) == 768
    await http.aclose()
