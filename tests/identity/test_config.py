"""Configuration is file-only for secrets and fails clearly when incomplete."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conductor.errors import ConfigurationError
from conductor.identity.config import DomainProviderMap, IdentityBridgeSettings


def _settings(tmp_path: Path, **overrides) -> IdentityBridgeSettings:
    values = {
        "enabled": True,
        "authentik_base_url": "https://auth.example.com",
        "authentik_service_token_file": str(tmp_path / "authentik-token"),
        "cloudstack_api_url": "https://cloud.example.com/client/api",
        "provisioner_api_key_file": str(tmp_path / "provisioner-key"),
        "provisioner_api_secret_file": str(tmp_path / "provisioner-secret"),
        "domain_provider_map_file": str(tmp_path / "domains.json"),
    }
    values.update(overrides)
    return IdentityBridgeSettings(**values)


def _write_minimum(tmp_path: Path) -> None:
    (tmp_path / "authentik-token").write_text("synthetic-token\n", encoding="utf-8")
    (tmp_path / "provisioner-key").write_text("synthetic-key\n", encoding="utf-8")
    (tmp_path / "provisioner-secret").write_text("synthetic-secret\n", encoding="utf-8")
    (tmp_path / "domains.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "domains": {
                    "33333333-3333-4333-8333-333333333333": {
                        "provider_id": "saml-domain-one",
                        "entity_id": "https://auth.example.com/application/saml/cloudstack-domain-one/metadata/",
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_missing_configuration_fails_without_echoing_values() -> None:
    settings = IdentityBridgeSettings(enabled=True)
    with pytest.raises(ConfigurationError) as error:
        settings.validate_for_runtime()
    message = str(error.value)
    assert "AUTHENTIK_BASE_URL" in message
    assert "synthetic" not in message  # names only, no secret value


def test_secrets_are_read_from_files(tmp_path: Path) -> None:
    _write_minimum(tmp_path)
    settings = _settings(tmp_path)
    settings.validate_for_runtime()
    assert settings.service_token() == "synthetic-token"
    credentials = settings.provisioner_credentials()
    assert credentials.api_key == "synthetic-key"
    assert credentials.api_secret == "synthetic-secret"
    providers = settings.domain_providers()
    assert providers.for_domain("33333333-3333-4333-8333-333333333333").provider_id == "saml-domain-one"  # type: ignore[union-attr]


def test_missing_secret_file_is_a_configuration_error(tmp_path: Path) -> None:
    _write_minimum(tmp_path)
    (tmp_path / "authentik-token").unlink()
    settings = _settings(tmp_path)
    with pytest.raises(ConfigurationError):
        settings.validate_for_runtime()


def test_duplicate_entity_ids_are_rejected(tmp_path: Path) -> None:
    _write_minimum(tmp_path)
    data = json.loads((tmp_path / "domains.json").read_text(encoding="utf-8"))
    data["domains"]["44444444-4444-4444-8444-444444444444"] = {
        "provider_id": "saml-domain-two",
        "entity_id": "https://auth.example.com/application/saml/cloudstack-domain-one/metadata/",
    }
    (tmp_path / "domains.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        _settings(tmp_path).domain_providers()


def test_http_is_rejected_by_default(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _settings(tmp_path, authentik_base_url="http://auth.example.com")


def test_http_can_be_enabled_explicitly(tmp_path: Path) -> None:
    settings = _settings(tmp_path, allow_insecure_http=True, authentik_base_url="http://auth.example.com")
    assert settings.authentik_base_url == "http://auth.example.com"


def test_provider_map_rejects_empty_domains() -> None:
    with pytest.raises(ValueError):
        DomainProviderMap(domains={})
