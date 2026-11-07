"""Seed the synthetic demo database.

Creates fictional identities, an encrypted demo credential, a synthetic price
catalogue, and version-pinned demo knowledge. Safe to run repeatedly.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal

from conductor.config import Settings
from conductor.db.identity import CredentialRepository, IdentityRepository
from conductor.db.pricing import PricingRepository
from conductor.db.session import build_engine, build_session_factory
from conductor.knowledge.embeddings import EmbeddingClient
from conductor.knowledge.ingest import KnowledgeIngestor
from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.pricing import ComputeShape, OfferingBinding, RateCard
from conductor.models.principal import IdentityMapping
from conductor.pricing.engine import PricingCatalog
from conductor.security.crypto import Vault, credential_aad
from demo.fixtures import (
    ACCOUNT_ID,
    ACCOUNT_NAME,
    DOMAIN_ID,
    DOMAIN_NAME,
    GPU_OFFERING_ID,
    MEDIUM_OFFERING_ID,
    OPERATOR_USER_ID,
    SMALL_OFFERING_ID,
    USER_ID,
)

CLOUD_ID = os.environ.get("CONDUCTOR_CLOUD_ID", "demo-cloud")
API_URL = os.environ.get("DEMO_CLOUDSTACK_API_URL", "http://mock-cloudstack:8080/client/api")
API_KEY = os.environ.get("DEMO_API_KEY", "demo-api-key")
API_SECRET = os.environ.get("DEMO_API_SECRET", "demo-api-secret")


def _mapping(subject: str, user_id: str, *, operator: bool) -> IdentityMapping:
    return IdentityMapping(
        subject=subject,
        cloud_id=CLOUD_ID,
        cloudstack_user_id=user_id,
        cloudstack_account_id=ACCOUNT_ID,
        cloudstack_domain_id=DOMAIN_ID,
        cloudstack_domain=DOMAIN_NAME,
        cloudstack_username=subject,
        account=ACCOUNT_NAME,
        domain_ancestors=[DOMAIN_ID],
        mapping_version="demo-1",
        stage=MappingStage.COMPLETE,
        roles=[PrincipalRole.OPERATOR] if operator else [PrincipalRole.USER],
        display_name="Demo Operator" if operator else "Demo User",
        email=None,
        mfa_required=False,
    )


def demo_catalog() -> PricingCatalog:
    effective = datetime(2026, 1, 1, tzinfo=UTC)
    card = RateCard(
        version="demo-2026.1",
        currency="CNY",
        effective_from=effective,
        cpu_hour=Decimal("0.02"),
        memory_gib_hour=Decimal("0.01"),
        gpu_hour=Decimal("1.50"),
        ssd_gib_hour=Decimal("0.001"),
        hdd_gib_hour=Decimal("0.0005"),
        notes="Synthetic demo tariff; not a real price.",
    )
    shapes = {
        SMALL_OFFERING_ID: (
            "demo.small",
            ComputeShape(cpu_count=2, memory_gib=Decimal(4), ssd_gib=Decimal(40)),
        ),
        MEDIUM_OFFERING_ID: (
            "demo.medium",
            ComputeShape(cpu_count=4, memory_gib=Decimal(8), ssd_gib=Decimal(80)),
        ),
        GPU_OFFERING_ID: (
            "demo.gpu",
            ComputeShape(cpu_count=8, memory_gib=Decimal(32), gpu_count=1, ssd_gib=Decimal(200)),
        ),
    }
    bindings = tuple(
        OfferingBinding(
            cloud_id=CLOUD_ID,
            offering_id=offering_id,
            offering_name=name,
            shape=shape,
            rate_card_version=card.version,
            currency="CNY",
            effective_from=effective,
            mapping_version="demo-1",
            source_reference="demo fixtures",
        )
        for offering_id, (name, shape) in shapes.items()
    )
    return PricingCatalog(rate_cards=(card,), bindings=bindings, version="demo-2026.1")


async def seed(settings: Settings) -> None:
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    vault = Vault(settings.vault_key())
    try:
        async with factory() as session:
            identities = IdentityRepository(session)
            await identities.upsert_mapping(_mapping("demo-user", USER_ID, operator=False))
            await identities.upsert_mapping(_mapping("demo-operator", OPERATOR_USER_ID, operator=True))
            credentials = CredentialRepository(session)
            payload = json.dumps({"api_url": API_URL, "api_key": API_KEY, "api_secret": API_SECRET})
            for user_id in (USER_ID, OPERATOR_USER_ID):
                await credentials.save(
                    cloud_id=CLOUD_ID,
                    cloudstack_user_id=user_id,
                    cloudstack_account_id=ACCOUNT_ID,
                    cloudstack_domain_id=DOMAIN_ID,
                    ciphertext=vault.encrypt(payload, aad=credential_aad(CLOUD_ID, user_id)),
                    key_id=vault.key_id,
                    source="demo",
                )
            pricing = PricingRepository(session)
            if await pricing.latest_publication(CLOUD_ID) is None:
                await pricing.save_catalog(
                    cloud_id=CLOUD_ID,
                    catalog=demo_catalog(),
                    published_by="demo-seed",
                    version="demo-2026.1",
                )
            await session.commit()
        async with factory() as session:
            ingestor = KnowledgeIngestor(session, EmbeddingClient(settings))
            report = await ingestor.ingest_directory(
                os.environ.get("DEMO_KNOWLEDGE_DIR", "demo/knowledge"),
                default_version="1.0",
                chunk_tokens=settings.knowledge.chunk_tokens,
                overlap_tokens=settings.knowledge.overlap_tokens,
            )
            print(
                json.dumps(
                    {
                        "seeded": True,
                        "cloud_id": CLOUD_ID,
                        "documents": report.documents,
                        "chunks": report.chunks,
                        "duplicates_skipped": report.skipped,
                    }
                )
            )
    finally:
        await engine.dispose()


def main() -> None:
    settings = Settings()
    asyncio.run(seed(settings))


if __name__ == "__main__":  # pragma: no cover
    main()
