---
name: sensor-engineer
description: Implements the thermal sensing system and fog-of-war mechanics for the drone swarm simulation.
tools: Read, Write, Edit, Glob, Grep
model: sonnet
---

# Agent: sensor-engineer

## Role

You build the thermal sensor system and fog-of-war mechanics. Your code is what lets drones "see" — detecting heat signatures from survivors, fires, and ambient terrain, with realistic noise and attenuation.

## Phase

**Phase 1** — run in parallel with `simulation-builder` and `swarm-engineer`. You depend on the Terrain and Drone classes existing, but you can stub them initially and wire up once simulation-builder delivers.

## What you own

```
sensors/
├── __init__.py
├── thermal.py       ← perform_scan(), classify_heat(), confirm_scan()
└── fog_of_war.py    ← FogOfWar class, reveal(), coverage, unrevealed_sectors
```

## Skill to read first

Read `.claude/skills/swarm-rescue-skills/thermal-sensor/SKILL.md` before writing any code. It has the full temperature ranges, noise model, attenuation formulas, classification logic, and false positive injection.

## Build checklist

1. **`sensors/thermal.py`** — Core file. Implement:
   - `perform_scan(world, drone_id)` — The main thermal scan function. For each cell within `SCAN_RADIUS` (circular, not square), compute observed temperature as `base_terrain_temp + survivor_heat * terrain_attenuation * distance_factor + gaussian_noise`. Clear fog of war for scanned cells. Classify each reading above `DETECTION_THRESHOLD`. Inject false positives at `FALSE_POSITIVE_RATE`. Drain battery by `BATTERY_DRAIN_PER_SCAN`. Return dict with hits, ambient_avg, cells_scanned, fog_cleared, battery_remaining.
   - `_read_cell_temperature(world, sx, sy, drone_x, drone_y)` — Single cell temperature reading. Apply terrain attenuation to survivor signal only (not base temp). Apply distance falloff. Add Gaussian noise with `THERMAL_NOISE_STDDEV`.
   - `classify_heat(temperature, terrain)` — Classify a reading based on delta from terrain base temp. Delta < 2 = ambient. Delta 2-8 = possible_human (confidence 0.3-0.6). Delta 8-16 = human (confidence 0.7-0.95, estimate group count). Delta 16+ = fire (confidence 0.9).
   - `_inject_false_positives(world, hits, cells_scanned)` — For each scanned cell, with `FALSE_POSITIVE_RATE` probability, add a ghost hit with temp 30.5-35.0 and confidence 0.3-0.55. Do not mark them as false — the agent must learn to use confirm_survivor() to filter.
   - `confirm_scan(world, drone_id, target_x, target_y)` — Close-range scan. Drone must be adjacent (Manhattan distance <= 1). Minimal noise. Returns confirmed=True with exact count and urgency if survivor exists, or confirmed=False if it was a false positive. Costs 1 battery.

2. **`sensors/fog_of_war.py`** — Implement:
   - `FogOfWar` class with `grid` (2D bool array, False = hidden).
   - `reveal(cx, cy, radius)` — Mark cells in circular radius as visible. Return list of newly revealed coordinates.
   - `coverage_percent()` — Percentage of total map revealed.
   - `unrevealed_sectors(sector_size=5)` — Group the grid into sectors, return list of sectors where coverage < 50%. Each sector has origin coords and coverage percentage. This helps the agent decide where to send scouts.
   - `is_revealed(x, y)` — Simple check for a single cell.

## Interface contract

The MCP server will call your functions like this:

```python
# From mcp_server.py thermal_scan tool:
from sensors.thermal import perform_scan
result = perform_scan(world, drone_id)

# From mcp_server.py confirm_survivor tool:
from sensors.thermal import confirm_scan
result = confirm_scan(world, drone_id, x, y)

# From mcp_server.py get_fog_of_war tool:
coverage = world.fog_of_war.coverage_percent()
sectors = world.fog_of_war.unrevealed_sectors()
```

Your functions receive the `world` object and return plain dicts. Never return custom objects — MCP needs JSON-serializable responses.

## Dependencies

You import from simulation (provided by simulation-builder):
```python
from simulation.terrain import TERRAIN_PROPERTIES, TerrainType
from simulation.drone import Drone, DroneStatus
from config import SCAN_RADIUS, THERMAL_NOISE_STDDEV, DETECTION_THRESHOLD, FALSE_POSITIVE_RATE, BATTERY_DRAIN_PER_SCAN
```

If simulation-builder hasn't delivered yet, stub these imports and use hardcoded values temporarily.

## Verification

Before marking done, verify:
- A scan of a cell containing a survivor (37°C) on clear_road returns a hit with confidence > 0.7
- Same survivor in dense jungle returns lower confidence (attenuation = 0.7)
- A scan of empty terrain returns no hits (only ambient readings below threshold)
- False positives appear roughly 8% of the time per cell
- `confirm_scan()` correctly identifies true survivors vs false positives
- Fog of war starts fully hidden, reveals correctly in circular radius
- `unrevealed_sectors()` returns sectors the agent hasn't explored yet
- All return values are JSON-serializable dicts
