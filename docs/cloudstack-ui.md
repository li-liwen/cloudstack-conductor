# CloudStack UI integration

The public repository does not include proprietary CloudStack UI source or
version-specific source diffs. Instead it provides a non-invasive, version-
checked runtime integration plus a documented path for private source patches.

## What is integrated

1. **Unified login entry** — adds an SSO button/link to the CloudStack login
   page, pointing at the portal or the domain-specific SAML initiation URL.
2. **Console launch** — adds a button on the instance view that calls the
   portal's console endpoint and opens the returned single-use launch link.

Both scripts are small, dependency-free, and read configuration from a single
`window.CONDUCTOR_UI_CONFIG` object injected by the reverse proxy. They never
contain deployment values.

## Why runtime injection

- CloudStack UI files change between minor releases; source patches are fragile
  and need private version-matched recreation after every upgrade.
- The public repo must not carry a customer's customizations.
- Injection is reversible by removing a proxy snippet, without rebuilding the
  CloudStack UI.

For a release that cannot use injection, regenerate a private source patch
against the exact upstream tree, review it, and reapply it after every upgrade.
Keep it in the private operator workspace; do not publish customer-specific
customizations.

## Files

```text
deploy/cloudstack-ui/
├── README.md
├── runtime/sso-entry.js
├── runtime/console-launch.js
├── runtime/config.example.js
├── nginx-injection.example.conf
└── verify.sh
```

## Apply

1. Copy the runtime scripts to a private static path served by the edge proxy.
2. Set the private config object:

```js
window.CONDUCTOR_UI_CONFIG = {
  portalBaseUrl: "https://portal.example.com",
  ssoEntryUrl: "https://portal.example.com/api/v1/auth/login",
  consoleApiBase: "https://portal.example.com/api/v1"
};
```

3. Inject the config, then the two scripts into the CloudStack UI HTML with
   `sub_filter` or an equivalent proxy mechanism. See
   `nginx-injection.example.conf`.
4. Clear caches and verify in a browser.
5. Roll back by removing the injection; no CloudStack files were modified.

## Version check

`verify.sh` checks the deployment's `cs_version` and confirms that the
configured static assets are reachable and contain the expected marker. It
does not assert undocumented upstream file paths.

## Security notes

- The SSO entry is a link to the identity provider; it never receives a
  password.
- The console button calls the portal, not CloudStack directly. The native
  console token stays server-side; the browser receives a single-use,
  user-bound link.
- Do not inject scripts from third-party CDNs. Serve reviewed files from the
  private origin with CSP hashes or a nonce.
- `unsafe-inline`/`unsafe-eval` must not be added to CloudStack's CSP merely to
  make an injection work; prefer a nonce or a script file.
- Do not place tenant names, endpoints, credentials, or the logo URL of a real
  deployment in the public scripts.

## Private header/logo branding

CloudStack UI branding is deployment-specific. Apply it through the private
proxy or a private, version-pinned patch. The public repository only documents
the mechanism.
