#!/usr/bin/env python
# -*- encoding: utf-8 -*-
import cloudscraper
import json
import requests
import urllib3

from decimal import Decimal
from bs4 import BeautifulSoup
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# urllib3.util.ssl_.DEFAULT_CIPHERS += 'HIGH:!DH:!aNULL'

# How a single source's collection went during this run. These values are
# published in the top-level "status" object of dolar.json so a consumer can
# tell "this exchange is closed / returned no quote" (invalid_value) from
# "we could not fetch it" (connection_error) or "the page no longer looks the
# way we expect" (structure_error).
STATUS_OK = "ok"
STATUS_CONNECTION_ERROR = "connection_error"
STATUS_STRUCTURE_ERROR = "structure_error"
STATUS_INVALID_VALUE = "invalid_value"

ALL_STATUSES = (
    STATUS_OK,
    STATUS_CONNECTION_ERROR,
    STATUS_STRUCTURE_ERROR,
    STATUS_INVALID_VALUE,
)

# Plausible range for the guaraní per US dollar. This mirrors the bounds the
# live suite uses: wide enough not to second-guess the market, narrow enough
# to catch a selector that read a date, a percentage or another currency.
MIN_RATE = Decimal("3000")
MAX_RATE = Decimal("20000")


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def format_decimal(number):
    return str(number).replace(".", "").replace(",", ".")


class SourceResult(tuple):
    """What a scraper returns: its price tuple plus this run's outcome.

    It stays a plain tuple subclass so existing callers that unpack it
    (``compra, venta = chaco()``), check its length or iterate over it keep
    working unchanged. ``status`` and ``detail`` are the new information.
    """

    def __new__(cls, values, status=STATUS_OK, detail=""):
        result = super(SourceResult, cls).__new__(cls, values)
        result.status = status
        result.detail = detail
        return result


def _short_detail(message):
    """Collapse whitespace and keep the explanation genuinely short."""
    return " ".join(str(message).split())[:160]


def _describe(exc):
    message = _short_detail(exc)
    name = exc.__class__.__name__
    return _short_detail("%s: %s" % (name, message) if message else name)


def _classify(exc):
    """Map an exception to one of the failure outcome types."""
    # An HTML error page where JSON was expected is a page-shape problem, not
    # a connection problem. ``requests`` uses its own JSONDecodeError subclass
    # that also inherits from RequestException, so check it first.
    if isinstance(exc, json.JSONDecodeError):
        return STATUS_STRUCTURE_ERROR
    if isinstance(exc, (requests.RequestException, OSError)):
        return STATUS_CONNECTION_ERROR
    # Exceptions raised by cloudscraper/urllib3/socket while talking to a site
    # (a Cloudflare challenge, a closed TLS tunnel) are request failures too.
    module = type(exc).__module__.split(".")[0]
    if module in ("cloudscraper", "urllib3", "http", "socket", "ssl"):
        return STATUS_CONNECTION_ERROR
    return STATUS_STRUCTURE_ERROR


def _zeros(arity):
    return tuple(Decimal(0) for _ in range(arity))


def _failure(arity, status, detail):
    """A failed source still publishes its usual keys, with prices at zero."""
    return SourceResult(_zeros(arity), status=status, detail=_short_detail(detail))


def _checked(response):
    """Turn an HTTP error status into an exception we can classify.

    Scrapers used to treat a 403/500 block page as a parsing failure because
    they never looked at the status code. Checking it here distinguishes
    "the request failed" from "the page structure changed".
    """
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and status >= 400:
        raise requests.HTTPError("HTTP %s" % status)
    return response


