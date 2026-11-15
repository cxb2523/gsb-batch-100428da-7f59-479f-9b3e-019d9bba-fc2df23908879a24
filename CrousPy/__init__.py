__title__ = "CrousPy"
__author__ = "CROUStillant Développement"
__version__ = "2.1.0"
__description__ = "A Python wrapper for the CROUS API."

__headers__ = {
    "User-Agent": f"CrousPy v{__version__} - https://github.com/CROUStillant-Developpement"
}

__baseURL__ = "https://webservices-v2.crous-mobile.fr/ws/v1"
__feedIndexURL__ = "http://webservices-v2.crous-mobile.fr/feed/feeds.json"


from .client import Crous

# Collecte groupée
from .collect import (
    CollectErrorType,
    Collector,
    CollectReport,
    RegionResult,
    RestaurantResult,
)

# Categories
from .entity.category import Category
from .entity.collection.feedCollection import Feeds
from .entity.collection.feedRuCollection import FeedRUs
from .entity.collection.menuCollection import Menus
from .entity.collection.regionCollection import Regions
from .entity.collection.ruCollection import RUs

# Dishes
from .entity.dish import Dish

# Feeds
from .entity.feed import Feed
from .entity.feedRu import FeedRU

# Meal
from .entity.meal import Meal

# Menus
from .entity.menu import Menu

# Regions
from .entity.region import Region

# Restaurants Universitaires
from .entity.ru import RU

# Exceptions
from .exceptions import (
    BadRequestError,
    ConflictWithServer,
    CrousAPIError,
    CrousDataError,
    FluxIntrouvable,
    ForbiddenError,
    InternalServerError,
    MenuIntrouvable,
    RedirectError,
    RegionIntrouvable,
    RestaurantIntrouvable,
    TeapotError,
    TooEarlyError,
)

# Utilitaires
from .utils import formatTitle

__all__ = [
    "RU",
    "BadRequestError",
    "Category",
    "CollectErrorType",
    "CollectReport",
    "Collector",
    "ConflictWithServer",
    "Crous",
    "CrousAPIError",
    "CrousDataError",
    "Dish",
    "Feed",
    "FeedRU",
    "FeedRUs",
    "Feeds",
    "FluxIntrouvable",
    "ForbiddenError",
    "InternalServerError",
    "Meal",
    "Menu",
    "MenuIntrouvable",
    "Menus",
    "RUs",
    "RedirectError",
    "Region",
    "RegionIntrouvable",
    "RegionResult",
    "Regions",
    "RestaurantIntrouvable",
    "RestaurantResult",
    "TeapotError",
    "TooEarlyError",
    "formatTitle",
]
