# FacebookScraper

This is the 2026/2027 version.

> DISCLAIMER: this script is not authorized by Facebook. For commercial
> use please contact Facebook. The purpose of this script is for
> **educational**, to demonstrate how Scrapy can be written to extract
> pages with less help of headless browser. Use it at your own risk.
>
> WARNING: your Facebook account might get suspended if your spider runs
> too fast. Please be careful. Try to increase download_delay in
> `settings.py`.

## 2024 version

Login was done through Splash. Facebook encrypts the password client
side before sending it to the server. Instead of sending a plaintext
password, `strongpass` gets encrypted like this:

`#PWD_BROWSER:5:........`

Reverse engineering that encryption would be tedious to get right, so
Splash performed the login request, grabbed the cookies, and the rest
was normal Scrapy parsing. The mobile version was parsed because its
layout was easier to scrape.

That world is gone. Splash is dead, the mobile layout is outdated, and
bot detection is much stronger.

## 2026 version

Splash was picked originally because it is scalable: a separate
service, not a big fat browser living inside the Python process. This
version does the same thing with Playwright. The browser runs as a
separate service in Docker, Scrapy only talks to it, so this script
stays lightweight and scalable.

The process is more complicated than before. Login is manual, one time,
to avoid bot detection, and the session is saved. The bot reuses that
session for a long run.

Scraping uses the desktop version instead of mobile. There is room to
optimize: a mobile version means fewer requests and a smaller request
body to scrape, but not currently. That will probably come later.

## Requirements

- Python 3.14 (developed on 3.14.8 via pyenv) with an isolated venv.
- `pip install -r requirement.text` (or `make install`).
- Pinned trio: `Scrapy==2.19.0`, `scrapy-playwright==0.0.48`,
  `playwright==1.63.0`.
- Docker for the browser server:
  `mcr.microsoft.com/playwright:v1.63.0-noble`. Client and server
  versions must match or the connection fails.

## Browser service

The browser runs outside Python, like Splash did:

- `make pw-server-up` starts Chromium headless in Docker, listening on
  port 3000.
- Scrapy connects over `PLAYWRIGHT_CONNECT_URL=ws://localhost:3000/`.
- `make pw-server-logs` tails it, `make pw-server-down` stops it.

Each scraped page is a remote Playwright page. The Python process stays
light and the browser side scales independently.

## Manual login

Automated login always trips the bot check, so login is manual, once:

- `make auth` opens a visible Chromium at the Facebook login page.
- Log in with the test account, clear 2FA or the bot challenge if one
  appears, wait for the feed, then press Enter.
- The session is saved to `auth.json` and every scrape request reuses it
  as a Playwright storage state.

If the session expires the run does not close silently anymore. The
spider records the expiry as its stop cause, writes one
`session_expired` outcome for the current page and every page that was
not requested yet, and stops sending further requests. Just run
`make auth` again and check the outcome report (below) to see what was
and was not collected.

## Running the scraper

```
make pw-server-up
make auth                      # once, or when the session expires
FB_PAGE_ID=ejeab FB_OUTPUT=fb.json make crawl
```

Or set them once as environment variables (for example in `.envrc`,
which is gitignored) and run plain `make crawl`.

`FB_PAGE_ID` accepts one page or several split by comma.
`FB_OUTPUT` defaults to `fb.json`. The flow is timeline to story to
item: the timeline render yields one story url, the story render yields
the post item with counts and comments.

## Per-page outcome report

When several pages are passed in `FB_PAGE_ID`, every page gets one
verifiable ending so an operator can tell which pages were collected and
which were not, and why. The endings are written alongside the crawl to
a JSON report, `fb_outcomes.json` by default (override with
`FB_OUTCOMES`, or the spider argument `outcome_output=...`). The report
is rewritten after every outcome, so the endings already recorded
survive the run being stopped mid-way.

Each entry in `outcomes` carries the `page_id`, its `page_url`, a
`status`, a human-readable `detail`, and — on success — the produced
post in `post_ids` / `post_urls` so the entry can be cross-checked
against the exported items. Possible statuses:

- `success`: the page produced a post item; `post_ids` and `post_urls`
  point at it.
- `no_post_id`: the timeline rendered but no story id was found in it,
  so no story request was sent.
