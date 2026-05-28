from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

JsonObject = dict[str, Any]


class FleetClientError(Exception):
    pass


class FleetAuthError(FleetClientError):
    pass


class FleetPayloadTooLargeError(FleetClientError):
    pass


class FleetValidationError(FleetClientError):
    def __init__(self, message: str, validation_errors: list[Any]) -> None:
        super().__init__(message)
        self.validation_errors = validation_errors


class FleetServerError(FleetClientError):
    pass


@dataclass(frozen=True)
class RegisterAssetResult:
    asset_id: str
    dashboard_url: str


@dataclass(frozen=True)
class DriftResult:
    added: int
    removed: int
    changed: int


@dataclass(frozen=True)
class BomUploadResult:
    bom_id: str
    asset_id: str
    component_count: int
    finding_count: int
    policy_violation_count: int
    drift: DriftResult
    dashboard_url: str


@dataclass(frozen=True)
class OrgResult:
    id: str
    name: str


@dataclass(frozen=True)
class TokenResult:
    id: str
    name: str
    last_used_at: str | None


@dataclass(frozen=True)
class MeResult:
    org: OrgResult
    token: TokenResult


@dataclass(frozen=True)
class AssetStatusResult:
    id: str
    asset_type: str
    external_id: str
    display_name: str
    owner_clerk_user_id: str | None
    team_name: str | None
    metadata: JsonObject
    latest_bom_id: str | None
    last_seen_at: str | None
    created_at: str
    component_count: int


class FleetClient:
    def __init__(
        self,
        *,
        api_url: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._token = token
        self._sleep = sleep
        self._request_id_factory = request_id_factory or (lambda: str(uuid.uuid4()))
        self._client = httpx.Client(transport=transport, timeout=30.0)

    def register_asset(self, payload: JsonObject) -> RegisterAssetResult:
        data = self._request("POST", "/api/v1/assets/register", payload)
        return RegisterAssetResult(
            asset_id=_required_str(data, "asset_id"),
            dashboard_url=_required_str(data, "dashboard_url"),
        )

    def upload_bom(self, payload: JsonObject) -> BomUploadResult:
        data = self._request("POST", "/api/v1/boms", payload)
        drift = _required_object(data, "drift")
        return BomUploadResult(
            bom_id=_required_str(data, "bom_id"),
            asset_id=_required_str(data, "asset_id"),
            component_count=_required_int(data, "component_count"),
            finding_count=_required_int(data, "finding_count"),
            policy_violation_count=_required_int(data, "policy_violation_count"),
            drift=DriftResult(
                added=_required_int(drift, "added"),
                removed=_required_int(drift, "removed"),
                changed=_required_int(drift, "changed"),
            ),
            dashboard_url=_required_str(data, "dashboard_url"),
        )

    def get_me(self) -> MeResult:
        data = self._request("GET", "/api/v1/me")
        org = _required_object(data, "org")
        token = _required_object(data, "token")
        return MeResult(
            org=OrgResult(id=_required_str(org, "id"), name=_required_str(org, "name")),
            token=TokenResult(
                id=_required_str(token, "id"),
                name=_required_str(token, "name"),
                last_used_at=_optional_str(token, "last_used_at"),
            ),
        )

    def get_asset(self, asset_id: str) -> AssetStatusResult:
        data = self._request("GET", f"/api/v1/assets/{asset_id}")
        return AssetStatusResult(
            id=_required_str(data, "id"),
            asset_type=_required_str(data, "asset_type"),
            external_id=_required_str(data, "external_id"),
            display_name=_required_str(data, "display_name"),
            owner_clerk_user_id=_optional_str(data, "owner_clerk_user_id"),
            team_name=_optional_str(data, "team_name"),
            metadata=_required_object(data, "metadata"),
            latest_bom_id=_optional_str(data, "latest_bom_id"),
            last_seen_at=_optional_str(data, "last_seen_at"),
            created_at=_required_str(data, "created_at"),
            component_count=_required_int(data, "component_count"),
        )

    def _request(self, method: str, path: str, payload: JsonObject | None = None) -> JsonObject:
        url = f"{self._api_url}{path}"
        delays = [1.0, 4.0]
        for attempt in range(3):
            response = self._client.request(
                method,
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "User-Agent": f"openaca-fleet/{_openaca_version()}",
                    "X-Request-Id": self._request_id_factory(),
                    "Accept": "application/json",
                },
            )
            if response.status_code in {502, 503} and attempt < len(delays):
                self._sleep(delays[attempt])
                continue
            return _handle_response(response)
        raise AssertionError("unreachable")


def _handle_response(response: httpx.Response) -> JsonObject:
    if 200 <= response.status_code < 300:
        return _response_object(response)
    body = _error_body(response)
    message = _error_message(response, body)
    if response.status_code == 401:
        raise FleetAuthError(message)
    if response.status_code == 413:
        raise FleetPayloadTooLargeError(message)
    if response.status_code == 422:
        errors = body.get("validation_errors", [])
        raise FleetValidationError(message, errors if isinstance(errors, list) else [])
    if response.status_code in {502, 503}:
        raise FleetServerError(message)
    raise FleetClientError(message)


def _response_object(response: httpx.Response) -> JsonObject:
    data = response.json()
    if not isinstance(data, dict):
        raise FleetClientError("Fleet backend returned a non-object response")
    return data


def _error_body(response: httpx.Response) -> JsonObject:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_message(response: httpx.Response, body: JsonObject) -> str:
    error = body.get("error") or body.get("detail")
    return error if isinstance(error, str) else response.reason_phrase


def _required_object(data: JsonObject, key: str) -> JsonObject:
    value = data.get(key)
    if not isinstance(value, dict):
        raise FleetClientError(f"Fleet backend response missing object field: {key}")
    return value


def _required_str(data: JsonObject, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise FleetClientError(f"Fleet backend response missing string field: {key}")
    return value


def _optional_str(data: JsonObject, key: str) -> str | None:
    value = data.get(key)
    if value is None or isinstance(value, str):
        return value
    raise FleetClientError(f"Fleet backend response has invalid string field: {key}")


def _required_int(data: JsonObject, key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise FleetClientError(f"Fleet backend response missing integer field: {key}")
    return value


def _openaca_version() -> str:
    try:
        return version("openaca")
    except PackageNotFoundError:
        return "unknown"
