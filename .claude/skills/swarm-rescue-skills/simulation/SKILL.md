---
name: simulation
description: Build and modify the simulation engine for the drone swarm rescue project. Covers terrain generation with elevation (Indonesian disaster zones), drone movement physics, survivor placement, the tick-based game loop, and 3D isometric Pygame rendering. Use when the user asks about the world map, terrain types, elevation, drone movement costs, grid rendering, isometric view, camera controls, game loop timing, or anything about the simulation layer that the drones operate in.
---

# Simulation Engine Sub-Skill

This skill covers Layer 3 — the Python simulation that models a post-disaster Indonesian terrain with drones, survivors, and environmental hazards. The simulation uses a **2D grid for logic** but renders as a **3D isometric view** using Pygame, with terrain elevation providing visual depth.

## Terrain System (terrain.py)

The world is a `GRID_WIDTH x GRID_HEIGHT` grid. Each cell has a terrain type that affects drone behavior and has an elevation value for 3D visualization.

### Terrain Types

```python
from enum import Enum

class TerrainType(Enum):
    JUNGLE = "jungle"
    FLOOD_ZONE = "flood_zone"
    URBAN_RUBBLE = "urban_rubble"
    MOUNTAIN = "mountain"
    CLEAR_ROAD = "clear_road"
    VOLCANIC_ASH = "volcanic_ash"    # Semeru scenario
    COASTLINE = "coastline"          # Tsunami scenario
```

### Terrain Properties

Each terrain type has properties that affect gameplay and 3D rendering:

```python
TERRAIN_PROPERTIES = {
    TerrainType.JUNGLE: {
        "move_cost": 2,              # ticks to cross
        "thermal_attenuation": 0.7,  # canopy blocks 30% of IR
        "base_temperature": 26.0,
        "traversable": True,
        "elevation": 1,              # raised canopy level
        "description": "Dense tropical canopy, slows drones and blocks thermal"
    },
    TerrainType.FLOOD_ZONE: {
        "move_cost": 1,
        "thermal_attenuation": 0.5,  # water reflects IR poorly
        "base_temperature": 18.0,
        "traversable": True,
        "elevation": -1,             # below sea level, flooded
        "description": "Standing water from tsunami/flood, cold signature"
    },
    TerrainType.URBAN_RUBBLE: {
        "move_cost": 3,              # debris field, slow and dangerous
        "thermal_attenuation": 0.6,
        "base_temperature": 24.0,
        "traversable": True,
        "elevation": 1,
        "description": "Collapsed buildings, survivors likely trapped here"
    },
    TerrainType.MOUNTAIN: {
        "move_cost": 4,
        "thermal_attenuation": 0.9,  # clear air, good for scanning
        "base_temperature": 20.0,
        "traversable": True,
        "elevation": 4,              # tallest terrain in isometric view
        "description": "High elevation, good vantage but slow to reach"
    },
    TerrainType.CLEAR_ROAD: {
        "move_cost": 1,
        "thermal_attenuation": 1.0,  # no obstruction
        "base_temperature": 28.0,
        "traversable": True,
        "elevation": 0,              # flat ground level
        "description": "Open road, fastest movement and best sensor clarity"
    },
    TerrainType.VOLCANIC_ASH: {
        "move_cost": 3,
        "thermal_attenuation": 0.3,  # ash cloud severely blocks IR
        "base_temperature": 35.0,    # residual volcanic heat
        "traversable": True,
        "elevation": 2,
        "description": "Ash fallout zone, very poor visibility"
    },
    TerrainType.COASTLINE: {
        "move_cost": 1,
        "thermal_attenuation": 0.8,
        "base_temperature": 22.0,
        "traversable": True,
        "elevation": 0,
        "description": "Coastal area, potential tsunami debris"
    },
}
```

### Elevation Values

Elevation is used purely for 3D isometric rendering — it does NOT affect gameplay logic:

