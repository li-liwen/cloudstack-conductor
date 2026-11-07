"""Integration test: plan -> approval -> durable execution against a mocked cloud.

Requires a PostgreSQL database with the pgvector extension. Set
CONDUCTOR_TEST_DATABASE_URL to run it; CI starts a service container.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import text

from conductor.config import Settings
from conductor.db.identity import CredentialRepository, IdentityRepository
from conductor.db.models import Base
from conductor.db.pricing import PricingRepository
from conductor.db.session import build_engine, build_session_factory
from conductor.errors import ConductorError
from conductor.execution.service import ExecutionService
from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.pricing import ComputeShape, OfferingBinding, RateCard
from conductor.models.principal import IdentityMapping
from conductor.pricing.engine import PricingCatalog
from conductor.security.crypto import Vault, credential_aad

DATABASE_URL = os.environ.get("CONDUCTOR_TEST_DATABASE_URL")
pytestmark = pytest.mark.requires_db

CLOUD = "integration-cloud"
ACCOUNT = "22222222-2222-4222-8222-222222222222"
DOMAIN = "11111111-1111-4111-8111-111111111111"
USER = "33333333-3333-4333-8333-333333333333"
OFFERING = "99999999-9999-4999-8999-999999999999"
TEMPLATE = "77777777-7777-4777-8777-777777777777"
NETWORK = "55555555-5555-4555-8555-555555555555"
ZONE = "44444444-4444-4444-8444-444444444444"
VM = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
API_URL = "http://cloudstack.test/client/api"


def _settings() -> Settings:
    return Settings(
        environment="demo",
        cloud_id=CLOUD,
        public_base_url="http://localhost:8080",
        database={"url": DATABASE_URL},
        auth={
            "mode": "demo",
            "allow_demo_auth": True,
            "secure_cookies": False,
            "session_secret_file": None,
        },
        features={"console_enabled": False, "mcp_enabled": False},
        llm={"mock": True},
        knowledge={"mock_embeddings": True},
    )


@pytest.fixture
async def service():
    if not DATABASE_URL:
        pytest.skip("CONDUCTOR_TEST_DATABASE_URL is not set")
    settings = _settings()
    engine = build_engine(settings)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = build_session_factory(engine)
    vault = Vault("integration-test-vault-key-0000000000000000")
    async with factory() as session:
        await IdentityRepository(session).upsert_mapping(
            IdentityMapping(
                subject="integration-user",
                cloud_id=CLOUD,
                cloudstack_user_id=USER,
                cloudstack_account_id=ACCOUNT,
                cloudstack_domain_id=DOMAIN,
                cloudstack_domain="demo.example",
                cloudstack_username="integration-user",
                account="demo-account",
                domain_ancestors=[DOMAIN],
                stage=MappingStage.COMPLETE,
                roles=[PrincipalRole.USER],
            )
        )
        await CredentialRepository(session).save(
            cloud_id=CLOUD,
            cloudstack_user_id=USER,
            cloudstack_account_id=ACCOUNT,
            cloudstack_domain_id=DOMAIN,
            ciphertext=vault.encrypt(
                f'{{"api_url": "{API_URL}", "api_key": "integration-key", "api_secret": "integration-secret"}}',
                aad=credential_aad(CLOUD, USER),
            ),
            key_id=vault.key_id,
        )
        card = RateCard(
            version="integration-1",
            currency="CNY",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            cpu_hour=Decimal("0.02"),
            memory_gib_hour=Decimal("0.01"),
            ssd_gib_hour=Decimal("0.001"),
            hdd_gib_hour=Decimal("0.0005"),
        )
        catalog = PricingCatalog(
            rate_cards=(card,),
            bindings=(
                OfferingBinding(
                    cloud_id=CLOUD,
                    offering_id=OFFERING,
                    offering_name="demo.small",
                    shape=ComputeShape(cpu_count=2, memory_gib=Decimal(4), ssd_gib=Decimal(40)),
                    rate_card_version=card.version,
                    currency="CNY",
                    effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ),
            version="integration",
        )
        await PricingRepository(session).save_catalog(
            cloud_id=CLOUD, catalog=catalog, published_by="test", version="integration"
        )
        await session.commit()
    service = ExecutionService(settings, factory, vault, None)
    yield service
    await engine.dispose()


def _vm(state: str = "Stopped") -> dict:
    return {
        "id": VM,
        "name": "integration-vm",
        "state": state,
        "zoneid": ZONE,
        "zonename": "demo-zone",
        "templateid": TEMPLATE,
        "templatename": "ubuntu-demo",
        "serviceofferingid": OFFERING,
        "serviceofferingname": "demo.small",
        "accountid": ACCOUNT,
        "account": "demo-account",
        "domainid": DOMAIN,
        "nic": [{"networkid": NETWORK, "ipaddress": "192.0.2.10"}],
    }


def _cloudstack_handler(request: httpx.Request) -> httpx.Response:
    command = request.url.params["command"].lower()
    if command == "listvirtualmachines":
        return httpx.Response(
            200,
            json={
                "listvirtualmachinesresponse": {
                    "count": 1,
                    "virtualmachine": [_vm("Stopped")],
                }
            },
        )
    if command == "listserviceofferings":
        return httpx.Response(
            200,
            json={
                "listserviceofferingsresponse": {
                    "count": 1,
                    "serviceoffering": [
                        {
                            "id": OFFERING,
                            "name": "demo.small",
                            "cpunumber": 2,
                            "cpuspeed": 2000,
                            "memory": 4096,
                        }
                    ],
                }
            },
        )
    if command == "startvirtualmachine":
        return httpx.Response(200, json={"startvirtualmachineresponse": {"jobid": "job-start-1"}})
    if command == "queryasyncjobresult":
        return httpx.Response(
            200,
            json={
                "queryasyncjobresultresponse": {
                    "jobstatus": 1,
                    "jobresult": {"virtualmachine": _vm("Running")},
                }
            },
        )
    return httpx.Response(200, json={"errorresponse": {"errorcode": 530, "errortext": "unsupported"}})


@respx.mock
async def test_plan_approval_and_execution(service: ExecutionService) -> None:
    respx.get(API_URL).mock(side_effect=_cloudstack_handler)
    plan = await service.create_plan(
        subject="integration-user",
        action="start_instance",
        parameters={"instance_id": VM},
        idempotency_key="integration-start-1",
        request_id="test",
    )
    assert plan.state == "awaiting_approval"
    assert plan.quote is not None and plan.quote.priced
    view = await service.get_plan(plan.plan_id, "integration-user")
    assert view.can_approve is True

    approved, operation = await service.approve_plan(
        plan_id=plan.plan_id,
        subject="integration-user",
        digest=plan.digest,
        note=None,
    )
    assert approved.state == "approved"
    assert operation.state.value == "queued"

    await service.runner.run(operation.operation_id)
    finished = await service.get_operation(operation.operation_id, "integration-user")
    assert finished.state.value == "succeeded", finished.error_code
    assert finished.resource_ids == [VM]


@respx.mock
async def test_wrong_digest_is_rejected(service: ExecutionService) -> None:
    respx.get(API_URL).mock(side_effect=_cloudstack_handler)
    plan = await service.create_plan(
        subject="integration-user",
        action="start_instance",
        parameters={"instance_id": VM},
        idempotency_key="integration-start-2",
        request_id="test",
    )
    with pytest.raises(ConductorError):
        await service.approve_plan(
            plan_id=plan.plan_id,
            subject="integration-user",
            digest="0" * 64,
            note=None,
        )


@respx.mock
async def test_start_requires_stopped_state(service: ExecutionService) -> None:
    def running_handler(request: httpx.Request) -> httpx.Response:
        command = request.url.params["command"].lower()
        if command == "listvirtualmachines":
            return httpx.Response(
                200,
                json={
                    "listvirtualmachinesresponse": {
                        "count": 1,
                        "virtualmachine": [_vm("Running")],
                    }
                },
            )
        if command == "listserviceofferings":
            return _cloudstack_handler(request)
        return _cloudstack_handler(request)

    respx.get(API_URL).mock(side_effect=running_handler)
    with pytest.raises(ConductorError):
        await service.create_plan(
            subject="integration-user",
            action="start_instance",
            parameters={"instance_id": VM},
            idempotency_key="integration-start-3",
            request_id="test",
        )
