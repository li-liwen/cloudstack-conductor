# Runtime branding

The public project and its images are branded **CloudStack Conductor** by
default. A deployment supplies its own identity at runtime; it is never
compiled into JavaScript or baked into a public image.

## Portal configuration

The portal loads `GET /api/v1/config` before rendering. The response contains
only allowlisted public values:

```json
{
  "branding": {
    "product_name": "CloudStack Conductor",
    "company_name": "CloudStack Conductor",
    "short_name": "Conductor",
    "tagline_zh": "…",
    "tagline_en": "…",
    "primary_color": "#0D5EF4",
    "accent_color": "#00BFA0",
    "logo_url": "/branding/logo.svg",
    "logo_mark_url": "/branding/logo-mark.svg",
    "favicon_url": "/branding/favicon.svg",
    "locale_default": "zh-CN",
    "locales": ["zh-CN", "en"],
    "environment_label": null
  }
}
```

The portal applies colors as CSS custom properties and sets the title, favicon,
and logo. Missing values use the generic defaults.

## Configuration document

Mount a read-only JSON file and set `CONDUCTOR_BRANDING_CONFIG_FILE`. Only keys
defined by `BrandingConfig` are accepted; unknown keys fail the service startup.
Asset URLs must be application-relative paths beginning with `/`.

## Assets

Mount a read-only directory and set `CONDUCTOR_BRANDING_ASSETS_DIR`. The API
serves `/branding/<filename>` with a filename allowlist and path-traversal
checks. If the directory is absent, the generic assets bundled in the portal
are served.

Do not copy a real deployment's logo files into the public repository or public
image. The demo uses original synthetic assets.

## Surfaces

Runtime branding can be applied to the portal, error pages, and MCP consent
where the identity-provider template supports it. CloudStack and Open WebUI
branding are deployment-specific and belong in private configuration or
reviewed UI patches; the public patches carry placeholders, not deployment
values.

## Frontend build rules

- No `VITE_*` variable may contain a deployment URL, company name, secret, or
  logo.
- The bundle must remain generic; source maps must not contain private paths.
- The API serves the built bundle at runtime. Rebuild the image when the portal
  changes; change branding by replacing the mounted runtime configuration and
  assets.
