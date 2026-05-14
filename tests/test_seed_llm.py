import json

import pytest

from tools.seed import llm


def _request() -> dict:
    return {
        "instructions": llm.INSTRUCTIONS,
        "annotation_schema": llm.load_annotation_schema(),
        "framework_documents": {"owasp-mcp-top-10-2025.md": "MCP docs"},
        "matched_by": ["package_name_mcp"],
        "osv_record": {"id": "GHSA-abcd-ef12-3456", "summary": "mcp command injection"},
    }


def _response_text() -> str:
    return json.dumps(
        {
            "decision": "annotate",
            "database_specific": {
                "asve": {
                    "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                    "evidence_level": "likely",
                },
            },
            "evidence": [{"field": "summary", "quote": "command injection"}],
        }
    )


def test_openai_provider_posts_chat_completion_and_extracts_json():
    calls = []

    def fake_post_json(url, headers, payload):
        calls.append((url, headers, payload))
        return {"choices": [{"message": {"content": _response_text()}}]}

    result = llm.annotate_with_provider(
        "openai",
        "gpt-test",
        "test-key",
        _request(),
        post_json=fake_post_json,
    )

    url, headers, payload = calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert payload["model"] == "gpt-test"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert payload["response_format"] == {"type": "json_object"}
    assert result.decision == "annotate"
    assert result.asve == {
        "taxonomies": {"owasp_agentic_top10": ["asi05"]},
        "evidence_level": "likely",
    }
    assert result.evidence == [{"field": "summary", "quote": "command injection"}]


def test_anthropic_provider_posts_messages_request_and_extracts_json():
    calls = []

    def fake_post_json(url, headers, payload):
        calls.append((url, headers, payload))
        return {"content": [{"type": "text", "text": _response_text()}]}

    result = llm.annotate_with_provider(
        "anthropic",
        "anthropic-test",
        "test-key",
        _request(),
        post_json=fake_post_json,
    )

    url, headers, payload = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "test-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert payload["model"] == "anthropic-test"
    assert payload["system"] == llm.INSTRUCTIONS
    assert payload["messages"][0]["role"] == "user"
    assert result.decision == "annotate"
    assert result.asve == {
        "taxonomies": {"owasp_agentic_top10": ["asi05"]},
        "evidence_level": "likely",
    }
    assert result.evidence == [{"field": "summary", "quote": "command injection"}]


def test_llm_provider_can_return_rejection_decision():
    response_text = json.dumps(
        {
            "decision": "reject",
            "reject_reason": "not_agent_stack",
            "evidence": [{"field": "summary", "quote": "generic package"}],
        }
    )

    def fake_post_json(_url, _headers, _payload):
        return {"choices": [{"message": {"content": response_text}}]}

    result = llm.annotate_with_provider(
        "openai",
        "gpt-test",
        "test-key",
        _request(),
        post_json=fake_post_json,
    )

    assert result.decision == "reject"
    assert result.reject_reason == "not_agent_stack"
    assert result.asve is None
    assert result.evidence == [{"field": "summary", "quote": "generic package"}]


def test_llm_provider_rejects_unsupported_provider():
    with pytest.raises(llm.LLMAnnotationError, match="unsupported LLM provider"):
        llm.annotate_with_provider("local", "model", "key", _request())


def test_llm_provider_does_not_alias_claude_to_anthropic():
    with pytest.raises(llm.LLMAnnotationError, match="unsupported LLM provider"):
        llm.annotate_with_provider("claude", "model", "key", _request())


def test_load_annotation_schema_extracts_asve_extension_from_schema_file(tmp_path):
    schema = json.loads(llm.SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$defs"]["asve_extension"]["properties"]["review_note"] = {"type": "string"}
    schema_path = tmp_path / "asve.schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    annotation_schema = llm.load_annotation_schema(schema_path)

    assert annotation_schema["required"] == ["taxonomies", "evidence_level"]
    assert "component_identity" not in annotation_schema["properties"]
    assert "component_type" not in annotation_schema["properties"]
    assert "surfaces" not in annotation_schema["properties"]
    assert "agent_impact" not in annotation_schema["properties"]
    assert annotation_schema["properties"]["review_note"] == {"type": "string"}
    assert (
        annotation_schema["properties"]["taxonomies"]["properties"]["owasp_mcp_top10"]["items"][
            "pattern"
        ]
        == "^mcp(0[1-9]|10):[0-9]{4}$"
    )
    assert "$ref" not in json.dumps(annotation_schema)
