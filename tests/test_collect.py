"""
Tests de la collecte groupée (``Crous.collect``) avec des sessions aiohttp
factices : aucun réseau réel n'est utilisé.

Couvre :
* toutes les régions réussissent (mode API et mode flux) ;
* une région en échec de requête (HTTP 500 puis timeout) n'interrompt pas les
  autres et ses données ne contaminent pas le rapport ;
* une région dont la structure de réponse est inattendue est classée en anomalie
  de données, distincte de l'échec de requête ;
* un restaurant absent du flux régional est sauté et consigné, sans affecter les
  autres restaurants de la région.
"""

import asyncio
from json import dumps
from typing import Self

import pytest
from aiohttp import ClientConnectionError, ContentTypeError

from CrousPy import CollectErrorType, Crous

# --------------------------------------------------------------------------- #
# Données factices
# --------------------------------------------------------------------------- #


def _ru(rid: int, xmlid: str, title: str = "Resto U Test") -> dict:
    return {
        "id": rid,
        "xmlid": xmlid,
        "title": title,
        "zone": "zone",
        "type": "restaurant",
        "closing": "0",
        "contact": {},
        "infos": {},
    }


def _menu(rid: int, date: str, mid: int | None = 1) -> dict:
    return {
        "id": mid,
        "restaurant_id": rid,
        "date": date,
        "meal": [{"name": "midi", "foodcategory": []}],
    }


def _feedRU(feedId: int, title: str, menus: list[dict]) -> dict:
    return {
        "id": feedId,
        "title": title,
        "lat": 0,
        "lon": 0,
        "area": "zone",
        "adresse": "",
        "opening": "",
        "closing": "0",
        "type": "restaurant",
        "menus": menus,
    }


# --------------------------------------------------------------------------- #
# Réponse et session factices
# --------------------------------------------------------------------------- #


class FakeResponse:
    """Réponse aiohttp factice : supporte ``async with``, ``.json()``, ``.text()``."""

    def __init__(
        self,
        payload=None,
        *,
        status: int = 200,
        text: str | None = None,
        invalidJSON: bool = False,
        jsonError: Exception | None = None,
    ) -> None:
        self.status = status
        self._payload = payload
        self._text = text
        self._invalidJSON = invalidJSON
        self._jsonError = jsonError

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def json(self):
        if self._jsonError is not None:
            raise self._jsonError
        if self._invalidJSON:
            # Comme requests.py : un corps non JSON lève ContentTypeError,
            # transformée en CrousAPIError par la couche de requêtes.
            raise ContentTypeError(None, {"Content-Type": "text/html"})
        return self._payload

    async def text(self) -> str:
        if self._text is not None:
            return self._text
        return dumps(self._payload)

    async def read(self) -> bytes:
        return dumps(self._payload).encode()


class FakeSession:
    """
    Session factice routant les URLs vers des réponses ou des exceptions.

    Une route est un sous-chemin d'URL (ex : ``"/regions/23/restaurants/"``).
    Une route peut pointer vers une exception levée à chaque appel, ou vers une
    :class:`FakeResponse`.
    """

    def __init__(self, routes: dict[str, FakeResponse | Exception]) -> None:
        self._routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        # Les motifs les plus longs (les plus spécifiques) sont testés en
        # premier : "/regions/23/restaurants/2/menus/" doit gagner sur
        # "/regions/23/restaurants/".
        for pattern in sorted(self._routes, key=len, reverse=True):
            handler = self._routes[pattern]
            if pattern in url:
                if isinstance(handler, Exception):
                    raise handler
                return handler
        raise AssertionError(f"URL non gérée par la fausse session : {url}")


def _makeCrous(routes: dict[str, FakeResponse | Exception]) -> Crous:
    return Crous(FakeSession(routes))


# --------------------------------------------------------------------------- #
# Helpers de construction de routes
# --------------------------------------------------------------------------- #


