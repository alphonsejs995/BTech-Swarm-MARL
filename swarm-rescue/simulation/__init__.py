# simulation package — 2D grid world for Swarm Rescue
# Exposes the primary public API used by the MCP server and tests.

from simulation.terrain import Terrain, TerrainType, TERRAIN_PROPERTIES
from simulation.drone import Drone, DroneRole, DroneStatus
from simulation.survivor import Survivor, spawn_survivors
from simulation.world import World

__all__ = [
    "Terrain",
    "TerrainType",
    "TERRAIN_PROPERTIES",
    "Drone",
    "DroneRole",
    "DroneStatus",
    "Survivor",
    "spawn_survivors",
    "World",
]
