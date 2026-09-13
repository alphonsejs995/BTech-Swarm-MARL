# simulation/survivor.py
# Survivor dataclass and terrain-biased spawn helper.

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulation.terrain import Terrain

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    SURVIVOR_BODY_TEMP,
    SURVIVOR_TEMP_STD,
    GROUP_TEMP_BONUS,
    URGENCY_MEDIUM_TO_HIGH,
    URGENCY_HIGH_TO_CRITICAL,
)
from simulation.terrain import TerrainType


@dataclass
class Survivor:
    """A single survivor (or small group) waiting to be found.

    Thermal signature
    -----------------
    ``body_temp`` has natural Gaussian variation.  When multiple people are
    grouped together their combined heat is approximated by adding
    ``GROUP_TEMP_BONUS`` per extra person, giving a stronger IR reading.

    Urgency escalation
    ------------------
    Urgency increases over time to push the agent toward confirmed targets:
        medium -> high after URGENCY_MEDIUM_TO_HIGH ticks
        high   -> critical after URGENCY_HIGH_TO_CRITICAL ticks
    """

    survivor_id: str
    x: int
    y: int

    body_temp: float = SURVIVOR_BODY_TEMP
    count: int = 1                          # number of people at this location
    urgency: str = "medium"                 # "low" | "medium" | "high" | "critical"
    rescued: bool = False
    ticks_alive: int = 0

    # Computed in __post_init__; not set by caller
    effective_temp: float = field(init=False)

    def __post_init__(self) -> None:
        # Natural body-temperature variation
        self.body_temp += random.gauss(0, SURVIVOR_TEMP_STD)
        # Group size amplifies the thermal signature
        self.effective_temp = self.body_temp + (self.count - 1) * GROUP_TEMP_BONUS

    # ----------------------------------------------------------------
    # Game-loop hook
    # ----------------------------------------------------------------

    def tick(self) -> None:
        """Advance time for this survivor; escalate urgency as needed."""
        self.ticks_alive += 1

        if self.ticks_alive > URGENCY_MEDIUM_TO_HIGH and self.urgency == "medium":
            self.urgency = "high"

        if self.ticks_alive > URGENCY_HIGH_TO_CRITICAL and self.urgency == "high":
            self.urgency = "critical"

    # ----------------------------------------------------------------
    # Serialisation
    # ----------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable snapshot (does NOT expose exact position to agent)."""
        return {
            "survivor_id": self.survivor_id,
            "position": [self.x, self.y],
            "count": self.count,
            "urgency": self.urgency,
            "rescued": self.rescued,
            "ticks_alive": self.ticks_alive,
        }

    def __repr__(self) -> str:
        return (
            f"Survivor({self.survivor_id!r} pos=({self.x},{self.y}) "
            f"count={self.count} urgency={self.urgency} "
            f"rescued={self.rescued})"
        )


# ---------------------------------------------------------------------------
# Spawn helper
# ---------------------------------------------------------------------------

def spawn_survivors(terrain: "Terrain", count: int) -> list[Survivor]:
    """Place ``count`` survivors on the terrain grid.

    Placement bias
    --------------
    70% of survivors land in URBAN_RUBBLE or COASTLINE cells — the most
    realistic locations for earthquake/tsunami casualties.  The remaining
    30% are placed anywhere traversable (simulating people caught in transit).

    A placement is retried until a valid cell is found, so this always
    returns exactly ``count`` Survivor objects.
    """
    high_priority_types = {TerrainType.URBAN_RUBBLE, TerrainType.COASTLINE}
    survivors: list[Survivor] = []

    for i in range(count):
        while True:
            x = random.randint(0, terrain.width - 1)
            y = random.randint(0, terrain.height - 1)
            cell_type = terrain.get(x, y)
            if cell_type is None:
                continue
            if not terrain.is_traversable(x, y):
                continue
            # Accept the cell with terrain bias
            if cell_type in high_priority_types or random.random() < 0.3:
                break

        survivors.append(
            Survivor(
                survivor_id=f"SUR-{i:03d}",
                x=x,
                y=y,
                count=random.randint(1, 4),
                urgency=random.choice(["low", "medium", "medium", "high"]),
            )
        )

    return survivors
