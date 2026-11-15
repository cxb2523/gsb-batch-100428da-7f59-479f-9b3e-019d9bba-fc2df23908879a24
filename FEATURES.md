# Features: Meta Ads Intelligence Platform

---

## Data Sync

### Automatic Scheduled Sync
- Runs on a configurable schedule: 1, 2, 3, or 4 times per day.
- Start time is user-defined. Additional runs are spaced evenly through the day.
- Schedule is applied immediately when settings are saved. No server restart required.
- Syncs all ad accounts across all organizations in sequence.

### Manual Sync
- One-click sync from the Settings page runs all accounts immediately.
- Result is visible in the sync status section without page reload.

### Date Range Modes
- **Auto mode:** Continues from the last synced date. On first run, uses a configured start date.
- **Manual mode:** Fixed date range (supports `today`, `yesterday`, or a specific `YYYY-MM-DD` date).

### Deduplication
- Each row is identified by a composite key: date + ad ID.
- Before writing, the sync engine loads all existing keys from the RAW_DATA sheet.
- Rows that already exist are skipped silently.
- Safe to re-run a sync for any date range without creating duplicate rows.

### Data Written to Google Sheets
- **RAW_DATA tab:** One row per ad per day, all raw API fields as returned.
- **ANALYTICS tab:** Rebuilt from RAW_DATA after every sync. Numeric fields are cleaned (type-cast), header row is bolded and frozen, and filters are applied automatically.

### Fields Synced from Meta API
Impressions, Clicks, Spend, Reach, Frequency, CPM, CPC, CTR, Unique CTR, Purchase ROAS, Purchase Value, Purchases, Add to Carts, Checkouts Initiated, Leads, Inline Link Clicks, Video Plays, Video P25/P50/P75/P100 Views, Campaign Name/ID, Ad Set Name/ID, Ad Name/ID, Date Start/End, Publisher Platform, and more.

### Error Handling
- Network errors, Meta API errors (token expired, account suspended, rate limits), and Google Sheets errors (sheet not found, worksheet missing) are all caught and written to sync status.
- Sync status shows per-account: status (success/error), rows added, message, and timestamp.
- Errors are logged to `logs/error.log` (rotating, 500 KB per file, 3 backups).

### Token Validation
- Before saving a new account, clicking Validate Token calls the Meta API immediately.
- If the token is invalid, the error message from Meta is shown before saving.

---

## Dashboard and Analytics

### KPI Cards
Displays summary totals for the selected date range with period-over-period change:
- Spend
- ROAS (Return on Ad Spend)
- CTR (Click-Through Rate)
- CPC (Cost Per Click)
- CPM (Cost Per Mille)
- Impressions
- Clicks
- Reach
- Purchases
- Leads

Change direction (up/down) and percentage are shown for each metric.

### Daily Trend Chart
- Line chart showing any metric over the selected date range.
- Switchable metric selector.
- Dual-axis support for combined metrics.

### Campaign Breakdown Table
- One row per campaign, sorted by spend descending.
- Columns: campaign name, spend, impressions, clicks, purchases, ROAS, CTR.
- Inline campaign trend chart on row click.

### Per-Campaign Metric Trend
- Line chart showing daily metric values for the top 5 campaigns by spend.
- Switchable metric: ROAS, CPL, CPM, Reach.

### Funnel Visualization
- Auto-detects funnel type from data:
  - Ecommerce: Impressions → Clicks → Add to Cart → Checkout → Purchase
  - Lead gen: Impressions → Clicks → Leads
- Shows absolute values at each stage and drop-off percentage between stages.
- Highlights the worst drop-off stage (leakage point) automatically.

### Frequency vs CTR Scatter Plot
- One point per ad.
- X axis: frequency. Y axis: CTR.
- Point size proportional to spend.
- Tooltip shows ad name, frequency, CTR, spend, CVR, ROAS, and CPL.

