"""Cover a bounding box with a grid of search cells."""

import math
from dataclasses import dataclass
from typing import List, Optional

METERS_PER_LAT_DEGREE = 111_320

# Cell edge length in metres by area size (the smaller side of the box).
CELL_SIZE_SMALL = 1_000  # under 10 km: neighbourhoods, small towns
CELL_SIZE_MEDIUM = 2_000  # under 30 km: cities
CELL_SIZE_LARGE = 5_000  # under 100 km: metro areas
CELL_SIZE_XLARGE = 50_000  # under 500 km: states, provinces
CELL_SIZE_XXLARGE = 100_000  # countries


@dataclass
class AreaBoundary:
    """A latitude/longitude bounding box."""

    name: str
    north: float
    south: float
    east: float
    west: float

    @property
    def center(self) -> "tuple[float, float]":
        return (self.north + self.south) / 2, (self.east + self.west) / 2


@dataclass
class GridCell:
    """One search cell: a centre point and the radius searched around it."""

    cell_id: int
    center_lat: float
    center_lng: float
    radius_meters: int


def meters_to_lat_degrees(meters: float) -> float:
    return meters / METERS_PER_LAT_DEGREE


def meters_to_lng_degrees(meters: float, latitude: float) -> float:
    cos_lat = max(math.cos(math.radians(latitude)), 1e-6)
    return meters / (METERS_PER_LAT_DEGREE * cos_lat)


def calculate_cell_size(boundary: AreaBoundary) -> int:
    """Pick a cell size (metres) from the size of ``boundary``."""
    height_km = (boundary.north - boundary.south) * 111
    width_km = (boundary.east - boundary.west) * 111 * math.cos(math.radians((boundary.north + boundary.south) / 2))
    smaller = min(height_km, width_km)
    if smaller < 10:
        return CELL_SIZE_SMALL
    if smaller < 30:
        return CELL_SIZE_MEDIUM
    if smaller < 100:
        return CELL_SIZE_LARGE
    if smaller < 500:
        return CELL_SIZE_XLARGE
    return CELL_SIZE_XXLARGE


def generate_grid(boundary: AreaBoundary, cell_size_meters: Optional[int] = None) -> List[GridCell]:
    """Return cells covering ``boundary``, ordered from the centre outwards.

    Starting in the centre means a run with a small result limit gets places
    from the middle of the area rather than from one corner.
    """
    if cell_size_meters is None:
        cell_size_meters = calculate_cell_size(boundary)
    if cell_size_meters <= 0:
        raise ValueError("cell_size_meters must be positive")

    lat_step = meters_to_lat_degrees(cell_size_meters)
    points = []
    lat = boundary.south + lat_step / 2
    while lat <= boundary.north or not points:
        lng_step = meters_to_lng_degrees(cell_size_meters, lat)
        lng = boundary.west + lng_step / 2
        row_started = False
        while lng <= boundary.east or not row_started:
            points.append((round(min(lat, boundary.north), 6), round(min(lng, boundary.east), 6)))
            row_started = True
            lng += lng_step
        lat += lat_step

    center_lat, center_lng = boundary.center
    cos_lat = math.cos(math.radians(center_lat))
    points.sort(key=lambda p: (p[0] - center_lat) ** 2 + ((p[1] - center_lng) * cos_lat) ** 2)
    return [
        GridCell(cell_id=i, center_lat=lat, center_lng=lng, radius_meters=cell_size_meters // 2)
        for i, (lat, lng) in enumerate(points)
    ]


def is_in_boundary(lat: float, lng: float, boundary: AreaBoundary) -> bool:
    """True when the point lies inside ``boundary``."""
    return boundary.south <= lat <= boundary.north and boundary.west <= lng <= boundary.east
