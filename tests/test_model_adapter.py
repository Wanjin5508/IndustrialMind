"""Exercise the real SDK serialization/parsing with a network transport double."""

import json
from dataclasses import replace

import httpx
import pytest
from openai import OpenAI
from test_files import fixture_run

from rfq_workbench.errors import DomainError
from rfq_workbench.extraction import LocalTextProvider, OpenAIProvider


def test_sdk_sends_images_schema_and_parses_response(client, settings, monkeypatch):
    body = fixture_run(client)
    extracted = LocalTextProvider().extract(body).fields.model_dump()
    seen = []

    def handle(request):
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(
            200,
            json={
                "id": "resp-test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "deepseek-flash",
                "output": [
                    {
                        "type": "message",
                        "id": "msg-test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(extracted),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "total_tokens": 1500,
                    "input_tokens_details": {"cached_tokens": 200},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    def factory(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr("rfq_workbench.extraction.OpenAI", factory)
    result = OpenAIProvider(
        replace(
            settings,
            model_key="test-only",
            model_name="deepseek-flash",
            model_base_url="https://api.deepseek.com",
        )
    ).extract(body)
    assert result.fields.drawing.material.value == "Al-6061-T6"
    assert seen[0]["text"]["format"]["type"] == "json_schema"
    assert any(p["type"] == "input_image" for p in seen[0]["input"][0]["content"])
    assert result.metrics["response_id"] == "resp-test"


@pytest.mark.parametrize(
    "failure,code",
    [
        (401, "MODEL_AUTH_FAILED"),
        (429, "MODEL_RATE_LIMIT"),
        (500, "MODEL_HTTP_ERROR"),
        ("timeout", "MODEL_TIMEOUT"),
    ],
)
def test_failure_metrics_never_include_provider_body(client, settings, monkeypatch, failure, code):
    body = fixture_run(client)

    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private-secret-do-not-log", request=request)
        return httpx.Response(failure, json={"error": {"message": "private-secret-do-not-log"}})

    def factory(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr("rfq_workbench.extraction.OpenAI", factory)
    with pytest.raises(DomainError) as error:
        OpenAIProvider(
            replace(settings, model_key="test-only", model_name="deepseek-flash")
        ).extract(body)
    assert error.value.code == code
    assert error.value.metrics["elapsed_ms"] >= 0
    assert "private-secret" not in json.dumps(error.value.metrics) + str(error.value)
    assert error.value.metrics["estimated_cost"] is None
