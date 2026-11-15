"""
Collecte groupée de plusieurs régions.

Contrairement aux appels unitaires (``crous.ru.get``, ``crous.menu.get`` ...)
qui lèvent une exception et interrompent tout le traitement, :meth:`Crous.collect`
isole chaque région : une région en échec (requête ou structure de données
inattendue) est consignée et les régions suivantes sont traitées normalement.

Deux sources de menus sont disponibles :

* ``source="api"`` (défaut) : les menus viennent de l'API, requêtés par
  identifiant de région et identifiant de restaurant ;
* ``source="feed"`` : les menus viennent du flux régional agrégé. Les
  identifiants de restaurants du flux diffèrent de ceux de l'API : la
  correspondance se fait avec :attr:`RU.feedId`. Un restaurant absent du flux
  est simplement sauté et consigné, sans affecter les autres restaurants.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from .entity.collection.feedRuCollection import FeedRUs
from .entity.collection.ruCollection import RUs
from .exceptions import CrousAPIError, CrousDataError

logger = logging.getLogger("CrousPy.collect")


class CollectErrorType(Enum):
    """
    Catégorie d'échec d'une région ou d'un restaurant.

    :ivar REQUEST: La requête a échoué (erreur HTTP, timeout, erreur réseau,
        réponse non décodable ...).
    :ivar DATA: La réponse a été reçue mais sa structure ne correspond pas à
        ce qui est attendu.
    """

    REQUEST = "request"
    DATA = "data"


@dataclass
class RestaurantResult:
    """
    Le résultat de la collecte des menus d'un restaurant.

    :ivar regionId: L'ID de la région (ID de l'API).
    :vartype regionId: int

    :ivar id: L'ID du restaurant côté API (``RU.id``).
    :vartype id: int

    :ivar feedId: L'ID du restaurant dans les flux (``RU.feedId``), ou ``None``.
    :vartype feedId: int | None

    :ivar title: Le titre du restaurant.
    :vartype title: str

    :ivar menus: Les menus collectés (vide en cas d'échec).
    :vartype menus: list

    :ivar success: Si la collecte a réussi.
    :vartype success: bool

    :ivar errorType: La catégorie d'erreur, le cas échéant.
    :vartype errorType: CollectErrorType | None

    :ivar reason: La raison de l'échec, le cas échéant.
    :vartype reason: str | None
    """

    regionId: int
    id: int
    feedId: int | None
    title: str
    menus: list = field(default_factory=list)
    success: bool = True
    errorType: CollectErrorType | None = None
    reason: str | None = None

    @property
    def skipped(self) -> bool:
        """Vrai si le restaurant a été sauté (absent du flux régional)."""
        return (
            not self.success
            and self.errorType is CollectErrorType.DATA
            and self.reason is not None
            and self.reason.startswith("Restaurant absent du flux")
        )


@dataclass
class RegionResult:
    """
    Le résultat de la collecte d'une région.

    :ivar regionId: L'ID de la région demandé.
    :vartype regionId: int

    :ivar success: Si la région a été traitée jusqu'au bout (une région dont
        certains restaurants ont échoué reste ``success=True``).
    :vartype success: bool

    :ivar errorType: La catégorie d'erreur au niveau régional, le cas échéant.
    :vartype errorType: CollectErrorType | None

    :ivar reason: La raison de l'échec régional, le cas échéant.
    :vartype reason: str | None

    :ivar restaurants: Les résultats restaurant par restaurant.
    :vartype restaurants: list[RestaurantResult]
    """

    regionId: int
    success: bool = True
    errorType: CollectErrorType | None = None
    reason: str | None = None
    restaurants: list[RestaurantResult] = field(default_factory=list)

    @property
    def menus(self) -> list:
        """Tous les menus collectés dans la région, tous restaurants confondus."""
        collected: list = []
        for restaurant in self.restaurants:
            collected.extend(restaurant.menus)
        return collected


class CollectReport:
    """
    Le rapport d'une collecte groupée : une entrée par région demandée, dans
    l'ordre de la requête.

    Les régions en échec y côtoient les régions réussies : les données déjà
    collectées ne sont jamais perdues.
    """

    def __init__(self, results: list[RegionResult] | None = None) -> None:
        self._results: list[RegionResult] = results or []

    @property
    def results(self) -> list[RegionResult]:
        return self._results

    @property
    def success(self) -> list[RegionResult]:
        return [result for result in self._results if result.success]

    @property
    def failed(self) -> list[RegionResult]:
        return [result for result in self._results if not result.success]

    @property
    def skippedRestaurants(self) -> list[RestaurantResult]:
        """Les restaurants sautés car absents d'un flux régional."""
        skipped: list[RestaurantResult] = []
        for region in self._results:
            skipped.extend(
                restaurant
                for restaurant in region.restaurants
                if not restaurant.success
            )
        return skipped

    def get(self, regionID: int) -> RegionResult | None:
        """Récupère le résultat d'une région par son ID."""
        for result in self._results:
            if result.regionId == regionID:
                return result
        return None

    @property
    def menus(self) -> list:
        """Tous les menus des régions réussies."""
        collected: list = []
        for result in self._results:
            if result.success:
                collected.extend(result.menus)
        return collected

    def __iter__(self):
        return iter(self._results)

    def __getitem__(self, index) -> RegionResult:
        return self._results[index]

    def __len__(self) -> int:
        return len(self._results)

    def __repr__(self) -> str:
        return (
            f"<CollectReport regions={len(self._results)} "
            f"success={len(self.success)} failed={len(self.failed)}>"
        )


