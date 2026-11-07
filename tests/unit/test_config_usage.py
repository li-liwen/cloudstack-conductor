from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from conductor.cloudstack.models import RawUsageRecord
from conductor.config import Settings
from conductor.errors import ConfigurationError
from conductor.usage.collector import _record_to_usage, _resource_type, _unit


def test_usage_record_mapping() -> None:
    raw = RawUsageRecord(
        usageid="usage-1",
        accountid="account",
        zoneid="zone",
        resourceid="resource",
        resourcename="demo-web",
        resourcetype="VirtualMachine",
        usagetype=1,
        usage=Decimal("24"),
        startdate=datetime(2026, 11, 1, tzinfo=UTC),
        enddate=datetime(2026, 11, 2, tzinfo=UTC),
        offeringid="offering",
    )
    record = _record_to_usage(raw, cloud_id="cloud", window_id="window")
    assert record.usage_type == 1
    assert record.unit == "hours"
    assert record.offering_id == "offering"
    assert record.quantity == Decimal("24")
    assert _resource_type(1) == "instance-running"
    assert _unit(5) == "gib-hours"


def test_production_rejects_mock_and_demo_modes() -> None:
    settings = Settings(
        environment="production",
        auth={"mode": "demo", "allow_demo_auth": True},
        features={"console_enabled": False, "mcp_enabled": False},
    )
    try:
        settings.validate_for_runtime()
    except ConfigurationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("production accepted demo auth")

    settings = Settings(
        environment="production",
        auth={"mode": "oidc", "allow_demo_auth": False, "secure_cookies": True},
        features={"console_enabled": False, "mcp_enabled": False},
        llm={"mock": True},
    )
    try:
        settings.validate_for_runtime()
    except ConfigurationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("production accepted a mock model")


def test_public_config_contains_no_secret_values() -> None:
    settings = Settings(
        environment="demo",
        auth={"mode": "demo", "allow_demo_auth": True},
        features={"console_enabled": False},
    )
    config = settings.public_config()
    text = str(config)
    assert "vault" not in text.lower()
    assert "password" not in text.lower()
    assert config["branding"]["product_name"] == "CloudStack Conductor"
