# Case Study: Meta Ads Intelligence Platform

> A self-hosted Meta ads reporting and automation platform built for media buyers and agencies who need live data, custom analysis, and automated performance flags without paying per-seat SaaS fees.

---

## Client / Context

This project was built for a performance marketing workflow where the client runs Meta (Facebook and Instagram) ads across multiple ad accounts and manages reporting for several brands simultaneously. The existing process involved daily manual exports from Meta Ads Manager, copy-pasting into Google Sheets, and rebuilding charts for each reporting cycle. The client's team spent 30-60 minutes per day on data housekeeping that added no analytical value.

The client also needed a way to consistently apply performance rules across dozens of ad sets without relying on memory or daily manual review. Campaigns that should have been paused weeks earlier were still running because no one had time to check every ad set every day.

---

## The Challenge

Meta's native reporting interface is not designed for analysis. It shows data, but it does not integrate with the spreadsheets and workflows performance marketers actually use day to day. Every reporting cycle required manual steps that were slow, inconsistent, and prone to human error.

Beyond raw data, there was no systematic way to flag underperforming ad sets for action. Without a rules-based system, the decision of when to pause or scale came down to whoever had time to look at the numbers, which was rarely consistent.

For an agency managing multiple clients, the problem was compounded. Each client needed separate data, separate access, and separate reporting, but there was no self-hosted tool that handled true data isolation without significant infrastructure work.

### Key Pain Points

- **Daily manual work with no analytical output.** 30-60 minutes per day spent downloading, formatting, and loading data that was already in Meta Ads Manager.
- **No systematic performance flagging.** Ad sets that should be paused or scaled were identified inconsistently, or not at all.
- **No multi-client isolation.** Existing tools either mixed all client data together or required separate paid subscriptions per client.
- **Data ownership concern.** SaaS reporting tools store ad account data on third-party servers. For clients handling large ad budgets, this was not acceptable.

---

## The Approach

### Discovery Phase

The core questions were:
- What data do marketers actually look at each day?
- What decisions do they make from that data, and how?
- What is the minimum infrastructure that provides the right data without creating new maintenance burden?
- How should multi-client isolation work without multiplying deployment complexity?

The answers shaped the architecture: Google Sheets as the visible data store (not replacing it, working with it), a web dashboard for analysis that goes beyond what Sheets can do natively, and a rules engine with user-configurable thresholds rather than fixed platform rules.

### Architecture Decision

The key decision was whether to store ad data in an internal database or in Google Sheets. An internal database is faster for queries and gives more control over the schema. Google Sheets means the data is always accessible outside the app in the format teams already know.

Google Sheets won because the goal was to remove work, not add a new tool. If teams already review data in Sheets, putting a dashboard on top of Sheets is less disruptive than asking them to switch entirely to a new interface. The 60-minute cache layer in the app recovered the query performance tradeoff for dashboard loading.

For multi-tenancy, the choice was between a shared database with tenant scoping and separate database files per tenant. Separate SQLite files per organization were chosen because isolation is guaranteed by file system separation rather than correct query construction. A bug that omits a tenant filter in a shared schema exposes all clients' data. That failure mode does not exist when databases are physically separate.

### Engineering Priorities

The project prioritized reliability over feature density. Data integrity (no duplicate rows, clean type handling) was more important than adding more charts. The sync pipeline was designed to be safe to re-run at any time without side effects. Every error surface shows a clear, actionable message rather than a generic failure notice.

---

## What Was Built

A web application that a marketer installs on their own server and connects to their Meta ad accounts and Google Sheets. From that point, daily data collection is automatic. The dashboard gives them analysis capabilities that Sheets cannot provide: metric enrichment, automated funnel visualization, day-of-week patterns, and rules-based performance flagging.

For agencies, a multi-tenant registration model means each client registers and gets a completely isolated environment. One installation serves multiple clients without any data touching between them.

### Core Capabilities

- **Automatic data sync to Google Sheets.** Configured once, runs on schedule, never creates duplicates.
- **Live analytics dashboard.** KPI cards, 8 chart types, 30+ filterable metrics, all computed from the same Google Sheet the client already has.
- **Automated performance rules.** User-defined thresholds flag each ad set as scale, pause, watch, or ok after every sync. Three business type modes (ecommerce, lead gen, awareness) use different signals for different campaign goals.
- **Multi-tenant architecture.** Multiple organizations share one installation with complete data isolation at the file system and database level.
- **Self-hosted with no per-seat cost.** The client pays for their own VPS. There are no recurring software fees beyond the license key for multi-account support.

### Technical Highlights

- ContextVar-based tenant routing propagates the active organization context through the entire call stack without passing parameters explicitly through every function.
- Fernet symmetric encryption applied transparently by the config manager keeps all API credentials encrypted at rest.
- APScheduler with runtime reschedule support lets users change sync frequency and timing without restarting the application.
- Service layer architecture keeps all business logic (sync, rules, metrics) fully separate from HTTP routing, making both easier to test and maintain independently.

---

## Results and Impact

| Metric | Before | After |
| --- | --- | --- |
| Daily data collection time | 30-60 minutes (manual) | 0 minutes (automated) |
| Time to identify underperforming ad sets | 15-30 minutes of manual review | Immediate (rules run post-sync) |
| Reporting latency | Same-day at best (manual process) | Data fresh within 6 hours (4x/day sync) |
| Multi-client data isolation | Separate tools or manual separation | Guaranteed by architecture |

The elimination of manual data collection freed the team from a task that produced no analytical insight. The rules engine meant that every morning, the first thing visible was a list of ad sets that needed action, not a spreadsheet that still needed to be reviewed.

---

## Lessons Learned

### What Went Well

- Keeping Google Sheets as the visible data layer rather than replacing it removed the need to convince clients to change their workflow. The dashboard became an enhancement to something they already used, not a replacement.
- File-based tenant isolation proved simpler to reason about and debug than row-level isolation in a shared database would have been. There was never a question of whether a tenant isolation bug was possible.
- Designing the sync pipeline to be idempotent from the start saved significant debugging time. Re-running a sync for any date range at any time always produces the same result.

### What I Would Do Differently

- Add a task queue for sync jobs instead of running them in-process with APScheduler. For higher data volumes or more organizations, an external worker process would prevent sync jobs from competing with web request handling for CPU time.
- Build a read-only API layer from the start. Several features (export, external dashboard embedding, webhook-driven automation) were requested later and would have been easier to add if the dashboard data was already served through a clean, versioned API rather than page-specific endpoints.

---

## Technologies Used

Python, FastAPI, Uvicorn, SQLite, SQLAlchemy, APScheduler, gspread, oauth2client, PyJWT, Fernet (cryptography), bcrypt, Jinja2, Chart.js, python-telegram-bot, httpx, Nginx

---

## Want to Discuss This Project?

I am available for a private demo and technical walkthrough.

- **LinkedIn:** [linkedin.com/in/salehmdasif](https://linkedin.com/in/salehmdasif)
- **Portfolio:** [asif.ravelweb.com](https://asif.ravelweb.com)
- **Email:** salehmdasif@gmail.com
