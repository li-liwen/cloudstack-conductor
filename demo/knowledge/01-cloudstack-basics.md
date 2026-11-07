---
title: CloudStack instances, storage, networks, and estimates
version: "1.0"
source_url: https://example.com/docs/cloudstack-basics
language: en
license: CC-BY-4.0
section: Demo knowledge
---

-- Synthetic demo knowledge for the public quick start. Not copied from any
-- private deployment runbook. Version-pinned and safe to publish.

# CloudStack instances

A CloudStack instance is a virtual machine created from a template and a service
offering. A service offering defines CPU and memory; a template provides the
operating system disk. Instances are created inside a zone and attached to one
or more networks.

Stopping an instance releases compute resources but keeps its volumes. Starting it
again resumes compute charging.

# Volumes and storage

A volume holds persistent data and is charged as storage until it is deleted.
Detaching a volume does not delete it. Volume expansion is supported; shrinking is
not. After expanding a block volume, the guest filesystem may need a separate
online grow operation.

# Networks and firewalls

An isolated network provides a private CIDR, gateway, and firewall rules. Exposing
a port to a source range is a security-relevant change and requires explicit
approval. Prefer narrow source ranges over 0.0.0.0/0.

# Prices and estimates

Charges are calculated from component rates: CPU count multiplied by the CPU rate,
memory multiplied by the memory rate, GPU count multiplied by the GPU rate, plus
storage multiplied by its tier rate. A compute discount never discounts storage.

Estimates are informational only. The final invoice prevails.

# Console access

Console access uses a short-lived token issued by CloudStack. The token stays on
the server; a browser receives a single-use launch link that expires after 60
seconds. Console access needs no separate approval because the user already has
permission for the instance.