def _apiRoutes(
    *,
    restaurantsByRegion: dict[int, list[dict]],
    menusByRestaurant: dict[tuple[int, int], list[dict]] | None = None,
    failures: dict[str, Exception | FakeResponse] | None = None,
) -> dict[str, FakeResponse | Exception]:
    routes: dict[str, FakeResponse | Exception] = {}
    menusByRestaurant = menusByRestaurant or {}
    failures = failures or {}

    for regionID, restaurants in restaurantsByRegion.items():
        pattern = f"/regions/{regionID}/restaurants/"
        if pattern in failures:
            routes[pattern] = failures[pattern]
        else:
            routes[pattern] = FakeResponse(restaurants)

    for (regionID, rid), menus in menusByRestaurant.items():
        pattern = f"/regions/{regionID}/restaurants/{rid}/menus/"
        if pattern in failures:
            routes[pattern] = failures[pattern]
        else:
            routes[pattern] = FakeResponse(menus)

    for pattern, handler in failures.items():
        routes.setdefault(pattern, handler)

    return routes


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 1. Toutes les régions réussissent
# --------------------------------------------------------------------------- #


def test_collect_all_success_api() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={
            23: [_ru(1, "r101", "Resto A"), _ru(2, "r102", "Resto B")],
            24: [_ru(3, "r201", "Resto C")],
        },
        menusByRestaurant={
            (23, 1): [_menu(1, "2026-11-16")],
            (23, 2): [_menu(2, "2026-11-16"), _menu(2, "2026-11-17")],
            (24, 3): [_menu(3, "2026-11-16")],
        },
    )

    report = _run(_makeCrous(routes).collect([23, 24]))

    assert len(report) == 2
    assert len(report.failed) == 0
    assert {result.regionId for result in report.success} == {23, 24}

    region23 = report.get(23)
    assert len(region23.restaurants) == 2
    assert all(restaurant.success for restaurant in region23.restaurants)
    assert {restaurant.id for restaurant in region23.restaurants} == {1, 2}
    assert region23.restaurants[0].feedId == 101
    assert len(region23.menus) == 3

    region24 = report.get(24)
    assert len(region24.restaurants) == 1
    assert len(region24.menus) == 1

    # Toutes les données réussies sont conservées (3 en région 23 + 1 en 24).
    assert len(report.menus) == 4


def test_collect_all_success_feed() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={
            23: [_ru(1, "r101", "Resto A"), _ru(2, "r102", "Resto B")],
        },
    )
    routes["/feed/feeds.json"] = FakeResponse(
        {
            "results": [
                {
                    "id": 9,
                    "name": "Region Vingt-Trois",
                    "url": "https://feed/23.json",
                    "is_default": True,
                },
            ]
        }
    )
    routes["/feed/23.json"] = FakeResponse(
        {
            "restaurants": [
                _feedRU(101, "Resto A", [_menu(101, "2026-11-16", mid=None)]),
                _feedRU(102, "Resto B", [_menu(102, "2026-11-16", mid=None)]),
            ]
        }
    )
    routes["/regions/"] = FakeResponse(
        [{"id": 23, "code": "R23", "name": "Region Vingt-Trois"}]
    )

    report = _run(_makeCrous(routes).collect([23], source="feed"))

    assert len(report.failed) == 0
    region = report.get(23)
    assert len(region.restaurants) == 2
    byApiId = {restaurant.id: restaurant for restaurant in region.restaurants}
    assert byApiId[1].feedId == 101
    assert len(byApiId[1].menus) == 1
    # Les menus de flux n'ont pas d'ID.
    assert byApiId[1].menus[0].id is None
    assert len(byApiId[2].menus) == 1


def test_collect_feed_explicit_url_skips_index() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={23: [_ru(1, "r101", "Resto A")]},
    )
    feedURL = "https://flux.example/23.json"
    routes[feedURL] = FakeResponse(
        {"restaurants": [_feedRU(101, "Resto A", [_menu(101, "2026-11-16", mid=None)])]}
    )

    session = FakeSession(routes)
    report = _run(Crous(session).collect([23], source="feed", feedURLs={23: feedURL}))

    assert report.get(23).success
    # L'URL fournie explicitement évite de requêter l'index des flux et la
    # liste des régions.
    assert all("feeds.json" not in url for url in session.calls)
    assert all(not url.endswith("/regions/") for url in session.calls)
    assert any(feedURL in url for url in session.calls)


# --------------------------------------------------------------------------- #
# 2. Une région en échec de requête n'interrompt pas les autres
# --------------------------------------------------------------------------- #


