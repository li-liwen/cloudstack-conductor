"""Private pricebook importer.

The importer reads component unit rates and computes compute and storage
separately. A pre-combined hourly total in the workbook is validated but is
never used as a compute-only rate. Real workbooks, rates, and row references
stay in the private operator workspace; public tests generate synthetic
spreadsheets at runtime.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from openpyxl import load_workbook

from conductor.errors import ValidationFailure
from conductor.models.common import ensure_utc, utcnow
from conductor.models.pricing import ComputeShape, OfferingBinding, RateCard
from conductor.pricing.engine import money4, unit6

IssueSeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class ImportIssue:
    code: str
    severity: IssueSeverity
    row_reference: str
    message: str


@dataclass(frozen=True)
class ImportedTariff:
    offering_name: str
    row_reference: str
    shape: ComputeShape
    compute_hourly: Decimal
    storage_hourly: Decimal
    total_hourly: Decimal
    combined_hourly: Decimal | None
    rate_card_version: str
    offering_id: str | None = None

    @property
    def source_reference(self) -> str:
        """Private row reference; never include in a public artifact."""

        return self.row_reference


@dataclass
class ImportResult:
    checksum: str
    sheet: str
    mapping_version: str
    currency: str
    effective_from: datetime
    rate_cards: list[RateCard] = field(default_factory=list)
    bindings: list[OfferingBinding] = field(default_factory=list)
    tariffs: list[ImportedTariff] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    skipped_rows: int = 0

    @property
    def errors(self) -> list[ImportIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ImportIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("实例名称", "服务名称", "规格名称", "名称", "instance name", "offering name", "name"),
    "cpu_count": ("CPU（核）", "CPU(核)", "CPU数量", "CPU核数", "cpu", "vcpu", "cpu count"),
    "cpu_rate": (
        "CPU单价（元/小时）",
        "CPU单价(元/小时)",
        "cpu unit rate",
        "cpu hourly rate",
        "cpu rate",
    ),
    "memory_gib": ("内存（GB）", "内存(GB)", "内存GB", "memory", "memory gib", "ram gib"),
    "memory_rate": (
        "内存单价（元/小时）",
        "内存单价(元/小时)",
        "memory unit rate",
        "memory hourly rate",
        "memory rate",
    ),
    "ssd_gib": (
        "默认固态硬盘（GB）",
        "默认固态硬盘(GB)",
        "固态硬盘（GB）",
        "ssd gib",
        "included ssd gib",
    ),
    "ssd_rate": (
        "固态硬盘单价（元/小时）",
        "固态硬盘单价(元/小时)",
        "ssd unit rate",
        "ssd hourly rate",
        "ssd rate",
    ),
    "hdd_gib": (
        "默认机械硬盘（GB）",
        "默认机械硬盘(GB)",
        "机械硬盘（GB）",
        "hdd gib",
        "included hdd gib",
    ),
    "hdd_rate": (
        "机械硬盘单价（元/小时）",
        "机械硬盘单价(元/小时)",
        "hdd unit rate",
        "hdd hourly rate",
        "hdd rate",
    ),
    "gpu_count": ("GPU数量", "GPU 数量", "gpu count", "gpu number"),
    "gpu_rate": (
        "GPU单价（元/小时）",
        "GPU单价(元/小时)",
        "gpu unit rate",
        "gpu hourly rate",
        "gpu rate",
    ),
    "gpu_model": ("GPU型号", "GPU 型号", "gpu model", "gpu type"),
    "combined_hourly": (
        "每小时价格（元）",
        "每小时价格(元)",
        "hourly price",
        "hourly total",
        "combined hourly",
    ),
    "prepaid_1y": ("1年预付价格（元）", "1年预付价格(元)", "1 year prepaid"),
    "prepaid_2y": ("2年预付价格（元）", "2年预付价格(元)", "2 year prepaid"),
    "prepaid_3y": ("3年预付价格（元）", "3年预付价格(元)", "3 year prepaid"),
}

_REQUIRED = ("name", "cpu_count", "cpu_rate", "memory_gib", "memory_rate")


def _normalise(value: object) -> str:
    text = str(value).strip().lower()
    text = text.replace("（", "(").replace("）", ")").replace("　", " ")
    return "".join(text.split())


@dataclass(frozen=True)
class WorkbookColumnMap:
    """Explicit column mapping, for workbooks whose headers differ.

    ``columns`` maps a logical field to a header name or a one-based column
    index. Any field omitted is auto-detected from :data:`DEFAULT_ALIASES`.
    """

    sheet: str | None = None
    header_row: int = 1
    columns: dict[str, str | int] = field(default_factory=dict)
    currency: str = "CNY"
    effective_from: datetime | None = None
    mapping_version: str = "1"
    offering_ids: dict[str, str] = field(default_factory=dict)
    expected_shapes: dict[str, ComputeShape] = field(default_factory=dict)
    strict_combined: bool = True


class WorkbookImporter:
    def __init__(self, column_map: WorkbookColumnMap | None = None) -> None:
        self.config = column_map or WorkbookColumnMap()

    def import_file(self, path: str | Path) -> ImportResult:
        source = Path(path)
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise ValidationFailure("the price workbook could not be read") from exc
        checksum = hashlib.sha256(content).hexdigest()
        effective_from = ensure_utc(self.config.effective_from or utcnow())
        try:
            workbook = load_workbook(source, read_only=True, data_only=True)
        except Exception as exc:  # openpyxl raises several exception types
            raise ValidationFailure("the price workbook is not a valid spreadsheet") from exc
        try:
            if self.config.sheet:
                if self.config.sheet not in workbook.sheetnames:
                    raise ValidationFailure("the configured worksheet was not found")
                sheet = workbook[self.config.sheet]
            else:
                sheet = workbook.worksheets[0]
            return self._parse_sheet(sheet, checksum=checksum, effective_from=effective_from, sheet_name=sheet.title)
        finally:
            workbook.close()

    def import_rows(
        self,
        rows: list[list[object]],
        *,
        checksum: str,
        sheet_name: str = "synthetic",
        effective_from: datetime | None = None,
    ) -> ImportResult:
        """Parse already-loaded rows (used by synthetic tests)."""

        return self._parse_rows(
            rows,
            checksum=checksum,
            effective_from=ensure_utc(effective_from or self.config.effective_from or utcnow()),
            sheet_name=sheet_name,
        )

    def _parse_sheet(self, sheet: Any, *, checksum: str, effective_from: datetime, sheet_name: str) -> ImportResult:
        rows: list[list[object]] = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(list(row))
        return self._parse_rows(rows, checksum=checksum, effective_from=effective_from, sheet_name=sheet_name)

    def _parse_rows(
        self,
        rows: list[list[object]],
        *,
        checksum: str,
        effective_from: datetime,
        sheet_name: str,
    ) -> ImportResult:
        result = ImportResult(
            checksum=checksum,
            sheet=sheet_name,
            mapping_version=self.config.mapping_version,
            currency=self.config.currency,
            effective_from=effective_from,
        )
        if not rows:
            result.issues.append(ImportIssue("empty_workbook", "error", "-", "the worksheet is empty"))
            return result
        header_index = max(0, self.config.header_row - 1)
        header = rows[header_index] if header_index < len(rows) else []
        columns, unresolved = self._resolve_columns(header)
        missing = [name for name in _REQUIRED if name not in columns]
        if missing:
            result.issues.append(
                ImportIssue(
                    "missing_columns",
                    "error",
                    "-",
                    "missing required columns: " + ", ".join(missing),
                )
            )
            return result
        if unresolved:
            result.issues.append(
                ImportIssue(
                    "unresolved_columns",
                    "warning",
                    "-",
                    "unresolved configured columns: " + ", ".join(sorted(unresolved)),
                )
            )

        cards_by_rates: dict[tuple[str, ...], RateCard] = {}
        names_seen: dict[str, tuple[ComputeShape, Decimal, Decimal]] = {}
        for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
            row_ref = f"{sheet_name}!{offset}"
            if row is None or all(cell is None or str(cell).strip() == "" for cell in row):
                result.skipped_rows += 1
                continue
            values: dict[str, object] = {}
            for name, index in columns.items():
                values[name] = row[index - 1] if index - 1 < len(row) else None
            name = str(values.get("name") or "").strip()
            if not name:
                result.issues.append(ImportIssue("missing_name", "warning", row_ref, "row has no offering name"))
                result.skipped_rows += 1
                continue
            parsed = self._parse_row(values)
            if isinstance(parsed, ImportIssue):
                result.issues.append(parsed)
                continue
            shape, cpu_rate, memory_rate, ssd_rate, hdd_rate, gpu_rate, combined = parsed

            rates_key = (
                format(cpu_rate, "f"),
                format(memory_rate, "f"),
                format(ssd_rate, "f"),
                format(hdd_rate, "f"),
                format(gpu_rate, "f"),
            )
            card = cards_by_rates.get(rates_key)
            if card is None:
                card = RateCard(
                    version=f"wb-{checksum[:10]}-{len(cards_by_rates) + 1:02d}",
                    currency=self.config.currency,
                    effective_from=effective_from,
                    cpu_hour=unit6(cpu_rate),
                    memory_gib_hour=unit6(memory_rate),
                    gpu_hour=unit6(gpu_rate),
                    ssd_gib_hour=unit6(ssd_rate),
                    hdd_gib_hour=unit6(hdd_rate),
                    source_checksum=checksum,
                    notes="Imported component rates",
                )
                cards_by_rates[rates_key] = card
                result.rate_cards.append(card)

            compute = money4(
                Decimal(shape.cpu_count) * cpu_rate
                + shape.memory_gib * memory_rate
                + Decimal(shape.gpu_count) * gpu_rate
            )
            storage = money4(shape.ssd_gib * ssd_rate + shape.hdd_gib * hdd_rate)
            total = money4(compute + storage)
            if combined is not None:
                tolerance = max(Decimal("0.01"), total * Decimal("0.005"))
                if abs(combined - total) > tolerance:
                    issue = ImportIssue(
                        "combined_total_mismatch",
                        "error" if self.config.strict_combined else "warning",
                        row_ref,
                        "the combined hourly total does not match component arithmetic",
                    )
                    result.issues.append(issue)
                    if issue.severity == "error":
                        continue
            if name in names_seen:
                previous = names_seen[name]
                if previous != (shape, compute, storage):
                    result.issues.append(
                        ImportIssue(
                            "duplicate_offering_name",
                            "error",
                            row_ref,
                            "duplicate offering name with a different shape or price",
                        )
                    )
                    continue
            names_seen[name] = (shape, compute, storage)

            offering_id = self.config.offering_ids.get(name)
            if offering_id:
                expected = self.config.expected_shapes.get(name)
                if expected is not None and expected != shape:
                    result.issues.append(
                        ImportIssue(
                            "shape_mismatch",
                            "error",
                            row_ref,
                            "the workbook shape does not match the CloudStack offering",
                        )
                    )
                    continue
                result.bindings.append(
                    OfferingBinding(
                        cloud_id="",  # filled by the operator import command
                        offering_id=offering_id,
                        offering_name=name,
                        shape=shape,
                        rate_card_version=card.version,
                        currency=self.config.currency,
                        effective_from=effective_from,
                        mapping_version=self.config.mapping_version,
                        source_reference=row_ref,
                    )
                )
            result.tariffs.append(
                ImportedTariff(
                    offering_name=name,
                    row_reference=row_ref,
                    shape=shape,
                    compute_hourly=compute,
                    storage_hourly=storage,
                    total_hourly=total,
                    combined_hourly=combined,
                    rate_card_version=card.version,
                    offering_id=offering_id,
                )
            )
        return result

    def _resolve_columns(self, header: list[object]) -> tuple[dict[str, int], set[str]]:
        resolved: dict[str, int] = {}
        unresolved: set[str] = set()
        normalised_headers = {_normalise(value): index for index, value in enumerate(header, 1)}
        for name, configured in self.config.columns.items():
            if isinstance(configured, int):
                resolved[name] = configured
                continue
            index = normalised_headers.get(_normalise(configured))
            if index:
                resolved[name] = index
            else:
                unresolved.add(name)
        for name, aliases in DEFAULT_ALIASES.items():
            if name in resolved:
                continue
            for alias in aliases:
                index = normalised_headers.get(_normalise(alias))
                if index:
                    resolved[name] = index
                    break
        return resolved, unresolved

    def _parse_row(
        self, values: dict[str, object]
    ) -> tuple[ComputeShape, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal | None] | ImportIssue:
        try:
            cpu_count = _int(values.get("cpu_count"))
            memory_gib = _decimal(values.get("memory_gib"))
            gpu_count = _int(values.get("gpu_count"), default=0)
            ssd_gib = _decimal(values.get("ssd_gib"), default=Decimal(0))
            hdd_gib = _decimal(values.get("hdd_gib"), default=Decimal(0))
            cpu_rate = _decimal(values.get("cpu_rate"))
            memory_rate = _decimal(values.get("memory_rate"))
            ssd_rate = _decimal(values.get("ssd_rate"), default=Decimal(0))
            hdd_rate = _decimal(values.get("hdd_rate"), default=Decimal(0))
            gpu_rate = _decimal(values.get("gpu_rate"), default=Decimal(0))
            combined = _decimal(values.get("combined_hourly"), default=None)
        except (ValueError, InvalidOperation):
            return ImportIssue("invalid_number", "error", "-", "a numeric cell could not be parsed")
        if cpu_count <= 0 or memory_gib <= 0:
            return ImportIssue("invalid_shape", "error", "-", "CPU and memory must be positive")
        if ssd_gib > 0 and ssd_rate <= 0:
            return ImportIssue("missing_storage_rate", "error", "-", "included SSD storage has no unit rate")
        if hdd_gib > 0 and hdd_rate <= 0:
            return ImportIssue("missing_storage_rate", "error", "-", "included HDD storage has no unit rate")
        if gpu_count > 0 and gpu_rate <= 0:
            return ImportIssue("missing_gpu_rate", "error", "-", "GPU count is set but no GPU unit rate exists")
        if any(rate < 0 for rate in (cpu_rate, memory_rate, ssd_rate, hdd_rate, gpu_rate)):
            return ImportIssue("negative_rate", "error", "-", "unit rates must not be negative")
        return (
            ComputeShape(
                cpu_count=cpu_count,
                memory_gib=memory_gib,
                gpu_count=gpu_count,
                ssd_gib=ssd_gib,
                hdd_gib=hdd_gib,
            ),
            cpu_rate,
            memory_rate,
            ssd_rate,
            hdd_rate,
            gpu_rate,
            combined,
        )


def _decimal(value: object, *, default: Decimal | None = None) -> Decimal:
    if value is None or str(value).strip() == "":
        if default is not None:
            return default
        raise ValueError("missing decimal")
    if isinstance(value, bool):
        raise ValueError("boolean is not a decimal")
    return Decimal(str(value).replace(",", "").strip())


def _int(value: object, *, default: int | None = None) -> int:
    parsed = _decimal(value, default=Decimal(default) if default is not None else None)
    if parsed != parsed.to_integral_value():
        raise ValueError("value must be an integer")
    return int(parsed)


def import_workbook(path: str | Path, column_map: WorkbookColumnMap | None = None) -> ImportResult:
    return WorkbookImporter(column_map).import_file(path)