def _to_decimal(value):
    """Convert a parsed value to Decimal without binary float noise."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value).strip())


def _quote_detail(values):
    labels = ("compra", "venta", "referencial_diario")
    return " / ".join(
        "%s %s" % (label, value) for label, value in zip(labels, values)
    )


def _validated(values, spread_check=True):
    """Check parsed prices before they are published.

    ``values`` is ``(compra, venta)`` for every source except BCP, which also
    returns the daily reference rate. BCP's compra/venta are hourly averages,
    so the two can legitimately cross; ``spread_check=False`` skips only that
    test for it while still range-checking all three numbers.
    """
    values = tuple(_to_decimal(value) for value in values)
    labels = ("compra", "venta", "referencial_diario")

    for label, value in zip(labels, values):
        if not value.is_finite():
            return _failure(
                len(values),
                STATUS_INVALID_VALUE,
                "%s %s is not a finite number" % (label, value),
            )
        if value < MIN_RATE or value > MAX_RATE:
            return _failure(
                len(values),
                STATUS_INVALID_VALUE,
                "%s %s outside %s-%s" % (label, value, MIN_RATE, MAX_RATE),
            )

    if spread_check and len(values) >= 2 and values[1] < values[0]:
        return _failure(
            len(values),
            STATUS_INVALID_VALUE,
            "venta %s below compra %s" % (values[1], values[0]),
        )

    return SourceResult(values, status=STATUS_OK, detail=_quote_detail(values))


# BCP publishes "POR CORTE HORARIO" compra/venta -- the average of the trades
# settled in that hour, not a counter quote -- and those two averages can
# legitimately cross.  Every other source is somebody selling dollars over a
# counter, where a sell price below the buy price means the columns got
# swapped, so an inverted spread there really is a bad parse.
NO_SPREAD_GUARANTEE = {"bcp"}

# Every active source: published key, scraper function and how many values it
# returns.  The function name is resolved at collection time (not imported
# into a separate table) so monkeypatching coti.<scraper> keeps working.
SOURCES = [
    ("cambiosalberdi", "alberdi", 2),
    ("cambioschaco", "chaco", 2),
    ("maxicambios", "maxi", 2),
    ("bcp", "bcp", 3),
    ("set", "setgov", 2),
    ("mydcambios", "myd", 2),
    ("eurocambios", "eurocambio", 2),
    ("familiar", "familiar", 2),
    ("gnbfusion", "bbva", 2),
    ("mundialcambios", "mundial", 2),
    ("bonanza", "bonanza", 2),
    ("lamoneda", "lamoneda", 2),
    ("continental", "continental", 2),
]


def chaco():
    try:
        payload = json.loads(
            _checked(
                requests.get(
                    "https://www.cambioschaco.com.py/api/branch_office/1/exchange",
                    timeout=10,
                    headers={"user-agent": "Mozilla/5.0"},
                )
            ).text
        )
        compra = _to_decimal(payload["items"][0]["purchasePrice"])
        venta = _to_decimal(payload["items"][0]["salePrice"])
    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


def maxi():
    url = "https://www.maxicambios.com.py/"
    try:
        soup = BeautifulSoup(
            _checked(
                requests.get(
                    url,
                    timeout=10,
                    headers={"user-agent": "Mozilla/5.0"},
                    verify=False,
                )
            ).text,
            "html.parser",
        )

        tr_dolar = (
            soup.find(class_="fixed-plugin").find("table").find("tbody").find("tr")
        )

        compra = _to_decimal(tr_dolar.find_all("td")[1].text)
        venta = _to_decimal(tr_dolar.find_all("td")[2].text)

    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


def alberdi():
    try:
        soup = BeautifulSoup(
            _checked(
                requests.get(
                    "https://www.cambiosalberdi.com/langes/index.php#sectionCotizacion",
                    timeout=10,
                    headers={"user-agent": "Mozilla/5.0"},
                    verify=False,
                )
            ).text,
            "html.parser",
        )

        table = soup.find("table", class_="table")
        if table is None or table.tbody is None:
            raise ValueError("rate table not found")

        for row in table.tbody.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                raise ValueError("rate row is missing cells")

            currency_name = cells[0].text.strip()

            if "Dólar Americano" in currency_name:
                compra = _to_decimal(cells[1].text.strip().replace(".", ""))
                venta = _to_decimal(cells[2].text.strip().replace(".", ""))
                return _validated((compra, venta))

        raise ValueError("Dólar Americano row not found")

    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))


def bcp():
    try:
        scraper = cloudscraper.create_scraper()
        url_ref = "https://www.bcp.gov.py/webapps/web/cotizacion/monedas"
        url_fluct = (
            "https://www.bcp.gov.py/webapps/web/cotizacion/referencial-fluctuante"
        )

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:139.0) Gecko/20100101 Firefox/139.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Sec-GPC': '1',
            'Priority': 'u=0, i',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
            'TE': 'trailers',
        }
        # Referencial diario
        response = _checked(scraper.get(url_ref, headers=headers))
        soup = BeautifulSoup(response.text, "html.parser")
        ref = soup.select("#cotizacion-interbancaria > tbody > tr > td:nth-of-type(4)")[
            0
        ].get_text()
        ref = _to_decimal(ref.replace(".", "").replace(",", "."))

        # referencial fluctuante
        response = _checked(scraper.get(url_fluct, headers=headers))
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find(
            class_="table table-striped table-bordered table-condensed"
        )
        if table is None:
            raise ValueError("hourly rates table not found")
        compra_array = table.select("tr > td:nth-of-type(4)")
        venta_array = table.select("tr > td:nth-of-type(5)")
        posicion = len(compra_array) - 1
        compra = _to_decimal(
            compra_array[posicion].get_text().replace(".", "").replace(",", ".")
        )
        venta = _to_decimal(
            venta_array[posicion].get_text().replace(".", "").replace(",", ".")
        )

    except Exception as exc:
        return _failure(3, _classify(exc), _describe(exc))

    # BCP publishes hourly averages, not a counter quote: compra and venta can
    # legitimately cross, so only the range check applies to it.
    return _validated((compra, venta, ref), spread_check=False)


def setgov():
    try:
        soup = BeautifulSoup(
            _checked(
                requests.get(
                    "https://www.dnit.gov.py/en/web/portal-institucional/cotizaciones",
                    timeout=10,
                    headers={"user-agent": "Mozilla/5.0"},
                    verify=False,
                )
            ).text,
            "html.parser",
        )

        table = soup.select("table")[0]

        last_row = table.select("tbody > tr")[-1]
        compra = _to_decimal(
            last_row.select("td")[1].text.replace(".", "").replace(",", ".").strip()
        )
        venta = _to_decimal(
            last_row.select("td")[2].text.replace(".", "").replace(",", ".").strip()
        )

    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


# def interfisa():
#     try:
#         jsonResult = requests.get(
#             "https://seguro.interfisa.com.py/rest/cotizaciones", timeout=10
#         ).json()
#         cotizaciones = jsonResult["operacionResponse"]["cotizaciones"]["monedaCot"]
#         for coti in cotizaciones:
#             for k, v in coti.items():
#                 if v == "DOLARES AMERICANOS":  # estamos en el dict de Dolares
#                     compra = coti["compra"]
#                     venta = coti["venta"]
#     except requests.ConnectionError:
#         compra, venta = 0, 0
#     except:
#         compra, venta = 0, 0
#
#     return Decimal(compra), Decimal(venta)


# def amambay():
#     try:
#         soup = BeautifulSoup(
#             requests.get(
#                 "https://www.bancobasa.com.py/", timeout=10).text,
#             "html.parser",
#         )
#         compra = soup.select(".trendscontent > li:nth-of-type(1) > a > .compra")[0].text.replace(".", "")
#         venta = soup.select(".trendscontent > li:nth-of-type(1) > a > .venta")[0].text.replace(".", "")
#     except requests.ConnectionError:
#         compra, venta = 0, 0
#     except:
#         compra, venta = 0, 0
#
#     return Decimal(compra), Decimal(venta)


def eurocambio():
    try:
        url = "https://eurocambios.com.py/v2/sgi/utilsDto.php"
        data = {"param": "getCotizacionesbySucursal", "sucursal": "1"}
        result = _checked(requests.post(url, data, timeout=10)).json()
        compra = _to_decimal(result[0]["compra"])
        venta = _to_decimal(result[0]["venta"])
    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


def myd():
    try:
        soup = BeautifulSoup(
            _checked(
                requests.get("https://www.mydcambios.com.py/", timeout=10)
            ).text,
            "html.parser",
        )
        compra = _to_decimal(
            soup.select(
                "div.cambios-banner-text.scrollbox > ul:nth-of-type(2) > li:nth-of-type(2) "
            )[0].text
        )
        venta = _to_decimal(
            soup.select(
                "div.cambios-banner-text.scrollbox > ul:nth-of-type(2) > li:nth-of-type(3) "
            )[0].text
        )
    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


def bonanza():
    url = "https://bonanzacambios.com.py/api/board"
    try:
        result = _checked(
            requests.get(
                url,
                timeout=10,
                headers={"user-agent": "Mozilla/5.0"},
            )
        ).json()

        matriz = next(s for s in result["sucursales"] if s["cod"] == 0)
        # "prefix" is a stable slug; "moneda" is an accented display name.
        dolar = next(c for c in matriz["cotizaciones"] if c["prefix"] == "usa")

        compra = _to_decimal(dolar["compra"])
        venta = _to_decimal(dolar["venta"])

    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


def familiar():
    try:
        soup = BeautifulSoup(
            _checked(requests.get("https://www.familiar.com.py/", timeout=10)).text,
            'html.parser',
        )

        compra = _to_decimal(
            soup.findAll(class_='content-item flex-between horizontal-between')[2]
            .findAll(class_='text-m')[1]
            .get_text()
        )
        venta = _to_decimal(
            soup.findAll(class_='content-item flex-between horizontal-between')[3]
            .findAll(class_='text-m')[1]
            .get_text()
        )

    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


def lamoneda():
    try:
        # soup = BeautifulSoup(
        #     requests.get(
        #         "http://www.lamoneda.com.py/", timeout=10).text,
        #     "html.parser",
        # )
        # casacentral = soup.select("#cotizaciones table tr:nth-of-type(2)")[0]
        # compra = (
        #     casacentral.select("td:nth-of-type(3) > div")[0].text.replace(".", "")
        # )
        # venta = (
        #     casacentral.select("td:nth-of-type(4) > div")[0].text.replace(".", "")
        # )
        url = "http://www.lamoneda.com.py/"
        soup = BeautifulSoup(
            _checked(
                requests.get(
                    url,
                    timeout=10,
                    headers={"user-agent": "Mozilla/5.0"},
                    verify=False,
                )
            ).text,
            "html.parser",
        )

        tr_dolar = soup.find("table").find("tbody").find("tr")

        compra = _to_decimal(tr_dolar.find_all('td')[1].text.replace(".", "").strip())
        venta = _to_decimal(tr_dolar.find_all('td')[2].text.replace(".", "").strip())
    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


def bbva():
    try:
        soup = _checked(
            requests.get(
                "https://www.bancognb.com.py/public/currency_quotations", timeout=10
            )
        ).json()
        compra = _to_decimal(soup["exchangeRates"][0]["cashBuyPrice"])
        venta = _to_decimal(soup["exchangeRates"][0]["cashSellPrice"])
    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


def mundial():
    try:
        soup = BeautifulSoup(
            _checked(
                requests.get(
                    'http://www.mundialcambios.com.py/?branch=6',
                    timeout=20,
                    headers={'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/84.0.4147.105 Safari/537.36'},
                )
            ).text,
            "html.parser",
        )
        compra = _to_decimal(format_decimal(soup.select('td')[1].get_text()))
        venta = _to_decimal(format_decimal(soup.select('td')[2].get_text()))
    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


CONTINENTAL_API = "https://apibanking-gw.bancontinental.com.py/"
# Public credentials shipped in bancontinental.com.py's own frontend (main.*.js)
CONTINENTAL_SUBSCRIPTION_KEY = "3c35bb9e5fa948adb8d64c123d9d1a45"
CONTINENTAL_CLIENT_SECRET = "006f7995-3d33-4afe-8c56-e3b47377092c"


def continental():
    try:
        # The bearer token only lasts 5 minutes, so get a fresh one every run
        token = _checked(
            requests.post(
                CONTINENTAL_API + "autenticarServicio/v1/realms/interno",
                timeout=10,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Subscription-Key": CONTINENTAL_SUBSCRIPTION_KEY,
                    "Client-Id": "webinstitucional",
                    "Client-Secret": CONTINENTAL_CLIENT_SECRET,
                    "Grant-Type": "client_credentials",
                    "Scope": "profile",
                },
            )
        ).json()["access_token"]

        result = _checked(
            requests.get(
                CONTINENTAL_API + "divisas/v1/api/monedas/cotizaciones",
                timeout=10,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Subscription-Key": CONTINENTAL_SUBSCRIPTION_KEY,
                    "Authorization": "Bearer " + token,
                },
            )
        ).json()

        # "99" is "DOLAR CHQ./TRANSF."; "01" is "DOLAR EFECTIVO"
        dolar = next(c for c in result if c["id"] == "99")

        compra = _to_decimal(dolar["compra"])
        venta = _to_decimal(dolar["venta"])

    except Exception as exc:
        return _failure(2, _classify(exc), _describe(exc))

    return _validated((compra, venta))


def _collect(key, function_name, arity):
    """Run one scraper and normalise whatever it returns into a SourceResult.

    A scraper that unexpectedly raises cannot stop the rest of the run: the
    exception is turned into a failed result here. Plain tuples are still
    accepted (tests and callers monkeypatch the scrapers with them), and get
    validated as if they had come from a scraper.
    """
    scraper = globals().get(function_name)
    if not callable(scraper):
        return _failure(
            arity, STATUS_STRUCTURE_ERROR, "scraper %s is missing" % function_name
        )

    try:
        result = scraper()
    except Exception as exc:
        return _failure(arity, _classify(exc), _describe(exc))

    try:
        values = tuple(result)
    except Exception as exc:
        return _failure(arity, STATUS_STRUCTURE_ERROR, _describe(exc))

    if len(values) != arity:
        return _failure(
            arity,
            STATUS_STRUCTURE_ERROR,
            "expected %s values, got %s" % (arity, len(values)),
        )

    # A scraper that already decided this source failed keeps its status and
    # reason; its zero prices must not then be "validated" into something else.
    if isinstance(result, SourceResult) and result.status != STATUS_OK:
        return result

    try:
        values = tuple(_to_decimal(value) for value in values)
        return _validated(values, spread_check=key not in NO_SPREAD_GUARANTEE)
    except Exception as exc:
        return _failure(arity, _classify(exc), _describe(exc))


def create_json():
    dolarpy = {}
    status = {}

    for key, function_name, arity in SOURCES:
        result = _collect(key, function_name, arity)
        values = tuple(result)

        quote = {"compra": values[0], "venta": values[1]}
        if arity == 3:
            quote["referencial_diario"] = values[2]

        dolarpy[key] = quote
        status[key] = {"status": result.status, "detail": result.detail}

    respjson = {
        "dolarpy": dolarpy,
        # New top-level field: how each source's collection went this run.
        # It is kept separate from dolarpy so existing consumers are untouched.
        "status": status,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    return json.dumps(
        respjson,
        indent=4,
        sort_keys=True,
        separators=(",", ": "),
        default=decimal_default,
    )


def get_output():
    with open("dolar.json", "r") as f:
        response = f.read()
    return response


def write_output():
    response = create_json()
    with open("dolar.json", "w") as f:
        f.write(response)


if __name__ == "__main__":
    write_output()
