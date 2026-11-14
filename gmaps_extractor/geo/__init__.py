"""Area handling: resolve an area name to a bounding box and cover it with a grid."""

from .grid import AreaBoundary, GridCell, calculate_cell_size, generate_grid, is_in_boundary
from .nominatim import get_area_boundary

__all__ = [
    "AreaBoundary",
    "GridCell",
    "calculate_cell_size",
    "generate_grid",
    "get_area_boundary",
    "is_in_boundary",
]
