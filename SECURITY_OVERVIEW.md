# Security Overview: Meta Ads Intelligence Platform

---

## Authentication

### Email and Password

Passwords are hashed with bcrypt before storage. The bcrypt work factor adapts over time and is resistant to brute-force and rainbow table attacks. Plaintext passwords are never stored or logged.

Password reset uses a time-limited token sent by email. The token is valid for 1 hour. After use, the token is invalidated. The flow does not leak whether a given email address exists in the system.

### Google OAuth

Google OAuth is an optional alternative to email/password login. The app follows the standard authorization code flow: the browser is redirected to Google, the user authenticates with Google, and Google returns a code that the app exchanges for a profile. The app never handles Google credentials.

New Google OAuth users are directed to a profile setup page before accessing any other page. This ensures every user has a username and organization assigned before any data is visible.

### Sessions

Sessions are implemented using Starlette's signed cookie middleware. Session cookies are:
- Signed with `SESSION_SECRET` (HMAC-based)
- Expire after 24 hours
- Not accessible to JavaScript (HttpOnly by default in Starlette)

Sessions store: `user_id`, `org_id`, `role`, and `is_profile_complete`.

---

## Authorization

### Role-Based Access Control

Two roles exist: `admin` and `staff`.

| Endpoint Category | Admin | Staff |
| --- | --- | --- |
| Dashboard (view) | Yes | Yes |
| Settings (read/write) | Yes | No |
| Sync (trigger) | Yes | No |
| Users (manage) | Yes | No |
| Profile (own account) | Yes | Yes |

Role is checked at the route level for every protected endpoint. Staff users who attempt to access admin endpoints receive a redirect or 403 response.

### Middleware Chain

Every request passes through `AuthMiddleware` before reaching any route handler. The middleware:
1. Checks if the path is on the public allowlist (login, register, forgot-password, static files, OAuth callback).
2. If not public and no `user_id` in session, redirects to `/login`.
3. If user exists but profile is incomplete, redirects to `/setup`.
4. Sets the tenant context (`org_id`) for the duration of the request.

---

## Data Isolation

### Tenant Isolation

Each organization's data is isolated at three levels:

1. **Settings and credentials:** Stored in a per-org directory (`data/tenants/{org_id}/`). No path can resolve to another org's directory.
2. **Sheet data cache:** Namespaced by account ID, which is scoped per org.
3. **Main database:** All user queries include `organization_id` in the WHERE clause. Users can only read their own org's data.

No shared database tables hold cross-tenant data in a single query-accessible form.

### API Credentials at Rest

Meta API tokens, Google Sheet IDs, and all other settings are encrypted at rest using Fernet symmetric encryption (AES-128-CBC with HMAC-SHA256). The encryption key is loaded from `SECRET_KEY` in the environment. Settings files on disk are opaque binary blobs that cannot be read without the key.

---

## API Security

### Super-Admin Endpoints

A small set of internal endpoints (`/api/*`) require an `X-API-Key` header matching `ADMIN_API_KEY` from the environment. These endpoints are for system health checks. Leaving `ADMIN_API_KEY` empty disables all `/api/*` endpoints entirely.

### External API Calls

The app makes outbound calls to:
- Meta Graph API (user's access token, scoped to ad account)
- Google Sheets API (service account, scoped to the specific sheet)
- Telegram Bot API (bot token, scoped to the configured chat)
- Google OAuth (app client ID/secret for authentication only)

All tokens and keys are loaded from encrypted settings at call time. They are never written to logs.

---

## License System

License keys are JWT tokens signed with a shared secret. The app decodes the JWT to read account limits and expiry. License validation logic is proprietary and not included in this public version. An invalid or expired license defaults to a 1-account limit rather than blocking access.

---

## Input Validation

FastAPI's request validation (based on Pydantic) rejects malformed request bodies before they reach route handlers. File uploads (Google API key JSON) are validated for JSON structure before being written to disk. Meta token validation calls the real Meta API before any token is saved to settings.

---

## Logging and Secrets

- The application log (`logs/error.log`) never writes API tokens, passwords, or encryption keys.
- Error messages from the Meta API are written to logs but contain only what Meta returns (error codes and messages, not credentials).
- The `.env` file is in `.gitignore`. The repository only includes `.env.example` with placeholder values.

---

## Dependency Notes

| Library | Security Role |
| --- | --- |
| `bcrypt` via `passlib` | Password hashing |
| `cryptography` (Fernet) | Settings file encryption at rest |
| `PyJWT` | License key decoding and session token handling |
| `itsdangerous` | Signed password reset tokens |
| `starlette` SessionMiddleware | Signed session cookies |
| `python-dotenv` | Environment variable loading (secrets never hardcoded) |
