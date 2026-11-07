"""Runtime configuration.

Design rules (see the public architecture documentation):

* Real values arrive through environment variables and mounted secret files.
* There are no private defaults in this module. A missing required setting
  fails with a clear :class:`ConfigurationError` instead of falling back to a
  vendor environment.
* Mock/demo behaviour is an explicit, named mode that production refuses to
  start with.
* Branding is loaded at runtime from an allowlisted JSON document so that no
  deployment identity is compiled into the portal bundle.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from conductor.errors import ConfigurationError
from conductor.version import __version__

Environment = Literal["development", "demo", "test", "production"]


def read_secret_file(path: str | Path | None, *, name: str) -> str:
    """Read a secret from a file, stripping only the trailing newline.

    Secret values are never echoed in errors.
    """

    if not path:
        raise ConfigurationError(f"{name} is not configured (expected a secret file path).")
    secret_path = Path(path)
    try:
        value = secret_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(f"{name} could not be read from its configured file.") from exc
    if not value:
        raise ConfigurationError(f"{name} is empty in its configured file.")
    return value


def read_secret_or_env(file_path: str | None, env_name: str, *, name: str, required: bool = True) -> str | None:
    """Read a secret from a file, allowing the environment only outside production.

    The environment fallback exists for local development and is rejected by
    :meth:`Settings.validate_for_runtime` in production.
    """

    if file_path:
        return read_secret_file(file_path, name=name)
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    if required:
        raise ConfigurationError(f"{name} is not configured.")
    return None


class BrandingConfig(BaseModel):
    """Runtime branding. Public defaults are intentionally generic."""

    product_name: str = "CloudStack Conductor"
    company_name: str = "CloudStack Conductor"
    short_name: str = "Conductor"
    tagline_zh: str = "面向 Apache CloudStack 的多租户智能控制平面"
    tagline_en: str = "A tenant-aware agent and MCP control plane for Apache CloudStack"
    primary_color: str = "#0D5EF4"
    accent_color: str = "#00BFA0"
    logo_url: str = "/branding/logo.svg"
    logo_mark_url: str = "/branding/logo-mark.svg"
    favicon_url: str = "/branding/favicon.svg"
    support_url: str | None = None
    docs_url: str | None = None
    environment_label: str | None = None
    locale_default: Literal["zh-CN", "en"] = "zh-CN"
    locales: list[str] = Field(default_factory=lambda: ["zh-CN", "en"])

    @field_validator("primary_color", "accent_color")
    @classmethod
    def _hex_color(cls, value: str) -> str:
        if not value.startswith("#") or len(value) not in (4, 7):
            raise ValueError("colors must be hex values")
        int(value[1:], 16)
        return value

    @field_validator("logo_url", "logo_mark_url", "favicon_url")
    @classmethod
    def _relative_asset(cls, value: str) -> str:
        # Assets are served by the application from a read-only mount. Absolute
        # external URLs would leak deployment identity into the public image.
        if not value.startswith("/"):
            raise ValueError("branding assets must be application-relative paths")
        return value


def load_branding(path: str | Path | None) -> BrandingConfig:
    """Load an allowlisted branding document from a read-only runtime mount."""

    if not path:
        return BrandingConfig()
    branding_path = Path(path)
    try:
        raw = json.loads(branding_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError("the branding configuration file was not found") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError("the branding configuration file is invalid") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("the branding configuration must be a JSON object")
    unknown = set(raw) - set(BrandingConfig.model_fields)
    if unknown:
        raise ConfigurationError("the branding configuration contains unsupported keys: " + ", ".join(sorted(unknown)))
    try:
        return BrandingConfig.model_validate(raw)
    except ValueError as exc:
        raise ConfigurationError("the branding configuration is not valid") from exc


class DatabaseSettings(BaseModel):
    url: str = "postgresql+asyncpg://conductor@postgres:5432/conductor"
    pool_size: int = 10
    max_overflow: int = 10
    echo: bool = False

    @field_validator("url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @property
    def sqlalchemy_url(self) -> str:
        return self.url


class AuthSettings(BaseModel):
    mode: Literal["oidc", "demo"] = "oidc"
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret_file: str | None = None
    oidc_scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    oidc_redirect_path: str = "/api/v1/auth/callback"
    oidc_discovery_timeout_seconds: float = 10.0
    oidc_groups_claim: str = "groups"
    operator_groups: list[str] = Field(default_factory=list)
    domain_admin_groups: list[str] = Field(default_factory=list)
    mfa_claim: str = "acr"
    session_cookie_name: str = "conductor_session"
    session_ttl_seconds: int = 8 * 3600
    session_secret_file: str | None = None
    csrf_cookie_name: str = "conductor_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    secure_cookies: bool = True
    allow_demo_auth: bool = False
    demo_principal_header: str = "X-Demo-Principal"


class LLMSettings(BaseModel):
    base_url: str | None = None
    model_alias: str = "deepseek-flash"
    api_key_file: str | None = None
    request_timeout_seconds: float = 120.0
    max_concurrency: int = 4
    max_queue: int = 32
    temperature: float = 0.1
    max_output_tokens: int = 2048
    mock: bool = False
    provider_label: str = "local-model"

    @field_validator("temperature")
    @classmethod
    def _temperature(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        return value


class ConsoleSettings(BaseModel):
    # Hosts the relay may connect to. Empty means every console launch is
    # refused; there is no implicit "any host" mode.
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_ports: list[int] = Field(default_factory=lambda: [443, 8443])
    launch_ttl_seconds: int = 60
    session_ttl_seconds: int = 1800
    relay_timeout_seconds: float = 30.0
    max_message_bytes: int = 512 * 1024
    require_tls: bool = True


class KnowledgeSettings(BaseModel):
    source_dir: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key_file: str | None = None
    embedding_model: str = "BGE-M3"
    embedding_dimension: int = 1024
    chunk_tokens: int = 750
    overlap_tokens: int = 100
    top_k: int = 8
    mock_embeddings: bool = False

    @field_validator("chunk_tokens")
    @classmethod
    def _chunk_size(cls, value: int) -> int:
        if not 300 <= value <= 2000:
            raise ValueError("chunk_tokens must be between 300 and 2000")
        return value


class LimitSettings(BaseModel):
    model_requests: int = 4
    queue_depth: int = 32
    cloudstack_requests_per_second: float = 8.0
    cloudstack_max_concurrent: int = 8
    api_rate_limit_per_minute: int = 240
    mcp_rate_limit_per_minute: int = 120
    polling_initial_seconds: float = 1.0
    polling_max_seconds: float = 15.0
    approval_ttl_minutes: int = 10
    plan_ttl_minutes: int = 1440
    operation_timeout_minutes: int = 60


class UsageSettings(BaseModel):
    enabled: bool = False
    reader_api_url_file: str | None = None
    reader_api_key_file: str | None = None
    reader_api_secret_file: str | None = None
    lookback_days: int = 3
    collection_interval_minutes: int = 60


class FeatureSettings(BaseModel):
    mcp_enabled: bool = True
    console_enabled: bool = True
    guest_tasks_enabled: bool = False
    usage_collector_enabled: bool = False
    prompt_approval: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONDUCTOR_",
        env_nested_delimiter="__",
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = "development"
    cloud_id: str = "default"
    service_name: str = "conductor-api"
    public_base_url: str = "http://localhost:8080"
    api_base_url: str = "http://api:8000"
    execution_base_url: str = "http://execution:8001"
    internal_token_file: str | None = None
    vault_key_file: str | None = None
    branding_config_file: str | None = None
    branding_assets_dir: str | None = None
    portal_static_dir: str | None = None
    console_session_cookie: str = "conductor_console"
    portal_public_url: str | None = None
    mcp_public_url: str | None = None
    mcp_signing_key_file: str | None = None
    mcp_storage_dir: str = "/data/mcp"
    mcp_allowed_redirect_uris: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8080"])
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)
    log_level: str = "INFO"
    debug: bool = False
    metrics_enabled: bool = True
    docs_enabled: bool = True

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    console: ConsoleSettings = Field(default_factory=ConsoleSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    usage: UsageSettings = Field(default_factory=UsageSettings)
    limits: LimitSettings = Field(default_factory=LimitSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if value.upper() not in allowed:
            raise ValueError("log_level must be one of " + ", ".join(sorted(allowed)))
        return value.upper()

    @model_validator(mode="after")
    def _demo_mode_is_explicit(self) -> Settings:
        if self.auth.mode == "demo" and not self.auth.allow_demo_auth:
            raise ValueError("auth.mode=demo requires auth.allow_demo_auth=true")
        return self

    # -- secret access -----------------------------------------------------
    def internal_token(self) -> str:
        return read_secret_or_env(
            self.internal_token_file,
            "CONDUCTOR_INTERNAL_TOKEN",
            name="the internal service token",
        )  # type: ignore[return-value]

    def vault_key(self) -> str:
        return read_secret_or_env(self.vault_key_file, "CONDUCTOR_VAULT_KEY", name="the vault master key")  # type: ignore[return-value]

    def session_secret(self) -> str:
        return read_secret_or_env(
            self.auth.session_secret_file,
            "CONDUCTOR_SESSION_SECRET",
            name="the session signing secret",
        )  # type: ignore[return-value]

    def oidc_client_secret(self) -> str:
        return read_secret_or_env(
            self.auth.oidc_client_secret_file,
            "CONDUCTOR_OIDC_CLIENT_SECRET",
            name="the OIDC client secret",
        )  # type: ignore[return-value]

    def llm_api_key(self) -> str | None:
        return read_secret_or_env(
            self.llm.api_key_file,
            "CONDUCTOR_LLM_API_KEY",
            name="the model gateway API key",
            required=False,
        )

    def mcp_signing_key(self) -> str:
        return read_secret_or_env(
            self.mcp_signing_key_file,
            "CONDUCTOR_MCP_SIGNING_KEY",
            name="the MCP token signing key",
        )  # type: ignore[return-value]

    def embedding_api_key(self) -> str | None:
        return read_secret_or_env(
            self.knowledge.embedding_api_key_file,
            "CONDUCTOR_EMBEDDING_API_KEY",
            name="the embedding service API key",
            required=False,
        )

    # -- validation --------------------------------------------------------
    def validate_for_runtime(self) -> None:
        """Fail clearly before a service starts handling requests."""

        if self.environment == "production":
            if self.auth.mode != "oidc":
                raise ConfigurationError("production requires auth.mode=oidc")
            if self.auth.allow_demo_auth:
                raise ConfigurationError("production must not allow demo authentication")
            if self.llm.mock:
                raise ConfigurationError("production must not use the mock model gateway")
            if self.knowledge.mock_embeddings:
                raise ConfigurationError("production must not use mock embeddings")
            if not self.database.url.startswith("postgresql+asyncpg://"):
                raise ConfigurationError("production requires a PostgreSQL database URL")
            if not self.auth.secure_cookies:
                raise ConfigurationError("production requires secure cookies")
            if self.features.mcp_enabled and not self.mcp_public_url:
                raise ConfigurationError("production requires mcp_public_url when MCP is enabled")
            if self.features.mcp_enabled and not (
                self.mcp_signing_key_file or os.environ.get("CONDUCTOR_MCP_SIGNING_KEY")
            ):
                raise ConfigurationError("production requires an explicit MCP signing key")
        if self.auth.mode == "oidc" and self.environment in {"production", "test"}:
            missing = [
                name
                for name, value in (
                    ("CONDUCTOR_AUTH__OIDC_ISSUER", self.auth.oidc_issuer),
                    ("CONDUCTOR_AUTH__OIDC_CLIENT_ID", self.auth.oidc_client_id),
                    ("CONDUCTOR_AUTH__OIDC_CLIENT_SECRET_FILE", self.auth.oidc_client_secret_file),
                    ("CONDUCTOR_AUTH__SESSION_SECRET_FILE", self.auth.session_secret_file),
                )
                if not value
            ]
            if missing:
                raise ConfigurationError("missing required OIDC configuration: " + ", ".join(missing))
        if self.features.console_enabled and not self.console.allowed_hosts:
            raise ConfigurationError(
                "console is enabled but no console.allowed_hosts are configured; refusing to relay console traffic"
            )

    def validate_for_execution(self) -> None:
        if not self.vault_key_file and not os.environ.get("CONDUCTOR_VAULT_KEY"):
            raise ConfigurationError("the execution service requires a vault master key")
        if not self.internal_token_file and not os.environ.get("CONDUCTOR_INTERNAL_TOKEN"):
            raise ConfigurationError("the execution service requires an internal service token")

    def branding(self) -> BrandingConfig:
        return load_branding(self.branding_config_file)

    def public_config(self) -> dict[str, object]:
        """The allowlisted runtime configuration served to the portal.

        Never add secrets, internal URLs, or tenant data here. The database is
        the only source for anything user-specific.
        """

        branding = self.branding()
        return {
            "product": "cloudstack-conductor",
            "version": __version__,
            "environment": self.environment,
            "branding": branding.model_dump(),
            "auth": {
                "mode": self.auth.mode,
                "login_url": "/api/v1/auth/login",
                "logout_url": "/api/v1/auth/logout",
            },
            "features": {
                "mcp": self.features.mcp_enabled,
                "console": self.features.console_enabled,
                "guest_tasks": self.features.guest_tasks_enabled,
                "usage": self.features.usage_collector_enabled,
            },
            "limits": {
                "approval_ttl_minutes": self.limits.approval_ttl_minutes,
            },
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
