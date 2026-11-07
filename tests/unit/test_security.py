from __future__ import annotations

import base64

import pytest

from conductor.config import load_branding
from conductor.errors import AuthenticationError, AuthorizationError, ConfigurationError
from conductor.logging import redact
from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.principal import IdentityMapping
from conductor.security.crypto import (
    Vault,
    console_aad,
    credential_aad,
    token_hash,
)
from conductor.security.oidc import mfa_satisfied, same_issuer
from conductor.security.principal import PrincipalService
from conductor.security.rate_limit import InMemoryRateLimiter
from conductor.security.sessions import CSRF, OIDCFlow, OIDCFlowCodec, SessionCodec

KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


def test_vault_round_trip_and_aad_binding() -> None:
    vault = Vault(KEY)
    envelope = vault.encrypt("synthetic-secret", aad=credential_aad("cloud", "user"))
    assert vault.decrypt(envelope, aad=credential_aad("cloud", "user")) == "synthetic-secret"
    with pytest.raises(ConfigurationError):
        vault.decrypt(envelope, aad=credential_aad("cloud", "other-user"))
    assert "synthetic-secret" not in envelope


def test_vault_rejects_tampering() -> None:
    vault = Vault(KEY)
    envelope = vault.encrypt("value", aad=console_aad("lease"))
    parts = envelope.split(".")
    parts[3] = parts[3][:-4] + "AAAA"
    with pytest.raises(ConfigurationError):
        vault.decrypt(".".join(parts), aad=console_aad("lease"))


def test_vault_requires_a_real_key() -> None:
    with pytest.raises(ConfigurationError):
        Vault("short")


def test_token_hash_is_stable_and_not_reversible() -> None:
    token = "synthetic-launch-token"
    assert token_hash(token) == token_hash(token)
    assert token not in token_hash(token)


def test_session_round_trip_and_csrf() -> None:
    codec = SessionCodec("synthetic-session-secret", ttl_seconds=3600)
    token, csrf = codec.encode("subject", mfa=True, amr=["pwd", "mfa"])
    data = codec.decode(token)
    assert data.subject == "subject"
    assert data.mfa is True
    protector = CSRF("X-CSRF-Token")
    protector.validate(data, csrf, csrf)
    with pytest.raises(AuthorizationError):
        protector.validate(data, csrf, "wrong")
    with pytest.raises(AuthorizationError):
        protector.validate(data, None, None)


def test_session_tampering_is_rejected() -> None:
    codec = SessionCodec("synthetic-session-secret", ttl_seconds=3600)
    token, _ = codec.encode("subject")
    with pytest.raises(AuthenticationError):
        codec.decode(token[:-3] + "abc")


def test_oidc_flow_codec_round_trip() -> None:
    codec = OIDCFlowCodec("synthetic-session-secret")
    flow = OIDCFlow(
        state="state",
        nonce="nonce",
        code_verifier="verifier",
        redirect_uri="https://app.example.com/callback",
        next_path="/plans",
    )
    assert codec.decode(codec.encode(flow)) == flow


def test_mfa_claim_detection() -> None:
    assert mfa_satisfied({"amr": ["pwd", "otp"]}) is True
    assert mfa_satisfied({"acr": "urn:example:loa2"}) is True
    assert mfa_satisfied({"amr": ["pwd"]}) is False


def test_redaction_removes_registered_values_and_shapes() -> None:
    from conductor.logging import register_secret

    register_secret("synthetic-unregistered-secret-value")
    bearer = "abc" + "defghijklmnopqrstuvwxyz0123456789"
    message = redact(f"token=synthetic-unregistered-secret-value Authorization: Bearer {bearer}")
    assert "synthetic-unregistered-secret-value" not in message
    assert bearer not in message


class _Mappings:
    def __init__(self, mapping: IdentityMapping | None) -> None:
        self.mapping = mapping

    async def get_mapping(self, subject: str):
        return self.mapping


def _mapping(stage: MappingStage = MappingStage.COMPLETE, mfa_required: bool = False) -> IdentityMapping:
    return IdentityMapping(
        subject="subject",
        cloud_id="cloud",
        cloudstack_user_id="user-id",
        cloudstack_account_id="account-id",
        cloudstack_domain_id="domain-id",
        cloudstack_domain="demo.example",
        cloudstack_username="demo-user",
        account="demo-account",
        domain_ancestors=["domain-id"],
        stage=stage,
        roles=[PrincipalRole.USER],
        mfa_required=mfa_required,
    )


async def test_principal_requires_complete_mapping() -> None:
    settings = __import__("conductor.config", fromlist=["Settings"]).Settings(
        environment="demo",
        auth={"mode": "demo", "allow_demo_auth": True},
        features={"console_enabled": False},
    )
    service = PrincipalService(settings)
    with pytest.raises(AuthorizationError):
        await service.resolve_claims(
            {"sub": "subject", "amr": ["pwd"]},
            _Mappings(_mapping(MappingStage.LEGACY_CREDENTIALS_VERIFIED)),
        )


async def test_principal_never_merges_and_requires_mfa() -> None:
    settings = __import__("conductor.config", fromlist=["Settings"]).Settings(
        environment="demo",
        auth={"mode": "demo", "allow_demo_auth": True},
        features={"console_enabled": False},
    )
    service = PrincipalService(settings)
    with pytest.raises(AuthenticationError):
        await service.resolve_claims(
            {"sub": "subject", "amr": ["pwd"]},
            _Mappings(_mapping(mfa_required=True)),
        )
    principal = await service.resolve_claims(
        {"sub": "subject", "amr": ["pwd", "otp"]},
        _Mappings(_mapping(mfa_required=True)),
    )
    assert principal.domain_ancestors == ["domain-id"]
    with pytest.raises(AuthenticationError):
        await service.resolve_claims({"sub": "unknown", "amr": ["pwd"]}, _Mappings(None))


async def test_rate_limiter_window() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        assert await limiter.allow("key", limit=3, window_seconds=60) is True
    assert await limiter.allow("key", limit=3, window_seconds=60) is False
    assert await limiter.allow("other", limit=3, window_seconds=60) is True


def test_branding_rejects_unknown_and_external_assets(tmp_path) -> None:
    path = tmp_path / "branding.json"
    path.write_text('{"product_name": "Example", "unknown": true}', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_branding(path)
    path.write_text('{"product_name": "Example", "logo_url": "https://example.com/logo.svg"}', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_branding(path)
    path.write_text('{"product_name": "Example", "primary_color": "#0D5EF4"}', encoding="utf-8")
    branding = load_branding(path)
    assert branding.product_name == "Example"


def test_oidc_issuer_comparison_tolerates_trailing_slash() -> None:
    assert same_issuer("https://auth.example.com/application/o/demo/", "https://auth.example.com/application/o/demo")
    assert not same_issuer(
        "https://auth.example.com/application/o/other/", "https://auth.example.com/application/o/demo"
    )
