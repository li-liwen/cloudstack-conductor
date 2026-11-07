"""Identity-bridge configuration.

Secrets are read from mounted files only. This module deliberately has no
environment-variable fallback for a token, API key, or secret: a deployment
must provide the file path, and a missing or unreadable file fails clearly.
Public values (base URLs, timeouts, paths to private files) may be supplied
through environment variables with the ``CONDUCTOR_IDENTITY__`` prefix.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from conductor.cloudstack.client import CloudStackCredentials
from conductor.config import read_secret_file
from conductor.errors import ConfigurationError


def _https_url(value: str, *, name: str, allow_http: bool = False) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"https"} and not (allow_http and parsed.scheme == "http"):
        raise ValueError(f"{name} must use HTTPS")
    if not parsed.hostname:
        raise ValueError(f"{name} must include a host")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not include a query or fragment")
    return value.rstrip("/")


class DomainSamlProvider(BaseModel):
    """One authentik SAML provider bound to one CloudStack domain.

    ``provider_id`` is an operator-selected stable label stored in the
    migration row and audit detail. ``entity_id`` is the SAML IdP entity ID
    that CloudStack's ``authorizeSamlSso`` command stores on the user. It must
    be unique per CloudStack domain.
    """

    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=255)
    entity_id: str = Field(min_length=1, max_length=512)
    metadata_url: str | None = None
    allow_idp_initiated: bool = False

    @field_validator("provider_id")
    @classmethod
    def _provider_id(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("provider_id must not contain whitespace")
        return value

    @field_validator("metadata_url")
    @classmethod
    def _metadata_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _https_url(value, name="metadata_url")

    @field_validator("entity_id")
    @classmethod
    def _entity_id(cls, value: str) -> str:
        value = value.strip()
        if not value or "\n" in value or "\r" in value:
            raise ValueError("entity_id must be a single non-empty line")
        return value


class DomainProviderMap(BaseModel):
    """Private JSON document mapping CloudStack domain IDs to SAML providers."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    domains: dict[str, DomainSamlProvider]

    @field_validator("domains")
    @classmethod
    def _domains(cls, value: dict[str, DomainSamlProvider]) -> dict[str, DomainSamlProvider]:
        if not value:
            raise ValueError("at least one domain provider is required")
        for domain_id, provider in value.items():
            if not domain_id or not domain_id.strip():
                raise ValueError("domain keys must be non-empty")
            if len(domain_id) > 255:
                raise ValueError("domain keys are too long")
            if any(character.isspace() for character in domain_id):
                raise ValueError("domain keys must not contain whitespace")
            if not provider.entity_id:
                raise ValueError("every domain provider needs an entity_id")
        return value

    @model_validator(mode="after")
    def _unique_providers(self) -> DomainProviderMap:
        entity_ids = [provider.entity_id for provider in self.domains.values()]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("entity_id values must be unique per CloudStack domain")
        provider_ids = [provider.provider_id for provider in self.domains.values()]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider_id values must be unique")
        if self.schema_version != 1:
            raise ValueError("unsupported domain provider map schema version")
        return self

    def for_domain(self, domain_id: str) -> DomainSamlProvider | None:
        return self.domains.get(domain_id)


class IdentityBridgeSettings(BaseSettings):
    """Configuration for the identity bridge service.

    Environment names use the ``CONDUCTOR_IDENTITY__`` prefix, for example
    ``CONDUCTOR_IDENTITY__AUTHENTIK_BASE_URL``. Secret file paths are still
    only paths; the secret value itself is never read from a variable.
    """

    model_config = SettingsConfigDict(
        env_prefix="CONDUCTOR_IDENTITY__",
        extra="ignore",
        case_sensitive=False,
    )

    enabled: bool = False
    allow_insecure_http: bool = False
    authentik_base_url: str | None = None
    authentik_api_prefix: str = "/api/v3"
    authentik_service_token_file: str | None = None
    cloudstack_api_url: str | None = None
    provisioner_api_key_file: str | None = None
    provisioner_api_secret_file: str | None = None
    domain_provider_map_file: str | None = None
    request_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    legacy_session_ttl_seconds: int = Field(default=300, ge=30, le=1800)
    require_tls: bool = True
    # Closing in the verification request is the safest option. Keeping the
    # temporary session until ``finish`` matches the multi-step flow, but it
    # requires the same worker (or sticky routing) for the following requests.
    close_legacy_session_immediately: bool = True

    @field_validator("authentik_base_url", "cloudstack_api_url")
    @classmethod
    def _base_url(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        allow_http = bool(info.data.get("allow_insecure_http"))
        return _https_url(value, name=str(info.field_name), allow_http=allow_http)

    @field_validator("authentik_api_prefix")
    @classmethod
    def _api_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("authentik_api_prefix must start with '/' and not end with '/'")
        return value

    @property
    def authentik_api_base(self) -> str:
        if not self.authentik_base_url:
            raise ConfigurationError("the authentik base URL is not configured")
        return self.authentik_base_url + self.authentik_api_prefix

    # -- secret access -----------------------------------------------------
    def service_token(self) -> str:
        return read_secret_file(self.authentik_service_token_file, name="the authentik service token")

    def provisioner_credentials(self) -> CloudStackCredentials:
        if not self.cloudstack_api_url:
            raise ConfigurationError("the CloudStack API URL is not configured for the identity bridge")
        return CloudStackCredentials(
            api_url=self.cloudstack_api_url,
            api_key=read_secret_file(self.provisioner_api_key_file, name="the provisioner CloudStack API key"),
            api_secret=read_secret_file(self.provisioner_api_secret_file, name="the provisioner CloudStack secret key"),
        )

    def domain_providers(self) -> DomainProviderMap:
        path = self.domain_provider_map_file
        if not path:
            raise ConfigurationError("the CloudStack domain SAML provider map is not configured")
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError("the CloudStack domain SAML provider map file was not found") from exc
        except OSError as exc:
            raise ConfigurationError("the CloudStack domain SAML provider map file could not be read") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError("the CloudStack domain SAML provider map is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise ConfigurationError("the CloudStack domain SAML provider map must be a JSON object")
        try:
            return DomainProviderMap.model_validate(raw)
        except ValueError as exc:
            raise ConfigurationError("the CloudStack domain SAML provider map is not valid") from exc

    def validate_for_runtime(self) -> None:
        """Load every required private input and fail with a stable message."""

        if not self.enabled:
            raise ConfigurationError("the identity bridge is not enabled")
        missing = [
            name
            for name, value in (
                ("CONDUCTOR_IDENTITY__AUTHENTIK_BASE_URL", self.authentik_base_url),
                ("CONDUCTOR_IDENTITY__AUTHENTIK_SERVICE_TOKEN_FILE", self.authentik_service_token_file),
                ("CONDUCTOR_IDENTITY__CLOUDSTACK_API_URL", self.cloudstack_api_url),
                ("CONDUCTOR_IDENTITY__PROVISIONER_API_KEY_FILE", self.provisioner_api_key_file),
                ("CONDUCTOR_IDENTITY__PROVISIONER_API_SECRET_FILE", self.provisioner_api_secret_file),
                ("CONDUCTOR_IDENTITY__DOMAIN_PROVIDER_MAP_FILE", self.domain_provider_map_file),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError("missing identity bridge configuration: " + ", ".join(missing))
        # Read the values here so a bad mount fails at startup, not mid-flow.
        self.service_token()
        self.provisioner_credentials()
        self.domain_providers()
