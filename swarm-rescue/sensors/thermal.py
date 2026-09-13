# sensors/thermal.py
# Core thermal scanning logic for the swarm-rescue simulation.
#
# Public API (called by mcp_server.py):
#   perform_scan(world, drone_id)           -> dict
#   classify_heat(temperature, terrain)     -> dict
#   confirm_scan(world, drone_id, x, y)     -> dict
#
# Private helpers:
#   _read_cell_temperature(world, sx, sy, drone_x, drone_y) -> dict
#   _inject_false_positives(world, hits, cells_scanned)     -> list

from __future__ import annotations

import math
import random
import sys
import os

# Allow this module to be imported when the package root is not on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    SCAN_RADIUS,
    THERMAL_NOISE_STD,       # config.py uses THERMAL_NOISE_STD, not THERMAL_NOISE_STDDEV
    DETECTION_THRESHOLD,
    FALSE_POSITIVE_RATE,
    BATTERY_DRAIN_PER_SCAN,
)
from simulation.terrain import TERRAIN_PROPERTIES, TerrainType


# ---------------------------------------------------------------------------
# Main scan entry point
# ---------------------------------------------------------------------------

def perform_scan(world, drone_id: str) -> dict:
    """Execute a thermal scan centred on a drone's current position.

    Algorithm
    ---------
    1. Validate drone exists and sensors are online.
    2. Drain battery by BATTERY_DRAIN_PER_SCAN.
    3. For every cell within SCAN_RADIUS (circular, not square):
       a. Compute observed temperature via _read_cell_temperature().
       b. Reveal the cell through the FogOfWar object.
       c. If observed_temp >= DETECTION_THRESHOLD, classify the reading.
       d. Append qualifying classifications to hits.
    4. Inject false positives at FALSE_POSITIVE_RATE per unoccupied cell.
    5. Advance the world by one tick.
    6. Return a JSON-serialisable result dict.

    Called by the MCP server's ``thermal_scan`` tool.
    """
    drone = world.drones.get(drone_id)
    if drone is None:
        return {"error": f"Drone {drone_id!r} not found"}
    if not drone.sensors_online:
        return {"error": f"Drone {drone_id!r} sensors are offline"}

    drone.drain_battery(BATTERY_DRAIN_PER_SCAN)

    cx, cy = drone.x, drone.y
    hits: list[dict] = []
    cells_scanned: list[list[int]] = []
    fog_cleared: list[list[int]] = []
    temp_readings: list[float] = []

    for dy in range(-SCAN_RADIUS, SCAN_RADIUS + 1):
        for dx in range(-SCAN_RADIUS, SCAN_RADIUS + 1):
            # Circular radius — skip corners outside the circle
            if dx * dx + dy * dy > SCAN_RADIUS * SCAN_RADIUS:
                continue

            sx, sy = cx + dx, cy + dy
            if not (0 <= sx < world.terrain.width and 0 <= sy < world.terrain.height):
                continue

            # Reveal via FogOfWar object (supports both .reveal() and direct indexing)
            # world.fog_of_war is a FogOfWar instance when created via sensors package,
            # but may be a raw 2D list when World initialises itself before sensors wire up.
            # Handle both cases gracefully.
            fog = world.fog_of_war
            if hasattr(fog, "reveal"):
                # FogOfWar object
                newly = fog.reveal(sx, sy, radius=0)
                fog_cleared.extend(newly)
            else:
                # Raw 2D list fallback
                if not fog[sy][sx]:
                    fog[sy][sx] = True
                    fog_cleared.append([sx, sy])

            cells_scanned.append([sx, sy])

            reading = _read_cell_temperature(world, sx, sy, cx, cy)
            temp_readings.append(reading["observed_temp"])

            # Classify cells that clear the detection threshold
            if reading["observed_temp"] >= DETECTION_THRESHOLD:
                terrain = world.terrain.get(sx, sy)
                classification = classify_heat(reading["observed_temp"], terrain)
                # Minimum confidence gate keeps very marginal readings out of hits
                if classification["confidence"] > 0.3:
                    hits.append({
                        "position": [sx, sy],
                        "observed_temp": round(reading["observed_temp"], 1),
                        "classification": classification["type"],
                        "confidence": round(classification["confidence"], 2),
                        "count_estimate": classification.get("count_estimate", 0),
                    })

    # Inject false positives before returning
    hits = _inject_false_positives(world, hits, cells_scanned)

    # Advance simulation one tick (survivor urgency escalation, drone movement, etc.)
    world.tick()

    ambient_avg = (
        sum(temp_readings) / len(temp_readings) if temp_readings else 0.0
    )

    return {
        "drone_id": drone_id,
        "scan_center": [cx, cy],
        "hits": hits,
        "ambient_avg": round(ambient_avg, 1),
        "cells_scanned": len(cells_scanned),
        "fog_cleared": fog_cleared,
        "battery_remaining": round(drone.battery, 1),
    }


