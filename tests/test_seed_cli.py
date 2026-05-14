import json
import zipfile

import yaml
from click.testing import CliRunner

from tools.seed import llm as seed_llm
from tools.seed.__main__ import discovery_reasons, main


def _llm_annotate_result(asve: dict, evidence=None):
    return seed_llm.LLMAnnotationResult(decision="annotate", asve=asve, evidence=evidence)


def _write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def _ghsa_record() -> dict:
    return {
        "schema_version": "1.7.5",
        "id": "GHSA-abcd-ef12-3456",
        "aliases": ["CVE-2026-12345"],
        "modified": "2026-05-13T00:00:00Z",
        "summary": "mcp-demo allows command injection",
        "details": "A Model Context Protocol server executes arbitrary commands.",
        "affected": [{"package": {"ecosystem": "npm", "name": "mcp-demo"}}],
        "references": [{"type": "ADVISORY", "url": "https://example.test/advisory"}],
    }


def _mal_record() -> dict:
    return {
        "schema_version": "1.7.5",
        "id": "MAL-2026-1234",
        "modified": "2026-05-13T00:00:00Z",
        "summary": "Malicious code in mcp-runcmd-server",
        "details": "This package executes arbitrary code during install.",
        "affected": [{"package": {"ecosystem": "PyPI", "name": "mcp-runcmd-server"}}],
    }


def _record(package_name: str, summary: str, details: str = "") -> dict:
    return {
        "schema_version": "1.7.5",
        "id": "GHSA-test-test-test",
        "modified": "2026-05-13T00:00:00Z",
        "summary": summary,
        "details": details,
        "affected": [{"package": {"ecosystem": "PyPI", "name": package_name}}],
    }


def test_discovery_matches_known_agent_stack_packages_without_mcp_token():
    examples = [
        _record(
            "fastmcp",
            "FastMCP has a command injection vulnerability",
        ),
        _record(
            "langchain-core",
            "LangChain serialization injection vulnerability enables secret extraction",
        ),
        _record(
            "praisonaiagents",
            "PraisonAIAgents: path traversal via unvalidated glob pattern",
        ),
        _record(
            "@anthropic-ai/sdk",
            "Claude SDK has insecure default file permissions in local filesystem memory tool",
        ),
    ]

    for record in examples:
        assert "package_name_agent_stack" in discovery_reasons(record)


def test_discovery_matches_agent_ai_feature_topics_in_generic_packages():
    examples = [
        _record(
            "gitlab",
            "GitLab Duo prompt injection can exfiltrate issue data",
        ),
        _record(
            "kibana",
            "Kibana Gemini connector leaks credentials through AI assistant tool calls",
        ),
        _record(
            "cursor",
            "Cursor terminal Cmd-K command execution via prompt injection",
        ),
    ]

    for record in examples:
        assert "topic_agent_ai_feature" in discovery_reasons(record)