| Terrain | Elevation | Visual effect |
|---|---|---|
| Mountain | 4 | Tallest tiles, prominent 3D walls |
| Volcanic ash | 2 | Medium height |
| Jungle | 1 | Slight raise (canopy) |
| Urban rubble | 1 | Slight raise (debris piles) |
| Clear road | 0 | Ground level |
| Coastline | 0 | Ground level |
| Flood zone | -1 | Sunken (water below ground) |

### Generating Terrain from AI Studio

Use Google AI Studio to generate terrain JSON. Prompt example:

```
Generate a 20x20 grid JSON map representing a post-earthquake coastal
area in Palu, Sulawesi, Indonesia. Each cell should be one of:
jungle, flood_zone, urban_rubble, mountain, clear_road, coastline.

The east side should be coastline with flood zones inland.
Urban rubble clusters should appear in the center (the city).
Jungle and mountain terrain should be on the west side.
Clear roads should connect key areas.

Return ONLY valid JSON: a 2D array of strings, 20 rows x 20 columns.
```

### Terrain class

```python
import json

class Terrain:
    def __init__(self, json_path: str):
        with open(json_path) as f:
            raw = json.load(f)
        self.width = len(raw[0])
        self.height = len(raw)
        self.grid = [
            [TerrainType(cell) for cell in row]
            for row in raw
        ]

    def get(self, x: int, y: int) -> TerrainType:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return None  # out of bounds

    def move_cost(self, x, y) -> int:
        t = self.get(x, y)
        return TERRAIN_PROPERTIES[t]["move_cost"] if t else float('inf')

    def thermal_factor(self, x, y) -> float:
        t = self.get(x, y)
        return TERRAIN_PROPERTIES[t]["thermal_attenuation"] if t else 0.0

    def base_temp(self, x, y) -> float:
        t = self.get(x, y)
        return TERRAIN_PROPERTIES[t]["base_temperature"] if t else 22.0

    def elevation(self, x, y) -> int:
        t = self.get(x, y)
        return TERRAIN_PROPERTIES[t]["elevation"] if t else 0
```

## Drone Model (drone.py)

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

class DroneRole(Enum):
    SCOUT = "scout"
    RESCUE = "rescue"
    RELAY = "relay"
    IDLE = "idle"

class DroneStatus(Enum):
    ACTIVE = "active"
    RETURNING = "returning"
    CHARGING = "charging"
    OFFLINE = "offline"

@dataclass
class Drone:
    drone_id: str
    x: int
    y: int
    battery: float = 100.0
    role: DroneRole = DroneRole.IDLE
    status: DroneStatus = DroneStatus.ACTIVE
    cargo: Optional[str] = None         # "medical_kit", "water", etc.
    is_leader: bool = False
    sensors_online: bool = True
    uptime_ticks: int = 0

    # Movement state
    move_target: Optional[tuple] = None
    move_progress: int = 0              # ticks spent moving to target

    def leader_score(self) -> float:
        """Score used for leader election."""
        return (
            self.battery * 0.4 +
            (1.0 if self.sensors_online else 0.0) * 20.0 +
            min(self.uptime_ticks / 50, 1.0) * 10.0 +
            (30.0 if self.status == DroneStatus.ACTIVE else 0.0)
        )

    def drain_battery(self, amount: float):
        self.battery = max(0, self.battery - amount)
        if self.battery <= 0:
            self.status = DroneStatus.OFFLINE
            self.sensors_online = False

    def charge(self, amount: float = 5.0):
        self.battery = min(100, self.battery + amount)
        if self.battery > 10:
            self.status = DroneStatus.ACTIVE
            self.sensors_online = True

    def to_dict(self) -> dict:
        return {
            "drone_id": self.drone_id,
            "position": [self.x, self.y],
            "battery": round(self.battery, 1),
            "role": self.role.value,
            "status": self.status.value,
            "is_leader": self.is_leader,
            "cargo": self.cargo,
            "sensors_online": self.sensors_online,
            "leader_score": round(self.leader_score(), 2),
        }
