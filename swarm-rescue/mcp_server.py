# mcp_server.py
# FastMCP server — the single bridge between the AI agent and the drone simulation.
# All agent-to-drone communication MUST go through these tools. No backdoors.
#
# Tool categories:
#   1. Fleet management  (4 tools): discover_fleet, get_drone_status, elect_leader, assign_role
#   2. Navigation        (4 tools): move_to, recall_drone, get_terrain, get_path_cost
#   3. Sensors           (5 tools): thermal_scan, get_heat_map, classify_signature,
#                                   confirm_survivor, get_fog_of_war
#   4. Swarm comms       (6 tools): broadcast_msg, send_direct, get_inbox,
#                                   load_supplies, drop_supply, get_mission_log
#   5. State             (1 tool):  get_map_state

from __future__ import annotations

import json
import os
import sys

# Ensure the package root is on sys.path when launched directly
sys.path.insert(0, os.path.dirname(__file__))

from fastmcp import FastMCP

from config import (
    BATTERY_DRAIN_PER_MOVE,
    BASE_X,
    BASE_Y,
    GRID_WIDTH,
    GRID_HEIGHT,
    NUM_DRONES,
    NUM_SURVIVORS,
    DETECTION_THRESHOLD,
)
from simulation.drone import DroneRole, DroneStatus
from simulation.terrain import TERRAIN_PROPERTIES
from simulation.world import World

# ---------------------------------------------------------------------------
# World initialization
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))

_terrain_file = os.environ.get("SWARM_TERRAIN", "terrain_data/sulawesi_earthquake.json")
_num_drones = int(os.environ.get("SWARM_DRONES", str(NUM_DRONES)))
_num_survivors = int(os.environ.get("SWARM_SURVIVORS", str(NUM_SURVIVORS)))

world = World(
    terrain_path=os.path.join(_HERE, _terrain_file),
    num_drones=_num_drones,
    num_survivors=_num_survivors,
)

# ---------------------------------------------------------------------------
# FastMCP server object
# ---------------------------------------------------------------------------

mcp = FastMCP("Drone Swarm Rescue")


# ===========================================================================
# CATEGORY 1 — Fleet Management
# ===========================================================================


@mcp.tool()
def discover_fleet() -> dict:
    """Discover all active drones on the network. Returns a list of drone IDs,
    current positions, battery levels, roles, and statuses. The agent MUST call
    this first — drone IDs are dynamically assigned and must not be hard-coded.
    Use the returned IDs for all subsequent tool calls."""
    world._log("discover_fleet called", {"tool": "discover_fleet"})

    fleet = [
        drone.to_dict()
        for drone in world.drones.values()
        if drone.status != DroneStatus.OFFLINE
    ]

    return {
        "fleet": fleet,
        "total_drones": len(world.drones),
        "active_drones": sum(
            1 for d in world.drones.values() if d.status == DroneStatus.ACTIVE
        ),
        "tick": world.tick_count,
    }


@mcp.tool()
def get_drone_status(drone_id: str) -> dict:
    """Get detailed status of a single drone including position, battery, role,
    cargo, sensor health, uptime, and current move target.

    Args:
        drone_id: The drone identifier (e.g. 'DRONE-A'). Use discover_fleet()
                  to get valid IDs.
    """
    world._log("get_drone_status called", {"tool": "get_drone_status", "drone_id": drone_id})

    drone = world.drones.get(drone_id)
    if not drone:
        return {
            "error": f"Drone {drone_id} not found. Use discover_fleet() to see available drones."
        }

    return drone.to_dict()


@mcp.tool()
def elect_leader() -> dict:
    """Trigger a leader election across the entire fleet. Each drone is scored on
    battery level (40%), active status (30%), sensor health (20%), and uptime (10%).
    The highest-scoring drone becomes the new leader. Call this at mission start and
    after any drone goes offline or its battery drops critically low."""
    from swarm.leader_election import run_election

    world._log("elect_leader called", {"tool": "elect_leader"})

    result = run_election(world.drones)
    world._log(
        f"Election complete: {result.get('elected_leader')} is new leader",
        {"tool": "elect_leader", "result": result},
    )
    return result


@mcp.tool()
def assign_role(drone_id: str, role: str) -> dict:
    """Assign a mission role to a drone. Only the agent or the current leader
    should call this. Valid roles are: 'scout' (explore and scan), 'rescue'
    (deliver supplies to survivors), 'relay' (extend comm range), 'idle' (standby).

    Args:
        drone_id: Target drone identifier.
        role:     One of 'scout', 'rescue', 'relay', 'idle'.
    """
    world._log(
        "assign_role called",
        {"tool": "assign_role", "drone_id": drone_id, "role": role},
    )

    drone = world.drones.get(drone_id)
    if not drone:
        return {
            "error": f"Drone {drone_id} not found. Use discover_fleet() to see available drones."
        }

    try:
        new_role = DroneRole(role)
    except ValueError:
        valid = [r.value for r in DroneRole]
        return {
            "error": f"Invalid role '{role}'. Valid roles are: {valid}"
        }

    drone.role = new_role
    world._log(
        f"{drone_id} assigned role {role}",
        {"tool": "assign_role", "drone_id": drone_id, "new_role": role},
    )

    return {
        "success": True,
        "drone_id": drone_id,
        "new_role": role,
        "tick": world.tick_count,
    }


