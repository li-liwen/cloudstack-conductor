# CloudStack integration

## Adapter

`conductor.cloudstack.CloudStackClient` is a typed asynchronous HTTPX client.
It receives explicit credentials; it never reads credentials from the
environment. Reads are retried with bounded backoff, while mutations are not
blindly retried.

### Signature v1

CloudStack canonicalises a request by sorting parameter names, URL-encoding
values like Java's `URLEncoder`, lower-casing the entire signing string
(including parameter names), then HMAC-SHA1 with the user's secret. The adapter
implements and unit-tests this behavior. A common symptom of getting it wrong is
`401 unable to verify user credentials` even when the key is correct.

### Errors

Upstream error text is reduced to a safe public message and a stable code for
the operation. Detailed text is written only to private logs. A lost response
during a mutation raises `ReconciliationRequired`; the operation becomes
`needs_reconciliation` and is not retried automatically.

### Pagination

List methods follow `page`/`pagesize`, deduplicate repeated IDs, and stop at the
reported count. `listall=true` is handled where the command supports it. Usage
collection uses a separate restricted administrative reader credential.

## Credential provisioning

Customer requests always use the corresponding CloudStack user's own API key.
The execution service decrypts it from the vault at call time. Provisioning is
an operator action:

```bash
conductor-admin provision-credential \
  --cloud-id "$CONDUCTOR_CLOUD_ID" \
  --user-id <cloudstack-user-uuid> \
  --account-id <cloudstack-account-uuid> \
  --domain-id <cloudstack-domain-uuid> \
  --api-url-file /run/secrets/cloudstack_api_url \
  --admin-key-file /run/secrets/cloudstack_provisioner_key \
  --admin-secret-file /run/secrets/cloudstack_provisioner_secret \
  --verify
```

- Existing keys are retrieved when permitted.
- Keys are generated only when absent and `--generate` is explicit.
- Existing keys are never rotated automatically.
- Disabled API access is respected. There is no administrator fallback for a
  customer request.

The encrypted payload is AES-GCM, bound to the cloud and CloudStack user IDs.
Deleting or moving a ciphertext to another user does not decrypt.

## Supported operations

The typed action catalogue is in `src/conductor/policy/actions.py`:

- instance lifecycle: create, start, stop, reboot, resize, delete, restore,
  reinstall;
- volumes: create, attach, detach, expand, delete;
- snapshots: create (live snapshots are refused while the VM is running) and
  restore-to-new-volume;
- isolated network creation/deletion and firewall rule changes;
- SSH public-key registration;
- console launch through the broker. CloudStack returns a noVNC HTML page, not
  the WebSocket endpoint. The broker derives the native noVNC WebSocket target,
  keeps the native token server-side, and relays with the `binary` subprotocol;
  the edge/reverse proxy must support that long-lived WebSocket;
- managed guest tasks behind an isolated executor (disabled by default).

Not automated in v1:

- volume shrinking (CloudStack does not support it; explain migration options);
- live snapshots while running;
- dynamic compute scaling (use stop/change/start and preserve power state);
- arbitrary CloudStack commands, SQL, shell, URLs, or credentials in tool
  parameters.

## Capability and preconditions

The planner and execution service check the actual version/provider
capabilities and recheck live state before mutating. Unsupported or changed
state fails closed. Quotas and resource limits should be enabled in CloudStack
and checked by the execution service for the deployment's offerings.

## Test cloud expectations

A representative test cloud should include:

- shared-network and isolated-network behaviour;
- managed Ubuntu/AlmaLinux templates and a Debian template;
- small lab offerings and a representative production-shaped offering;
- duplicate usernames in separate domains, read-only, domain-admin, and legacy
  MFA identities;
- at most six concurrently managed test VMs and less than 24 GiB allocated
  CloudStack RAM;
- fixture ownership markers and cleanup restricted to positively identified
  fixture resources.

Preserve existing working resources and operational tooling. Record any
fidelity limits (shared VLANs do not imply packet isolation; NFS tier labels do
not prove SSD/CEPH behavior; GPU passthrough and multi-host failover are not
reproduced by a nested lab).

## Private upgrade preparation

Keep the CloudStack version pinned for the first delivery. Maintain private
baseline manifests, pre/post-upgrade tests, management/database backup
procedures, versioned UI-patch reapplication, system-VM checks, and disposable
workload restore tests. An upgrade requires an explicit target version and
coordinated recovery points.
