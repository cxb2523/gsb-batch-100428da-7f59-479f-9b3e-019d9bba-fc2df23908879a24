# Architecture: Meta Ads Intelligence Platform

---

## Overview

The platform is a Python web application built on FastAPI. It has three distinct layers: a web layer that handles HTTP, an authentication layer, and a core layer that contains all data processing logic. A background scheduler runs independently of the web layer for timed sync jobs.

All persistent data lives in one of three places: the main SQLite database (users and organization metadata), per-tenant SQLite databases (reserved for future use), and per-tenant file directories (encrypted settings, Google API keys, data cache).

---

## Module Map

```
meta-ads-intelligence/
├── run.py                  Entry point — starts Uvicorn
├── web/
│   ├── app.py              FastAPI app instance, middleware registration, startup events
│   └── routes/
│       ├── auth.py         Login, register, Google OAuth, password reset, profile update
│       ├── dashboard.py    Dashboard HTML page + JSON data endpoints
│       ├── settings.py     Settings page + all config save/update endpoints
│       ├── sync.py         Manual sync trigger
│       ├── setup.py        Post-OAuth profile completion
│       ├── users.py        Admin: create and delete staff users
│       └── api.py          Super-admin API endpoints (health, status)
├── core/
│   ├── meta_fetcher.py     Calls Meta Graph API, handles pagination and errors
│   ├── sync_engine.py      Orchestrates one full sync: fetch → deduplicate → write
│   ├── sheets_writer.py    Connects to Google Sheets, writes RAW_DATA, rebuilds ANALYTICS
│   ├── row_builder.py      Maps Meta API response fields to flat sheet columns
│   ├── data_reader.py      Reads ANALYTICS sheet, manages 60-min cache, computes metrics
│   ├── rules_engine.py     Groups rows by ad set, evaluates automation rules, assigns flags
│   ├── scheduler.py        APScheduler: starts, configures, and reschedules sync jobs
│   ├── config_manager.py   Load/save per-tenant encrypted settings.json
│   └── license.py          JWT license key decoder, account limit enforcement
├── db/
│   ├── main_db.py          SQLAlchemy models: Organization, User, TenantDatabase
│   ├── tenant_manager.py   ContextVar-based tenant routing, DB provisioning, path resolution
│   └── password.py         bcrypt hash and verify wrapper
└── scripts/
    └── migrate_existing.py One-time migration for legacy single-user installations
```

---

## Request Lifecycle

```
Browser Request
    |
    v
SessionMiddleware         (decode session cookie, attach session to request)
    |
    v
AuthMiddleware            (check session.user_id; redirect to /login if absent)
    |                     (set ContextVar org_id for duration of request)
    v
Route Handler             (validate input, call core services)
    |
    v
Core Service              (business logic: read, compute, write)
    |
    v
Response                  (JSON or HTML via Jinja2)
```

---

## Data Flow: Sync Pipeline

```
Meta Marketing API
    |  paginated HTTP GET (ad insights, level=ad, time_increment=1)
    v
meta_fetcher.py
    |  list of raw API row dicts
    v
sync_engine.py
    |  loads account settings from config_manager
    |  checks existing RAW_DATA sheet for known date+ad_id keys
    |  filters out duplicates
    v
row_builder.py
    |  maps API fields to sheet columns using field_map config
    v
sheets_writer.py
    |  appends new rows to RAW_DATA
    |  rebuilds ANALYTICS tab from RAW_DATA (cleans numeric types, applies formatting)
    v
Google Sheets
    |
    v
sync_engine.py
    |  invalidates data_reader cache for this account
    |  sends Telegram summary via Telegram Bot API
```

---

## Data Flow: Dashboard

```
Browser GET /dashboard/{account_id}
    |
    v
dashboard.py (route)
    |  returns HTML shell (Jinja2 template)
    v
Browser (JavaScript)
    |  fires GET /api/chart-data/{account_id}?start=...&end=...
    v
dashboard.py (JSON endpoint)
    |
    v
data_reader.py
    |  checks file cache (data/cache/{account_id}.json, 60-min TTL)
    |  if stale: fetches all rows from ANALYTICS worksheet via gspread
    |  enriches every row with computed metrics (Hook Rate, CVR, etc.)
    |  applies date filter
    |
    v
rules_engine.py
    |  groups enriched rows by Ad Set ID
    |  evaluates each ad set against pause/scale rules
    |  assigns rule_flag: scale | pause | watch | ok
    |
    v
data_reader.py (aggregation)
    |  computes KPI totals with period-over-period change
    |  computes daily trend per metric
    |  computes campaign breakdown
    |  computes funnel with leakage detection
    |  computes frequency vs CTR scatter data
    |  computes day-of-week heatmap
    |  computes platform breakdown
    |
    v
JSON response to Browser
    |
    v
Chart.js renders all charts
```