```

## Survivor Model (survivor.py)

```python
import random
from dataclasses import dataclass

@dataclass
class Survivor:
    survivor_id: str
    x: int
    y: int
    body_temp: float = 37.0       # normal human temp
    count: int = 1                # group size (1-5)
    urgency: str = "medium"       # low, medium, high, critical
    rescued: bool = False
    ticks_alive: int = 0

    def __post_init__(self):
        self.body_temp += random.gauss(0, 0.5)
        self.effective_temp = self.body_temp + (self.count - 1) * 0.8

    def tick(self):
        self.ticks_alive += 1
        if self.ticks_alive > 50 and self.urgency == "medium":
            self.urgency = "high"
        if self.ticks_alive > 100 and self.urgency == "high":
            self.urgency = "critical"

    def to_dict(self) -> dict:
        return {
            "survivor_id": self.survivor_id,
            "position": [self.x, self.y],
            "count": self.count,
            "urgency": self.urgency,
            "rescued": self.rescued,
        }
```

Spawn survivors with bias toward urban rubble and coastline:

```python
def spawn_survivors(terrain: Terrain, count: int) -> list[Survivor]:
    survivors = []
    high_priority = [TerrainType.URBAN_RUBBLE, TerrainType.COASTLINE]

    for i in range(count):
        while True:
            x = random.randint(0, terrain.width - 1)
            y = random.randint(0, terrain.height - 1)
            t = terrain.get(x, y)
            if t in high_priority or random.random() < 0.3:
                break

        survivors.append(Survivor(
            survivor_id=f"SUR-{i:03d}",
            x=x, y=y,
            count=random.randint(1, 4),
            urgency=random.choice(["low", "medium", "medium", "high"]),
        ))
    return survivors
```

## World State Manager (world.py)

```python
class World:
    def __init__(self, terrain_path: str, num_drones: int, num_survivors: int):
        self.terrain = Terrain(terrain_path)
        self.tick_count = 0

        self.drones = {
            f"DRONE-{chr(65+i)}": Drone(
                drone_id=f"DRONE-{chr(65+i)}",
                x=0, y=0
            )
            for i in range(num_drones)
        }

        self.survivors = spawn_survivors(self.terrain, num_survivors)
        self.fog_of_war = [[False] * self.terrain.width
                           for _ in range(self.terrain.height)]
        self.mission_log = []
        self.rescued_count = 0

    def tick(self):
        self.tick_count += 1
        for drone in self.drones.values():
            if drone.status == DroneStatus.ACTIVE:
                drone.uptime_ticks += 1
                self._process_movement(drone)
            elif drone.status == DroneStatus.CHARGING:
                drone.charge()
        for survivor in self.survivors:
            if not survivor.rescued:
                survivor.tick()

    def _process_movement(self, drone: Drone):
        if drone.move_target is None:
            return
        tx, ty = drone.move_target
        cost = self.terrain.move_cost(tx, ty)
        drone.move_progress += 1
        if drone.move_progress >= cost:
            drone.x, drone.y = tx, ty
            drone.move_target = None
            drone.move_progress = 0
            drone.drain_battery(BATTERY_DRAIN_PER_MOVE)

    def get_state(self) -> dict:
        return {
            "tick": self.tick_count,
            "drones": {k: v.to_dict() for k, v in self.drones.items()},
            "rescued": self.rescued_count,
            "total_survivors": len(self.survivors),
            "coverage": self._calc_coverage(),
        }

    def _calc_coverage(self) -> float:
        revealed = sum(1 for row in self.fog_of_war for cell in row if cell)
        total = self.terrain.width * self.terrain.height
        return round(revealed / total * 100, 1)