### Day-of-Week Heatmap
- Shows performance by weekday for the selected date range.
- Switchable metric: Spend, CTR, ROAS, CPM, Frequency, Reach.
- Useful for identifying which days perform better or worse.

### Platform Breakdown
- Splits spend, results, and ROAS by publisher platform.
- Platforms: Facebook, Instagram, Audience Network, Messenger.
- Shows each platform's percentage of total spend.

### Top Ads Table
- Shows top N ads sorted by any metric.
- Default sort: ROAS descending.

### Ad Set Level Table
- One row per ad set, with expandable rows showing individual ads inside.
- Columns: ad set name, spend, ROAS, CTR, CPC, CPM, frequency, purchases, leads.
- Rule flag badge (scale / pause / watch / ok) per row.

---

## Calculated Metrics

These metrics are added to every row automatically after reading from the sheet.

| Metric | Formula |
| --- | --- |
| Hook Rate % | Video Plays / Impressions x 100 |
| Hold Rate % | Video P100 Views / Video Plays x 100 |
| Thumb Stop Rate % | Video P25 Views / Impressions x 100 |
| Link CTR % | Inline Link Clicks / Impressions x 100 |
| CVR % | Purchases / Clicks x 100 |
| ATC Rate % | Add to Carts / Clicks x 100 |
| Checkout Rate % | Checkouts / Add to Carts x 100 |
| Cost Per Lead | Spend / Leads |
| Cost Per ATC | Spend / Add to Carts |
| AOV (Average Order Value) | Purchase Value / Purchases |

---

## Filtering System

### Metric Filters
- Available for 30+ metrics.
- Operators: greater-than, less-than, greater-or-equal, less-or-equal.
- Multiple filters combine with AND logic.
- Each filter shows the count of ads that pass the condition.

### Filterable Metrics
Spend, ROAS, CTR, Unique CTR, CPC, CPM, CPP, Impressions, Reach, Frequency, Clicks, Inline Clicks, Purchases, Leads, Add-to-Carts, Checkouts, Purchase Value, Cost Per Purchase, Hook Rate, Hold Rate, Thumb Stop Rate, Link CTR, CVR, ATC Rate, Checkout Rate, Cost Per Lead, Cost Per ATC, AOV.

### Special Filters
- **Above Break-even ROAS:** One-click filter to show only ads with ROAS above the configured break-even threshold.
- **Campaign:** Filter to a single campaign by name.
- **Ad Set:** Filter to a single ad set by name.
- **Rule Flag:** Filter to show only ads with a specific flag (scale / pause / watch / ok).

### Active Filter Display
- Active filters appear as chips above the table.
- Each chip shows the metric name, operator, and value.
- Chips can be removed individually.
- "Clear All Filters" removes all filters at once.

---

## Automation Rules Engine

### Rule Structure
- **Pause rule:** Triggers when an ad set has spent enough and meets performance conditions that indicate underperformance.
- **Scale rule:** Triggers when an ad set has spent enough and meets performance conditions that indicate strong performance.
- Both rules require minimum spend and minimum days running to be met before evaluation begins.
- Ad sets that have not yet met the minimum thresholds are labeled "watch."

