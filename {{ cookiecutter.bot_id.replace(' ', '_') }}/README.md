# {{ cookiecutter.project_name }}

{{ cookiecutter.project_short_description}}

## WARNING

You are using an outdated version of the template which is no longer recommended.

While this template works and is valid, it is strongly recommended to create a new project with the latest template version.

Please refer to our [documentation page](https://documentation.botcity.dev/tutorials/python-automations/desktop/).

## Web crawler configuration

For Web projects, `bot.py` implements a configurable site crawler. It is
configured through environment variables:

- `BOT_START_URL` (required): URL where the crawl starts.
- `BOT_MAX_PAGES` (required): maximum number of pages to visit (positive integer).
- `BOT_OUTPUT_CSV` (optional): path of the results CSV. Defaults to
  `crawl_results.csv` in the project directory.
- `BOT_PAGE_TIMEOUT` (optional): page load timeout in seconds (default: 30).

Each visited page is appended to the CSV immediately (`url`, `title`, `links`,
`status`). Pages already recorded are skipped on rerun, and pages that time
out or fail are logged with a `failed: <reason>` status without stopping the
crawl. Run the offline tests with `python -m pytest tests/`.

