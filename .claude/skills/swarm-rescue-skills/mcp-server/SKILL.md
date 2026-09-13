---
name: mcp-server
description: Build and modify the FastMCP server that bridges the AI agent to the drone simulation. Covers all 24 MCP tool definitions, JSON-RPC transport, tool schemas, and the server lifecycle. Use when the user asks about MCP tools, adding new tools, fixing tool return values, server startup, or anything about how the agent communicates with the simulation. Also use when debugging "tool not found" or "invalid params" errors.
---

# MCP Server Sub-Skill

This skill covers the FastMCP server — the bridge between the AI command agent (Layer 1) and the simulation engine (Layer 3). Every interaction the agent has with drones goes through this server. No direct simulation access is allowed.

## Server Setup (mcp_server.py)

```python
from fastmcp import FastMCP
from simulation.world import World

# Initialize world
world = World(
    terrain_path="terrain_data/sulawesi_earthquake.json",
    num_drones=5,
    num_survivors=8,
)

mcp = FastMCP("Drone Swarm Rescue")
```

## Tool Categories and Definitions

There are 4 categories with a total of ~24 tools. Every tool must return a JSON-serializable dict. Every tool that modifies state should also call `world.tick()` to advance the simulation.

### Category 1: Fleet Management

```python
@mcp.tool()
def discover_fleet() -> dict:
    """Discover all active drones on the network.
    Returns a list of drone IDs, positions, battery levels, and roles.
    The agent must call this first — drone IDs are NOT hard-coded."""
    return {
        "fleet": [
            d.to_dict() for d in world.drones.values()
            if d.status != DroneStatus.OFFLINE
        ],
        "total_drones": len(world.drones),
        "active_drones": sum(
            1 for d in world.drones.values() 
            if d.status == DroneStatus.ACTIVE
        ),
    }

@mcp.tool()
def get_drone_status(drone_id: str) -> dict:
    """Get detailed status of a specific drone.
    Args:
        drone_id: The drone identifier (e.g., 'DRONE-A')
    """
    drone = world.drones.get(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    return drone.to_dict()

@mcp.tool()
def elect_leader() -> dict:
    """Trigger a leader election across the fleet.
    Each drone's leader_score is computed and the highest wins.
    Returns the new leader and all scores."""
    from swarm.leader_election import run_election
    result = run_election(world.drones)
    return result

@mcp.tool()
def assign_role(drone_id: str, role: str) -> dict:
    """Assign a role to a drone. Only the leader or the agent can do this.
    Args:
        drone_id: Target drone
        role: One of 'scout', 'rescue', 'relay', 'idle'
    """
    drone = world.drones.get(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    try:
        drone.role = DroneRole(role)
        return {"success": True, "drone_id": drone_id, "new_role": role}
    except ValueError:
        return {"error": f"Invalid role: {role}. Use: scout, rescue, relay, idle"}
```

### Category 2: Navigation

```python
@mcp.tool()
def move_to(drone_id: str, x: int, y: int) -> dict:
    """Command a drone to move to grid coordinates (x, y).
    Movement is not instant — it takes `move_cost` ticks based on terrain.
    The drone will drain battery upon arrival.
    Args:
        drone_id: Which drone to move
        x: Target x coordinate (0 to GRID_WIDTH-1)
        y: Target y coordinate (0 to GRID_HEIGHT-1)
    """
    drone = world.drones.get(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if drone.status != DroneStatus.ACTIVE:
        return {"error": f"Drone {drone_id} is {drone.status.value}, cannot move"}
    if not (0 <= x < world.terrain.width and 0 <= y < world.terrain.height):
        return {"error": f"Coordinates ({x},{y}) out of bounds"}
    
    terrain = world.terrain.get(x, y)
    cost = world.terrain.move_cost(x, y)
    
    drone.move_target = (x, y)
    drone.move_progress = 0
    
    # Advance simulation ticks until drone arrives
    for _ in range(cost):
        world.tick()
    
    return {
        "success": True,
        "drone_id": drone_id,
        "new_position": [drone.x, drone.y],
        "battery_remaining": round(drone.battery, 1),
        "terrain_crossed": terrain.value,
        "ticks_elapsed": cost,
    }

@mcp.tool()
def recall_drone(drone_id: str) -> dict:
    """Recall a drone to the base charging station at (0,0).
    The drone will navigate back and begin charging automatically."""
    drone = world.drones.get(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    drone.status = DroneStatus.RETURNING
    # Simplified: teleport back (in a real sim, pathfind)
    drone.x, drone.y = 0, 0
    drone.status = DroneStatus.CHARGING
    drone.move_target = None
    return {
        "success": True,
        "drone_id": drone_id,
        "status": "charging",
        "battery": round(drone.battery, 1),
    }

@mcp.tool()
def get_terrain(x: int, y: int) -> dict:
    """Get terrain information for a specific cell.
    Args:
        x: X coordinate
        y: Y coordinate
    """
    t = world.terrain.get(x, y)
    if t is None:
        return {"error": "Out of bounds"}
    props = TERRAIN_PROPERTIES[t]
    return {
        "position": [x, y],
        "terrain_type": t.value,
        "move_cost": props["move_cost"],
        "thermal_factor": props["thermal_attenuation"],
        "base_temperature": props["base_temperature"],
        "description": props["description"],
    }

@mcp.tool()
def get_path_cost(from_x: int, from_y: int, to_x: int, to_y: int) -> dict:
    """Estimate the total movement cost between two points.
    Uses Manhattan distance weighted by terrain costs."""
    # Simple estimate — not true pathfinding
    total_cost = 0
    cx, cy = from_x, from_y
    while (cx, cy) != (to_x, to_y):
        if cx < to_x: cx += 1
        elif cx > to_x: cx -= 1
        elif cy < to_y: cy += 1
        elif cy > to_y: cy -= 1
        total_cost += world.terrain.move_cost(cx, cy)
    return {
        "from": [from_x, from_y],
        "to": [to_x, to_y],
        "estimated_ticks": total_cost,
        "estimated_battery_cost": total_cost * BATTERY_DRAIN_PER_MOVE,
    }
```

