"""Posture rule: flag Claude settings that override the Anthropic API endpoint."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from tools.posture.finding import PostureFinding, Standards

RULE_ID = "openaca-posture-api-endpoint-override"
TITLE = "Claude API endpoint is overridden"
CONFIDENCE = "medium"
REMEDIATION = (
    "Review the configured API endpoint and any adjacent token/model settings. "
    "Project-local endpoint overrides can silently route prompts, code context, "
    "and responses through a different service."
)

_ENDPOINT_KEYS = {
    "anthropic_base_url",
    "anthropic_api_url",
    "anthropic_api_base_url",
    "anthropicbaseurl",  # camelCase anthropicBaseUrl after normalisation
}
_TOKEN_KEYS = {
    "anthropic_auth_token",
    "anthropic_api_key",
}
_MODEL_KEYS = {"anthropic_model"}

_STANDARDS = Standards(
    owasp_app_top_10=["A05:2021"],
    owasp_agentic_top10=["asi04"],
)


def check_api_endpoint_override(
    manifests: list[tuple[Path, dict]],
) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for path, manifest in manifests:
        endpoint = _endpoint_override(manifest)
        if endpoint is None:
            continue
        severity = "high" if _has_token_or_model_override(manifest) else "medium"
        findings.append(
            PostureFinding(
                rule_id=RULE_ID,
                title=TITLE,
                severity=severity,
                confidence=CONFIDENCE,
                component={
                    "type": "agent_config",
                    "name": f"claude-settings/api-endpoint @ {endpoint}",
                    "source": {"url": endpoint},
                },
                active_in=["claude-code"],
                declared_by={"kind": "manifest", "path": str(path)},
                component_path=[
                    {"type": "agent_config", "name": f"api-endpoint @ {_host_label(endpoint)}"}
                ],
                standards=_STANDARDS,
                remediation=REMEDIATION,
            )
        )
    return findings


def _endpoint_override(manifest: dict) -> str | None:
    for key, value in _candidate_settings(manifest):
        if _normal_key(key) in _ENDPOINT_KEYS and isinstance(value, str) and value:
            return value
    return None


def _has_token_or_model_override(manifest: dict) -> bool:
    for key, value in _candidate_settings(manifest):
        normalized = _normal_key(key)
        if normalized in _TOKEN_KEYS and isinstance(value, str) and value:
            return True
        if normalized in _MODEL_KEYS and isinstance(value, str) and value:
            return True
    return False


def _candidate_settings(manifest: dict) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for key, value in manifest.items():
        if isinstance(key, str):
            out.append((key, value))
    env = manifest.get("env")
    if isinstance(env, dict):
        for key, value in env.items():
            if isinstance(key, str):
                out.append((key, value))
    return out


def _normal_key(key: str) -> str:
    return key.replace("-", "_").lower()


def _host_label(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    return parsed.netloc or url
