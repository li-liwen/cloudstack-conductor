# CloudStack UI integration assets

These files implement the non-invasive integration described in
[docs/cloudstack-ui.md](../../docs/cloudstack-ui.md).

```text
runtime/config.example.js     template; copy privately and fill placeholders
runtime/sso-entry.js          unified SSO entry link
runtime/console-launch.js     portal-mediated console button
nginx-injection.example.conf  generic sub_filter example
verify.sh                     asset/marker check
```

## Steps

1. Copy the runtime files to a private read-only static directory.
2. Replace `config.example.js` values with the deployment's public portal and
   SSO URLs. Do not put credentials, tenant names, or private addresses in the
   public scripts.
3. Inject `config.js`, `sso-entry.js`, and `console-launch.js` into the
   CloudStack UI HTML. The example uses `sub_filter`; review the CSP and prefer
   a nonce/hash over `unsafe-inline`.
4. Set `instanceSelector` to a stable selector for the deployment's CloudStack
   UI version if the default `[data-instance-id]` does not match.
5. Run `verify.sh <asset-base-url>` and test login/console in a browser.
6. Roll back by removing the injection; no CloudStack source files change.

## Version-specific source patches

If a deployment must modify CloudStack source instead, keep the version-matched
patch in the private operator workspace, regenerate it after every upgrade, and
review it before applying. Do not publish customer customizations here.