# ===========================================================================
# CATEGORY 2 — Navigation
# ===========================================================================


@mcp.tool()
def move_to(drone_id: str, x: int, y: int) -> dict:
    """Command a drone to move to grid coordinates (x, y). Movement is NOT instant
    — the drone spends move_cost ticks crossing each cell, and battery drains by
    BATTERY_DRAIN_PER_MOVE per cell. The simulation is ticked for every step so
    survivors' urgency escalates in real time. Returns the final position, remaining
    battery, and total ticks consumed.

    Args:
        drone_id: Which drone to move.
        x:        Target x coordinate (0 to GRID_WIDTH-1).
        y:        Target y coordinate (0 to GRID_HEIGHT-1).
    """
    world._log(
        "move_to called",
        {"tool": "move_to", "drone_id": drone_id, "target": [x, y]},
    )

    drone = world.drones.get(drone_id)
    if not drone:
        return {
            "error": f"Drone {drone_id} not found. Use discover_fleet() to see available drones."
        }

    if drone.status == DroneStatus.OFFLINE:
        return {"error": f"Drone {drone_id} is OFFLINE and cannot move."}

    if drone.status == DroneStatus.CHARGING:
        return {
            "error": (
                f"Drone {drone_id} is CHARGING at base. "
                "Wait for it to reach ACTIVE status before moving."
            )
        }

    if not (0 <= x < world.terrain.width and 0 <= y < world.terrain.height):
        return {
            "error": (
                f"Coordinates ({x},{y}) are out of bounds. "
                f"Grid is {world.terrain.width}x{world.terrain.height}."
            )
        }

    if not world.terrain.is_traversable(x, y):
        return {"error": f"Cell ({x},{y}) is not traversable (blocked terrain)."}

    start_x, start_y = drone.x, drone.y
    start_battery = drone.battery
    ticks_elapsed = 0
    steps_taken = []

    # Build a Manhattan path: move along X first, then Y
    path: list[tuple[int, int]] = []
    cx, cy = start_x, start_y
    while cx != x:
        cx += 1 if cx < x else -1
        path.append((cx, cy))
    while cy != y:
        cy += 1 if cy < y else -1
        path.append((cx, cy))

    for step_x, step_y in path:
        # Abort if drone goes offline mid-journey (critical battery)
        if drone.status == DroneStatus.OFFLINE:
            return {
                "success": False,
                "drone_id": drone_id,
                "error": (
                    "Drone went OFFLINE mid-journey due to battery depletion. "
                    f"Last position: ({drone.x},{drone.y})"
                ),
                "position_at_failure": [drone.x, drone.y],
                "battery_remaining": round(drone.battery, 1),
                "ticks_elapsed": ticks_elapsed,
            }

        # Abort early if the drone was auto-recalled for low battery
        if drone.status == DroneStatus.RETURNING:
            return {
                "success": False,
                "drone_id": drone_id,
                "error": (
                    "Drone was automatically recalled due to critically low battery. "
                    f"Stopped at ({drone.x},{drone.y})."
                ),
                "position_at_stop": [drone.x, drone.y],
                "battery_remaining": round(drone.battery, 1),
                "ticks_elapsed": ticks_elapsed,
            }

        result = world.move_drone(drone_id, step_x, step_y)
        if not result.get("success"):
            return {
                "success": False,
                "drone_id": drone_id,
                "error": result.get("error", "Unknown movement error"),
                "position_at_failure": [drone.x, drone.y],
                "ticks_elapsed": ticks_elapsed,
            }

        step_cost = world.terrain.move_cost(step_x, step_y)
        for _ in range(step_cost):
            world.tick()
            ticks_elapsed += 1

        steps_taken.append([step_x, step_y])

    world._log(
        f"{drone_id} moved to ({drone.x},{drone.y})",
        {
            "tool": "move_to",
            "drone_id": drone_id,
            "from": [start_x, start_y],
            "to": [drone.x, drone.y],
            "ticks": ticks_elapsed,
            "battery_used": round(start_battery - drone.battery, 1),
        },
    )

    return {
        "success": True,
        "drone_id": drone_id,
        "from": [start_x, start_y],
        "new_position": [drone.x, drone.y],
        "battery_remaining": round(drone.battery, 1),
        "battery_used": round(start_battery - drone.battery, 1),
        "ticks_elapsed": ticks_elapsed,
        "steps": steps_taken,
        "tick": world.tick_count,
    }