def _normalizeName(name: str | None) -> set[str]:
    """Découpe un nom de région/flux en jetons comparables (minuscules, alnum)."""
    if not name:
        return set()
    return {
        token
        for token in "".join(
            char.lower() if char.isalnum() else " " for char in str(name)
        ).split()
        if token
    }


def _classify(error: Exception) -> tuple[CollectErrorType, str]:
    """
    Classe une exception en échec de requête ou anomalie de données.

    Les erreurs de typage/attribut/clé/décodage de structures (souvent levées
    paresseusement par les entités quand le corps reçu n'a pas la forme
    attendue) sont considérées comme des anomalies de données ; les autres
    erreurs sont des échecs de requête.

    :return: La catégorie d'erreur et une raison lisible.
    """
    if isinstance(error, CrousDataError):
        return CollectErrorType.DATA, str(error.error)
    if isinstance(error, CrousAPIError):
        return CollectErrorType.REQUEST, str(error.error)
    if isinstance(
        error,
        (
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            LookupError,
        ),
    ):
        return CollectErrorType.DATA, f"Structure inattendue : {error}"
    # Timeouts (asyncio.TimeoutError/TimeoutError), aiohttp.ClientError,
    # OSError ... : la réponse n'a pas pu être obtenue.
    return CollectErrorType.REQUEST, f"{type(error).__name__}: {error}"


def _validateRUs(rus: RUs, regionID: int) -> None:
    """Vérifie que la liste des restaurants d'une région est exploitable."""
    if not isinstance(rus, RUs):
        raise CrousDataError(
            f"Région {regionID} : la liste des restaurants n'est pas valide."
        )
    for ru in rus:
        if not isinstance(ru.id, int):
            raise CrousDataError(
                f"Région {regionID} : restaurant sans identifiant numérique "
                f"(id={getattr(ru, 'id', None)!r})."
            )
        # Déclenche les accès aux propriétés qui parseraient une structure
        # inattendue (titre, zone, type ...).
        _ = ru.title