def test_seed_writes_reviewable_candidate_for_mcp_record(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    candidate = yaml.safe_load((out / "GHSA-abcd-ef12-3456.yaml").read_text(encoding="utf-8"))
    assert candidate["id"] == "GHSA-abcd-ef12-3456"
    assert candidate["aliases"] == ["CVE-2026-12345"]
    assert candidate["_candidate"]["review_status"] == "needs_review"
    assert "package_name_mcp" in candidate["_candidate"]["matched_by"]
    assert candidate["database_specific"]["asve"]["taxonomies"]["owasp_agentic_top10"] == ["asi05"]
    assert candidate["summary"] == "mcp-demo allows command injection"


def test_seed_marks_mal_records_as_malicious_package(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "MAL-2026-1234.json", _mal_record())

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    candidate = yaml.safe_load((out / "MAL-2026-1234.yaml").read_text(encoding="utf-8"))
    asve = candidate["database_specific"]["asve"]
    assert asve["threat_kind"] == "malicious_package"
    assert asve["taxonomies"]["owasp_agentic_top10"] == ["asi05"]


def test_seed_marks_ghsa_records_with_mal_alias_as_malicious_package(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    record = _ghsa_record()
    record["aliases"] = ["CVE-2026-12345", "MAL-2026-1234"]
    _write_json(dump / "GHSA-abcd-ef12-3456.json", record)

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    candidate = yaml.safe_load((out / "GHSA-abcd-ef12-3456.yaml").read_text(encoding="utf-8"))
    asve = candidate["database_specific"]["asve"]
    assert asve["threat_kind"] == "malicious_package"


def test_seed_llm_provider_ignores_threat_kind_for_non_mal_records(tmp_path, monkeypatch):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    def fake_annotate(provider, model, api_key, request):
        return _llm_annotate_result(
            {
                "taxonomies": {"owasp_agentic_top10": ["asi03"]},
                "evidence_level": "likely",
                "threat_kind": "malicious_package",
            }
        )

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "openai",
            "--llm-model",
            "test-model",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code == 0, result.output
    candidate = yaml.safe_load((out / "GHSA-abcd-ef12-3456.yaml").read_text(encoding="utf-8"))
    assert "threat_kind" not in candidate["database_specific"]["asve"]


def test_seed_skips_records_already_covered_by_existing_overlay_alias(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())
    (existing / "CVE-2026-12345.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.7.5",
                "id": "CVE-2026-12345",
                "aliases": ["GHSA-abcd-ef12-3456"],
                "modified": "2026-05-13T00:00:00Z",
                "database_specific": {
                    "asve": {
                        "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                        "evidence_level": "likely",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    assert not (out / "GHSA-abcd-ef12-3456.yaml").exists()
    assert "1 already in overlays" in result.output
    assert "0 duplicate aliases" in result.output


def test_seed_dry_run_does_not_write_candidates(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    result = CliRunner().invoke(
        main, [str(dump), "--out", str(out), "--existing", str(existing), "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "would write" in result.output
    assert not out.exists()


def test_seed_skips_candidate_with_unsafe_id(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    bad = {
        "schema_version": "1.7.5",
        "id": "../evil",
        "modified": "2026-05-13T00:00:00Z",
        "summary": "mcp server command injection",
        "affected": [{"package": {"ecosystem": "npm", "name": "mcp-demo"}}],
    }
    _write_json(dump / "evil.json", bad)

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0
    assert not (tmp_path / "evil.yaml").exists()
    assert "unsafe" in result.output
    assert "0 candidate" in result.output


def test_seed_deduplicates_aliases_within_run(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    ghsa = _ghsa_record()  # id=GHSA-abcd-ef12-3456, aliases=[CVE-2026-12345]
    cve = {
        "schema_version": "1.7.5",
        "id": "CVE-2026-12345",
        "aliases": ["GHSA-abcd-ef12-3456"],
        "modified": "2026-05-13T00:00:00Z",
        "summary": "mcp-demo allows command injection",
        "affected": [{"package": {"ecosystem": "npm", "name": "mcp-demo"}}],
        "references": [{"type": "ADVISORY", "url": "https://example.test/advisory"}],
    }
    # Sorted iteration processes GHSA first, then CVE should be deduplicated
    _write_json(dump / "GHSA-abcd-ef12-3456.json", ghsa)
    _write_json(dump / "CVE-2026-12345.json", cve)

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    written_files = list(out.glob("*.yaml"))
    assert len(written_files) == 1
    assert "0 already in overlays" in result.output
    assert "1 duplicate alias" in result.output


def test_seed_reads_osv_all_zip(tmp_path):
    zip_path = tmp_path / "all.zip"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    existing.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("GHSA-abcd-ef12-3456.json", json.dumps(_ghsa_record()))

    result = CliRunner().invoke(
        main, [str(zip_path), "--out", str(out), "--existing", str(existing)]
    )

    assert result.exit_code == 0, result.output
    assert (out / "GHSA-abcd-ef12-3456.yaml").exists()


def test_seed_reads_only_new_records_from_top_level_modified_index(tmp_path):
    records = tmp_path / "records"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    records.mkdir()
    existing.mkdir()
    (records / "npm").mkdir()
    (records / "PyPI").mkdir()
    _write_json(records / "npm" / "GHSA-abcd-ef12-3456.json", _ghsa_record())
    older = _mal_record()
    older["id"] = "MAL-2026-9999"
    _write_json(records / "PyPI" / "MAL-2026-9999.json", older)
    modified = tmp_path / "modified_id.csv"
    modified.write_text(
        "\n".join(
            [
                "2026-05-13T00:00:00Z,npm/GHSA-abcd-ef12-3456",
                "2026-05-12T00:00:00Z,PyPI/MAL-2026-9999",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "last_modified": "2026-05-12T00:00:00Z",
                "last_modified_ids": ["PyPI/MAL-2026-9999"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "--modified-index",
            str(modified),
            "--records-root",
            str(records),
            "--state",
            str(state),
            "--out",
            str(out),
            "--existing",
            str(existing),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "GHSA-abcd-ef12-3456.yaml").exists()
    assert not (out / "MAL-2026-9999.yaml").exists()
    assert yaml.safe_load(state.read_text(encoding="utf-8")) == {
        "last_modified": "2026-05-13T00:00:00Z",
        "last_modified_ids": ["npm/GHSA-abcd-ef12-3456"],
    }


def test_seed_reads_per_ecosystem_modified_index(tmp_path):
    records = tmp_path / "records" / "PyPI"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    records.mkdir(parents=True)
    existing.mkdir()
    _write_json(records / "MAL-2026-1234.json", _mal_record())
    modified = tmp_path / "modified_id.csv"
    modified.write_text("2026-05-13T00:00:00Z,MAL-2026-1234\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "--modified-index",
            str(modified),
            "--records-root",
            str(records),
            "--out",
            str(out),
            "--existing",
            str(existing),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "MAL-2026-1234.yaml").exists()


def test_seed_reads_per_ecosystem_modified_index_from_all_zip(tmp_path):
    records = tmp_path / "records" / "PyPI"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    records.mkdir(parents=True)
    existing.mkdir()
    with zipfile.ZipFile(records / "all.zip", "w") as zf:
        zf.writestr("MAL-2026-1234.json", json.dumps(_mal_record()))
    modified = tmp_path / "modified_id.csv"
    modified.write_text("2026-05-13T00:00:00Z,MAL-2026-1234\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "--modified-index",
            str(modified),
            "--records-root",
            str(records),
            "--out",
            str(out),
            "--existing",
            str(existing),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "MAL-2026-1234.yaml").exists()


def test_seed_reads_top_level_modified_index_from_ecosystem_all_zip(tmp_path):
    records = tmp_path / "records"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    (records / "npm").mkdir(parents=True)
    existing.mkdir()
    with zipfile.ZipFile(records / "npm" / "all.zip", "w") as zf:
        zf.writestr("GHSA-abcd-ef12-3456.json", json.dumps(_ghsa_record()))
    modified = tmp_path / "modified_id.csv"
    modified.write_text(
        "2026-05-13T00:00:00Z,npm/GHSA-abcd-ef12-3456\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "--modified-index",
            str(modified),
            "--records-root",
            str(records),
            "--out",
            str(out),
            "--existing",
            str(existing),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "GHSA-abcd-ef12-3456.yaml").exists()


def test_seed_dry_run_does_not_update_incremental_state(tmp_path):
    records = tmp_path / "records" / "npm"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    records.mkdir(parents=True)
    existing.mkdir()
    _write_json(records / "GHSA-abcd-ef12-3456.json", _ghsa_record())
    modified = tmp_path / "modified_id.csv"
    modified.write_text("2026-05-13T00:00:00Z,npm/GHSA-abcd-ef12-3456\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text('{"last_modified": "2026-05-12T00:00:00Z"}\n', encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "--modified-index",
            str(modified),
            "--records-root",
            str(tmp_path / "records"),
            "--state",
            str(state),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(state.read_text(encoding="utf-8")) == {
        "last_modified": "2026-05-12T00:00:00Z"
    }


def test_seed_modified_index_deduplicates_aliases_within_run(tmp_path):
    records = tmp_path / "records" / "npm"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    records.mkdir(parents=True)
    existing.mkdir()
    ghsa = _ghsa_record()
    cve = {
        "schema_version": "1.7.5",
        "id": "CVE-2026-12345",
        "aliases": ["GHSA-abcd-ef12-3456"],
        "modified": "2026-05-13T00:00:00Z",
        "summary": "mcp-demo allows command injection",
        "affected": [{"package": {"ecosystem": "npm", "name": "mcp-demo"}}],
    }
    _write_json(records / "GHSA-abcd-ef12-3456.json", ghsa)
    _write_json(records / "CVE-2026-12345.json", cve)
    modified = tmp_path / "modified_id.csv"
    modified.write_text(
        "\n".join(
            [
                "2026-05-13T00:01:00Z,npm/GHSA-abcd-ef12-3456",
                "2026-05-13T00:00:00Z,npm/CVE-2026-12345",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "--modified-index",
            str(modified),
            "--records-root",
            str(tmp_path / "records"),
            "--out",
            str(out),
            "--existing",
            str(existing),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(list(out.glob("*.yaml"))) == 1
    assert "0 already in overlays" in result.output
    assert "1 duplicate alias" in result.output


def test_seed_incremental_does_not_drop_records_at_cursor_timestamp(tmp_path):
    """OSV may publish new rows at the same timestamp as the cursor; they must not be skipped."""
    records = tmp_path / "records" / "npm"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    records.mkdir(parents=True)
    existing.mkdir()

    # Two records with the same modified timestamp — GHSA was seen in a prior run,
    # CVE-NEW is freshly published at the same second.
    _write_json(records / "GHSA-abcd-ef12-3456.json", _ghsa_record())
    new_record = {
        "schema_version": "1.7.5",
        "id": "CVE-2026-99999",
        "modified": "2026-05-13T00:00:00Z",
        "summary": "New mcp-demo vulnerability at same timestamp",
        "affected": [{"package": {"ecosystem": "npm", "name": "mcp-demo"}}],
    }
    _write_json(records / "CVE-2026-99999.json", new_record)

    modified = tmp_path / "modified_id.csv"
    modified.write_text(
        "\n".join(
            [
                "2026-05-13T00:00:00Z,npm/CVE-2026-99999",
                "2026-05-13T00:00:00Z,npm/GHSA-abcd-ef12-3456",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # Simulate a prior run that saw GHSA but not CVE-99999 (published later at same timestamp)
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "last_modified": "2026-05-13T00:00:00Z",
                "last_modified_ids": ["npm/GHSA-abcd-ef12-3456"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "--modified-index",
            str(modified),
            "--records-root",
            str(tmp_path / "records"),
            "--state",
            str(state),
            "--out",
            str(out),
            "--existing",
            str(existing),
        ],
    )

    assert result.exit_code == 0, result.output
    # CVE-99999 is new at the cursor timestamp — must be seeded
    assert (out / "CVE-2026-99999.yaml").exists()
    # GHSA was already seen — must not be duplicated
    assert not (out / "GHSA-abcd-ef12-3456.yaml").exists()
    # State accumulates both IDs at the cursor timestamp
    saved = yaml.safe_load(state.read_text(encoding="utf-8"))
    assert saved["last_modified"] == "2026-05-13T00:00:00Z"
    assert set(saved["last_modified_ids"]) == {
        "npm/CVE-2026-99999",
        "npm/GHSA-abcd-ef12-3456",
    }


def test_seed_llm_provider_receives_framework_docs_and_overrides_annotation(tmp_path, monkeypatch):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    record = _ghsa_record()
    record["summary"] = "mcp-demo allows prompt injection through tool metadata"
    record["details"] = "A malicious MCP tool description can hijack agent tool calls."
    _write_json(dump / "GHSA-abcd-ef12-3456.json", record)

    calls = []

    def fake_annotate(provider, model, api_key, request):
        calls.append((provider, model, api_key, request))
        return _llm_annotate_result(
            {
                "taxonomies": {
                    "owasp_agentic_top10": ["asi01"],
                    "owasp_mcp_top10": ["mcp03:2025"],
                    "owasp_llm_top10": ["llm01:2025"],
                    "mitre_atlas": ["AML.T0051.001", "AML.T0053"],
                },
                "evidence_level": "likely",
            },
            [{"field": "details", "quote": "tool description"}],
        )

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "openai",
            "--llm-model",
            "test-model",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    provider, model, api_key, request = calls[0]
    assert provider == "openai"
    assert model == "test-model"
    assert api_key == "test-key"
    assert request["osv_record"]["id"] == "GHSA-abcd-ef12-3456"
    assert request["annotation_schema"]["required"] == ["taxonomies", "evidence_level"]
    assert "component_type" not in request["annotation_schema"]["properties"]
    assert "mitre-atlas.md" in request["framework_documents"]
    assert "owasp-mcp-top-10-2025.md" in request["framework_documents"]
    candidate = yaml.safe_load((out / "GHSA-abcd-ef12-3456.yaml").read_text(encoding="utf-8"))
    metadata = candidate["_candidate"]
    asve = candidate["database_specific"]["asve"]
    assert metadata["annotation_source"] == "llm"
    assert metadata["llm_provider"] == "openai"
    assert metadata["llm_model"] == "test-model"
    assert "mitre-atlas.md" in metadata["framework_documents"]
    assert asve["taxonomies"] == {
        "owasp_agentic_top10": ["asi01"],
        "owasp_mcp_top10": ["mcp03:2025"],
        "owasp_llm_top10": ["llm01:2025"],
        "mitre_atlas": ["AML.T0051.001", "AML.T0053"],
    }
    assert candidate["_evidence"] == [{"field": "details", "quote": "tool description"}]


def test_seed_llm_provider_prints_progress_before_annotation(tmp_path, monkeypatch):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    def fake_annotate(provider, model, api_key, request):
        return _llm_annotate_result(
            {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "likely",
            },
            None,
        )

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "anthropic",
            "--llm-model",
            "test-model",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "llm: annotating GHSA-abcd-ef12-3456 with anthropic/test-model" in result.output


def test_seed_llm_provider_can_read_api_key_env(tmp_path, monkeypatch):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())
    monkeypatch.setenv("ASVE_LLM_API_KEY", "env-key")
    calls = []

    def fake_annotate(provider, model, api_key, request):
        calls.append((provider, model, api_key, request))
        return _llm_annotate_result(
            {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "likely",
            },
            None,
        )

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "anthropic",
            "--llm-model",
            "anthropic-test",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0][0] == "anthropic"
    assert calls[0][1] == "anthropic-test"
    assert calls[0][2] == "env-key"


def test_seed_llm_provider_writes_rejected_candidate_artifact(tmp_path, monkeypatch):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    def fake_annotate(provider, model, api_key, request):
        return seed_llm.LLMAnnotationResult(
            decision="reject",
            reject_reason="not_agent_stack",
            evidence=[{"field": "summary", "quote": "mcp-demo"}],
        )

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "openai",
            "--llm-model",
            "test-model",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (out / "GHSA-abcd-ef12-3456.yaml").exists()
    rejected = yaml.safe_load((out / "rejected" / "GHSA-abcd-ef12-3456.yaml").read_text())
    assert rejected["_candidate"]["review_status"] == "rejected"
    assert rejected["_candidate"]["reject_reason"] == "not_agent_stack"
    assert rejected["_evidence"] == [{"field": "summary", "quote": "mcp-demo"}]


def test_seed_llm_provider_requires_model_when_enabled(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "openai",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code != 0
    assert "--llm-model is required" in result.output
    assert not out.exists()


def test_seed_llm_provider_error_writes_rejected_candidate_artifact(tmp_path, monkeypatch):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    def fake_annotate(provider, model, api_key, request):
        raise seed_llm.LLMAnnotationError("LLM provider returned invalid JSON")

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "openai",
            "--llm-model",
            "test-model",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code == 0
    assert "llm: rejected GHSA-abcd-ef12-3456 after provider error" in result.output
    rejected = yaml.safe_load((out / "rejected" / "GHSA-abcd-ef12-3456.yaml").read_text())
    assert rejected["_candidate"]["review_status"] == "rejected"
    assert rejected["_candidate"]["reject_reason"] == "unsupported_record"
    assert rejected["_candidate"]["llm_error"] == "LLM provider returned invalid JSON"
    assert rejected["_evidence"] == [
        {"field": "summary", "quote": "mcp-demo allows command injection"}
    ]


def test_seed_llm_provider_http_error_fails_hard(tmp_path, monkeypatch):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    def fake_annotate(provider, model, api_key, request):
        raise seed_llm.LLMProviderError(
            "LLM provider returned HTTP 400: json_schema response format not supported"
        )

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "openai",
            "--llm-model",
            "gpt-4",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code != 0
    assert "HTTP 400" in result.output
    assert not (out / "rejected").exists()


def test_seed_llm_provider_invalid_annotation_writes_rejected_candidate_artifact(
    tmp_path, monkeypatch
):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    def fake_annotate(provider, model, api_key, request):
        return seed_llm._project_response({"database_specific": ["not", "a", "dict"]})

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "openai",
            "--llm-model",
            "test-model",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code == 0
    assert "llm: rejected GHSA-abcd-ef12-3456 after provider error" in result.output
    rejected = yaml.safe_load((out / "rejected" / "GHSA-abcd-ef12-3456.yaml").read_text())
    assert rejected["_candidate"]["review_status"] == "rejected"
    assert rejected["_candidate"]["reject_reason"] == "unsupported_record"
    assert rejected["_candidate"]["llm_error"] == "LLM response must include database_specific.asve"


def test_seed_api_key_env_alone_does_not_enable_llm_mode(tmp_path, monkeypatch):
    """ASVE_SEED_LLM_API_KEY in env must not trigger LLM mode; plain asve-seed must still work."""
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())
    monkeypatch.setenv("ASVE_SEED_LLM_API_KEY", "secret-key")

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    assert (out / "GHSA-abcd-ef12-3456.yaml").exists()
    candidate = yaml.safe_load((out / "GHSA-abcd-ef12-3456.yaml").read_text(encoding="utf-8"))
    assert candidate["_candidate"]["annotation_source"] == "deterministic"


def test_seed_limit_stops_after_requested_candidates_without_advancing_state(tmp_path):
    records = tmp_path / "records" / "npm"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    state = tmp_path / "state.json"
    records.mkdir(parents=True)
    existing.mkdir()
    first = _ghsa_record()
    second = _ghsa_record()
    second["id"] = "GHSA-bbbb-ef12-3456"
    second["aliases"] = ["CVE-2026-22222"]
    _write_json(records / "GHSA-abcd-ef12-3456.json", first)
    _write_json(records / "GHSA-bbbb-ef12-3456.json", second)
    modified = tmp_path / "modified_id.csv"
    modified.write_text(
        "\n".join(
            [
                "2026-05-13T00:01:00Z,npm/GHSA-abcd-ef12-3456",
                "2026-05-13T00:00:00Z,npm/GHSA-bbbb-ef12-3456",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "--modified-index",
            str(modified),
            "--records-root",
            str(tmp_path / "records"),
            "--state",
            str(state),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(list(out.glob("*.yaml"))) == 1
    assert "limit 1 reached" in result.output
    assert not state.exists()


def test_seed_limit_not_reached_still_advances_state(tmp_path):
    """When --limit is provided but the run exhausts all records before hitting it,
    state should be written so the next run does not reprocess the same records."""
    records = tmp_path / "records" / "npm"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    state = tmp_path / "state.json"
    records.mkdir(parents=True)
    existing.mkdir()
    record = _ghsa_record()
    _write_json(records / "GHSA-abcd-ef12-3456.json", record)
    modified = tmp_path / "modified_id.csv"
    modified.write_text(
        "2026-05-13T00:01:00Z,npm/GHSA-abcd-ef12-3456\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "--modified-index",
            str(modified),
            "--records-root",
            str(tmp_path / "records"),
            "--state",
            str(state),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--limit",
            "10",  # higher than the 1 matching record
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(list(out.glob("*.yaml"))) == 1
    assert "limit" not in result.output
    assert state.exists(), "state must be written when limit was not the reason for stopping"
    saved = yaml.safe_load(state.read_text(encoding="utf-8"))
    assert saved["last_modified"] == "2026-05-13T00:01:00Z"


def test_seed_llm_provider_does_not_backfill_missing_annotation_from_heuristics(
    tmp_path, monkeypatch
):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    def fake_annotate(provider, model, api_key, request):
        return seed_llm.LLMAnnotationResult(decision="annotate", asve={}, evidence=None)

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "openai",
            "--llm-model",
            "test-model",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code != 0
    assert "taxonomies" in result.output
    assert not (out / "GHSA-abcd-ef12-3456.yaml").exists()


def test_seed_llm_provider_rejects_missing_annotation_block(tmp_path, monkeypatch):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    def fake_annotate(provider, model, api_key, request):
        return seed_llm.LLMAnnotationResult(decision="annotate", asve=None, evidence=None)

    monkeypatch.setattr(seed_llm, "annotate_with_provider", fake_annotate)

    result = CliRunner().invoke(
        main,
        [
            str(dump),
            "--out",
            str(out),
            "--existing",
            str(existing),
            "--llm-provider",
            "openai",
            "--llm-model",
            "test-model",
            "--llm-api-key",
            "test-key",
        ],
    )

    assert result.exit_code != 0
    assert "database_specific.asve" in result.output
    assert not (out / "GHSA-abcd-ef12-3456.yaml").exists()