@mcp.tool()
def recall_drone(drone_id: str) -> dict:
    """Recall a drone back to the base charging station at (0, 0). The drone's
    status is set to RETURNING and it will switch to CHARGING on arrival.
    Call this when a drone's battery is low (below 20%) or when it has
    completed its mission objective.

    Args:
        drone_id: The drone to recall.
    """
    world._log(
        "recall_drone called",
        {"tool": "recall_drone", "drone_id": drone_id},
    )

    drone = world.drones.get(drone_id)
    if not drone:
        return {
            "error": f"Drone {drone_id} not found. Use discover_fleet() to see available drones."
        }

    if drone.status == DroneStatus.OFFLINE:
        return {"error": f"Drone {drone_id} is OFFLINE and cannot be recalled."}

    if drone.status == DroneStatus.CHARGING:
        return {
            "success": True,
            "drone_id": drone_id,
            "note": "Drone is already at base charging.",
            "status": "charging",
            "battery": round(drone.battery, 1),
        }

    drone.status = DroneStatus.RETURNING
    drone.move_target = (BASE_X, BASE_Y)
    drone.move_progress = 0

    world._log(
        f"{drone_id} recalled to base",
        {
            "tool": "recall_drone",
            "drone_id": drone_id,
            "from": [drone.x, drone.y],
            "battery": round(drone.battery, 1),
        },
    )

    return {
        "success": True,
        "drone_id": drone_id,
        "status": "returning",
        "current_position": [drone.x, drone.y],
        "base_position": [BASE_X, BASE_Y],
        "battery": round(drone.battery, 1),
        "tick": world.tick_count,
    }


@mcp.tool()
def get_terrain(x: int, y: int) -> dict:
    """Get terrain information for a specific grid cell. Returns the terrain type,
    movement cost in ticks, thermal sensor attenuation factor (1.0 = clear,
    0.3 = heavily blocked), ambient base temperature, and a description.

    Args:
        x: X coordinate (0 to GRID_WIDTH-1).
        y: Y coordinate (0 to GRID_HEIGHT-1).
    """
    world._log(
        "get_terrain called",
        {"tool": "get_terrain", "x": x, "y": y},
    )

    if not (0 <= x < world.terrain.width and 0 <= y < world.terrain.height):
        return {
            "error": (
                f"Coordinates ({x},{y}) are out of bounds. "
                f"Grid is {world.terrain.width}x{world.terrain.height}."
            )
        }

    terrain_type = world.terrain.get(x, y)
    if terrain_type is None:
        return {"error": f"No terrain data found at ({x},{y})."}

    props = TERRAIN_PROPERTIES[terrain_type]

    return {
        "position": [x, y],
        "terrain_type": terrain_type.value,
        "move_cost": props["move_cost"],
        "thermal_factor": props["thermal_attenuation"],
        "base_temperature": props["base_temperature"],
        "traversable": props["traversable"],
        "elevation": props["elevation"],
        "description": props["description"],
    }


@mcp.tool()
def get_path_cost(from_x: int, from_y: int, to_x: int, to_y: int) -> dict:
    """Estimate the total movement cost (in ticks) and battery drain for a path
    between two grid positions. Uses a Manhattan path (X first, then Y) weighted
    by per-cell terrain costs. Returns total ticks and estimated battery drain.

    Args:
        from_x: Origin x coordinate.
        from_y: Origin y coordinate.
        to_x:   Destination x coordinate.
        to_y:   Destination y coordinate.
    """
    world._log(
        "get_path_cost called",
        {
            "tool": "get_path_cost",
            "from": [from_x, from_y],
            "to": [to_x, to_y],
        },
    )

    for label, px, py in [("Origin", from_x, from_y), ("Destination", to_x, to_y)]:
        if not (0 <= px < world.terrain.width and 0 <= py < world.terrain.height):
            return {
                "error": (
                    f"{label} ({px},{py}) is out of bounds. "
                    f"Grid is {world.terrain.width}x{world.terrain.height}."
                )
            }

    total_ticks = 0
    steps = 0
    cx, cy = from_x, from_y
    traversable = True

    while cx != to_x:
        cx += 1 if cx < to_x else -1
        if not world.terrain.is_traversable(cx, cy):
            traversable = False
            break
        total_ticks += world.terrain.move_cost(cx, cy)
        steps += 1

    if traversable:
        while cy != to_y:
            cy += 1 if cy < to_y else -1
            if not world.terrain.is_traversable(cx, cy):
                traversable = False
                break
            total_ticks += world.terrain.move_cost(cx, cy)
            steps += 1

    return {
        "from": [from_x, from_y],
        "to": [to_x, to_y],
        "estimated_ticks": total_ticks,
        "estimated_battery_cost": round(steps * BATTERY_DRAIN_PER_MOVE, 1),
        "steps": steps,
        "path_clear": traversable,
    }


# ===========================================================================
# CATEGORY 3 — Sensors
# ===========================================================================


