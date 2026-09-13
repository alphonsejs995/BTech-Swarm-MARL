# simulation/drone.py
# Drone dataclass with role, status, battery management, and leader scoring.

from __future__ import annotations

import sys
import os

# Allow running this file directly without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from enum import Enum
from dataclasses import dataclass
from typing import Optional

from config import (
    BATTERY_MAX,
    BATTERY_CHARGE_RATE,
    LEADER_WEIGHT_BATTERY,
    LEADER_WEIGHT_SENSOR,
    LEADER_WEIGHT_UPTIME,
    LEADER_WEIGHT_ACTIVE,
)


class DroneRole(Enum):
    """What mission task this drone is currently assigned to."""
    SCOUT = "scout"       # Explore unseen territory, reveal fog of war
    RESCUE = "rescue"     # Deliver supplies to confirmed survivors
    RELAY = "relay"       # Extend communication range between base and field
    IDLE = "idle"         # No assignment yet (initial state)


class DroneStatus(Enum):
    """Operational lifecycle state of a drone."""
    ACTIVE = "active"         # Flying and executing tasks
    RETURNING = "returning"   # Heading back to base station to recharge
    CHARGING = "charging"     # At base station, restoring battery
    OFFLINE = "offline"       # Battery depleted or hardware failure


@dataclass
class Drone:
    """Represents a single autonomous drone in the simulation.

    Movement model
    --------------
    Drones do not teleport. When ``move_target`` is set, the drone spends
    ``terrain.move_cost(tx, ty)`` ticks incrementing ``move_progress``.
    When ``move_progress >= move_cost``, the drone arrives and both fields
    are reset. The ``_process_movement()`` method in ``World`` drives this.

    Battery model
    -------------
    Battery is drained per action (move, scan) rather than per tick.
    Charging happens at the base station at ``BATTERY_CHARGE_RATE`` per tick.
    """

    drone_id: str
    x: int
    y: int

    # Energy
    battery: float = 100.0

    # Mission state
    role: DroneRole = DroneRole.IDLE
    status: DroneStatus = DroneStatus.ACTIVE

    # Payload (e.g. "medical_kit", "water", "food")
    cargo: Optional[str] = None

    # Leadership
    is_leader: bool = False

    # Health flags
    sensors_online: bool = True

    # Uptime accumulator (incremented each tick while ACTIVE)
    uptime_ticks: int = 0

    # Movement state machine
    move_target: Optional[tuple[int, int]] = None
    move_progress: int = 0   # ticks spent working toward move_target

    # Mesh network — updated every tick by World._update_mesh_network()
    neighbors: list = None          # drone_ids within COMM_RANGE (set by world tick)
    isolated_ticks: int = 0         # consecutive ticks with zero neighbors
    last_known_centroid: tuple = None  # swarm centroid for self-healing nav

    def __post_init__(self):
        if self.neighbors is None:
            self.neighbors = []
        if self.last_known_centroid is None:
            self.last_known_centroid = (self.x, self.y)

    # ----------------------------------------------------------------
    # Leader election
    # ----------------------------------------------------------------

    def leader_score(self) -> float:
        """Composite score used during leader election.

        Scoring breakdown (max 100):
          battery       : up to 40 pts  (battery * 0.4)
          sensors_online: 20 pts bonus
          uptime        : up to 10 pts  (normalised over 50 ticks)
          active_status : 30 pts bonus
        """
        return (
            self.battery * LEADER_WEIGHT_BATTERY
            + (LEADER_WEIGHT_SENSOR if self.sensors_online else 0.0)
            + min(self.uptime_ticks / 50.0, 1.0) * LEADER_WEIGHT_UPTIME
            + (LEADER_WEIGHT_ACTIVE if self.status == DroneStatus.ACTIVE else 0.0)
        )

    # ----------------------------------------------------------------
    # Battery management
    # ----------------------------------------------------------------

    def drain_battery(self, amount: float) -> None:
        """Reduce battery by ``amount``. Go OFFLINE if it reaches zero."""
        self.battery = max(0.0, self.battery - amount)
        if self.battery <= 0.0:
            self.status = DroneStatus.OFFLINE
            self.sensors_online = False

    def charge(self, amount: float = BATTERY_CHARGE_RATE) -> None:
        """Restore battery by ``amount`` (capped at BATTERY_MAX)."""
        self.battery = min(BATTERY_MAX, self.battery + amount)
        # Come back online once there is meaningful charge
        if self.battery > 10.0 and self.status in (DroneStatus.CHARGING, DroneStatus.OFFLINE):
            self.status = DroneStatus.ACTIVE
            self.sensors_online = True

    # ----------------------------------------------------------------
    # Serialisation
    # ----------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable snapshot of this drone's state."""
        return {
            "drone_id": self.drone_id,
            "position": [self.x, self.y],
            "battery": round(self.battery, 1),
            "role": self.role.value,
            "status": self.status.value,
            "is_leader": self.is_leader,
            "cargo": self.cargo,
            "sensors_online": self.sensors_online,
            "uptime_ticks": self.uptime_ticks,
            "leader_score": round(self.leader_score(), 2),
            "move_target": list(self.move_target) if self.move_target else None,
            "move_progress": self.move_progress,
            "neighbors": list(self.neighbors),
            "isolated_ticks": self.isolated_ticks,
        }

    def __repr__(self) -> str:
        return (
            f"Drone({self.drone_id!r} pos=({self.x},{self.y}) "
            f"bat={self.battery:.1f} role={self.role.value} "
            f"status={self.status.value})"
        )
