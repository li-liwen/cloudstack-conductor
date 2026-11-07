# Pricing, usage, and estimates

All money is calculated with `decimal.Decimal` and serialized as a decimal
string. Binary floats are not used for prices. No payment, invoice, credits
ledger, or authoritative balance is implemented.

## Component formula

```text
compute/hour =
  CPU count × CPU unit rate
  + RAM GiB × memory unit rate
  + GPU count × GPU unit rate

storage/hour =
  SSD GiB × SSD unit rate
  + HDD GiB × HDD unit rate
```

Included default storage is priced as storage. It is never folded into a
compute-only rate, and a pre-combined hourly total from a private workbook is
never used as the compute rate.

Private deployment rules:

- compute charging stops when an instance stops;
- storage continues until the volume is deleted;
- detached volumes continue to count;
- compute discounts never discount storage;
- retained data disks are explained separately from instance deletion.

## Bindings and precedence

Prices bind to CloudStack service-offering UUIDs, not names. The binding records
the billable shape and a mapping version; shape mismatches are rejected.

Effective-dated overrides apply in this order:

1. account override;
2. nearest applicable domain override;
3. imported default tariff.

Historical versions and quote snapshots are retained. A quote is immutable once
attached to a plan.

## Unpriced offerings

An unpriced offering is shown as unpriced. It is not assigned a guessed price,
cannot be newly provisioned through the agent without a valid required quote,
and makes aggregate estimates explicitly partial.

## Importing a private workbook

`conductor-admin import-prices` reads a spreadsheet with component columns and a
private JSON mapping. The mapping supplies the worksheet/header row, column
aliases, currency, effective date, offering UUIDs, expected shapes, and whether
the combined-total validation is strict.

The importer:

- computes compute and storage separately;
- validates that a combined total equals component arithmetic, when present;
- rejects rows with missing component rates, negative rates, or a shape
  mismatch;
- groups rows by component-rate tuple into versioned rate cards;
- records the workbook checksum, row reference, and mapping version privately;
- closes open-ended bindings at the new effective date before publishing.

Real workbooks, rates, filenames, row references, and golden values stay in the
private operator workspace. Public tests generate temporary synthetic
spreadsheets at runtime.

## Prepaid multipliers

Prepaid multipliers are imported/configured privately and assigned by
administrators. A user cannot claim a discount in conversation or change it
through a resource tag.

## Estimates and forecasts

Display periods are computed in `Asia/Shanghai`; timestamps are stored in UTC.
Supported periods are calendar months and January–June / July–December
half-years.

A forecast uses known accrued usage plus remaining period cost assuming the
current resource state continues, using actual calendar duration. It states the
assumption. Recent unaggregated usage is labelled provisional.

Every result carries completeness, generation timestamp, pricing version, and:

> 费用预估，仅供参考，以最终账单为准。

## Usage collection

The collector uses a separate restricted CloudStack administrative reader. It:

- separates running from allocated instance usage;
- handles offering and volume-size changes by recording each record version;
- reconciles overlapping collection windows by usage record ID without
  duplication;
- reports gaps rather than reconstructing history from present state;
- partitions records by cloud/account/resource/period.

Privileged collection is separate from tenant queries.
