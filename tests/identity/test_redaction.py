"""Secret material must not appear in public models, representations, or errors."""

from __future__ import annotations

import httpx

from conductor.identity.authentik import AuthentikAdminAdapter
from conductor.identity.cloudstack_login import CloudStackLoginSession, CloudStackNativeLoginAdapter
from conductor.identity.errors import IdentityProviderError
from conductor.identity.routes import LegacyVerifyRequest


def test_request_model_masks_password_and_otp() -> None:
    request = LegacyVerifyRequest(username="user.one", password="Synthetic-Secret-1", otp="123456")
    assert "Synthetic-Secret-1" not in repr(request)
    assert "123456" not in repr(request)
    dumped = request.model_dump_json()
    assert "Synthetic-Secret-1" not in dumped
    assert "123456" not in dumped
    assert "**********" in dumped


def test_session_representation_hides_the_session_key() -> None:
    adapter = CloudStackNativeLoginAdapter(api_url="https://cloud.example.com/client/api")
    session = CloudStackLoginSession(
        user_id="11111111-1111-4111-8111-111111111111",
        account_id="22222222-2222-4222-8222-222222222222",
        domain_id="33333333-3333-4333-8333-333333333333",
        session_key="synthetic-session-key",
        _client=httpx.AsyncClient(),
        _adapter=adapter,
    )
    assert "synthetic-session-key" not in repr(session)


def test_authentik_adapter_repr_does_not_contain_token() -> None:
    adapter = AuthentikAdminAdapter(
        base_url="https://auth.example.com",
        api_prefix="/api/v3",
        token="synthetic-service-token",
    )
    assert "synthetic-service-token" not in repr(adapter)


def test_identity_provider_error_has_a_safe_public_message() -> None:
    error = IdentityProviderError()
    assert error.public_message
    assert "password" not in error.public_message.lower()