### Condition Logic
- Conditions within a rule use AND or OR logic (user's choice).
- Pause rule conditions: ROAS below threshold, CTR below threshold, CPC above threshold.
- Scale rule conditions: ROAS above threshold, CTR above threshold, frequency below threshold.

### Business Type Modes
- **Ecommerce:** Uses ROAS and CTR signals. Pause/scale conditions are user-configured.
- **Lead Generation:** Uses cost-per-lead vs target CPL threshold. Evaluation logic is proprietary.
- **Awareness:** Uses frequency as the primary signal. Evaluation logic is proprietary.

### Flag Outputs
- `scale`: Ad set meets scale conditions.
- `pause`: Ad set meets pause conditions.
- `watch`: Ad set has not yet met the minimum spend/days threshold for evaluation.
- `ok`: Ad set does not meet either scale or pause conditions.

### Dashboard Integration
- Flag counts (how many scale / pause / watch / ok) shown as summary chips.
- Dashboard can be filtered to show only flagged ad sets.

---

## Multi-Account Support

- Add multiple Meta ad accounts to one organization.
- Each account stores: name, Meta access token, ad account ID, API version, Google Sheet ID, raw worksheet name, analytics worksheet name.
- Account limit is controlled by the license key.
- Live token validation before saving.
- Delete account requires password confirmation.
- Each account tracks its own sync status independently.

---

## Multi-Tenant Architecture

- Each user who registers creates a new organization.
- Organizations are completely isolated: separate settings, separate cache, separate API keys.
- One admin per organization. Admins can create staff users.
- Staff users within the same organization share the same data and settings view.
- No organization can see any data from another organization.

---

## User Management

- Two roles: admin and staff.
- Admin can create staff accounts from the Users page.
- Admin can delete staff accounts. Admin cannot delete their own account.
- Staff users see the dashboard but cannot access Settings or Users pages.
- All users belong to exactly one organization.

---

## Authentication

- Email and password login with bcrypt hashing.
- Google OAuth login (optional; enabled by setting Google Client ID and Secret in the environment).
- First Google OAuth login redirects to a profile setup page to complete username and display name.
- Forgot password flow sends a time-limited email link (valid for 1 hour).
- Change password from the Settings page.
- Update display name and username from the Settings page.
- Sessions last 24 hours.

---

## Notifications

- Telegram bot integration.
- After every sync (scheduled or manual), sends a per-account summary message.
- Summary shows: account name, status (success/failure), rows added or error message.
- Send Test Message button in Settings to verify the bot connection.

---

## License System

- JWT-based license keys.
- License stores: client name, expiry date, account limit.
- Settings page shows current license status: client name, expiry, allowed accounts.
- Wrong license, expired license, or no license defaults to a limit of 1 account.
- License validation logic is proprietary and not included in this public version.

---

## Settings Page

Everything is configurable from the web UI. No manual file editing after initial setup.

### Account Management
- Add account (name, token, account ID, sheet ID, worksheet names).
- Validate token (live API test).
- Update account (edit any field).
- Delete account (password confirmation required).

### Google API Key
- Upload Google service account JSON key from the browser.
- Service account email shown after upload.

### Sync Configuration
- Enable/disable scheduled sync.
- Set frequency (1, 2, 3, or 4 times per day).
- Set start time.
- Set date range mode (auto or manual) and dates.

### Dashboard Defaults
- Default date range (7, 14, 30 days, or custom).
- Business type (ecommerce, lead gen, awareness).
- Break-even ROAS threshold.
- Target CPL (for lead gen mode).

### Automation Rules
- Configure pause rule: enable/disable, min spend, min days, conditions, logic (AND/OR).
- Configure scale rule: enable/disable, min spend, min days, conditions, logic (AND/OR).

### Notifications
- Telegram bot token.
- Telegram chat ID.
- Test message button.

### License
- License key input.
- Current license status display.

### Organization
- Business manager / organization name.

---

## Edge Cases and Error Handling

| Situation | Behavior |
| --- | --- |
| Google Sheet not found | Sync fails with clear error message referencing the sheet ID |
| ANALYTICS worksheet missing | Error message: "Run a sync first to create the ANALYTICS tab" |
| Meta token expired | Sync fails with Meta API error code and message |
| Empty date range (already up to date) | Sync reports success with "Already up to date" |
| No data returned for date range | Sync reports success with "No data returned for range" |
| Cache file corrupted | Cache miss; fresh data fetched from Google Sheets |
| Scheduler startup failure | Logged to error.log; web app continues running |
| License key expired or wrong product | Falls back to 1 account limit |
| Telegram send failure | Logged to error.log; sync result is unaffected |
| Google Sheets API rate limit | Handled by gspread retry behavior; errors surface to sync status |