def test_collect_region_http_error_then_continues() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={
            23: [_ru(1, "r101", "Resto A")],
            24: [_ru(3, "r201", "Resto C")],
            25: [_ru(4, "r301", "Resto D")],
        },
        menusByRestaurant={
            (23, 1): [_menu(1, "2026-11-16")],
            (24, 3): [_menu(3, "2026-11-16")],
            (25, 4): [_menu(4, "2026-11-16")],
        },
        failures={
            "/regions/24/restaurants/": FakeResponse({"message": "panne"}, status=500),
        },
    )

    report = _run(_makeCrous(routes).collect([23, 24, 25]))

    assert len(report) == 3
    assert {result.regionId for result in report.failed} == {24}

    failed = report.get(24)
    assert failed.success is False
    assert failed.errorType is CollectErrorType.REQUEST
    assert failed.reason
    assert failed.restaurants == []

    # Les régions avant et après ont bien été traitées et leurs données gardées.
    assert report.get(23).success
    assert len(report.get(23).menus) == 1
    assert report.get(25).success
    assert len(report.get(25).menus) == 1
    assert len(report.menus) == 2


def test_collect_region_timeout_then_continues() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={
            23: [_ru(1, "r101", "Resto A")],
            24: [_ru(2, "r201", "Resto B")],
        },
        menusByRestaurant={(23, 1): [_menu(1, "2026-11-16")]},
        failures={
            "/regions/24/restaurants/": TimeoutError(),
        },
    )

    report = _run(_makeCrous(routes).collect([23, 24]))

    failed = report.get(24)
    assert failed.success is False
    assert failed.errorType is CollectErrorType.REQUEST
    assert report.get(23).success


def test_collect_region_network_error_then_continues() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={
            23: [_ru(1, "r101", "Resto A")],
            24: [_ru(2, "r201", "Resto B")],
        },
        menusByRestaurant={(23, 1): [_menu(1, "2026-11-16")]},
        failures={
            "/regions/24/restaurants/": ClientConnectionError("connexion refusée"),
        },
    )

    report = _run(_makeCrous(routes).collect([23, 24]))

    failed = report.get(24)
    assert failed.success is False
    assert failed.errorType is CollectErrorType.REQUEST
    assert "ClientConnectionError" in failed.reason
    assert report.get(23).success


def test_collect_region_non_json_body_is_request_error() -> None:
    # Réponse 200 avec un corps non JSON : c'est un échec de requête
    # (réponse inexploitable), distinct d'une anomalie de structure de données.
    routes = _apiRoutes(
        restaurantsByRegion={
            23: [_ru(1, "r101", "Resto A")],
            24: [_ru(2, "r201", "Resto B")],
        },
        menusByRestaurant={(23, 1): [_menu(1, "2026-11-16")]},
        failures={
            "/regions/24/restaurants/": FakeResponse(
                text="<html>maintenance</html>", invalidJSON=True
            ),
        },
    )

    report = _run(_makeCrous(routes).collect([23, 24]))

    failed = report.get(24)
    assert failed.success is False
    assert failed.errorType is CollectErrorType.REQUEST
    assert report.get(23).success
    assert len(report.get(23).menus) == 1


def test_collect_single_restaurant_failure_isolated() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={23: [_ru(1, "r101"), _ru(2, "r102")]},
        menusByRestaurant={
            (23, 1): [_menu(1, "2026-11-16")],
            (23, 2): [],
        },
        failures={
            "/regions/23/restaurants/2/menus/": FakeResponse(
                {"message": "introuvable"}, status=404
            ),
        },
    )

    report = _run(_makeCrous(routes).collect([23]))

    region = report.get(23)
    assert region.success  # la région reste réussie malgré un restaurant en échec
    byId = {restaurant.id: restaurant for restaurant in region.restaurants}
    assert byId[1].success and len(byId[1].menus) == 1
    assert byId[2].success is False
    assert byId[2].errorType is CollectErrorType.REQUEST
    assert len(report.menus) == 1


# --------------------------------------------------------------------------- #
# 3. Structure de réponse inattendue -> anomalie de données
# --------------------------------------------------------------------------- #


