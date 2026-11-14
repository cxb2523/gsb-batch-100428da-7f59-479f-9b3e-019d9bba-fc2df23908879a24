# Configuration

There is no configuration file. Everything is an argument, a command-line option or one of these environment
variables.

## Proxy

Read in this order; the first one set wins:

1. `proxy=` argument (Python) or `--proxy` (command line)
2. `GMAPS_PROXY_URL`, e.g. `http://user:pass@host:port` or `socks5://host:port`
   (SOCKS needs `pip install "httpx[socks]"`)
3. `GMAPS_PROXY_HOST` (`host:port`), with `GMAPS_PROXY_USER` and `GMAPS_PROXY_PASS` when the proxy needs a login
   (set both or neither; they are URL-encoded for you)

```sh
export GMAPS_PROXY_URL="http://user:pass@host:port"
gmaps-collect "Leeds, UK" "dentist"
```

The URL is handed to httpx unchanged and used for both Google Maps and Nominatim. Without any of these, httpx's
usual environment handling applies (for example `HTTPS_PROXY`).

Keep proxy logins in environment variables or a secrets manager, never in a file inside the project.

## Request pacing

`workers` (default 4) requests run in parallel, and one client sends at most one request every `min_interval`
seconds (default 0.5). A failed request is retried once after 2 seconds.
