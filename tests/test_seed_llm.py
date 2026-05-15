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
                "openaca": {
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
    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "openaca_seed_annotation"
    response_schema = response_format["json_schema"]["schema"]
    assert response_schema["properties"]["decision"]["enum"] == ["annotate", "reject"]
    reject_reason = response_schema["properties"]["reject_reason"]
    assert "not_agent_stack" in reject_reason["enum"]
    assert "unsupported_record" in reject_reason["enum"]
    assert result.decision == "annotate"
    assert result.openaca == {
        "taxonomies": {"owasp_agentic_top10": ["asi05"]},
        "evidence_level": "likely",
    }
    assert result.evidence == [{"field": "summary", "quote": "command injection"}]


def test_anthropic_provider_posts_messages_request_and_extracts_json():
    calls = []

    def fake_post_json(url, headers, payload):
        calls.append((url, headers, payload))
        return {
            "content": [
                {
                    "type": "tool_use",
                    "name": "openaca_seed_annotation",
                    "input": json.loads(_response_text()),
                }
            ]
        }

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
    assert payload["tools"][0]["name"] == "openaca_seed_annotation"
    assert payload["tools"][0]["input_schema"]["properties"]["decision"]["enum"] == [
        "annotate",
        "reject",
    ]
    reject_reason = payload["tools"][0]["input_schema"]["properties"]["reject_reason"]
    assert "not_agent_stack" in reject_reason["enum"]
    assert "unsupported_record" in reject_reason["enum"]
    assert payload["tool_choice"] == {"type": "tool", "name": "openaca_seed_annotation"}
    assert result.decision == "annotate"
    assert result.openaca == {
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
    assert result.openaca is None
    assert result.evidence == [{"field": "summary", "quote": "generic package"}]


def test_llm_provider_coerces_unknown_reject_reason_to_unsupported_record():
    response_text = json.dumps(
        {
            "decision": "reject",
            "reject_reason": "not relevant",
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
    assert result.reject_reason == "unsupported_record"
    assert result.openaca is None


def test_response_schema_omits_deterministic_threat_kind_but_uses_canonical_enums():
    annotation_schema = llm.load_annotation_schema()

    response_schema = llm.build_response_schema(annotation_schema)

    openaca_schema = response_schema["properties"]["database_specific"]["properties"]["openaca"]
    assert (
        openaca_schema["properties"]["evidence_level"]["enum"]
        == annotation_schema["properties"]["evidence_level"]["enum"]
    )
    assert "threat_kind" not in openaca_schema["properties"]
    assert "threat_kind" not in openaca_schema["required"]


def test_response_schema_supplemental_taxonomies_allows_arbitrary_string_array_values():
    response_schema = llm.build_response_schema()

    taxonomies = response_schema["properties"]["database_specific"]["properties"]["openaca"][
        "properties"
    ]["taxonomies"]
    supp = taxonomies["properties"]["supplemental_taxonomies"]
    assert supp["type"] == "object"
    assert isinstance(supp.get("additionalProperties"), dict), (
        "supplemental_taxonomies must allow arbitrary keys"
        " (additionalProperties must be a schema, not false)"
    )
    assert supp["additionalProperties"]["type"] == "array"


def test_response_schema_taxonomy_families_are_not_required():
    response_schema = llm.build_response_schema()

    taxonomies = response_schema["properties"]["database_specific"]["properties"]["openaca"][
        "properties"
    ]["taxonomies"]
    assert "required" not in taxonomies, (
        "taxonomy families must be optional to match the canonical openaca_taxonomies schema"
    )


def test_build_request_removes_threat_kind_from_llm_annotation_schema():
    request = llm.build_request(
        {"id": "MAL-2026-1234", "summary": "Malicious code in mcp-demo"},
        ["package_name_mcp"],
        {"owasp-mcp-top-10-2025.md": "MCP docs"},
    )

    assert "threat_kind" not in request["annotation_schema"]["properties"]
    assert "Do not return threat_kind" in request["instructions"]


def test_post_json_raises_provider_error_on_http_failure():
    import io
    import unittest.mock
    import urllib.error

    http_error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=400,
        msg="Bad Request",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"error": {"message": "json_schema not supported"}}'),
    )

    with unittest.mock.patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(llm.LLMProviderError, match="HTTP 400"):
            llm._post_json("https://api.openai.com/v1/chat/completions", {}, {})


def test_call_openai_raises_provider_error_when_choices_missing():
    def fake_post_json(_url, _headers, _payload):
        return {"unexpected": []}

    with pytest.raises(llm.LLMProviderError, match="OpenAI response did not include"):
        llm._call_openai("gpt-test", "test-key", _request(), fake_post_json)


def test_call_openai_raises_provider_error_when_content_is_not_string():
    def fake_post_json(_url, _headers, _payload):
        return {"choices": [{"message": {"content": [{"type": "text"}]}}]}

    with pytest.raises(llm.LLMProviderError, match="OpenAI message content"):
        llm._call_openai("gpt-test", "test-key", _request(), fake_post_json)


def test_call_anthropic_raises_provider_error_when_tool_input_is_not_dict():
    def fake_post_json(_url, _headers, _payload):
        return {
            "content": [
                {
                    "type": "tool_use",
                    "name": "openaca_seed_annotation",
                    "input": "not-json",
                }
            ]
        }

    with pytest.raises(llm.LLMProviderError, match="Anthropic tool input"):
        llm._call_anthropic("anthropic-test", "test-key", _request(), fake_post_json)


def test_call_anthropic_raises_provider_error_when_no_tool_use_or_text():
    def fake_post_json(_url, _headers, _payload):
        return {"content": [{"type": "thinking", "text": ""}]}

    with pytest.raises(llm.LLMProviderError, match="Anthropic response did not include"):
        llm._call_anthropic("anthropic-test", "test-key", _request(), fake_post_json)


def test_loads_response_json_raises_provider_error_for_invalid_json():
    with pytest.raises(llm.LLMProviderError, match="invalid JSON"):
        llm._loads_response_json("not json")


def test_provider_error_is_subclass_of_annotation_error():
    assert issubclass(llm.LLMProviderError, llm.LLMAnnotationError)


def test_llm_provider_rejects_unsupported_provider():
    with pytest.raises(llm.LLMAnnotationError, match="unsupported LLM provider"):
        llm.annotate_with_provider("local", "model", "key", _request())


def test_llm_provider_does_not_alias_claude_to_anthropic():
    with pytest.raises(llm.LLMAnnotationError, match="unsupported LLM provider"):
        llm.annotate_with_provider("claude", "model", "key", _request())


def test_load_annotation_schema_extracts_openaca_extension_from_schema_file(tmp_path):
    schema = json.loads(llm.SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$defs"]["openaca_extension"]["properties"]["review_note"] = {"type": "string"}
    schema_path = tmp_path / "openaca.schema.json"
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
