import json

import pytest

from tools.seed import llm


def _request() -> dict:
    return {
        "instructions": llm.INSTRUCTIONS,
        "annotation_schema": llm.ANNOTATION_SCHEMA,
        "framework_documents": {"owasp-mcp-top-10-2025.md": "MCP docs"},
        "matched_by": ["package_name_mcp"],
        "osv_record": {"id": "GHSA-abcd-ef12-3456", "summary": "mcp command injection"},
    }


def _response_text() -> str:
    return json.dumps(
        {
            "database_specific": {
                "asve": {
                    "component_type": "mcp_server",
                    "surfaces": ["tool_invocation", "stdio"],
                    "agent_impact": {"code_execution": True},
                    "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                    "evidence_level": "likely",
                }
            },
            "evidence": [{"field": "summary", "quote": "command injection"}],
        }
    )


def test_openai_provider_posts_chat_completion_and_extracts_json():
    calls = []

    def fake_post_json(url, headers, payload):
        calls.append((url, headers, payload))
        return {"choices": [{"message": {"content": _response_text()}}]}

    asve, evidence = llm.annotate_with_provider(
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
    assert asve["component_type"] == "mcp_server"
    assert evidence == [{"field": "summary", "quote": "command injection"}]


def test_anthropic_provider_posts_messages_request_and_extracts_json():
    calls = []

    def fake_post_json(url, headers, payload):
        calls.append((url, headers, payload))
        return {"content": [{"type": "text", "text": _response_text()}]}

    asve, evidence = llm.annotate_with_provider(
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
    assert asve["component_type"] == "mcp_server"
    assert evidence == [{"field": "summary", "quote": "command injection"}]


def test_llm_provider_rejects_unsupported_provider():
    with pytest.raises(llm.LLMAnnotationError, match="unsupported LLM provider"):
        llm.annotate_with_provider("local", "model", "key", _request())


def test_llm_provider_does_not_alias_claude_to_anthropic():
    with pytest.raises(llm.LLMAnnotationError, match="unsupported LLM provider"):
        llm.annotate_with_provider("claude", "model", "key", _request())
