# ADR 0002: Decimal component pricing and digest-bound plans

## Context

A private workbook may contain a combined hourly total, default storage, and
prepaid prices. Using a combined total as a compute rate hides storage and makes
discounts incorrect. Prices and plans also change over time.

## Decision

- Calculate compute and storage from component unit rates using
  `decimal.Decimal`; never use binary floats for money.
- Bind rates to CloudStack offering UUIDs and a validated billable shape.
- Treat included default storage as storage, not compute.
- Apply account overrides before nearest-domain overrides before the imported
  tariff; discounts apply to compute only.
- Snapshot the quote into an immutable plan, hash the material plan fields, and
  bind approval to that digest.
- Keep historical rate cards, bindings, overrides, and quote snapshots.

## Consequences

Quotes are reproducible and explainable. A material change requires new
approval. Unpriced offerings are visible and cannot be provisioned through the
agent without a valid quote; aggregate estimates are explicitly partial.
