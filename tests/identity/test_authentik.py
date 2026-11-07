"""authentik adapter tests with respx (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from conductor.identity.authentik import AuthentikAdminAdapter
from conductor.identity.errors import IdentityNotProvisioned, IdentityProviderError
from conftest import synthetic_password  # type: ignore[import-not-found]

BASE = "https://auth.example.com/api/v3"
TOKEN = "synthetic-authentik-service-token"
UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _user(*, uid: str = UUID, pk: int = 7, active: bool = True) -> dict[str, object]:
    return {
        "pk": pk,
        "uuid": UUID,
        "uid": uid,
        "username": "user.one",
        "name": "Synthetic User",
        "is_active": active,
        "groups_obj": [{"name": "cloudstack-users"}],
        "attributes": {"cloudstack_username": "user.one"},
    }


def _adapter() -> AuthentikAdminAdapter:
    return AuthentikAdminAdapter(base_url="https://auth.example.com", api_prefix="/api/v3", token=TOKEN)


@pytest.mark.asyncio
@respx.mock
async def test_find_user_by_subject_uses_uuid_filter() -> None:
    route = respx.get(f"{BASE}/core/users/").mock(return_value=httpx.Response(200, json={"results": [_user()]}))
    adapter = _adapter()
    found = await adapter.find_user_by_subject(UUID)
    assert found is not None and found.pk == 7
    request = route.calls[0].request
    assert request.url.params["uuid"] == UUID
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_find_user_by_subject_does_not_match_username() -> None:
    respx.get(f"{BASE}/core/users/").mock(
        return_value=httpx.Response(200, json={"results": [_user(uid="another-uid")]})
    )
    adapter = _adapter()
    assert await adapter.find_user_by_subject("user.one") is None
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_set_password_posts_json_and_never_puts_password_in_url() -> None:
    respx.get(f"{BASE}/core/users/").mock(return_value=httpx.Response(200, json={"results": [_user()]}))
    route = respx.post(f"{BASE}/core/users/7/set_password/").mock(return_value=httpx.Response(204))
    adapter = _adapter()
    await adapter.set_password(UUID, synthetic_password())
    request = route.calls[0].request
    assert request.url.query == b""
    assert json.loads(request.content) == {"password": synthetic_password()}
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_set_password_does_not_echo_password_on_error() -> None:
    respx.get(f"{BASE}/core/users/").mock(return_value=httpx.Response(200, json={"results": [_user()]}))
    respx.post(f"{BASE}/core/users/7/set_password/").mock(
        return_value=httpx.Response(400, text="Synthetic-Pass-1 is not allowed")
    )
    adapter = _adapter()
    with pytest.raises(IdentityProviderError) as error:
        await adapter.set_password(UUID, synthetic_password())
    assert synthetic_password() not in str(error.value)
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_mfa_ready_reads_confirmed_devices() -> None:
    respx.get(f"{BASE}/core/users/").mock(return_value=httpx.Response(200, json={"results": [_user()]}))
    route = respx.get(f"{BASE}/authenticators/admin/all/").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"pk": "dev-1", "name": "phone", "type": "totp", "confirmed": True},
                {"pk": "dev-2", "name": "pending", "type": "webauthn", "confirmed": False},
            ],
        )
    )
    adapter = _adapter()
    assert await adapter.mfa_ready(UUID) is True
    assert route.calls[0].request.url.params["user"] == "7"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_mfa_ready_falls_back_to_per_type_endpoints() -> None:
    respx.get(f"{BASE}/core/users/").mock(return_value=httpx.Response(200, json={"results": [_user()]}))
    respx.get(f"{BASE}/authenticators/admin/all/").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/authenticators/admin/totp/").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(f"{BASE}/authenticators/admin/webauthn/").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"pk": "dev-1", "name": "key", "confirmed": True, "user": {"pk": 7}}]},
        )
    )
    adapter = _adapter()
    assert await adapter.mfa_ready(UUID) is True
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_inactive_user_is_not_provisioned() -> None:
    respx.get(f"{BASE}/core/users/").mock(return_value=httpx.Response(200, json={"results": [_user(active=False)]}))
    adapter = _adapter()
    with pytest.raises(IdentityNotProvisioned):
        await adapter.set_password(UUID, synthetic_password())
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_revoke_sessions_deletes_each_session() -> None:
    respx.get(f"{BASE}/core/users/").mock(return_value=httpx.Response(200, json={"results": [_user()]}))
    respx.get(f"{BASE}/core/authenticated_sessions/").mock(
        return_value=httpx.Response(200, json={"results": [{"uuid": "session-1"}, {"uuid": "session-2"}]})
    )
    deletes = respx.delete(url__regex=rf"{BASE}/core/authenticated_sessions/session-[12]/").mock(
        return_value=httpx.Response(204)
    )
    adapter = _adapter()
    assert await adapter.revoke_sessions(UUID) == 2
    assert len(deletes.calls) == 2
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_authentik_error_does_not_expose_token(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(f"{BASE}/core/users/").mock(return_value=httpx.Response(403))
    adapter = _adapter()
    with pytest.raises(IdentityProviderError) as error:
        await adapter.find_user_by_subject(UUID)
    assert TOKEN not in str(error.value)
    assert TOKEN not in caplog.text
    await adapter.aclose()
