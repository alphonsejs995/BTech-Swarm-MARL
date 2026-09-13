# swarm/roles.py
# Role descriptions and dynamic role suggestion for the drone swarm.

from __future__ import annotations

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulation.drone import DroneRole, DroneStatus


# ---------------------------------------------------------------------------
# Role metadata
# ---------------------------------------------------------------------------

ROLE_DESCRIPTIONS: dict[DroneRole, dict] = {
    DroneRole.SCOUT: {
        "description": (
            "Explore uncharted grid sectors using thermal_scan(). "
            "Prioritise cells not yet visible in the fog of war."
        ),
        "priority_actions": [
            "move_to unscanned sectors",
            "thermal_scan",
            "report findings via SENSOR_REPORT",
        ],
        "battery_threshold": 25,  # recall to base below this level
    },
    DroneRole.RESCUE: {
        "description": (
            "Deliver supplies to confirmed survivor locations. "
            "Navigate to the target, drop cargo, confirm survivor."
        ),
        "priority_actions": [
            "move_to survivor location",
            "drop_supply",
            "confirm_survivor",
            "broadcast SUPPLY_DROPPED",
        ],
        "battery_threshold": 20,  # needs more reserve to make the return trip
    },
    DroneRole.RELAY: {
        "description": (
            "Position between base station and distant field drones to "
            "extend the swarm communication range."
        ),
        "priority_actions": [
            "move_to relay midpoint",
            "maintain position",
            "forward messages",
        ],
        "battery_threshold": 30,  # relay drones must stay airborne longer
    },
    DroneRole.IDLE: {
        "description": (
            "No current assignment. Awaiting orders from the leader. "
            "Return to base if battery is low."
        ),
        "priority_actions": ["await orders"],
        "battery_threshold": 15,
    },
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _has_comm_gap(drones: dict, world) -> bool:
    """Return True if any active non-leader drone is beyond COMM_RANGE of
    the current leader, indicating that a relay drone is needed.

    Args:
        drones: dict mapping drone_id -> Drone.
        world : World instance (unused here but part of the interface contract
                so callers can pass it consistently).
    """
    from config import COMM_RANGE

    leader = next((d for d in drones.values() if d.is_leader), None)
    if leader is None:
        return False

    for drone in drones.values():
        if drone.drone_id == leader.drone_id:
            continue
        if drone.status != DroneStatus.ACTIVE:
            continue
        dist = math.sqrt(
            (leader.x - drone.x) ** 2 + (leader.y - drone.y) ** 2
        )
        if dist > COMM_RANGE:
            return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_role_assignment(drones: dict, world) -> dict:
    """Suggest optimal role assignments for the current fleet state.

    Assignment logic (in priority order):
    1. Battery < 20 %          -> IDLE  (needs to recall and recharge)
    2. Drone carries cargo     -> RESCUE (supply is loaded, deliver it)
    3. Comm gap detected       -> one non-leader drone becomes RELAY
    4. Remaining high-battery  -> SCOUT (up to 2 scouts)
    5. Everything else         -> IDLE

    The leader drone is never assigned RELAY — it stays in place to
    coordinate.

    This function only *suggests* roles; the AI agent makes the final call
    via the assign_role MCP tool.

    Args:
        drones: dict mapping drone_id -> Drone.
        world : World instance (used for COMM_RANGE via _has_comm_gap).

    Returns:
        JSON-serialisable dict with a single key "suggested_roles" that maps
        drone_id -> role string (matches DroneRole.value).
    """
    suggestions: dict[str, str] = {}

    active_drones = [
        d for d in drones.values() if d.status == DroneStatus.ACTIVE
    ]

    # Sort by battery descending so high-battery drones get first pick.
    active_drones.sort(key=lambda d: d.battery, reverse=True)

    comm_gap = _has_comm_gap(drones, world)

    scouts_needed = 2
    rescue_needed = 1
    relay_needed = 1 if comm_gap else 0

    for drone in active_drones:
        # Low battery — send home.
        if drone.battery < 20.0:
            suggestions[drone.drone_id] = DroneRole.IDLE.value
            continue

        # Drone is already carrying supplies — it should deliver them.
        if drone.cargo and rescue_needed > 0:
            suggestions[drone.drone_id] = DroneRole.RESCUE.value
            rescue_needed -= 1
            continue

        # Fill relay slot (but not the leader — it must stay reachable).
        if relay_needed > 0 and not drone.is_leader:
            suggestions[drone.drone_id] = DroneRole.RELAY.value
            relay_needed -= 1
            continue

        # Fill scout slots.
        if scouts_needed > 0:
            suggestions[drone.drone_id] = DroneRole.SCOUT.value
            scouts_needed -= 1
            continue

        # Default fallback.
        suggestions[drone.drone_id] = DroneRole.IDLE.value

    return {"suggested_roles": suggestions}
