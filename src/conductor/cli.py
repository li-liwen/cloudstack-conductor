"""Operator CLI.

Runs against private configuration outside the public repository. It never prints
API keys, secrets, or raw private workbook values.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from conductor.cloudstack.client import CloudStackClient, CloudStackCredentials
from conductor.config import Settings, read_secret_file
from conductor.db.base import utcnow
from conductor.db.identity import IdentityRepository
from conductor.db.pricing import PricingRepository
from conductor.db.session import build_engine, build_session_factory
from conductor.errors import ConductorError, ConfigurationError
from conductor.knowledge.embeddings import EmbeddingClient
from conductor.knowledge.ingest import KnowledgeIngestor
from conductor.models.pricing import ComputeShape
from conductor.models.principal import IdentityMapping
from conductor.pricing.engine import PricingCatalog
from conductor.pricing.importer import WorkbookColumnMap, WorkbookImporter
from conductor.security.crypto import Vault, credential_aad


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conductor-admin",
        description="CloudStack Conductor operator commands (private configuration required).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("config-check", help="Validate the runtime configuration without printing secrets.")
    check.add_argument("--service", default="api", choices=["api", "execution", "worker", "mcp"])

    mapping = sub.add_parser("import-mapping", help="Import identity mappings from a private JSON file.")
    mapping.add_argument("file", help="JSON array or object with mapping documents.")

    credential = sub.add_parser("provision-credential", help="Store a user's CloudStack API credential.")
    credential.add_argument("--cloud-id", required=True)
    credential.add_argument("--user-id", required=True)
    credential.add_argument("--account-id", required=True)
    credential.add_argument("--domain-id", required=True)
    credential.add_argument("--api-url-file", required=True)
    credential.add_argument("--api-key-file", help="User API key file.")
    credential.add_argument("--api-secret-file", help="User API secret file.")
    credential.add_argument("--admin-key-file", help="Admin API key file for key retrieval.")
    credential.add_argument("--admin-secret-file", help="Admin API secret file.")
    credential.add_argument(
        "--generate",
        action="store_true",
        help="Generate keys only when the user has none (never rotates existing keys).",
    )
    credential.add_argument("--verify", action="store_true", help="Verify the credential against the API.")

    prices = sub.add_parser("import-prices", help="Import a private price workbook.")
    prices.add_argument("workbook")
    prices.add_argument("--cloud-id", required=True)
    prices.add_argument("--mapping", required=True, help="Private JSON column/offering mapping.")
    prices.add_argument("--version", help="Catalogue version; defaults to a timestamp.")
    prices.add_argument("--imported-by", default="operator")

    docs = sub.add_parser("ingest-docs", help="Ingest public documentation into the knowledge index.")
    docs.add_argument("directory")
    docs.add_argument("--version", required=True)
    docs.add_argument("--chunk-tokens", type=int, default=750)
    docs.add_argument("--overlap-tokens", type=int, default=100)

    return parser


async def _config_check(settings: Settings, service: str) -> int:
    if service == "execution":
        settings.validate_for_execution()
    if service in {"api", "mcp"} and settings.environment == "production":
        settings.validate_for_runtime()
    if service == "mcp" and not settings.mcp_public_url:
        raise ConfigurationError("mcp_public_url is required")
    print(
        json.dumps(
            {
                "environment": settings.environment,
                "cloud_id": settings.cloud_id,
                "auth_mode": settings.auth.mode,
                "mcp_enabled": settings.features.mcp_enabled,
                "console_enabled": settings.features.console_enabled,
                "database_driver": settings.database.url.split(":", 1)[0],
                "ok": True,
            },
            indent=2,
        )
    )
    return 0


async def _import_mapping(settings: Settings, file: str) -> int:
    payload = json.loads(Path(file).read_text(encoding="utf-8"))
    documents = payload if isinstance(payload, list) else payload.get("items", [payload])
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            repo = IdentityRepository(session)
            count = 0
            for document in documents:
                mapping = IdentityMapping.model_validate(document)
                await repo.upsert_mapping(mapping)
                count += 1
            await session.commit()
    finally:
        await engine.dispose()
    print(json.dumps({"imported": count}))
    return 0


async def _provision_credential(settings: Settings, args: argparse.Namespace) -> int:
    api_url = read_secret_file(args.api_url_file, name="the CloudStack API URL")
    api_key = read_secret_file(args.api_key_file, name="the user API key") if args.api_key_file else None
    api_secret = read_secret_file(args.api_secret_file, name="the user API secret") if args.api_secret_file else None
    vault = Vault(settings.vault_key())
    if not api_key or not api_secret:
        if not (args.admin_key_file and args.admin_secret_file):
            raise ConfigurationError("provide the user key files, or admin key files to retrieve existing keys")
        admin_credentials = CloudStackCredentials(
            api_url=api_url,
            api_key=read_secret_file(args.admin_key_file, name="the admin API key"),
            api_secret=read_secret_file(args.admin_secret_file, name="the admin API secret"),
        )
        async with CloudStackClient(admin_credentials) as client:
            keys = await client.get_user_keys(args.user_id)
            if keys is None and args.generate:
                keys = await client.register_user_keys(args.user_id)
            if keys is None:
                raise ConfigurationError("the user has no API keys; pass --generate to create them")
            api_key, api_secret = keys
    credentials = CloudStackCredentials(api_url=api_url, api_key=api_key, api_secret=api_secret)
    if args.verify:
        async with CloudStackClient(credentials) as client:
            await client.list_zones()
    payload = json.dumps({"api_url": api_url, "api_key": api_key, "api_secret": api_secret})
    ciphertext = vault.encrypt(payload, aad=credential_aad(args.cloud_id, args.user_id))
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        from conductor.db.identity import CredentialRepository

        async with factory() as session:
            await CredentialRepository(session).save(
                cloud_id=args.cloud_id,
                cloudstack_user_id=args.user_id,
                cloudstack_account_id=args.account_id,
                cloudstack_domain_id=args.domain_id,
                ciphertext=ciphertext,
                key_id=vault.key_id,
                source="retrieved" if not args.generate else "generated",
            )
            await session.commit()
    finally:
        await engine.dispose()
    print(json.dumps({"stored": True, "verified": bool(args.verify), "key_id": vault.key_id}))
    return 0


def _load_mapping_config(path: str) -> WorkbookColumnMap:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    effective_from = payload.get("effective_from")
    expected = {
        name: ComputeShape.model_validate(shape) for name, shape in (payload.get("expected_shapes") or {}).items()
    }
    return WorkbookColumnMap(
        sheet=payload.get("sheet"),
        header_row=int(payload.get("header_row", 1)),
        columns=payload.get("columns", {}),
        currency=payload.get("currency", "CNY"),
        effective_from=datetime.fromisoformat(effective_from) if effective_from else None,
        mapping_version=str(payload.get("mapping_version", "1")),
        offering_ids=payload.get("offering_ids", {}),
        expected_shapes=expected,
        strict_combined=bool(payload.get("strict_combined", True)),
    )


async def _import_prices(settings: Settings, args: argparse.Namespace) -> int:
    config = _load_mapping_config(args.mapping)
    result = WorkbookImporter(config).import_file(args.workbook)
    if not result.ok:
        print(
            json.dumps(
                {
                    "ok": False,
                    "errors": [{"code": issue.code, "row": issue.row_reference} for issue in result.errors],
                },
                indent=2,
            )
        )
        return 2
    result.bindings = [binding.model_copy(update={"cloud_id": args.cloud_id}) for binding in result.bindings]
    effective_from = config.effective_from or result.effective_from
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            repo = PricingRepository(session)
            existing = await repo.load_catalog(args.cloud_id)
            combined = PricingCatalog(
                rate_cards=tuple(existing.rate_cards) + tuple(result.rate_cards),
                bindings=tuple(
                    binding
                    for binding in existing.bindings
                    if binding.offering_id not in {item.offering_id for item in result.bindings}
                )
                + tuple(result.bindings),
                overrides=existing.overrides,
                version=args.version or f"import-{utcnow().strftime('%Y%m%d%H%M%S')}",
            )
            combined._validate()
            await repo.close_open_bindings(
                args.cloud_id,
                [binding.offering_id for binding in result.bindings],
                effective_to=effective_from,
            )
            await repo.save_import(
                cloud_id=args.cloud_id,
                result=result,
                imported_by=args.imported_by,
                catalog_version=combined.version,
                effective_from=effective_from,
            )
            await session.commit()
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "ok": True,
                "tariffs": len(result.tariffs),
                "bindings": len(result.bindings),
                "rate_cards": len(result.rate_cards),
                "checksum": result.checksum,
                "version": args.version or combined.version,
            }
        )
    )
    return 0


async def _ingest_docs(settings: Settings, args: argparse.Namespace) -> int:
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            ingestor = KnowledgeIngestor(session, EmbeddingClient(settings))
            report = await ingestor.ingest_directory(
                args.directory,
                default_version=args.version,
                chunk_tokens=args.chunk_tokens,
                overlap_tokens=args.overlap_tokens,
            )
    finally:
        await engine.dispose()
    print(json.dumps(report.model_dump(mode="json"), indent=2))
    return 0


async def _dispatch(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.command == "config-check":
        return await _config_check(settings, args.service)
    if args.command == "import-mapping":
        return await _import_mapping(settings, args.file)
    if args.command == "provision-credential":
        return await _provision_credential(settings, args)
    if args.command == "import-prices":
        return await _import_prices(settings, args)
    if args.command == "ingest-docs":
        return await _ingest_docs(settings, args)
    raise ConfigurationError("unknown command")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except ConductorError as exc:
        print(json.dumps({"ok": False, "code": exc.code}), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