def test_collect_region_bad_restaurant_structure() -> None:
    # Le corps est une liste mais chaque restaurant n'a pas d'ID numérique :
    # la construction du RU passe, mais la validation de structure échoue.
    routes = _apiRoutes(
        restaurantsByRegion={
            23: [{"id": "pas-un-id", "xmlid": "r101"}],
            24: [_ru(2, "r201")],
        },
        menusByRestaurant={(24, 2): [_menu(2, "2026-11-16")]},
    )

    report = _run(_makeCrous(routes).collect([23, 24]))

    failed = report.get(23)
    assert failed.success is False
    assert failed.errorType is CollectErrorType.DATA
    assert failed.reason
    # La région suivante est bien traitée.
    assert report.get(24).success
    assert len(report.get(24).menus) == 1


def test_collect_region_unexpected_payload_type() -> None:
    # Réponse 200 mais corps qui n'est pas une liste de restaurants :
    # RUs itère sur les caractères d'une chaîne -> RU.feedId explose à l'accès.
    routes = _apiRoutes(
        restaurantsByRegion={
            23: "structure-inattendue",
            24: [_ru(2, "r201")],
        },
        menusByRestaurant={(24, 2): [_menu(2, "2026-11-16")]},
    )

    report = _run(_makeCrous(routes).collect([23, 24]))

    failed = report.get(23)
    assert failed.success is False
    assert failed.errorType is CollectErrorType.DATA
    assert report.get(24).success


def test_collect_request_error_distinct_from_data_error() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={
            23: [_ru(1, "r101")],
            24: [{"id": "bad"}],
        },
        menusByRestaurant={(23, 1): [_menu(1, "2026-11-16")]},
        failures={
            "/regions/23/restaurants/1/menus/": FakeResponse(status=503, payload={})
        },
    )
    # 23 : échec de requête sur les menus (la région reste partielle)
    # 24 : anomalie de structure sur les restaurants
    report = _run(_makeCrous(routes).collect([23, 24]))

    region23 = report.get(23)
    assert region23.success
    assert region23.restaurants[0].errorType is CollectErrorType.REQUEST

    assert report.get(24).errorType is CollectErrorType.DATA


# --------------------------------------------------------------------------- #
# 4. Flux : restaurant absent -> sauté et consigné
# --------------------------------------------------------------------------- #


def test_collect_feed_missing_restaurant_is_skipped() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={
            23: [_ru(1, "r101", "Resto A"), _ru(2, "r102", "Resto B")],
        },
    )
    # Un seul des deux restaurants est présent dans le flux.
    routes["https://flux.example/23.json"] = FakeResponse(
        {"restaurants": [_feedRU(101, "Resto A", [_menu(101, "2026-11-16", mid=None)])]}
    )

    report = _run(
        _makeCrous(routes).collect(
            [23], source="feed", feedURLs={23: "https://flux.example/23.json"}
        )
    )

    region = report.get(23)
    assert region.success  # la région n'échoue pas globalement
    byId = {restaurant.id: restaurant for restaurant in region.restaurants}

    assert byId[1].success
    assert len(byId[1].menus) == 1

    missing = byId[2]
    assert missing.success is False
    assert missing.errorType is CollectErrorType.DATA
    assert missing.menus == []
    assert "absent du flux" in missing.reason
    assert missing.skipped is True
    assert report.skippedRestaurants == [missing]

    # Le restaurant suivant de la même région n'existe pas ici ; on vérifie
    # surtout que les menus du restaurant présent sont bien conservés.
    assert len(region.menus) == 1


def test_collect_feed_unknown_feed_is_data_error() -> None:
    routes = _apiRoutes(
        restaurantsByRegion={23: [_ru(1, "r101", "Resto A")]},
    )
    routes["/regions/"] = FakeResponse(
        [{"id": 23, "code": "R23", "name": "Region Inconnue"}]
    )
    routes["/feed/feeds.json"] = FakeResponse(
        {"results": [{"id": 1, "name": "Autre Region", "url": "https://feed/x.json"}]}
    )

    report = _run(_makeCrous(routes).collect([23], source="feed"))

    failed = report.get(23)
    assert failed.success is False
    assert failed.errorType is CollectErrorType.DATA
    assert "Aucun flux" in failed.reason


# --------------------------------------------------------------------------- #
# 5. Paramètres
# --------------------------------------------------------------------------- #


def test_collect_invalid_source_raises() -> None:
    crous = _makeCrous({})
    with pytest.raises(ValueError):
        _run(crous.collect([23], source="rss"))


def test_collect_empty_region_list() -> None:
    report = _run(_makeCrous({}).collect([]))
    assert len(report) == 0
    assert report.menus == []
    assert report.failed == []