```

## 3D Isometric Renderer (renderer.py)

The simulation is visualized using a **Pygame-based isometric 3D renderer**. The underlying simulation logic remains 2D grid-based, but the renderer projects tiles into an isometric view with elevation-based height.

### Isometric Projection

Grid coordinates (x, y) map to screen pixels (sx, sy):

```
sx = (x - y) * (TILE_WIDTH // 2) + camera_x
sy = (x + y) * (TILE_HEIGHT // 2) - elevation * ELEVATION_SCALE + camera_y
```

### Tile Rendering

Each tile is drawn with three faces:
- **Top face**: isometric diamond in the terrain's colour
- **South wall**: darker shade, extends downward from bottom and right corners
- **East wall**: medium shade, extends downward from right and top corners

Wall height is proportional to elevation, giving mountains tall visible sides and flood zones a sunken appearance.

### Rendering Config (config.py)

```python
TILE_WIDTH = 64          # isometric tile width in pixels
TILE_HEIGHT = 32         # isometric tile height in pixels
ELEVATION_SCALE = 12     # pixels per elevation unit
SCREEN_WIDTH = 1400
SCREEN_HEIGHT = 900
FPS = 30
CAMERA_SPEED = 10        # pixels per frame for camera pan
```

### Terrain Colours (RGB)

| Terrain | Top face colour |
|---|---|
| Clear road | (200, 200, 200) light gray |
| Jungle | (34, 139, 34) dark green |
| Flood zone | (65, 105, 225) blue |
| Urban rubble | (178, 134, 86) brown |
| Mountain | (128, 128, 128) gray |
| Volcanic ash | (139, 69, 19) dark red-brown |
| Coastline | (238, 214, 175) sandy |
| Fog of war | (25, 25, 40) very dark |

### Entity Rendering

- **Drones**: Coloured circles hovering above tiles with sine-wave bob animation. Colour by role (Scout=cyan, Rescue=red, Relay=yellow, Idle=white, Offline=dark gray). Leader drone is 40% larger with a white rim. Shadow ellipse below.
- **Survivors**: Pulsing red dots. Pulse speed scales with urgency (low=0.5Hz, medium=1Hz, high=2Hz, critical=4Hz).
- **Base station**: Green circle with white ring and "BASE" label.

### HUD Overlay

Three semi-transparent panels drawn on top of the 3D view:
1. **Top-left**: tick count, coverage %, rescued count
2. **Right panel**: fleet status with per-drone battery bars (green/yellow/red)
3. **Bottom legend**: colour-coded entity legend + controls hint

### Camera Controls

| Input | Action |
|---|---|
| Arrow keys / WASD | Pan camera |
| Mouse scroll | Zoom in/out (0.3x – 3.0x) |
| R | Reset camera to default |

### Two Renderers Available

```python
# 3D isometric (primary — requires pygame)
from simulation.renderer import IsometricRenderer
renderer = IsometricRenderer(world)
renderer.run()

# Terminal fallback (headless/CI)
from simulation.renderer import TerminalRenderer
renderer = TerminalRenderer()
renderer.render(world)
```

### Standalone Demo

```bash
cd swarm-rescue
python simulation/renderer.py
```

## Important Design Rules

- All state lives in the `World` object. The MCP server reads/writes through World methods only.
- Drones move one cell per `move_cost` ticks — they don't teleport. The agent must plan for travel time.
- Battery drain is per-action, not per-tick. Moving costs 2, scanning costs 3, idling costs 0.
- Survivors escalate urgency over time. The agent should prioritize confirmed high-urgency targets.
- The fog of war starts fully opaque. Only `thermal_scan()` reveals cells. This forces the agent to explore.
- The base charging station is always at (0, 0). Drones must physically return there to charge.
- Elevation is **visual only** — it affects 3D tile rendering height but NOT simulation logic (movement, scanning, etc.).
- The `IsometricRenderer` is the primary renderer for demos and the hackathon. The `TerminalRenderer` is kept as a fallback for headless environments.