- `session_expired`: the login form was returned. The current page and
  every later page get this status; no request is sent for the later
  pages.
- `parse_error`: any other parsing failure. The exception type and
  message are in `detail`, and the remaining pages are still processed.

The top-level `stop_reason` is `null` on a full run, or explains why the
run stopped early (currently only a session expiry). Example:

```json
{
  "stop_reason": "session expired at https://www.facebook.com/deadpage: rerun python save_auth.py",
  "outcomes": [
    {
      "page_id": "goodpage",
      "page_url": "https://www.facebook.com/goodpage",
      "status": "success",
      "detail": "produced 1 post item (999888)",
      "post_ids": ["999888"],
      "post_urls": ["https://www.facebook.com/story.php?story_fbid=999888&id=10001"]
    },
    {
      "page_id": "blankpage",
      "page_url": "https://www.facebook.com/blankpage",
      "status": "no_post_id",
      "detail": "no story id found in timeline response",
      "post_ids": [],
      "post_urls": []
    },
    {
      "page_id": "deadpage",
      "page_url": "https://www.facebook.com/deadpage",
      "status": "session_expired",
      "detail": "login form shown on timeline response",
      "post_ids": [],
      "post_urls": []
    }
  ]
}
```

Pages are now processed one after another (each timeline request is
chained from the previous page's ending) instead of being fanned out up
front, which is what lets a session expiry stop the later pages
deterministically. Rate limiting (`DOWNLOAD_DELAY`) is unchanged.

## Data schema

Each item in the output JSON:

- page_id, page_name, page_url: the scraped page.
- post_id: story fbid from the UFI JSON.
- post_url: `story.php?story_fbid=...&id=...` permalink.
- post_text: full message text.
- image_urls: image urls attached to the post.
- reaction_count, comment_count, share_count.
- comments: list of comment_id, comment_text, comment_reaction_count,
  author_name, author_url.

## Configuration

- `USER_AGENT` in `settings.py`: desktop Chrome 126. It is forwarded
  into browser navigations, so changing surfaces means changing it too.
- `DOWNLOAD_DELAY = 3`: seconds between requests. Raise it if the
  account gets challenged.
- `PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30 * 1000`: slow renders
  need the headroom.
- `TWISTED_REACTOR`: asyncio selector reactor, required by
  scrapy-playwright on modern Scrapy.

## Tests

The outcome logic has offline tests that never touch the network or the
Playwright browser. Responses are built from local HTML fragments in
`tests/fixtures/` covering a timeline that yields a story id (success),
a timeline without a story id (`no_post_id`), and the login form
(session expiry), including asserting that after an expiry no request is
yielded for the later pages. Run them in isolated mode:

```
python -I -m pytest tests/ -v
```

The root-level `conftest.py` puts the repository root on `sys.path`, so
the package is importable even under `-I` (which ignores `PYTHONPATH`).

## Troubleshooting

- Bot challenge or checkpoint after login. Slow down (`DOWNLOAD_DELAY`),
  solve it in the `make auth` window, and reuse the saved session.
- Interstitial or stub pages. The UA does not match the surface. Check
  `USER_AGENT` in `settings.py` against the Playwright context UA, the
  handler forwards Scrapy headers into navigations.
- Connection refused on `ws://localhost:3000/`. The server is down
  (`make pw-server-up`) or the `playwright` client and the Docker image
  versions drifted. They must match.
- Session expired mid-run. The spider records a `session_expired`
  outcome for the current page and the pages left unprocessed, sets
  `stop_reason` in `fb_outcomes.json`, and sends no further requests.
  Refresh `auth.json` with `make auth` and scrape again; the report
  tells you which pages need rerunning.
- Empty output. The timeline render changed. Save a fresh snapshot and
  check the article selectors against it. A page whose timeline has no
  story id is reported as `no_post_id` in `fb_outcomes.json` instead of
  being skipped silently.
- Wrong text language. Post and comment language follows the test
  account's language setting, not the code. English account gets machine
  translations, Thai account gets the original text.

## TODO

- Mobile version for optimization. Fewer requests and a smaller
  response body to scrape than desktop. Not currently planned in
  detail, probably later.
- Infinite scroll to get more posts and comments. One timeline load
  yields the newest post only. Older posts and full comment pages need
  scroll-driven loading.
