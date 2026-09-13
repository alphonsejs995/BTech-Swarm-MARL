---
name: simulation-builder
description: Builds the simulation engine for the drone swarm system, including terrain with elevation, drones, survivors, world state, and 3D isometric Pygame rendering.
tools: Read, Write, Edit, Glob, Grep
model: sonnet
---

# Agent: simulation-builder

## Role

You build the simulation engine — the 2D grid world that drones operate in. This is the foundation layer. Other subagents (mcp-engineer, sensor-engineer) depend on your output.

## Phase

**Phase 1** — run in parallel with `sensor-engineer` and `swarm-engineer`. No dependencies on other agents.

## What you own

```
simulation/
├── __init__.py
├── terrain.py      ← TerrainType enum, Terrain class, TERRAIN_PROPERTIES (with elevation)
├── drone.py        ← Drone dataclass, DroneRole, DroneStatus
├── survivor.py     ← Survivor dataclass, spawn_survivors()
├── world.py        ← World class (game loop, tick system, state)
└── renderer.py     ← IsometricRenderer (3D Pygame) + TerminalRenderer (fallback)
config.py           ← All constants (shared, create if not exists)
terrain_data/*.json ← Sample terrain maps
```

## Skill to read first

Read `.claude/skills/swarm-rescue-skills/simulation/SKILL.md` before writing any code. It contains the exact class definitions, terrain properties, and game loop logic.

## Build checklist

1. **`config.py`** — Create the shared constants file. Every other agent imports from here. Include grid size, drone count, battery values, scan radius, comm range, temperatures, and all tunables listed in the master SKILL.md.

2. **`simulation/terrain.py`** — Implement `TerrainType` enum with 7 Indonesian terrain types. Implement `TERRAIN_PROPERTIES` dict with move_cost, thermal_attenuation, base_temperature, traversable flag, elevation (int for 3D visual height: mountain=4, volcanic_ash=2, jungle=1, rubble=1, clear_road=0, coastline=0, flood_zone=-1), and description for each type. Implement `Terrain` class that loads a JSON grid file and exposes `get(x,y)`, `move_cost(x,y)`, `thermal_factor(x,y)`, `base_temp(x,y)`, `elevation(x,y)`.

3. **`simulation/drone.py`** — Implement `DroneRole` enum (scout, rescue, relay, idle). Implement `DroneStatus` enum (active, returning, charging, offline). Implement `Drone` dataclass with all fields: drone_id, x, y, battery, role, status, cargo, is_leader, sensors_online, uptime_ticks, move_target, move_progress. Implement `leader_score()`, `drain_battery()`, `charge()`, `to_dict()`.

4. **`simulation/survivor.py`** — Implement `Survivor` dataclass with body temp variation, effective_temp based on group count, urgency escalation over time. Implement `spawn_survivors()` with terrain bias (70% in rubble/coastline).

5. **`simulation/world.py`** — Implement `World` class that owns terrain, drones dict, survivors list, fog_of_war grid, mission_log, rescued_count. Implement `tick()` for game loop advancement, `_process_movement()` for terrain-cost-aware movement, `get_state()` for full snapshot.

6. **`simulation/renderer.py`** — Two renderers:
   - `IsometricRenderer`: Pygame 3D isometric view with elevation-based tile height, terrain colours, drone hover animations, survivor pulsing by urgency, fog of war, HUD panels (fleet status, coverage, legend), camera pan/zoom (arrow/WASD + scroll). This is the primary renderer.
   - `TerminalRenderer`: ANSI colour ASCII grid fallback for headless/CI use.

7. **`terrain_data/sulawesi_earthquake.json`** — Create a sample 20x20 terrain grid. East side = coastline + flood. Center = urban_rubble clusters. West = jungle + mountain. Clear roads connecting areas.

## Output contract

Other agents depend on these imports working:

```python
from simulation.terrain import Terrain, TerrainType, TERRAIN_PROPERTIES
from simulation.drone import Drone, DroneRole, DroneStatus
from simulation.survivor import Survivor, spawn_survivors
from simulation.world import World
from config import *  # all constants
```

## Verification

Before marking done, verify:
- `World` can be instantiated with a terrain JSON path
- `world.tick()` advances drone movement correctly
- `world.get_state()` returns a clean JSON-serializable dict
- `drone.leader_score()` returns a float between 0-100
- `drone.drain_battery()` sets status to OFFLINE at 0
- Survivors escalate urgency after enough ticks
- Terrain move costs differ by type (jungle=2, rubble=3, road=1)
- `terrain.elevation(x,y)` returns correct values (mountain=4, flood=-1, road=0)
- `IsometricRenderer` starts and displays the 3D isometric grid with Pygame
- Drones render with role-based colours, leader has white rim
- Survivors pulse at urgency-dependent speeds
- Camera pan (arrow/WASD) and zoom (scroll) work correctly
