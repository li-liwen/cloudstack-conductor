# CloudStack Conductor — Portal

The browser application for CloudStack Conductor. It is a React 19 + TypeScript
+ Vite single-page application served by the Conductor API from the same origin.

- Conversational workspace with streaming run updates, plan/quote/resource cards,
  and citations
- Plan review and digest-bound browser approval
- Resource inventory (instances, volumes, networks, offerings, templates),
  instance detail drawer, one-time console launch
- Durable operation history, step timelines, live events, cancellation, and
  manual reconciliation
- Cost estimates with explicit completeness and the billing disclaimer
- Indexed documentation search with citations
- MCP connected-agent list with scopes and revocation
- Account and session information
- Operator views (identity mappings, pricing, migrations, health, audit), with
  price-override creation and a guarded mapping-write section
- Runtime branding and a Mandarin-first / English interface

All demo data in this directory is synthetic. No deployment identity, private
hostnames, credentials, real CloudStack identifiers, or real prices belong in
the bundle.

## Requirements

- Node.js 20.19 or newer
- npm (this project uses `npm` and commits `package-lock.json`)

## Commands

```bash
npm install       # install dependencies from package-lock.json
npm run dev       # Vite dev server on http://localhost:5173
npm test          # Vitest + React Testing Library
npm run build     # typecheck and build dist/
npm run preview   # preview the production bundle
```

`npm run dev` proxies `/api` to `http://localhost:8080` (see `vite.config.ts`).
Adjust the proxy locally if the API listens elsewhere. No `.env` file is
required, and the portal reads no `VITE_*` secrets or deployment values.

## Runtime configuration

At startup the portal fetches:

```
GET /api/v1/config
```

The response is the runtime allowlist served by the Conductor API. The portal
reads:

- `branding.product_name`, `company_name`, `short_name`, and taglines
- `branding.primary_color`, `accent_color` → CSS variables `--brand-primary`
  and `--brand-accent` (with a computed `--brand-contrast` for button text)
- `branding.logo_url`, `logo_mark_url`, and `favicon_url`
- `branding.environment_label`, `locale_default`, and `locales`
- `auth.mode`, `auth.login_url`, and `auth.logout_url`
- `features.mcp` and `features.console`
- `limits.approval_ttl_minutes`

The provider also sets the document title, favicon, logo, and product/company
name. If `/api/v1/config` is unavailable, generic defaults are used and a safe
banner is shown; no internal error text is rendered.

Branding asset URLs are accepted only as application-relative paths (or, for
support links, HTTP(S) URLs). Protocol-relative and external logo/favicon URLs
are rejected.

## Authentication, CSRF, and redirects

- Browser requests use `credentials: "same-origin"` and the signed session
  cookie.
- `GET /api/v1/auth/session` supplies the principal, expiry, MFA status, and
  `csrf_token`.
- Every mutating request sends `X-CSRF-Token`; the token is also read from the
  readable `conductor_csrf` cookie as a fallback.
- Any authenticated request that returns `401` clears client session state and
  redirects to `/login`, preserving the intended path.
- In `demo` mode, `/login` posts `{ subject }` to
  `/api/v1/auth/demo-login` and then re-reads `/api/v1/auth/session`.
  Otherwise it presents a same-origin SSO link to the configured login path.
- Logout posts to the configured logout endpoint; an IdP logout URL returned by
  the API is opened only when it is HTTP(S).

The operator router in the current API applies CSRF validation to its **read**
endpoints as well as writes. The portal therefore sends the CSRF header on
operator GET requests too.

## Streaming

Run streams use `EventSource` against:

```
GET /api/v1/runs/{run_id}/events
```

The server sends named events (`status`, `message`, `plan`, `done`, `error`)
whose JSON payload is a run-event envelope with `event`, `data`, and
`event_id`. The portal:

- parses the envelope and renders message parts, plan cards, quote cards,
  resource cards, progress notes, and citations;
- tracks the last event id and relies on the browser's `Last-Event-ID`
  reconnection; manual reconnects add `?after=<id>` as an explicit cursor;
- de-duplicates events by id and messages by durable `message_id`, so a replay
  after reconnection cannot duplicate a message;
- closes the stream on `done` or `error`.

Operation detail pages use the operation stream
(`GET /api/v1/operations/{id}/stream`), merge durable events by `event_id`, and
close when the operation reaches a terminal state.

## API integration notes

- The API serves the built SPA through `CONDUCTOR_PORTAL_STATIC_DIR`
  (`portal_static_dir` in settings). It mounts `dist/assets` at `/assets`,
  serves branding files from `dist/branding` (or the configured branding asset
  directory), and falls back to `dist/index.html` for client-side routes.
- `GET /api/v1/plans` returns bare plan objects, while
  `GET /api/v1/plans/{id}` returns a `PlanView` envelope containing `plan`,
  `approval`, `quote_summary`, and `can_approve`.
- Prices and estimates are decimal strings. The UI formats them by grouping the
  string's digits and never converts them to binary floating point.
- An `awaiting_approval` run is not treated as terminal by the API's run-event
  endpoint. The portal does not reconnect to such a run; it only resumes
  streams for `queued` and `running` runs.
- The console page is a relay-status placeholder only. It opens the WebSocket
  (`/api/v1/console/ws/{sessionId}`), reports whether the relay is active, and
  does not implement a VNC decoder. Console launches are one-time application
  URLs, opened in the same tab.

## Internationalization and accessibility

- zh-CN is the default locale. English switches persist in
  `localStorage["conductor.locale"]`.
- All static UI copy is in `src/i18n/messages.ts`. Server-provided localized
  pairs (plan effects, operation steps/events, run status) are selected by the
  active locale.
- Layouts respond down to 360 px, use visible focus styles, labels for inputs
  and icon buttons, a skip link, ARIA tab patterns, accessible drawers with
  focus trapping, and status regions for asynchronous work.
- Colors are CSS variables with a dark color-scheme block. Runtime brand colors
  drive buttons, navigation, and accents.

## Project structure

```
src/
  api/          same-origin client, typed endpoints, DTOs
  auth/         session provider and route guards
  components/   shell, shared UI, message parts, drawer, tabs
  config/       runtime config fetching, normalization, branding
  hooks/        abortable API query and named-event SSE stream
  i18n/         locale provider and zh-CN/en message catalog
  lib/          money, dates, safety, errors, SSE parsing, message merging
  pages/        one component per route
  styles/       global accessible/responsive CSS
  test/         Vitest setup and tests
public/branding/ original generic SVG logo, mark, and favicon
```

Routes: `/login`, `/`, `/plans`, `/plans/:id`, `/resources`, `/operations`,
`/operations/:id`, `/estimates`, `/docs`, `/connections`, `/account`,
`/operator` (operator role only), and `/console/:sessionId`.

## Tests

`npm test` runs five Vitest files covering runtime branding/config fallbacks,
locale switching and persistence, money formatting, SSE parsing/replay
de-duplication, and protected-route redirects.

The tests install a small in-memory `localStorage` shim when the test runtime's
storage is unavailable (some Node 26 + jsdom combinations shadow it); the
application itself still uses the browser's real storage.