### Category 3: Sensors (see thermal-sensor sub-skill for full details)

```python
@mcp.tool()
def thermal_scan(drone_id: str) -> dict:
    """Perform a thermal scan around the drone's current position.
    Reveals fog-of-war in a radius and detects heat signatures.
    Costs BATTERY_DRAIN_PER_SCAN battery."""
    # Delegates to sensors/thermal.py — see thermal-sensor sub-skill
    from sensors.thermal import perform_scan
    return perform_scan(world, drone_id)

@mcp.tool()
def get_heat_map(sector_x: int, sector_y: int, radius: int = 3) -> dict:
    """Get aggregated heat data for a sector. Only shows revealed cells."""
    # Returns temp readings for already-scanned cells in the area
    ...

@mcp.tool()
def classify_signature(temperature: float, x: int, y: int) -> dict:
    """Classify a heat reading. Returns probability of human/fire/ambient."""
    from sensors.thermal import classify_heat
    return classify_heat(temperature, world.terrain.get(x, y))

@mcp.tool()
def confirm_survivor(drone_id: str, x: int, y: int) -> dict:
    """Perform a close-range confirmation scan on a suspected survivor.
    Drone must be adjacent to or on the target cell. Higher accuracy."""
    ...

@mcp.tool()
def get_fog_of_war() -> dict:
    """Get the current visibility state of the map.
    Returns percentage explored and list of unexplored sectors."""
    ...
```

### Category 4: Swarm Communication (see swarm-comms sub-skill for full details)

```python
@mcp.tool()
def broadcast_msg(sender_id: str, message_type: str, payload: dict) -> dict:
    """Broadcast a message from one drone to all drones in comm range."""
    from swarm.message_bus import broadcast
    return broadcast(world, sender_id, message_type, payload)

@mcp.tool()
def send_direct(from_id: str, to_id: str, message_type: str, payload: dict) -> dict:
    """Send a direct message between two drones."""
    ...

@mcp.tool()
def get_inbox(drone_id: str) -> dict:
    """Retrieve all pending messages for a drone."""
    ...

@mcp.tool()
def drop_supply(drone_id: str, x: int, y: int) -> dict:
    """Drop a supply package at coordinates. Drone must have cargo and be at location."""
    ...

@mcp.tool()
def get_mission_log() -> dict:
    """Get the full mission log with timestamps, actions, and outcomes."""
    return {"log": world.mission_log, "tick": world.tick_count}

@mcp.tool()
def get_map_state() -> dict:
    """Get the full current state: drones, coverage, rescues, tick count."""
    return world.get_state()
```

## Running the Server

```python
if __name__ == "__main__":
    mcp.run(transport="stdio")  # or "sse" for HTTP
```

For HTTP/SSE transport (useful for dashboard):
```python
    mcp.run(transport="sse", host="localhost", port=8000)
```

## Important Rules

- Every tool returns a dict. Never return raw strings or None.
- Always include an `"error"` key in failure responses so the agent can reason about what went wrong.
- Tools that change world state should call `world.tick()` to advance the simulation.
- The `discover_fleet()` tool is the entry point — the agent must call it first to learn drone IDs.
- Tool names use snake_case. Parameter names use snake_case. This matches MCP convention.
- Keep tool descriptions clear — Gemini reads these to understand what each tool does.
- The MCP server is the ONLY interface between the agent and the simulation. No backdoors.