@mcp.tool()
def thermal_scan(drone_id: str) -> dict:
    """Perform a thermal scan centred on the drone's current position. Reveals
    fog-of-war cells within SCAN_RADIUS (3 cells). Returns heat readings for every
    revealed cell, including any signatures above the detection threshold. Drains
    BATTERY_DRAIN_PER_SCAN battery. The scan includes Gaussian noise (+-1.5 deg C)
    and 8% false positive rate — use confirm_survivor() to verify ambiguous hits.

    Args:
        drone_id: The drone performing the scan.
    """
    from sensors.thermal import perform_scan

    world._log(
        "thermal_scan called",
        {"tool": "thermal_scan", "drone_id": drone_id},
    )

    drone = world.drones.get(drone_id)
    if not drone:
        return {
            "error": f"Drone {drone_id} not found. Use discover_fleet() to see available drones."
        }

    if drone.status != DroneStatus.ACTIVE:
        return {
            "error": (
                f"Drone {drone_id} is {drone.status.value} and cannot scan. "
                "Only ACTIVE drones can perform thermal scans."
            )
        }

    if not drone.sensors_online:
        return {"error": f"Drone {drone_id} sensors are offline and cannot scan."}

    result = perform_scan(world, drone_id)

    world._log(
        f"{drone_id} thermal scan: {len(result.get('hits', []))} hits",
        {
            "tool": "thermal_scan",
            "drone_id": drone_id,
            "hits": len(result.get("hits", [])),
            "cells_scanned": result.get("cells_scanned", 0),
        },
    )

    return result


@mcp.tool()
def get_heat_map(sector_x: int, sector_y: int, radius: int) -> dict:
    """Retrieve aggregated heat data for all revealed cells within a rectangular
    sector centred on (sector_x, sector_y) with the given radius. Only cells
    already revealed by previous thermal scans are included. Useful for identifying
    hot zones without performing a new scan.

    Args:
        sector_x: Centre x coordinate of the sector.
        sector_y: Centre y coordinate of the sector.
        radius:   Number of cells in each direction from the centre (e.g. 3 = 7x7 area).
    """
    world._log(
        "get_heat_map called",
        {
            "tool": "get_heat_map",
            "sector_x": sector_x,
            "sector_y": sector_y,
            "radius": radius,
        },
    )

    if radius < 0:
        return {"error": "Radius must be a non-negative integer."}

    if not (0 <= sector_x < world.terrain.width and 0 <= sector_y < world.terrain.height):
        return {
            "error": (
                f"Sector centre ({sector_x},{sector_y}) is out of bounds. "
                f"Grid is {world.terrain.width}x{world.terrain.height}."
            )
        }

    cells = []
    hot_cells = []

    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            cx = sector_x + dx
            cy = sector_y + dy
            if not (0 <= cx < world.terrain.width and 0 <= cy < world.terrain.height):
                continue

            # Only include revealed cells
            if not world.fog_of_war.is_revealed(cx, cy):
                continue

            terrain_type = world.terrain.get(cx, cy)
            base_temp = world.terrain.base_temp(cx, cy)
            thermal_factor = world.terrain.thermal_factor(cx, cy)

            # Check for survivors at this cell
            survivors_here = [
                s for s in world.survivors if s.x == cx and s.y == cy and not s.rescued
            ]
            has_survivor = len(survivors_here) > 0
            estimated_temp = base_temp
            if has_survivor:
                for s in survivors_here:
                    delta = (s.effective_temp - base_temp) * thermal_factor
                    estimated_temp += max(0.0, delta)

            cell_data = {
                "position": [cx, cy],
                "terrain": terrain_type.value if terrain_type else "unknown",
                "base_temperature": base_temp,
                "estimated_temperature": round(estimated_temp, 1),
                "thermal_factor": thermal_factor,
                "revealed": True,
            }
            cells.append(cell_data)

            if estimated_temp >= DETECTION_THRESHOLD:
                hot_cells.append([cx, cy])

    return {
        "sector_center": [sector_x, sector_y],
        "radius": radius,
        "cells_in_area": len(cells),
        "revealed_cells": cells,
        "hot_cells": hot_cells,
        "tick": world.tick_count,
    }


@mcp.tool()
def classify_signature(temperature: float, x: int, y: int) -> dict:
    """Classify a thermal reading at a given position. Returns the most likely
    type (ambient, possible_human, human, or fire), a confidence score (0.0-1.0),
    and an estimated survivor count. The classification accounts for the terrain's
    baseline temperature at that position.

    Args:
        temperature: Observed temperature reading in degrees Celsius.
        x:           X coordinate of the reading (used to determine terrain baseline).
        y:           Y coordinate of the reading.
    """
    from sensors.thermal import classify_heat

    world._log(
        "classify_signature called",
        {
            "tool": "classify_signature",
            "temperature": temperature,
            "x": x,
            "y": y,
        },
    )

    if not (0 <= x < world.terrain.width and 0 <= y < world.terrain.height):
        return {
            "error": (
                f"Coordinates ({x},{y}) are out of bounds. "
                f"Grid is {world.terrain.width}x{world.terrain.height}."
            )
        }

    terrain_type = world.terrain.get(x, y)
    result = classify_heat(temperature, terrain_type)

    return {
        "position": [x, y],
        "temperature": temperature,
        "terrain": terrain_type.value if terrain_type else "unknown",
        **result,
    }


