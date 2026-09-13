# simulation/world.py
# World class — the single source of truth for the entire simulation state.
# The MCP server reads and writes through World methods only; there are
# no backdoors or direct field mutations from outside this module.

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from typing import Optional

import math

from config import (
    NUM_DRONES,
    NUM_SURVIVORS,
    BASE_X,
    BASE_Y,
    BATTERY_DRAIN_PER_MOVE,
    BATTERY_DRAIN_PER_SCAN,
    BATTERY_LOW_THRESHOLD,
    BATTERY_CRITICAL,
    SCAN_RADIUS,
    DEFAULT_TERRAIN_PATH,
    MAX_TICKS,
    COMM_RANGE,
)
from simulation.terrain import Terrain, TerrainType
from simulation.drone import Drone, DroneRole, DroneStatus
from simulation.survivor import Survivor, spawn_survivors
from sensors.fog_of_war import FogOfWar


class World:
    """Top-level simulation container.

    Owns:
    - ``terrain``     : the 2-D terrain grid loaded from JSON
    - ``drones``      : dict mapping drone_id -> Drone
    - ``survivors``   : list of all Survivor objects
    - ``fog_of_war``  : 2-D bool grid (True = revealed)
    - ``mission_log`` : ordered list of log entries (dicts)
    - ``rescued_count``: running total of rescued survivors
    - ``tick_count``  : current simulation tick

    Design rules (from SKILL.md)
    ----------------------------
    - All state lives here; the MCP server calls World methods.
    - Drones move one cell per ``move_cost`` ticks — no teleportation.
    - Battery drain is per-action, not per tick.
    - Fog of war starts fully opaque; only thermal_scan() reveals cells.
    - Base charging station is always at (BASE_X, BASE_Y).
    """

    def __init__(
        self,
        terrain_path: str = DEFAULT_TERRAIN_PATH,
        num_drones: int = NUM_DRONES,
        num_survivors: int = NUM_SURVIVORS,
    ) -> None:
        self.terrain = Terrain(terrain_path)
        self.tick_count: int = 0
        self.rescued_count: int = 0
        self.mission_log: list[dict] = []

        # Spawn drones clustered at base station
        self.drones: dict[str, Drone] = {
            f"DRONE-{chr(65 + i)}": Drone(
                drone_id=f"DRONE-{chr(65 + i)}",
                x=BASE_X,
                y=BASE_Y,
            )
            for i in range(num_drones)
        }

        # Terrain-biased survivor placement
        self.survivors: list[Survivor] = spawn_survivors(self.terrain, num_survivors)

        # Fog of war — False means not yet revealed.
        # FogOfWar supports both 2D-list indexing (fog[y][x]) and
        # method calls (.coverage_percent(), .unrevealed_sectors(), .reveal()).
        self.fog_of_war: FogOfWar = FogOfWar(self.terrain.width, self.terrain.height)

        # Reveal cells around the base station immediately
        self._reveal_cells(BASE_X, BASE_Y, radius=SCAN_RADIUS)

        self._log("World initialised", {
            "terrain": terrain_path,
            "drones": num_drones,
            "survivors": num_survivors,
            "grid": f"{self.terrain.width}x{self.terrain.height}",
        })

    # ------------------------------------------------------------------
    # Main game-loop entry point
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Advance the simulation by exactly one tick.

        Order of operations each tick:
        1. Increment tick counter
        2. Process each drone (movement, battery top-up while charging)
        3. Tick every unrescued survivor (urgency escalation)
        """
        self.tick_count += 1

        for drone in self.drones.values():
            if drone.status == DroneStatus.ACTIVE:
                drone.uptime_ticks += 1
                self._process_movement(drone)
                self._check_low_battery(drone)
            elif drone.status == DroneStatus.RETURNING:
                drone.uptime_ticks += 1
                self._process_movement(drone)
                # Arrive at base -> switch to charging
                if drone.x == BASE_X and drone.y == BASE_Y and drone.move_target is None:
                    drone.status = DroneStatus.CHARGING
                    self._log(f"{drone.drone_id} arrived at base, charging", {
                        "drone_id": drone.drone_id,
                        "battery": drone.battery,
                    })
            elif drone.status == DroneStatus.CHARGING:
                drone.charge()
                # Full battery — ready to fly again
                if drone.battery >= 100.0:
                    drone.status = DroneStatus.ACTIVE
                    self._log(f"{drone.drone_id} fully charged", {"drone_id": drone.drone_id})

        for survivor in self.survivors:
            if not survivor.rescued:
                survivor.tick()

        # Decentralized self-healing: update mesh network + auto-recover
        self._update_mesh_network()

    # ------------------------------------------------------------------
    # Decentralized mesh network & self-healing
    # ------------------------------------------------------------------

    _ISOLATION_THRESHOLD = 3  # ticks alone before auto-return

    def _update_mesh_network(self) -> None:
        """Update each drone's neighbor list and trigger autonomous
        self-healing behaviour when a drone becomes isolated.

        This runs every tick and simulates the on-board firmware that each
        drone would run in a real decentralized swarm — no central controller
        needed.  Behaviours:

        1. **Neighbor discovery** — each drone knows who is within COMM_RANGE.
        2. **Isolation detection** — if a drone has zero neighbors for
           ``_ISOLATION_THRESHOLD`` consecutive ticks, it autonomously
           navigates back toward the swarm centroid (last known).
        3. **Leader failover** — if the leader goes offline or drops below
           critical battery, a new election is triggered automatically.
        """
        active = [
            d for d in self.drones.values()
            if d.status in (DroneStatus.ACTIVE, DroneStatus.RETURNING)
        ]

        if len(active) < 1:
            return

        # Compute swarm centroid
        centroid_x = sum(d.x for d in active) / len(active)
        centroid_y = sum(d.y for d in active) / len(active)

        # --- Step 1: Neighbor discovery ---
        for drone in active:
            neighbors = []
            for other in active:
                if other.drone_id == drone.drone_id:
                    continue
                dist = math.sqrt(
                    (drone.x - other.x) ** 2 + (drone.y - other.y) ** 2
                )
                if dist <= COMM_RANGE:
                    neighbors.append(other.drone_id)
            drone.neighbors = neighbors

            # Update centroid knowledge for any drone that still has comms
            if neighbors:
                drone.last_known_centroid = (
                    int(round(centroid_x)),
                    int(round(centroid_y)),
                )

        # --- Step 2: Isolation detection & self-healing ---
        for drone in active:
            if len(drone.neighbors) == 0 and len(active) > 1:
                drone.isolated_ticks += 1
                if (
                    drone.isolated_ticks >= self._ISOLATION_THRESHOLD
                    and drone.status == DroneStatus.ACTIVE
                    and drone.move_target is None
                ):
                    # Autonomous return toward last known centroid
                    tx, ty = drone.last_known_centroid
                    tx = max(0, min(self.terrain.width - 1, tx))
                    ty = max(0, min(self.terrain.height - 1, ty))
                    if drone.x != tx or drone.y != ty:
                        dx = 1 if tx > drone.x else (-1 if tx < drone.x else 0)
                        dy = 1 if ty > drone.y else (-1 if ty < drone.y else 0)
                        # Prefer X movement first (Manhattan)
                        nx = drone.x + (dx if dx != 0 else 0)
                        ny = drone.y + (0 if dx != 0 else dy)
                        if self.terrain.is_traversable(nx, ny):
                            self.move_drone(drone.drone_id, nx, ny)
                            self._log(
                                f"{drone.drone_id} SELF-HEALING: isolated for "
                                f"{drone.isolated_ticks} ticks, moving toward swarm",
                                {
                                    "event": "self_healing",
                                    "drone_id": drone.drone_id,
                                    "isolated_ticks": drone.isolated_ticks,
                                    "target": [tx, ty],
                                },
                            )
            else:
                drone.isolated_ticks = 0

        # --- Step 3: Automatic leader failover ---
        leader = next(
            (d for d in self.drones.values() if d.is_leader), None
        )
        needs_reelection = (
            leader is None
            or leader.status not in (DroneStatus.ACTIVE, DroneStatus.RETURNING)
            or leader.battery < BATTERY_CRITICAL
        )
        if needs_reelection and len(active) > 0:
            from swarm.leader_election import run_election

            old_leader_id = leader.drone_id if leader else "none"
            result = run_election(self.drones)
            self._log(
                f"AUTO-FAILOVER: leader {old_leader_id} replaced by "
                f"{result.get('elected_leader')}",
                {
                    "event": "auto_failover",
                    "old_leader": old_leader_id,
                    "new_leader": result.get("elected_leader"),
                    "reason": "offline_or_critical_battery",
                },
            )

    # ------------------------------------------------------------------
    # Movement subsystem
    # ------------------------------------------------------------------

    def _process_movement(self, drone: Drone) -> None:
        """Advance a drone one tick toward its move_target (if any).

        A drone takes ``terrain.move_cost(tx, ty)`` ticks to enter a cell.
        When ``move_progress`` reaches the cost, the drone is teleported to
        the target, battery is drained, and move state is cleared.
        """
        if drone.move_target is None:
            return

        tx, ty = drone.move_target
        cost = self.terrain.move_cost(tx, ty)

        drone.move_progress += 1

        if drone.move_progress >= cost:
            # Arrival
            drone.x, drone.y = tx, ty
            drone.move_target = None
            drone.move_progress = 0
            drone.drain_battery(BATTERY_DRAIN_PER_MOVE)

    def move_drone(self, drone_id: str, tx: int, ty: int) -> dict:
        """Request a drone to move one step toward (tx, ty).

        Only accepts a single-cell step (adjacent Manhattan neighbours).
        For multi-step paths the MCP server should call this once per step.
        Returns a result dict consumed by the MCP tool layer.
        """
        if drone_id not in self.drones:
            return {"success": False, "error": f"Unknown drone {drone_id!r}"}

        drone = self.drones[drone_id]

        if drone.status == DroneStatus.OFFLINE:
            return {"success": False, "error": "Drone is offline"}

        if not self.terrain.is_traversable(tx, ty):
            return {"success": False, "error": f"Cell ({tx},{ty}) is not traversable"}

        if drone.move_target is not None:
            return {"success": False, "error": "Drone is already moving"}

        drone.move_target = (tx, ty)
        drone.move_progress = 0

        return {
            "success": True,
            "drone_id": drone_id,
            "from": [drone.x, drone.y],
            "to": [tx, ty],
            "move_cost": self.terrain.move_cost(tx, ty),
        }

    # ------------------------------------------------------------------
    # Scanning / fog of war
    # ------------------------------------------------------------------

    def scan_area(self, drone_id: str) -> dict:
        """Perform a thermal scan from drone's current position.

        Reveals cells within SCAN_RADIUS.  Returns ambient temperatures
        with Gaussian noise so the agent must reason about detections.
        Battery is drained by BATTERY_DRAIN_PER_SCAN.
        """
        import math
        import random

        if drone_id not in self.drones:
            return {"success": False, "error": f"Unknown drone {drone_id!r}"}

        drone = self.drones[drone_id]

        if not drone.sensors_online:
            return {"success": False, "error": "Sensors are offline"}

        drone.drain_battery(BATTERY_DRAIN_PER_SCAN)
        self._reveal_cells(drone.x, drone.y, radius=SCAN_RADIUS)

        # Build readings grid
        readings: list[dict] = []
        for dy in range(-SCAN_RADIUS, SCAN_RADIUS + 1):
            for dx in range(-SCAN_RADIUS, SCAN_RADIUS + 1):
                cx, cy = drone.x + dx, drone.y + dy
                if not (0 <= cx < self.terrain.width and 0 <= cy < self.terrain.height):
                    continue
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > SCAN_RADIUS:
                    continue

                base = self.terrain.base_temp(cx, cy)
                attn = self.terrain.thermal_factor(cx, cy)

                # Check for survivors at this cell
                survivor_bonus = 0.0
                for s in self.survivors:
                    if not s.rescued and s.x == cx and s.y == cy:
                        raw_heat = (s.effective_temp - base) * attn
                        survivor_bonus += max(0.0, raw_heat)

                # Add Gaussian noise from config
                from config import THERMAL_NOISE_STD
                noise = random.gauss(0, THERMAL_NOISE_STD)
                measured = base + survivor_bonus + noise

                readings.append({
                    "x": cx,
                    "y": cy,
                    "temperature": round(measured, 2),
                    "terrain": self.terrain.get(cx, cy).value,
                })

        return {
            "success": True,
            "drone_id": drone_id,
            "scan_origin": [drone.x, drone.y],
            "readings": readings,
            "battery_remaining": round(drone.battery, 1),
        }

    def _reveal_cells(self, cx: int, cy: int, radius: int) -> None:
        """Mark all cells within circular radius of (cx, cy) as revealed.

        Delegates to FogOfWar.reveal() which uses the same Euclidean circle
        check (dx^2 + dy^2 <= radius^2) and handles bounds clamping.
        """
        self.fog_of_war.reveal(cx, cy, radius)

    # ------------------------------------------------------------------
    # Rescue action
    # ------------------------------------------------------------------

    def rescue_survivor(self, drone_id: str, survivor_id: str) -> dict:
        """Mark a survivor as rescued if the drone is at the survivor's position."""
        if drone_id not in self.drones:
            return {"success": False, "error": f"Unknown drone {drone_id!r}"}

        drone = self.drones[drone_id]
        target = next((s for s in self.survivors if s.survivor_id == survivor_id), None)

        if target is None:
            return {"success": False, "error": f"Unknown survivor {survivor_id!r}"}

        if target.rescued:
            return {"success": False, "error": "Survivor already rescued"}

        if drone.x != target.x or drone.y != target.y:
            return {
                "success": False,
                "error": (
                    f"Drone at ({drone.x},{drone.y}), "
                    f"survivor at ({target.x},{target.y}) — must be co-located"
                ),
            }

        target.rescued = True
        self.rescued_count += 1
        self._log(f"Survivor {survivor_id} rescued by {drone_id}", {
            "drone_id": drone_id,
            "survivor_id": survivor_id,
            "tick": self.tick_count,
            "count": target.count,
        })

        return {
            "success": True,
            "survivor_id": survivor_id,
            "count": target.count,
            "total_rescued": self.rescued_count,
        }

    # ------------------------------------------------------------------
    # Battery helpers
    # ------------------------------------------------------------------

    def _check_low_battery(self, drone: Drone) -> None:
        """Automatically set a drone to RETURNING if battery is critically low."""
        if drone.battery <= BATTERY_CRITICAL and drone.status == DroneStatus.ACTIVE:
            drone.status = DroneStatus.RETURNING
            drone.move_target = (BASE_X, BASE_Y)
            drone.move_progress = 0
            self._log(f"{drone.drone_id} battery critical, returning to base", {
                "drone_id": drone.drone_id,
                "battery": drone.battery,
            })

    # ------------------------------------------------------------------
    # State snapshot
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        """Return a complete, JSON-serialisable snapshot of the world.

        Used by the MCP server and the Streamlit dashboard.
        """
        return {
            "tick": self.tick_count,
            "mission_complete": self._is_mission_complete(),
            "drones": {k: v.to_dict() for k, v in self.drones.items()},
            "survivors": [s.to_dict() for s in self.survivors],
            "rescued": self.rescued_count,
            "total_survivors": len(self.survivors),
            "coverage_pct": self._calc_coverage(),
            "fog_of_war": [[cell for cell in row] for row in self.fog_of_war],
            "grid_size": [self.terrain.width, self.terrain.height],
        }

    def get_drone_state(self, drone_id: str) -> Optional[dict]:
        """Return the state dict for a single drone or None if not found."""
        drone = self.drones.get(drone_id)
        return drone.to_dict() if drone else None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, message: str, data: Optional[dict] = None) -> None:
        entry = {
            "tick": self.tick_count,
            "message": message,
        }
        if data:
            entry.update(data)
        self.mission_log.append(entry)

    # ------------------------------------------------------------------
    # Derived metrics
    # ------------------------------------------------------------------

    def _calc_coverage(self) -> float:
        """Percentage of grid cells that have been revealed."""
        revealed = sum(1 for row in self.fog_of_war for cell in row if cell)
        total = self.terrain.width * self.terrain.height
        return round(revealed / total * 100, 1)

    def _is_mission_complete(self) -> bool:
        """True when all survivors are rescued or max ticks exceeded."""
        all_rescued = all(s.rescued for s in self.survivors)
        return all_rescued or self.tick_count >= MAX_TICKS

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"World(tick={self.tick_count} "
            f"drones={len(self.drones)} "
            f"survivors={len(self.survivors)} "
            f"rescued={self.rescued_count} "
            f"coverage={self._calc_coverage()}%)"
        )
