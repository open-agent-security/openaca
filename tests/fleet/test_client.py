import httpx
import pytest

from tools.fleet.client import (
    FleetAuthError,
    FleetClient,
    FleetPayloadTooLargeError,
    FleetServerError,
    FleetValidationError,
)


def test_register_asset_sends_bearer_token_and_returns_asset_id():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["user_agent"] = request.headers.get("user-agent")
        seen["request_id"] = request.headers.get("x-request-id")
        seen["json"] = request.read().decode()
        return httpx.Response(
            201,
            json={"asset_id": "asset-123", "dashboard_url": "https://app.test/assets/asset-123"},
        )

    client = FleetClient(
        api_url="https://api.test",
        token="ot_TEST",
        transport=httpx.MockTransport(handler),
        request_id_factory=lambda: "req-test",
    )

    result = client.register_asset({"asset_type": "endpoint", "external_id": "host"})

    assert result.asset_id == "asset-123"
    assert seen["authorization"] == "Bearer ot_TEST"
    assert isinstance(seen["user_agent"], str)
    assert seen["user_agent"].startswith("openaca-fleet/")
    assert seen["request_id"] == "req-test"
    assert '"external_id":"host"' in str(seen["json"])


def test_upload_bom_sends_bom_and_posture_findings():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read().decode()
        return httpx.Response(
            201,
            json={
                "bom_id": "bom-123",
                "asset_id": "asset-123",
                "component_count": 1,
                "finding_count": 2,
                "policy_violation_count": 0,
                "drift": {"added": 1, "removed": 0, "changed": 0},
                "dashboard_url": "https://app.test/boms/bom-123",
            },
        )

    client = FleetClient(
        api_url="https://api.test",
        token="ot_TEST",
        transport=httpx.MockTransport(handler),
    )

    result = client.upload_bom(
        {
            "asset_id": "asset-123",
            "bom": {"bomFormat": "CycloneDX"},
            "posture_findings": [{"rule_id": "openaca-posture-test"}],
        }
    )

    assert result.bom_id == "bom-123"
    assert result.finding_count == 2
    assert '"bomFormat":"CycloneDX"' in str(captured["payload"])
    assert '"posture_findings":[{"rule_id":"openaca-posture-test"}]' in str(captured["payload"])


def test_get_me_returns_org_and_token_context():
    client = FleetClient(
        api_url="https://api.test",
        token="ot_TEST",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "org": {"id": "org-123", "name": "Acme"},
                    "token": {"id": "tok-123", "name": "demo", "last_used_at": None},
                },
            )
        ),
    )

    result = client.get_me()

    assert result.org.id == "org-123"
    assert result.token.name == "demo"


def test_get_asset_returns_asset_summary():
    client = FleetClient(
        api_url="https://api.test",
        token="ot_TEST",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "id": "asset-123",
                    "asset_type": "endpoint",
                    "external_id": "host",
                    "display_name": "host",
                    "owner_clerk_user_id": None,
                    "team_name": None,
                    "metadata": {},
                    "latest_bom_id": "bom-123",
                    "last_seen_at": "2026-05-28T00:00:00Z",
                    "created_at": "2026-05-28T00:00:00Z",
                    "component_count": 3,
                },
            )
        ),
    )

    result = client.get_asset("asset-123")

    assert result.id == "asset-123"
    assert result.component_count == 3


def test_401_raises_auth_error():
    client = FleetClient(
        api_url="https://api.test",
        token="ot_TEST",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "bad"})),
    )

    with pytest.raises(FleetAuthError):
        client.get_me()


def test_413_raises_payload_too_large_error():
    client = FleetClient(
        api_url="https://api.test",
        token="ot_TEST",
        transport=httpx.MockTransport(lambda request: httpx.Response(413, json={"error": "large"})),
    )

    with pytest.raises(FleetPayloadTooLargeError):
        client.upload_bom({"bom": {}})


def test_422_includes_backend_validation_errors():
    client = FleetClient(
        api_url="https://api.test",
        token="ot_TEST",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                422, json={"error": "invalid", "validation_errors": [{"field": "bom"}]}
            )
        ),
    )

    with pytest.raises(FleetValidationError) as exc:
        client.upload_bom({"bom": {}})

    assert exc.value.validation_errors == [{"field": "bom"}]


def test_502_and_503_retry_with_backoff():
    statuses = [503, 502, 200]
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        if status == 200:
            return httpx.Response(
                200,
                json={
                    "org": {"id": "org-123", "name": "Acme"},
                    "token": {"id": "tok-123", "name": "demo", "last_used_at": None},
                },
            )
        return httpx.Response(status, json={"error": "try later"})

    client = FleetClient(
        api_url="https://api.test",
        token="ot_TEST",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )

    result = client.get_me()

    assert result.org.id == "org-123"
    assert sleeps == [1.0, 4.0]


def test_repeated_503_raises_server_error_after_retries():
    client = FleetClient(
        api_url="https://api.test",
        token="ot_TEST",
        transport=httpx.MockTransport(lambda request: httpx.Response(503, json={"error": "down"})),
        sleep=lambda _: None,
    )

    with pytest.raises(FleetServerError):
        client.get_me()