# ---------------------------------------------------------------------------
# Single-cell temperature reading
# ---------------------------------------------------------------------------

def _read_cell_temperature(
    world,
    sx: int,
    sy: int,
    drone_x: int,
    drone_y: int,
) -> dict:
    """Compute the observed temperature of cell (sx, sy) from a drone at (drone_x, drone_y).

    Formula
    -------
    observed_temp = base_terrain_temp
                  + (survivor_heat_delta * terrain_attenuation * distance_factor)
                  + gaussian_noise

    - ``survivor_heat_delta``: difference between survivor's effective_temp and base_temp.
      Attenuation is applied to this delta only, not to the ambient baseline.
    - ``terrain_attenuation``: IR signal multiplier (1.0 = clear, 0.3 = heavily blocked).
    - ``distance_factor``: gentle linear falloff; min 0.5 so distant cells still register.
    - ``gaussian_noise``: sampled from N(0, THERMAL_NOISE_STD).

    Returns a dict with observed_temp, true_temp (ground truth), position, and terrain.
    The true_temp is NOT forwarded to the agent — it is for internal verification only.
    """
    terrain = world.terrain.get(sx, sy)
    if terrain is None:
        return {
            "position": [sx, sy],
            "observed_temp": 0.0,
            "true_temp": 0.0,
            "terrain": "unknown",
        }

    props = TERRAIN_PROPERTIES[terrain]
    base_temp: float = props["base_temperature"]
    attenuation: float = props["thermal_attenuation"]

    # Accumulate survivor signal for this cell (multiple survivors possible)
    survivor_delta = 0.0
    for survivor in world.survivors:
        if survivor.x == sx and survivor.y == sy and not survivor.rescued:
            # effective_temp already accounts for group size bonus
            delta = survivor.effective_temp - base_temp
            survivor_delta += max(0.0, delta)

    # Apply terrain IR attenuation to the survivor signal delta
    attenuated_signal = survivor_delta * attenuation

    # Distance falloff — linear, minimum 0.5 so far cells still register
    dist = math.sqrt((sx - drone_x) ** 2 + (sy - drone_y) ** 2)
    distance_factor = max(0.5, 1.0 - dist * 0.1)
    attenuated_signal *= distance_factor

    # Gaussian sensor noise (simulates real IR sensor imprecision)
    noise = random.gauss(0.0, THERMAL_NOISE_STD)

    observed_temp = base_temp + attenuated_signal + noise

    return {
        "position": [sx, sy],
        "observed_temp": observed_temp,
        "true_temp": base_temp + survivor_delta,   # ground truth — never shown to agent
        "terrain": terrain.value,
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_heat(temperature: float, terrain: TerrainType) -> dict:
    """Classify a temperature reading relative to the terrain's ambient baseline.

    Classification table
    --------------------
    delta < 2          -> ambient      (not a rescue target)
    2 <= delta < 8     -> possible_human  (confidence 0.30-0.60)
    8 <= delta < 16    -> human           (confidence 0.70-0.95)
    delta >= 16        -> fire            (confidence 0.90)

    The ``delta`` is temperature minus the terrain's base_temperature, so the
    same absolute reading means different things in a jungle (26°C base) vs
    volcanic ash (35°C base).

    Group count estimate
    --------------------
    For human readings, count_estimate = max(1, int(delta / 4)).
    This gives the agent a rough sense of group size to prioritise rescue.

    Args:
        temperature: Observed temperature in degrees Celsius.
        terrain:     The TerrainType of the cell being classified.

    Returns:
        A JSON-serialisable dict with keys: type, confidence, count_estimate (where applicable).
    """
    if terrain is None or terrain not in TERRAIN_PROPERTIES:
        base = 28.0  # fallback to ambient outdoor temp
    else:
        base = TERRAIN_PROPERTIES[terrain]["base_temperature"]

    delta = temperature - base

    if delta < 2.0:
        return {"type": "ambient", "confidence": 0.1, "count_estimate": 0}

    if 2.0 <= delta < 8.0:
        # Weak signal — could be human attenuated by dense canopy / rubble
        confidence = min(0.6, 0.3 + delta * 0.05)
        return {
            "type": "possible_human",
            "confidence": round(confidence, 2),
            "count_estimate": 1,
        }

    if 8.0 <= delta < 16.0:
        # Strong human signal — either clear terrain or a group
        confidence = min(0.95, 0.7 + delta * 0.02)
        count_estimate = max(1, int(delta / 4))
        return {
            "type": "human",
            "confidence": round(confidence, 2),
            "count_estimate": count_estimate,
        }

    # delta >= 16.0 — too hot for a human; fire or engine wreckage
    return {
        "type": "fire",
        "confidence": 0.9,
        "count_estimate": 0,
    }


# ---------------------------------------------------------------------------
# False positive injection
# ---------------------------------------------------------------------------

def _inject_false_positives(world, hits: list, cells_scanned: list) -> list:
    """Randomly inject ghost hits to simulate real-world sensor noise.

    For each scanned cell, with probability FALSE_POSITIVE_RATE, a fake hit
    is added if no real hit already exists at that position. The ghost reading
    looks like a plausible human signal (temp 30.5-35.0°C, confidence 0.30-0.55)
    so the agent cannot trivially ignore it.

    Critically, the ghost hits carry NO ``_is_false_positive`` flag — the agent
    MUST use ``confirm_scan()`` to distinguish true from false detections.

    Args:
        world:         The World simulation object (unused currently but kept
                       for future hazard-layer checks).
        hits:          Existing confirmed hits from the real scan pass.
        cells_scanned: All cell positions covered by this scan.

    Returns:
        Updated hits list with false positives appended (order not guaranteed).
    """
    for cell in cells_scanned:
        if random.random() < FALSE_POSITIVE_RATE:
            # Only inject if no real hit already occupies this cell
            if not any(h["position"] == cell for h in hits):
                fake_temp = round(random.uniform(30.5, 35.0), 1)
                fake_confidence = round(random.uniform(0.30, 0.55), 2)
                hits.append({
                    "position": cell,
                    "observed_temp": fake_temp,
                    "classification": "possible_human",
                    "confidence": fake_confidence,
                    "count_estimate": 1,
                    # Intentionally no "_is_false_positive" key
                })
    return hits


# ---------------------------------------------------------------------------
# Confirmation scan
# ---------------------------------------------------------------------------

def confirm_scan(world, drone_id: str, target_x: int, target_y: int) -> dict:
    """Close-range confirmation scan at a single cell.

    The drone must be adjacent (Manhattan distance <= 1) to the target cell.
    This represents the drone hovering close and using high-resolution mode.

    Costs 1 battery unit (lighter than a full scan).

    Returns
    -------
    If a live survivor exists at (target_x, target_y):
        {confirmed: True, position: [x, y], count: int, urgency: str,
         body_temp: float, confidence: 0.98}

    If no survivor (false positive or already rescued):
        {confirmed: False, position: [x, y], note: str, confidence: 0.95}

    Error cases (drone not found, too far) return:
        {error: str}
    """
    drone = world.drones.get(drone_id)
    if drone is None:
        return {"error": f"Drone {drone_id!r} not found"}

    manhattan = abs(drone.x - target_x) + abs(drone.y - target_y)
    if manhattan > 1:
        return {
            "error": (
                f"Drone {drone_id!r} is too far from target ({target_x},{target_y}). "
                f"Manhattan distance is {manhattan}, maximum allowed is 1."
            )
        }

    drone.drain_battery(1.0)

    # Search for an unrescued survivor at exactly the target cell
    for survivor in world.survivors:
        if (
            survivor.x == target_x
            and survivor.y == target_y
            and not survivor.rescued
        ):
            return {
                "confirmed": True,
                "position": [target_x, target_y],
                "survivor_id": survivor.survivor_id,
                "count": survivor.count,
                "urgency": survivor.urgency,
                "body_temp": round(survivor.body_temp, 1),
                "confidence": 0.98,
            }

    return {
        "confirmed": False,
        "position": [target_x, target_y],
        "note": "No survivor at this location. Likely a false positive or already rescued.",
        "confidence": 0.95,
    }