@mcp.tool()
def confirm_survivor(drone_id: str, x: int, y: int) -> dict:
    """Perform a close-range high-resolution confirmation scan at a single cell.
    The drone must be adjacent (Manhattan distance <= 1) to the target. Costs
    only 1 battery unit. Returns confirmed=True with survivor details if found,
    or confirmed=False for false positives. Always call this before drop_supply
    to avoid wasting supplies on ghost readings.

    Args:
        drone_id: The drone performing the confirmation scan.
        x:        Target x coordinate to confirm.
        y:        Target y coordinate to confirm.
    """
    from sensors.thermal import confirm_scan

    world._log(
        "confirm_survivor called",
        {
            "tool": "confirm_survivor",
            "drone_id": drone_id,
            "target": [x, y],
        },
    )

    drone = world.drones.get(drone_id)
    if not drone:
        return {
            "error": f"Drone {drone_id} not found. Use discover_fleet() to see available drones."
        }

    if drone.status != DroneStatus.ACTIVE:
        return {
            "error": (
                f"Drone {drone_id} is {drone.status.value} and cannot scan. "
                "Only ACTIVE drones can confirm survivors."
            )
        }

    if not (0 <= x < world.terrain.width and 0 <= y < world.terrain.height):
        return {
            "error": (
                f"Target ({x},{y}) is out of bounds. "
                f"Grid is {world.terrain.width}x{world.terrain.height}."
            )
        }

    result = confirm_scan(world, drone_id, x, y)

    world._log(
        f"{drone_id} confirm_scan at ({x},{y}): confirmed={result.get('confirmed')}",
        {
            "tool": "confirm_survivor",
            "drone_id": drone_id,
            "target": [x, y],
            "confirmed": result.get("confirmed"),
        },
    )

    return result


@mcp.tool()
def get_fog_of_war() -> dict:
    """Get the current fog-of-war state. Returns the total coverage percentage and
    a list of unexplored sectors (5x5 blocks with less than 50% cells revealed).
    Each unexplored sector includes its origin, coverage percentage, and a centre
    coordinate suitable for dispatching a scout drone.

    Use this to identify gaps in coverage and prioritise where to send scouts next.
    """
    world._log("get_fog_of_war called", {"tool": "get_fog_of_war"})

    coverage_pct = world.fog_of_war.coverage_percent()
    unexplored = world.fog_of_war.unrevealed_sectors()

    return {
        "coverage_percent": coverage_pct,
        "unexplored_sectors": unexplored,
        "total_unexplored_sectors": len(unexplored),
        "grid_size": [world.terrain.width, world.terrain.height],
        "tick": world.tick_count,
    }


# ===========================================================================
# CATEGORY 4 — Swarm Communication
# ===========================================================================


@mcp.tool()
def broadcast_msg(sender_id: str, message_type: str, payload: str) -> dict:
    """Broadcast a message from one drone to all reachable active drones within
    comm range (5 cells). Relay drones extend reach by another 5 cells. The sender
    does not receive its own message. Valid message types: HEARTBEAT, TASK_ASSIGN,
    SENSOR_REPORT, SURVIVOR_FOUND, ELECTION, SUPPLY_DROPPED, LOW_BATTERY_ALERT.

    Args:
        sender_id:    Drone sending the broadcast.
        message_type: One of the MessageType constants (e.g. 'SURVIVOR_FOUND').
        payload:      JSON string containing the message payload dict.
                      Example: '{"position": [5, 7], "confidence": 0.92}'
    """
    from swarm.message_bus import broadcast as bus_broadcast

    world._log(
        "broadcast_msg called",
        {
            "tool": "broadcast_msg",
            "sender_id": sender_id,
            "message_type": message_type,
        },
    )

    drone = world.drones.get(sender_id)
    if not drone:
        return {
            "error": f"Drone {sender_id} not found. Use discover_fleet() to see available drones."
        }

    if drone.status != DroneStatus.ACTIVE:
        return {
            "error": (
                f"Drone {sender_id} is {drone.status.value} and cannot broadcast. "
                "Only ACTIVE drones can send messages."
            )
        }

    # Parse JSON payload string
    try:
        payload_dict = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as exc:
        return {
            "error": (
                f"Invalid payload JSON: {exc}. "
                "Provide a valid JSON string, e.g. '{\"key\": \"value\"}'."
            )
        }

    result = bus_broadcast(world, sender_id, message_type, payload_dict)

    world._log(
        f"{sender_id} broadcast {message_type} to {result.get('total_reached', 0)} drones",
        {
            "tool": "broadcast_msg",
            "sender_id": sender_id,
            "message_type": message_type,
            "delivered_to": result.get("delivered_to", []),
        },
    )

    return result


