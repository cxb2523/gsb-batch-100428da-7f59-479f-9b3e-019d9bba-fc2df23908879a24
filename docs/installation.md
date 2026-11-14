# Installation

```sh
pip install gmaps-extractor
```

- Python 3.9 or newer.
- One dependency: `httpx`.
- No API key and no account. A proxy is optional (see [Configuration](configuration.md)).

From a checkout, for development:

```sh
git clone https://github.com/promisingcoder/GoogleMapsCollector.git
cd GoogleMapsCollector
pip install -e ".[dev]"
pytest -q
```
