# Database Overview: Meta Ads Intelligence Platform

---

## Storage Architecture

The platform uses three distinct storage layers:

1. **Main SQLite database** (`data/main.db`) — stores users, organizations, and tenant DB mappings.
2. **Per-tenant SQLite databases** (`data/tenants/db_org_{id}.db`) — reserved for future tenant-specific relational data.
3. **Per-tenant file directories** (`data/tenants/{id}/`) — stores encrypted settings and cached sheet data.

This split was chosen to provide hard tenant isolation without a database server.

---

## Entity Relationship Diagram

> View this diagram: open the `.mmd` file in [Mermaid Live Editor](https://mermaid.live)

```mermaid
erDiagram
    Organization ||--o{ User : "has members"
    Organization ||--o| TenantDatabase : "maps to"

    Organization {
        id PK
        name
        created_at
    }

    User {
        id PK
        email
        username
        name
        role
        organization_id FK
        is_profile_complete
        google_id
        created_at
    }

    TenantDatabase {
        organization_id PK FK
        db_name
    }
```

---

## Table Purposes

### `organizations`

Stores each registered tenant. Created when a user first registers.

| Field | Purpose |
| --- | --- |
| id | Primary key, used throughout as the tenant identifier |
| name | Display name for the organization |
| created_at | Registration timestamp |

### `users`

Stores all user accounts across all organizations. A user belongs to exactly one organization.

| Field | Purpose |
| --- | --- |
| id | Primary key |
| email | Used for login and password reset (nullable for staff accounts) |
| username | Login handle, unique across all users |
| password_hash | bcrypt-hashed password (nullable for Google-only accounts) |
| name | Display name shown in the UI |
| role | Either "admin" or "staff" |
| organization_id | Foreign key to organizations |
| is_profile_complete | False until the profile setup step is complete (relevant for Google OAuth new users) |
| google_id | Google OAuth subject identifier (nullable for email/password users) |
| created_at | Account creation timestamp |

### `tenant_databases`

Maps each organization to the filename of its SQLite database. Used when provisioning a new org or looking up an existing tenant DB.

| Field | Purpose |
| --- | --- |
| organization_id | Primary key, foreign key to organizations |
| db_name | The database filename (e.g., `db_org_1`) |

---

## Per-Tenant File Storage

Each organization has a directory at `data/tenants/{org_id}/`. All sensitive data lives here.

### `settings.json` (encrypted)

Contains all configuration for the organization. Stored as a Fernet-encrypted binary file. Never readable in plaintext on disk.

Top-level keys:
- `accounts` — list of Meta ad accounts (token, ad account ID, sheet ID, worksheet names)
- `sync` — schedule settings (enabled, frequency, sync_time)
- `date_range` — mode and date bounds for sync
- `dashboard` — UI defaults (date range, business type, break-even ROAS, target CPL)
- `automation` — pause and scale rule definitions
- `telegram` — bot token and chat ID
- `license_key` — JWT license key string

### `google-api-key.json`

The Google service account JSON key file, uploaded via the Settings page.

### `sync_status.json`

Tracks the last sync result per ad account. Written after every sync run.

Example structure:
```json
{
  "account-uuid-here": {
    "account_id": "account-uuid-here",
    "account_name": "My Ad Account",
    "timestamp": "2025-01-15T08:05:22",
    "status": "success",
    "rows_added": 47,
    "message": "47 rows added, 12 skipped"
  }
}
```

---

## Cache Layer

Sheet data is cached in `data/cache/` to avoid repeated Google Sheets API calls.

Each ad account gets one cache file named `{account_id}.json`.

Cache file structure:
```json
{
  "timestamp": "2025-01-15T09:15:00",
  "data": [ ... enriched row dicts ... ]
}
```

TTL: 60 minutes from the timestamp. After a sync completes, the cache file for that account is deleted immediately so the next dashboard load fetches fresh data.

---

## Google Sheets Schema

The platform writes to two worksheets per ad account.

### `RAW_DATA` worksheet

One row per ad per day. All fields as returned by the Meta API. No transformations applied. This tab is the source of truth and is append-only (existing rows are never modified).

### `ANALYTICS` worksheet

Rebuilt from RAW_DATA after every sync. All numeric fields are type-cast (floats and integers cleaned to consistent types). Header row is bolded, frozen, and filtered.

The ANALYTICS tab is what the dashboard reads. It is also the tab users browse directly in Google Sheets.

---

## Schema Design Notes

**Why SQLite instead of PostgreSQL for the main database?**
The main database has very low write volume (only on user registration and login). SQLite with WAL mode handles this without contention. The zero-configuration deployment is a significant advantage for a self-hosted tool.

**Why separate SQLite per tenant instead of a shared database with tenant_id columns?**
Separate files provide hard isolation. A bug that accidentally omits a `WHERE organization_id = ?` clause in a shared database would expose all tenants' data. With separate databases, no such cross-tenant query is physically possible.

**Why file storage for settings instead of a database table?**
Settings are a single document per tenant that is read and written as a whole. A JSONB column in PostgreSQL or a key-value table would work, but file storage with Fernet encryption provides the same result with simpler implementation and no additional database dependency.

**Why an in-file cache instead of Redis?**
The cache is simple: one JSON file per account, 60-minute TTL, invalidated after sync. Redis would add infrastructure complexity for a use case that file I/O handles adequately.
