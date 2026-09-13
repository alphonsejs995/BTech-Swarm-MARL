# simulation/terrain.py
# TerrainType enum, TERRAIN_PROPERTIES dict, and Terrain grid loader.

import json
from enum import Enum
from typing import Optional


class TerrainType(Enum):
    """Seven terrain types representing Indonesian post-disaster zones."""
    CLEAR_ROAD = "clear_road"
    JUNGLE = "jungle"
    FLOOD_ZONE = "flood_zone"
    URBAN_RUBBLE = "urban_rubble"
    MOUNTAIN = "mountain"
    VOLCANIC_ASH = "volcanic_ash"
    COASTLINE = "coastline"


# Properties for every terrain type.
# move_cost         : integer ticks a drone must spend to cross one cell of this type
# thermal_attenuation: multiplier applied to IR signal before the sensor reads it
#                      (1.0 = full signal, 0.3 = 70% blocked)
# base_temperature  : ambient temperature (°C) reported when no survivor is present
# traversable       : whether drones can fly over this cell at all
# description       : human-readable note shown in the dashboard
TERRAIN_PROPERTIES = {
    TerrainType.CLEAR_ROAD: {
        "move_cost": 1,
        "thermal_attenuation": 1.0,   # no obstruction
        "base_temperature": 28.0,
        "traversable": True,
        "elevation": 0,
        "description": "Open road, fastest movement and best sensor clarity",
    },
    TerrainType.JUNGLE: {
        "move_cost": 2,
        "thermal_attenuation": 0.7,   # dense canopy blocks ~30% of IR
        "base_temperature": 26.0,
        "traversable": True,
        "elevation": 1,
        "description": "Dense tropical canopy, slows drones and blocks thermal",
    },
    TerrainType.FLOOD_ZONE: {
        "move_cost": 1,
        "thermal_attenuation": 0.5,   # water reflects IR poorly
        "base_temperature": 18.0,
        "traversable": True,
        "elevation": -1,              # below sea level, flooded
        "description": "Standing water from tsunami/flood, cold signature",
    },
    TerrainType.URBAN_RUBBLE: {
        "move_cost": 3,
        "thermal_attenuation": 0.6,
        "base_temperature": 24.0,
        "traversable": True,
        "elevation": 1,
        "description": "Collapsed buildings, survivors likely trapped here",
    },
    TerrainType.MOUNTAIN: {
        "move_cost": 4,
        "thermal_attenuation": 0.9,   # clear air, good for scanning
        "base_temperature": 20.0,
        "traversable": True,
        "elevation": 4,
        "description": "High elevation, good vantage but slow to reach",
    },
    TerrainType.VOLCANIC_ASH: {
        "move_cost": 3,
        "thermal_attenuation": 0.3,   # ash cloud severely blocks IR
        "base_temperature": 35.0,     # residual volcanic heat
        "traversable": True,
        "elevation": 2,
        "description": "Ash fallout zone, very poor visibility",
    },
    TerrainType.COASTLINE: {
        "move_cost": 1,
        "thermal_attenuation": 0.8,
        "base_temperature": 22.0,
        "traversable": True,
        "elevation": 0,
        "description": "Coastal area, potential tsunami debris",
    },
}


class Terrain:
    """Loads a 2-D terrain grid from a JSON file and provides per-cell queries.

    JSON format: a 2-D array of strings (rows x cols), where each string is a
    valid TerrainType value, e.g. "clear_road", "urban_rubble", etc.

    Example (3x3 excerpt):
        [
            ["clear_road", "jungle", "mountain"],
            ["flood_zone", "urban_rubble", "coastline"],
            ...
        ]
    """

    def __init__(self, json_path: str):
        with open(json_path) as fh:
            raw = json.load(fh)

        self.height = len(raw)
        self.width = len(raw[0]) if self.height > 0 else 0

        self.grid: list[list[TerrainType]] = [
            [TerrainType(cell) for cell in row]
            for row in raw
        ]

    # ------------------------------------------------------------------
    # Core accessors
    # ------------------------------------------------------------------

    def get(self, x: int, y: int) -> Optional[TerrainType]:
        """Return the TerrainType at grid position (x, y) or None if out-of-bounds."""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return None

    def move_cost(self, x: int, y: int) -> int:
        """Number of ticks a drone must accumulate before arriving at (x, y)."""
        t = self.get(x, y)
        if t is None:
            return float("inf")
        return TERRAIN_PROPERTIES[t]["move_cost"]

    def thermal_factor(self, x: int, y: int) -> float:
        """IR signal multiplier at (x, y). Lower = more blocked."""
        t = self.get(x, y)
        if t is None:
            return 0.0
        return TERRAIN_PROPERTIES[t]["thermal_attenuation"]

    def base_temp(self, x: int, y: int) -> float:
        """Ambient temperature (°C) at (x, y) with no survivors present."""
        t = self.get(x, y)
        if t is None:
            return 22.0
        return TERRAIN_PROPERTIES[t]["base_temperature"]

    def is_traversable(self, x: int, y: int) -> bool:
        """Whether a drone can fly over this cell."""
        t = self.get(x, y)
        if t is None:
            return False
        return TERRAIN_PROPERTIES[t]["traversable"]

    def elevation(self, x: int, y: int) -> int:
        """Return the elevation value at (x, y)."""
        t = self.get(x, y)
        if t is None:
            return 0
        return TERRAIN_PROPERTIES[t]["elevation"]

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def to_symbol(self, x: int, y: int) -> str:
        """Single-character symbol used by the terminal renderer."""
        _SYMBOLS = {
            TerrainType.CLEAR_ROAD: ".",
            TerrainType.JUNGLE: "*",
            TerrainType.FLOOD_ZONE: "~",
            TerrainType.URBAN_RUBBLE: "#",
            TerrainType.MOUNTAIN: "^",
            TerrainType.VOLCANIC_ASH: "%",
            TerrainType.COASTLINE: "=",
        }
        t = self.get(x, y)
        return _SYMBOLS.get(t, "?") if t else "?"

    def __repr__(self) -> str:
        return f"Terrain(width={self.width}, height={self.height})"