---

## Multi-Tenant Architecture

### Tenant Context

Each HTTP request that arrives for a logged-in user carries an `org_id` in the session cookie. The auth middleware reads this value and calls `set_current_org(org_id)`. This sets a Python `ContextVar` for the duration of the request.

Any code anywhere in the call stack can call `get_current_org()` to find the active organization without being passed the org_id explicitly. The config_manager and tenant_manager use this to resolve all file paths.

### Isolation Layers

| Layer | Isolation Mechanism |
| --- | --- |
| Settings files | Stored at `data/tenants/{org_id}/settings.json` |
| Google API key | Stored at `data/tenants/{org_id}/google-api-key.json` |
| Sheet data cache | Stored at `data/cache/{account_id}.json` (account IDs are org-scoped) |
| Tenant database | Stored at `data/tenants/db_org_{org_id}.db` |
| Main database | All SQL queries join on `organization_id` |

### Organization Provisioning

When a user registers, the system:
1. Creates an `Organization` row in the main database.
2. Calls `create_tenant_db(org_id)` to create the tenant SQLite file.
3. Creates a `TenantDatabase` mapping row.
4. Creates the user with `organization_id` set to the new org.
5. Creates the tenant data directory.

---

## Scheduler Architecture

APScheduler runs a `BackgroundScheduler` (thread pool, not async) inside the same process as FastAPI. The scheduler is started in the `startup` lifecycle event.

When the user saves new sync settings, the route calls `reschedule(app)`. This removes all existing `sync_job_*` jobs and adds new ones based on the updated frequency and start time.

Sync jobs call `_run_all_tenants()`, which queries the main database for all organization IDs, sets the tenant context for each, and runs `run_all_accounts()` for each org sequentially.

**Supported frequencies:**
- 1x/day: one job at the configured time
- 2x/day: two jobs 12 hours apart
- 3x/day: three jobs 8 hours apart
- 4x/day: four jobs 6 hours apart

---

## Settings Encryption

All settings (including Meta API tokens and Google Sheet IDs) are stored encrypted on disk. The config_manager uses Fernet symmetric encryption (from the `cryptography` library). The encryption key is loaded from the `SECRET_KEY` environment variable. Settings are decrypted only in memory when needed and never written to disk in plaintext.

---

## Cache Strategy

The data_reader uses a file-based cache with a 60-minute TTL. Cache files are JSON stored under `data/cache/`. When a sync completes, the sync engine calls `invalidate_cache(account_id)` to delete the cache file for that account. The next dashboard load fetches fresh data from Google Sheets.

This design keeps the dashboard fast during normal use (most loads serve cached data) while ensuring post-sync data is always visible immediately.

---

## Service Dependency Map

```
web/routes/dashboard.py
    -> core/data_reader.py
        -> gspread (Google Sheets)
        -> core/rules_engine.py
    -> core/config_manager.py
        -> db/tenant_manager.py

web/routes/settings.py
    -> core/config_manager.py
    -> core/license.py
    -> core/scheduler.py (reschedule)
    -> core/meta_fetcher.py (validate_meta_token)
    -> core/sync_engine.py (send_telegram_alert)

web/routes/sync.py
    -> core/sync_engine.py
        -> core/meta_fetcher.py
        -> core/row_builder.py
        -> core/sheets_writer.py
        -> core/config_manager.py
        -> core/data_reader.py (invalidate_cache)

core/scheduler.py
    -> db/main_db.py (query all org IDs)
    -> db/tenant_manager.py (set_current_org)
    -> core/sync_engine.py (run_all_accounts)

web/routes/auth.py
    -> db/main_db.py
    -> db/password.py
    -> db/tenant_manager.py (create_tenant_db on register)
```

---

## Technology Choices: Rationale

| Decision | Alternative Considered | Why This Was Chosen |
| --- | --- | --- |
| FastAPI | Flask | Async-native, built-in request validation, type hints throughout |
| SQLite per tenant | Shared PostgreSQL with tenant_id column | Hard isolation by construction; no query can accidentally cross tenants |
| APScheduler (in-process) | Celery + Redis | No external infrastructure needed; sync frequency is low (max 4x/day) |
| Google Sheets as data store | Internal database | Clients already work in Sheets; data accessible outside the app |
| Fernet encryption for settings | Plaintext JSON | API tokens and credentials never readable at rest |
| ContextVar for tenant routing | Passing org_id as a parameter | Clean call chain without threading issues; idiomatic for async code |