@mcp.tool()
def send_direct(from_id: str, to_id: str, message_type: str, payload: str) -> dict:
    """Send a point-to-point message from one drone to another. Delivery only
    succeeds if both drones exist and the recipient is within COMM_RANGE (5 cells)
    of the sender. Use this for targeted commands from the leader to a specific drone.

    Args:
        from_id:      Sending drone identifier.
        to_id:        Receiving drone identifier.
        message_type: One of the MessageType constants.
        payload:      JSON string containing the message payload dict.
    """
    from swarm.message_bus import send_direct as bus_send_direct

    world._log(
        "send_direct called",
        {
            "tool": "send_direct",
            "from_id": from_id,
            "to_id": to_id,
            "message_type": message_type,
        },
    )

    if not world.drones.get(from_id):
        return {
            "error": f"Sender drone {from_id} not found. Use discover_fleet() to see available drones."
        }

    if not world.drones.get(to_id):
        return {
            "error": f"Recipient drone {to_id} not found. Use discover_fleet() to see available drones."
        }

    # Parse JSON payload string
    try:
        payload_dict = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as exc:
        return {
            "error": (
                f"Invalid payload JSON: {exc}. "
                "Provide a valid JSON string, e.g. '{\"key\": \"value\"}'."
            )
        }

    result = bus_send_direct(world, from_id, to_id, message_type, payload_dict)

    world._log(
        f"{from_id} direct message {message_type} to {to_id}: delivered={result.get('delivered')}",
        {
            "tool": "send_direct",
            "from_id": from_id,
            "to_id": to_id,
            "message_type": message_type,
            "delivered": result.get("delivered"),
        },
    )

    return result


@mcp.tool()
def get_inbox(drone_id: str) -> dict:
    """Retrieve and consume all pending messages in a drone's inbox. Messages are
    consumed on read (each message is delivered exactly once). Returns the list of
    messages with sender, type, payload, and tick number. Call this after any
    broadcast to process incoming coordination messages.

    Args:
        drone_id: The drone whose inbox to drain.
    """
    from swarm.message_bus import get_inbox as bus_get_inbox

    world._log(
        "get_inbox called",
        {"tool": "get_inbox", "drone_id": drone_id},
    )

    drone = world.drones.get(drone_id)
    if not drone:
        return {
            "error": f"Drone {drone_id} not found. Use discover_fleet() to see available drones."
        }

    result = bus_get_inbox(drone_id)

    world._log(
        f"{drone_id} inbox: {result.get('message_count', 0)} messages",
        {
            "tool": "get_inbox",
            "drone_id": drone_id,
            "message_count": result.get("message_count", 0),
        },
    )

    return result


@mcp.tool()
def load_supplies(drone_id: str) -> dict:
    """Load a medical supply kit onto a rescue drone at the base station. The drone
    must satisfy all three conditions before cargo can be loaded:

      1. The drone must be ACTIVE status.
      2. The drone must have the RESCUE role — use assign_role() first if needed.
      3. The drone must be physically at the base station (BASE_X, BASE_Y). Use
         move_to() or recall_drone() to return the drone to base before calling this.

    The drone must also not already be carrying cargo; drop existing supplies with
    drop_supply() before reloading.

    Once loaded, send the drone to a confirmed survivor position and use drop_supply()
    to deliver the kit and mark the survivor as rescued.

    Args:
        drone_id: The rescue drone to load supplies onto.
    """
    world._log(
        "load_supplies called",
        {"tool": "load_supplies", "drone_id": drone_id},
    )

    drone = world.drones.get(drone_id)
    if not drone:
        return {
            "error": f"Drone {drone_id} not found. Use discover_fleet() to see available drones."
        }

    if drone.status != DroneStatus.ACTIVE:
        return {
            "error": (
                f"Drone {drone_id} is {drone.status.value} and cannot load supplies. "
                "Only ACTIVE drones can be loaded at the base station."
            )
        }

    if drone.role != DroneRole.RESCUE:
        return {
            "error": (
                f"Drone {drone_id} has role '{drone.role.value}', but only RESCUE drones "
                "can carry supplies. Call assign_role(drone_id, 'rescue') first."
            )
        }

    if drone.x != BASE_X or drone.y != BASE_Y:
        return {
            "error": (
                f"Drone {drone_id} is at ({drone.x},{drone.y}), but supplies can only be "
                f"loaded at the base station ({BASE_X},{BASE_Y}). "
                "Use move_to() or recall_drone() to return to base first."
            )
        }

    if drone.cargo is not None:
        return {
            "error": (
                f"Drone {drone_id} already carries '{drone.cargo}'. "
                "Drop the existing cargo with drop_supply() before reloading."
            )
        }

    cargo_type = "medical_kit"
    drone.cargo = cargo_type

    world._log(
        f"{drone_id} loaded with {cargo_type} at base ({BASE_X},{BASE_Y})",
        {
            "tool": "load_supplies",
            "drone_id": drone_id,
            "cargo": cargo_type,
            "position": [drone.x, drone.y],
        },
    )

    return {
        "success": True,
        "drone_id": drone_id,
        "cargo": cargo_type,
        "position": [drone.x, drone.y],
        "tick": world.tick_count,
    }