class Collector:
    """
    Moteur de collecte groupée, utilisé par :meth:`Crous.collect`.

    :param client: Le client Crous exposant les services ``region``, ``ru``,
        ``menu`` et ``feed``.
    :type client: Crous
    """

    def __init__(self, client) -> None:
        self.client = client

    async def collect(
        self,
        regionIDs: list[int],
        source: Literal["api", "feed"] = "api",
        feedURLs: dict[int, str] | None = None,
    ) -> CollectReport:
        """
        Collecte les restaurants et leurs menus pour plusieurs régions.

        Chaque région est isolée : une erreur HTTP, un timeout ou une structure
        de réponse inattendue sont consignés dans le résultat de la région et
        n'interrompent pas les régions suivantes.

        :param regionIDs: Les ID des régions à collecter.
        :type regionIDs: list[int]

        :param source: La source des menus : ``"api"`` (ID région + ID
            restaurant) ou ``"feed"`` (flux régional, correspondance par
            :attr:`RU.feedId`).
        :type source: str

        :param feedURLs: Un mapping optionnel ``regionID -> URL de flux`` pour
            le mode ``"feed"``. Sans lui, l'URL est résolue en comparant les
            noms des régions et des flux.
        :type feedURLs: dict[int, str] | None

        :return: Le rapport de collecte, une :class:`RegionResult` par région.
        :rtype: CollectReport
        """
        if source not in ("api", "feed"):
            raise ValueError('source doit valoir "api" ou "feed".')

        report = CollectReport()

        for regionID in regionIDs:
            try:
                if source == "feed":
                    result = await self._collectRegionFromFeed(regionID, feedURLs or {})
                else:
                    result = await self._collectRegionFromAPI(regionID)
            except Exception as error:
                errorType, reason = _classify(error)
                logger.warning(
                    "Région %s en échec (%s) : %s", regionID, errorType.value, reason
                )
                result = RegionResult(
                    regionId=regionID,
                    success=False,
                    errorType=errorType,
                    reason=reason,
                )
            report.results.append(result)

        return report

    async def _collectRegionFromAPI(self, regionID: int) -> RegionResult:
        result = RegionResult(regionId=regionID)

        rus = await self.client.ru.get(regionID)
        _validateRUs(rus, regionID)

        for ru in rus:
            restaurant = RestaurantResult(
                regionId=regionID,
                id=ru.id,
                feedId=ru.feedId,
                title=ru.title,
            )
            try:
                menus = await self.client.menu.get(regionID, ru.id)
                restaurant.menus = list(menus)
            except Exception as error:
                errorType, reason = _classify(error)
                restaurant.success = False
                restaurant.errorType = errorType
                restaurant.reason = reason
                logger.warning(
                    "Menus du restaurant %s (région %s) en échec (%s) : %s",
                    ru.id,
                    regionID,
                    errorType.value,
                    reason,
                )
            result.restaurants.append(restaurant)

        return result

    async def _resolveFeedURL(self, regionID: int, feedURLs: dict[int, str]) -> str:
        if regionID in feedURLs:
            return feedURLs[regionID]

        regions = await self.client.region.get()
        region = regions.getFromID(regionID)
        if region is None:
            raise CrousDataError(f"Région {regionID} absente de la liste des régions.")

        feeds = await self.client.feed.get()
        regionTokens = _normalizeName(region.name)
        for feed in feeds:
            feedTokens = _normalizeName(feed.name)
            if regionTokens and (
                regionTokens == feedTokens
                or regionTokens.issubset(feedTokens)
                or feedTokens.issubset(regionTokens)
            ):
                return feed.url

        raise CrousDataError(
            f"Aucun flux régional ne correspond à la région {regionID} ({region.name})."
        )

    async def _collectRegionFromFeed(
        self, regionID: int, feedURLs: dict[int, str]
    ) -> RegionResult:
        result = RegionResult(regionId=regionID)

        rus = await self.client.ru.get(regionID)
        _validateRUs(rus, regionID)

        url = await self._resolveFeedURL(regionID, feedURLs)
        feedRUs: FeedRUs = await self.client.feed.getByURL(url)
        if not isinstance(feedRUs, FeedRUs):
            raise CrousDataError(
                f"Région {regionID} : le flux ne contient pas de restaurants."
            )

        byFeedId = {feedRU.id: feedRU for feedRU in feedRUs}

        for ru in rus:
            restaurant = RestaurantResult(
                regionId=regionID,
                id=ru.id,
                feedId=ru.feedId,
                title=ru.title,
            )
            feedRU = byFeedId.get(ru.feedId)
            if feedRU is None:
                restaurant.success = False
                restaurant.errorType = CollectErrorType.DATA
                restaurant.reason = (
                    f"Restaurant absent du flux régional (feedId={ru.feedId})."
                )
                logger.warning(
                    "Restaurant %s (région %s) introuvable dans le flux "
                    "(feedId=%s) : sauté.",
                    ru.id,
                    regionID,
                    ru.feedId,
                )
                result.restaurants.append(restaurant)
                continue

            try:
                restaurant.menus = list(feedRU.menus)
            except Exception as error:
                errorType, reason = _classify(error)
                restaurant.success = False
                restaurant.errorType = errorType
                restaurant.reason = reason
                logger.warning(
                    "Menus du flux pour le restaurant %s (région %s) en échec "
                    "(%s) : %s",
                    ru.id,
                    regionID,
                    errorType.value,
                    reason,
                )
            result.restaurants.append(restaurant)

        return result
