from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

from openpyxl import Workbook

from conductor.pricing.importer import WorkbookColumnMap, WorkbookImporter

HEADERS = [
    "实例名称",
    "CPU（核）",
    "CPU单价（元/小时）",
    "内存（GB）",
    "内存单价（元/小时）",
    "默认固态硬盘（GB）",
    "固态硬盘单价（元/小时）",
    "默认机械硬盘（GB）",
    "机械硬盘单价（元/小时）",
    "GPU数量",
    "GPU单价（元/小时）",
    "每小时价格（元）",
]


def _workbook(tmp_path, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "synthetic"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "synthetic-pricebook.xlsx"
    workbook.save(path)
    workbook.close()
    return path


def test_compute_excludes_included_storage(tmp_path) -> None:
    # Prices chosen only for this test; neither names nor rates are real.
    rows = [
        ["demo.small", 2, 0.02, 4, 0.01, 40, 0.001, 0, 0.0005, 0, 0, 0.12],
        ["demo.medium", 4, 0.02, 8, 0.01, 80, 0.001, 0, 0.0005, 0, 0, 0.24],
    ]
    path = _workbook(tmp_path, rows)
    result = WorkbookImporter().import_file(path)
    assert result.ok, [(issue.code, issue.row_reference) for issue in result.errors]
    assert len(result.tariffs) == 2
    small = result.tariffs[0]
    assert small.compute_hourly == Decimal("0.0800")
    assert small.storage_hourly == Decimal("0.0400")
    assert small.total_hourly == Decimal("0.1200")
    assert small.combined_hourly == Decimal("0.12")
    # The combined total must never be used as a compute-only rate.
    assert small.compute_hourly != small.combined_hourly


def test_combined_total_mismatch_blocks_import(tmp_path) -> None:
    rows = [["demo.mismatch", 2, 0.02, 4, 0.01, 40, 0.001, 0, 0.0005, 0, 0, 0.99]]
    result = WorkbookImporter().import_file(_workbook(tmp_path, rows))
    assert not result.ok
    assert any(issue.code == "combined_total_mismatch" for issue in result.errors)
    assert result.tariffs == []


def test_combined_only_rows_are_not_accepted(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "synthetic"
    sheet.append(["实例名称", "每小时价格（元）"])
    sheet.append(["demo.combined-only", 0.5])
    path = tmp_path / "combined-only.xlsx"
    workbook.save(path)
    result = WorkbookImporter().import_file(path)
    assert not result.ok
    assert any(issue.code == "missing_columns" for issue in result.errors)
    assert result.tariffs == []


def test_shapes_and_bindings(tmp_path) -> None:
    offering_id = "44444444-4444-4444-8444-444444444444"
    rows = [["demo.bound", 2, 0.02, 4, 0.01, 0, 0, 100, 0.0005, 1, 1.5, 0.04 + 0.1 + 1.5]]
    path = _workbook(tmp_path, rows)
    config = WorkbookColumnMap(
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        offering_ids={"demo.bound": offering_id},
    )
    result = WorkbookImporter(config).import_file(path)
    assert result.ok, [issue.code for issue in result.errors]
    assert len(result.bindings) == 1
    binding = result.bindings[0]
    assert binding.offering_id == offering_id
    assert binding.shape.hdd_gib == Decimal("100")
    assert binding.shape.gpu_count == 1
    assert binding.cloud_id == ""


def test_checksum_is_recorded(tmp_path) -> None:
    path = _workbook(tmp_path, [["demo.small", 1, 0.01, 1, 0.01, 10, 0.001, 0, 0, 0, 0, 0.03]])
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    result = WorkbookImporter().import_file(path)
    assert result.checksum == expected
    assert all(card.source_checksum == expected for card in result.rate_cards)


def test_negative_rates_are_rejected(tmp_path) -> None:
    rows = [["demo.negative", 2, -0.02, 4, 0.01, 0, 0, 0, 0, 0, 0, 0.06]]
    result = WorkbookImporter().import_file(_workbook(tmp_path, rows))
    assert not result.ok
    assert any(issue.code == "negative_rate" for issue in result.errors)


def test_expected_shape_mismatch_blocks_binding(tmp_path) -> None:
    from conductor.models.pricing import ComputeShape

    offering_id = "55555555-5555-4555-8555-555555555555"
    rows = [["demo.shape", 2, 0.02, 4, 0.01, 40, 0.001, 0, 0, 0, 0, 0.12]]
    path = _workbook(tmp_path, rows)
    config = WorkbookColumnMap(
        offering_ids={"demo.shape": offering_id},
        expected_shapes={"demo.shape": ComputeShape(cpu_count=8, memory_gib=Decimal(16), ssd_gib=Decimal(40))},
    )
    result = WorkbookImporter(config).import_file(path)
    assert not result.ok
    assert any(issue.code == "shape_mismatch" for issue in result.errors)
    assert result.bindings == []