@mcp.tool()
def drop_supply(drone_id: str, x: int, y: int) -> dict:
    """Drop a supply package at the specified coordinates. The drone must be
    physically at position (x, y) and must have cargo loaded. If a confirmed
    survivor is present at the same cell, they are marked as rescued. Call
    confirm_survivor() first to verify a real survivor is present.

    Args:
        drone_id: The rescue drone dropping the supply.
        x:        X coordinate for the supply drop.
        y:        Y coordinate for the supply drop.
    """
    world._log(
        "drop_supply called",
        {"tool": "drop_supply", "drone_id": drone_id, "drop_location": [x, y]},
    )

    drone = world.drones.get(drone_id)
    if not drone:
        return {
            "error": f"Drone {drone_id} not found. Use discover_fleet() to see available drones."
        }

    if drone.status != DroneStatus.ACTIVE:
        return {
            "error": (
                f"Drone {drone_id} is {drone.status.value} and cannot drop supplies. "
                "Only ACTIVE drones can perform supply drops."
            )
        }

    if drone.cargo is None:
        return {
            "error": (
                f"Drone {drone_id} has no cargo to drop. "
                f"Return to base ({BASE_X},{BASE_Y}), assign the RESCUE role with "
                "assign_role(), then call load_supplies() to pick up a medical kit."
            )
        }

    if drone.x != x or drone.y != y:
        return {
            "error": (
                f"Drone {drone_id} is at ({drone.x},{drone.y}), "
                f"but drop target is ({x},{y}). "
                "Move the drone to the target location before dropping supplies."
            )
        }

    cargo_dropped = drone.cargo
    drone.cargo = None

    # Check if a survivor is present at the drop location and rescue them
    rescued_survivors = []
    for survivor in world.survivors:
        if survivor.x == x and survivor.y == y and not survivor.rescued:
            survivor.rescued = True
            world.rescued_count += 1
            rescued_survivors.append({
                "survivor_id": survivor.survivor_id,
                "count": survivor.count,
                "urgency": survivor.urgency,
            })
            world._log(
                f"Survivor {survivor.survivor_id} rescued by {drone_id} via supply drop",
                {
                    "tool": "drop_supply",
                    "drone_id": drone_id,
                    "survivor_id": survivor.survivor_id,
                    "position": [x, y],
                    "total_rescued": world.rescued_count,
                },
            )

    world.tick()

    world._log(
        f"{drone_id} dropped {cargo_dropped} at ({x},{y}), {len(rescued_survivors)} rescued",
        {
            "tool": "drop_supply",
            "drone_id": drone_id,
            "cargo": cargo_dropped,
            "position": [x, y],
            "rescued": len(rescued_survivors),
        },
    )

    return {
        "success": True,
        "drone_id": drone_id,
        "cargo_dropped": cargo_dropped,
        "drop_location": [x, y],
        "survivors_rescued": rescued_survivors,
        "total_rescued": world.rescued_count,
        "total_survivors": len(world.survivors),
        "tick": world.tick_count,
    }


@mcp.tool()
def get_mission_log() -> dict:
    """Retrieve the full mission log containing every action, event, and outcome
    recorded since mission start. Each entry includes the simulation tick number,
    a human-readable message, and relevant context data. Use this to review
    mission progress or for the final debrief report.
    """
    world._log("get_mission_log called", {"tool": "get_mission_log"})

    return {
        "log": world.mission_log,
        "total_entries": len(world.mission_log),
        "tick": world.tick_count,
    }


# ===========================================================================
# CATEGORY 5 — State
# ===========================================================================


@mcp.tool()
def get_map_state() -> dict:
    """Get a complete snapshot of the current world state. Returns all drone
    statuses, survivor states, fog-of-war coverage percentage, total rescued count,
    and current tick number. Use this for a full situational overview before making
    high-level strategic decisions.
    """
    world._log("get_map_state called", {"tool": "get_map_state"})

    state = world.get_state()

    # get_state() returns fog_of_war as a 2D list — summarise it instead of
    # dumping the full grid to keep the response readable for the agent
    fog_grid = state.pop("fog_of_war", None)
    state["fog_coverage_percent"] = world.fog_of_war.coverage_percent()
    state["unexplored_sectors_count"] = len(world.fog_of_war.unrevealed_sectors())

    return state


