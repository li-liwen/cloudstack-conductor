"""Persistent schema.

JSON columns hold only already-public or operator-only domain documents.
Never store a CloudStack secret in plaintext: credentials and console endpoints
are encrypted by the vault before they reach these tables.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from conductor.db.base import Base, JSONType, new_id, utcnow


class IdentityMappingRow(Base):
    __tablename__ = "identity_mappings"

    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cloudstack_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cloudstack_account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cloudstack_domain_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cloudstack_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    cloudstack_username: Mapped[str] = mapped_column(String(255), nullable=False)
    account: Mapped[str] = mapped_column(String(255), nullable=False)
    domain_ancestors: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    mapping_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="unmigrated")
    saml_provider_id: Mapped[str | None] = mapped_column(String(255))
    roles: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    project_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    delegated_account_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    mfa_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    legacy_2fa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("cloud_id", "cloudstack_user_id", name="uq_identity_cloud_user"),
        Index("ix_identity_domain_username", "cloudstack_domain_id", "cloudstack_username"),
    )


class CredentialRow(Base):
    __tablename__ = "cloudstack_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cred"))
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cloudstack_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cloudstack_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cloudstack_domain_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="retrieved")
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("cloud_id", "cloudstack_user_id", name="uq_credential_cloud_user"),)


class ConversationRow(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("conv"))
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tenant_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    cloud_context: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (Index("ix_conversation_owner_updated", "principal_subject", "updated_at"),)


class MessageRow(Base):
    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg"))
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    parts: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    citations: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    run_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_message_conversation_created", "conversation_id", "created_at"),)


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), index=True
    )
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_id: Mapped[str | None] = mapped_column(String(64))
    operation_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    user_message_id: Mapped[str | None] = mapped_column(String(64))
    assistant_message_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class RunEventRow(Base):
    __tablename__ = "run_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("runs.run_id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_run_event_run_id_event", "run_id", "event_id"),)


class PlanRow(Base):
    __tablename__ = "plans"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tenant_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    resources: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    quote: Mapped[dict | None] = mapped_column(JSONType)
    pricing_version: Mapped[str | None] = mapped_column(String(200))
    preconditions: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    effects: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    downtime_seconds: Mapped[int | None] = mapped_column(Integer)
    data_effects: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    approval_requirement: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation_zh: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation_en: Mapped[str] = mapped_column(Text, nullable=False, default="")
    request_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("principal_subject", "idempotency_key", name="uq_plan_principal_idempotency"),
        Index("ix_plan_owner_created", "principal_subject", "created_at"),
    )


class ApprovalRow(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("apr"))
    plan_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("plans.plan_id", ondelete="CASCADE"), unique=True, index=True
    )
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="browser_session")
    note: Mapped[str | None] = mapped_column(String(500))
    client_fingerprint: Mapped[str | None] = mapped_column(String(64))
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class OperationRow(Base):
    __tablename__ = "operations"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("op"))
    plan_id: Mapped[str] = mapped_column(String(64), ForeignKey("plans.plan_id", ondelete="RESTRICT"), index=True)
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tenant_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_id: Mapped[str | None] = mapped_column(String(64))
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    cloudstack_job_id: Mapped[str | None] = mapped_column(String(128))
    resource_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    steps: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    result: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    needs_reconciliation_reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("principal_subject", "idempotency_key", name="uq_operation_principal_idempotency"),
        Index("ix_operation_owner_created", "principal_subject", "created_at"),
    )


class OperationEventRow(Base):
    __tablename__ = "operation_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    operation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("operations.operation_id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    message_zh: Mapped[str] = mapped_column(Text, nullable=False)
    message_en: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_op_event_operation_event", "operation_id", "event_id"),)


class RateCardRow(Base):
    __tablename__ = "price_rate_cards"

    cloud_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(100), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cpu_hour: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    memory_gib_hour: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    gpu_hour: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    ssd_gib_hour: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    hdd_gib_hour: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    source_checksum: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PriceBindingRow(Base):
    __tablename__ = "price_bindings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("bind"))
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    offering_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    offering_name: Mapped[str] = mapped_column(String(255), nullable=False)
    shape: Mapped[dict] = mapped_column(JSONType, nullable=False)
    rate_card_version: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mapping_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    source_reference: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("cloud_id", "offering_id", "effective_from", name="uq_binding_cloud_offering_effective"),
    )


class PriceOverrideRow(Base):
    __tablename__ = "price_overrides"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PriceImportRow(Base):
    __tablename__ = "price_imports"

    import_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pimp"))
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(32), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_by: Mapped[str] = mapped_column(String(255), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    manifest: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)


class PricingPublicationRow(Base):
    __tablename__ = "pricing_publications"

    version: Mapped[str] = mapped_column(String(100), primary_key=True)
    cloud_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    published_by: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)


class ConsoleLeaseRow(Base):
    __tablename__ = "console_leases"

    lease_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("lease"))
    vm_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vm_name: Mapped[str | None] = mapped_column(String(255))
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tenant_key: Mapped[str] = mapped_column(String(160), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    launch_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    endpoint_ciphertext: Mapped[str | None] = mapped_column(Text)
    endpoint_key_id: Mapped[str | None] = mapped_column(String(32))
    endpoint_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MCPConnectionRow(Base):
    __tablename__ = "mcp_connections"

    connection_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("mcp"))
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(64))
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(500))


class MCPAuditRow(Base):
    __tablename__ = "mcp_audit"

    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("mcpa"))
    connection_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("mcp_connections.connection_id", ondelete="CASCADE"), index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str | None] = mapped_column(String(32))


class KnowledgeDocumentRow(Base):
    __tablename__ = "knowledge_documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    section: Mapped[str | None] = mapped_column(String(300))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    license_note: Mapped[str | None] = mapped_column(String(500))
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("source_url", "version", "content_hash", name="uq_doc_source_version_hash"),)


class KnowledgeChunkRow(Base):
    __tablename__ = "knowledge_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("knowledge_documents.document_id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(300))
    lexical_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024).with_variant(JSON(), "sqlite"))

    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_chunk_document_ordinal"),)


class UsageRecordRow(Base):
    __tablename__ = "usage_records"

    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    zone_id: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_name: Mapped[str | None] = mapped_column(String(255))
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_type: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    offering_id: Mapped[str | None] = mapped_column(String(64))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))
    offered_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    window_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    __table_args__ = (
        Index("ix_usage_account_period", "cloud_id", "account_id", "start_at"),
        Index("ix_usage_resource_period", "cloud_id", "resource_id", "start_at"),
    )


class UsageWindowRow(Base):
    __tablename__ = "usage_windows"

    window_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cloud_id: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    records_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_stored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gaps: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("audit"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    actor_subject: Mapped[str | None] = mapped_column(String(255), index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)


class PasswordMigrationRow(Base):
    __tablename__ = "password_migrations"

    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    cloudstack_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    domain_id: Mapped[str] = mapped_column(String(64), nullable=False)
    saml_provider_id: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    legacy_session_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
