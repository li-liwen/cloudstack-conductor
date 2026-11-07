"""Native CloudStack login adapter tests with respx (no network)."""

from __future__ import annotations

import urllib.parse

import httpx
import pytest
import respx

from conductor.identity.cloudstack_login import CloudStackNativeLoginAdapter
from conductor.identity.errors import (
    IdentityProviderCapabilityError,
    LegacyCredentialsInvalid,
    LegacySecondFactorInvalid,
    LegacySecondFactorRequired,
)
from conftest import legacy_password  # type: ignore[import-not-found]

API = "https://cloud.example.com/client/api"
USER_ID = "11111111-1111-4111-8111-111111111111"
ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
DOMAIN_ID = "33333333-3333-4333-8333-333333333333"


def _body(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in urllib.parse.parse_qs(request.content.decode()).items()}


def _login(**overrides: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "userid": USER_ID,
        "domainid": DOMAIN_ID,
        "sessionkey": "synthetic-session-key",
        "username": "user.one",
        "account": "account-one",
        "is2faenabled": "false",
        "is2faverified": "true",
    }
    payload.update(overrides)
    return {"loginresponse": payload}


def _users() -> dict[str, object]:
    return {"listusersresponse": {"count": 1, "user": [{"id": USER_ID, "accountid": ACCOUNT_ID}]}}


@pytest.mark.asyncio
@respx.mock
async def test_native_login_uses_post_body_and_reads_account_id() -> None:
    route = respx.post(API).mock(
        side_effect=[
            httpx.Response(200, json=_login()),
            httpx.Response(200, json=_users()),
            httpx.Response(200, json={"logoutresponse": {"description": "success"}}),
        ]
    )
    adapter = CloudStackNativeLoginAdapter(api_url=API)
    session = await adapter.native_login("user.one", legacy_password(), DOMAIN_ID)
    assert (session.user_id, session.account_id, session.domain_id) == (USER_ID, ACCOUNT_ID, DOMAIN_ID)
    assert session.session_key == "synthetic-session-key"
    assert session.mfa_enabled is False

    requests = [call.request for call in route.calls]
    assert all(request.url.query == b"" for request in requests)
    first = _body(requests[0])
    assert first["command"] == "login"
    assert first["username"] == "user.one"
    assert first["password"] == legacy_password()
    assert first["domainid"] == DOMAIN_ID
    assert "apiKey" not in first and "signature" not in first
    second = _body(requests[1])
    assert second["command"] == "listUsers"
    assert second["id"] == USER_ID
    assert second["sessionkey"] == "synthetic-session-key"

    await session.logout()
    assert _body(route.calls[2].request)["command"] == "logout"
    await session.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_native_login_validates_second_factor() -> None:
    route = respx.post(API).mock(
        side_effect=[
            httpx.Response(200, json=_login(is2faenabled="true", is2faverified="false")),
            httpx.Response(200, json=_users()),
            httpx.Response(200, json={"validateusertwofactorauthenticationcoderesponse": {"success": "true"}}),
            httpx.Response(200, json={"logoutresponse": {"description": "success"}}),
        ]
    )
    adapter = CloudStackNativeLoginAdapter(api_url=API)
    session = await adapter.native_login("user.one", legacy_password(), DOMAIN_ID, otp="123456")
    assert session.mfa_enabled is True
    assert session.mfa_verified is True
    validate = _body(route.calls[2].request)
    assert validate["command"] == "validateUserTwoFactorAuthenticationCode"
    assert validate["codefor2fa"] == "123456"
    await session.logout()
    await session.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_missing_second_factor_code_fails_and_logs_out() -> None:
    route = respx.post(API).mock(
        side_effect=[
            httpx.Response(200, json=_login(is2faenabled="true", is2faverified="false")),
            httpx.Response(200, json=_users()),
            httpx.Response(200, json={"logoutresponse": {"description": "success"}}),
        ]
    )
    adapter = CloudStackNativeLoginAdapter(api_url=API)
    with pytest.raises(LegacySecondFactorRequired):
        await adapter.native_login("user.one", legacy_password(), DOMAIN_ID)
    assert _body(route.calls[-1].request)["command"] == "logout"


@pytest.mark.asyncio
@respx.mock
async def test_invalid_second_factor_is_rejected() -> None:
    route = respx.post(API).mock(
        side_effect=[
            httpx.Response(200, json=_login(is2faenabled="true", is2faverified="false")),
            httpx.Response(200, json=_users()),
            httpx.Response(401, json={"errorresponse": {"errorcode": 435, "errortext": "bad code"}}),
            httpx.Response(200, json={"logoutresponse": {"description": "success"}}),
        ]
    )
    adapter = CloudStackNativeLoginAdapter(api_url=API)
    with pytest.raises(LegacySecondFactorInvalid):
        await adapter.native_login("user.one", legacy_password(), DOMAIN_ID, otp="000000")
    assert _body(route.calls[-1].request)["command"] == "logout"


@pytest.mark.asyncio
@respx.mock
async def test_unknown_second_factor_state_is_a_capability_error() -> None:
    respx.post(API).mock(
        side_effect=[
            httpx.Response(200, json={"loginresponse": {"userid": USER_ID, "domainid": DOMAIN_ID, "sessionkey": "s"}}),
            httpx.Response(200, json={"logoutresponse": {"description": "success"}}),
        ]
    )
    adapter = CloudStackNativeLoginAdapter(api_url=API)
    with pytest.raises(IdentityProviderCapabilityError):
        await adapter.native_login("user.one", legacy_password(), DOMAIN_ID)


@pytest.mark.asyncio
@respx.mock
async def test_invalid_legacy_password_is_rejected_without_echoing_upstream_text() -> None:
    respx.post(API).mock(
        return_value=httpx.Response(
            401,
            json={"errorresponse": {"errorcode": 531, "errortext": "user.one Legacy-Pass-1"}},
        )
    )
    adapter = CloudStackNativeLoginAdapter(api_url=API)
    with pytest.raises(LegacyCredentialsInvalid) as error:
        await adapter.native_login("user.one", legacy_password(), DOMAIN_ID)
    assert legacy_password() not in str(error.value)


def test_session_repr_hides_the_session_key() -> None:
    # Construction only; no network is used by this assertion.
    adapter = CloudStackNativeLoginAdapter(api_url=API)
    assert "synthetic-session-key" not in repr(adapter)