@mcp.tool()
def get_swarm_cohesion() -> dict:
    """Analyse how close the drone swarm is flying together. Returns the swarm
    centroid (average position), the maximum distance between any two active
    drones, which drones are isolated (outside COMM_RANGE of every other drone),
    and a suggested rally point for regrouping. Use this BEFORE every movement
    phase to ensure the fleet stays within communication range of each other.
    """
    import math as _math
    from config import COMM_RANGE

    world._log("get_swarm_cohesion called", {"tool": "get_swarm_cohesion"})

    active = [
        d for d in world.drones.values()
        if d.status in (DroneStatus.ACTIVE, DroneStatus.RETURNING)
    ]

    if len(active) < 2:
        return {
            "centroid": [active[0].x, active[0].y] if active else [0, 0],
            "max_spread": 0.0,
            "isolated_drones": [],
            "all_connected": True,
            "positions": {d.drone_id: [d.x, d.y] for d in active},
            "rally_point": [active[0].x, active[0].y] if active else [0, 0],
            "tick": world.tick_count,
        }

    # Centroid
    cx = sum(d.x for d in active) / len(active)
    cy = sum(d.y for d in active) / len(active)

    # Max spread (furthest pair)
    max_dist = 0.0
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            dist = _math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)
            if dist > max_dist:
                max_dist = dist

    # Isolated drones (not within COMM_RANGE of ANY other active drone)
    isolated = []
    for d in active:
        has_neighbor = False
        for other in active:
            if other.drone_id == d.drone_id:
                continue
            dist = _math.sqrt((d.x - other.x) ** 2 + (d.y - other.y) ** 2)
            if dist <= COMM_RANGE:
                has_neighbor = True
                break
        if not has_neighbor:
            isolated.append({
                "drone_id": d.drone_id,
                "position": [d.x, d.y],
                "nearest_distance": min(
                    _math.sqrt((d.x - o.x) ** 2 + (d.y - o.y) ** 2)
                    for o in active if o.drone_id != d.drone_id
                ),
            })

    # Rally point: centroid clamped to grid
    rally_x = max(0, min(world.terrain.width - 1, int(round(cx))))
    rally_y = max(0, min(world.terrain.height - 1, int(round(cy))))

    return {
        "centroid": [round(cx, 1), round(cy, 1)],
        "max_spread": round(max_dist, 1),
        "isolated_drones": isolated,
        "all_connected": len(isolated) == 0,
        "comm_range": COMM_RANGE,
        "active_count": len(active),
        "positions": {d.drone_id: [d.x, d.y] for d in active},
        "rally_point": [rally_x, rally_y],
        "tick": world.tick_count,
    }


@mcp.tool()
def check_network_health() -> dict:
    """Analyse the mesh network topology of the drone swarm. Returns:
    - The full adjacency graph (who can reach whom within COMM_RANGE)
    - Network partitions (groups of drones that are disconnected from each other)
    - Per-drone isolation status and neighbor count
    - Whether the network is fully connected (single partition)
    - Self-healing events that have occurred (drones auto-returning to swarm)

    This tool reveals the TRUE decentralized state of the swarm. In a real
    disaster with no cell towers, a partitioned network means lost drones.
    Call this to verify network integrity before and after movement phases.
    """
    import math as _math
    from config import COMM_RANGE

    world._log("check_network_health called", {"tool": "check_network_health"})

    active = [
        d for d in world.drones.values()
        if d.status in (DroneStatus.ACTIVE, DroneStatus.RETURNING)
    ]

    # Build adjacency graph
    adjacency: dict[str, list[str]] = {}
    for d in active:
        adjacency[d.drone_id] = list(d.neighbors)

    # Find connected components (network partitions) via BFS
    visited: set[str] = set()
    partitions: list[list[str]] = []
    for d in active:
        if d.drone_id in visited:
            continue
        # BFS from this drone
        component: list[str] = []
        queue = [d.drone_id]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            for neighbor_id in adjacency.get(current, []):
                if neighbor_id not in visited:
                    queue.append(neighbor_id)
        partitions.append(sorted(component))

    # Per-drone health
    drone_health = []
    for d in active:
        drone_health.append({
            "drone_id": d.drone_id,
            "position": [d.x, d.y],
            "battery": round(d.battery, 1),
            "neighbor_count": len(d.neighbors),
            "neighbors": list(d.neighbors),
            "isolated_ticks": d.isolated_ticks,
            "is_self_healing": d.isolated_ticks >= 3,
            "is_leader": d.is_leader,
        })

    # Collect recent self-healing events from mission log
    healing_events = [
        entry for entry in world.mission_log[-20:]
        if entry.get("event") in ("self_healing", "auto_failover")
    ]

    fully_connected = len(partitions) <= 1
    leader = next((d for d in active if d.is_leader), None)

    return {
        "fully_connected": fully_connected,
        "partition_count": len(partitions),
        "partitions": partitions,
        "adjacency": adjacency,
        "drone_health": drone_health,
        "leader": leader.drone_id if leader else None,
        "leader_reachable_by": adjacency.get(leader.drone_id, []) if leader else [],
        "recent_healing_events": healing_events,
        "active_count": len(active),
        "tick": world.tick_count,
    }


# ===========================================================================
# Server entry point
# ===========================================================================


def _run_mcp_in_thread() -> None:
    """Run the MCP stdio server inside a daemon thread so the main thread
    is free for pygame (macOS requires pygame on the main thread)."""
    import asyncio as _aio

    def _target():
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)
        mcp.run(transport="stdio")

    import threading
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    if os.environ.get("SWARM_RENDER") == "1":
        # MCP server runs in a background thread; pygame gets the main thread
        _run_mcp_in_thread()

        from simulation.renderer import IsometricRenderer
        renderer = IsometricRenderer(world)
        record = os.environ.get("SWARM_RECORD") == "1"
        renderer.run(tick_world=False, record=record)  # never exits until window closed
    else:
        mcp.run(transport="stdio")
