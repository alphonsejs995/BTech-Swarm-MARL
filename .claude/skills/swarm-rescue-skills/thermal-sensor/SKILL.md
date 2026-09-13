---
name: thermal-sensor
description: Build and modify the thermal sensor simulation for drone heat detection. Covers heat signature modeling, Gaussian noise injection, terrain attenuation, signal classification (human vs fire vs ambient), confidence scoring, false positive simulation, fog-of-war mechanics, and the scan radius system. Use when the user asks about thermal scanning, heat maps, survivor detection accuracy, sensor noise, fog of war, why a scan missed a survivor, or how to tune detection sensitivity.
---

# Thermal Sensor Sub-Skill

This skill covers the heat/thermal detection system that drones use to find survivors. It simulates realistic infrared sensor behavior with noise, attenuation, and classification.

## Heat Source Model

Every cell in the grid has a temperature that is the sum of:

```
cell_temp = base_terrain_temp + survivor_heat (if any) + noise + environmental_factors
```

### Temperature Ranges (Tropical Indonesia)

| Source | Temperature Range | Notes |
|---|---|---|
| Human (alive) | 36.0 - 38.5 C | Group of 3+ reads higher |
| Human (deceased, recent) | 30.0 - 34.0 C | Fades over time |
| Fire / engine wreckage | 40.0 - 120.0 C | Easy to detect, not a rescue target |
| Ambient jungle | 25.0 - 28.0 C | Warm tropical baseline |
| Ambient flood water | 16.0 - 20.0 C | Cold contrast helps detection |
| Ambient urban rubble | 22.0 - 26.0 C | Sun-heated concrete |
| Ambient mountain | 18.0 - 22.0 C | Cooler at elevation |
| Volcanic ash zone | 30.0 - 45.0 C | Hot background masks survivors |

## Core Sensor Implementation (sensors/thermal.py)

```python
import random
import math
from typing import Optional
from config import (
    SCAN_RADIUS, THERMAL_NOISE_STDDEV, DETECTION_THRESHOLD,
    FALSE_POSITIVE_RATE, BATTERY_DRAIN_PER_SCAN
)

def perform_scan(world, drone_id: str) -> dict:
    """Execute a thermal scan around a drone's position.
    
    This is the main entry point called by the MCP server's thermal_scan() tool.
    
    Returns:
        dict with hits, ambient data, cells scanned, and fog cleared.
    """
    drone = world.drones.get(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    if not drone.sensors_online:
        return {"error": f"Drone {drone_id} sensors are offline"}
    
    # Drain battery for scanning
    drone.drain_battery(BATTERY_DRAIN_PER_SCAN)
    
    cx, cy = drone.x, drone.y
    hits = []
    cells_scanned = []
    fog_cleared = []
    temp_readings = []
    
    # Scan all cells within radius
    for dy in range(-SCAN_RADIUS, SCAN_RADIUS + 1):
        for dx in range(-SCAN_RADIUS, SCAN_RADIUS + 1):
            # Circular radius check
            if dx*dx + dy*dy > SCAN_RADIUS * SCAN_RADIUS:
                continue
            
            sx, sy = cx + dx, cy + dy
            if not (0 <= sx < world.terrain.width and 0 <= sy < world.terrain.height):
                continue
            
            # Clear fog of war
            if not world.fog_of_war[sy][sx]:
                world.fog_of_war[sy][sx] = True
                fog_cleared.append([sx, sy])
            
            cells_scanned.append([sx, sy])
            
            # Calculate observed temperature
            reading = _read_cell_temperature(world, sx, sy, cx, cy)
            temp_readings.append(reading)
            
            # Check if it's a hit
            if reading["observed_temp"] >= DETECTION_THRESHOLD:
                classification = classify_heat(
                    reading["observed_temp"],
                    world.terrain.get(sx, sy)
                )
                if classification["confidence"] > 0.3:  # minimum threshold
                    hits.append({
                        "position": [sx, sy],
                        "observed_temp": round(reading["observed_temp"], 1),
                        "classification": classification["type"],
                        "confidence": round(classification["confidence"], 2),
                        "count_estimate": classification.get("count_estimate", 0),
                    })
    
    # Inject false positives
    hits = _inject_false_positives(world, hits, cells_scanned)
    
    # Advance simulation
    world.tick()
    
    ambient_avg = sum(r["observed_temp"] for r in temp_readings) / max(len(temp_readings), 1)
    
    return {
        "drone_id": drone_id,
        "scan_center": [cx, cy],
        "hits": hits,
        "ambient_avg": round(ambient_avg, 1),
        "cells_scanned": len(cells_scanned),
        "fog_cleared": fog_cleared,
        "battery_remaining": round(drone.battery, 1),
    }


def _read_cell_temperature(world, sx: int, sy: int, drone_x: int, drone_y: int) -> dict:
    """Read the temperature of a single cell as seen by the drone sensor."""
    terrain = world.terrain.get(sx, sy)
    base_temp = TERRAIN_PROPERTIES[terrain]["base_temperature"]
    attenuation = TERRAIN_PROPERTIES[terrain]["thermal_attenuation"]
    
    # Check for survivors in this cell
    survivor_heat = 0.0
    for survivor in world.survivors:
        if survivor.x == sx and survivor.y == sy and not survivor.rescued:
            survivor_heat = survivor.effective_temp - base_temp
            break
    
    # Check for fire/wreckage (environmental hazards)
    # These could be added as a separate hazard layer
    
    # Apply terrain attenuation to the survivor signal
    # Attenuation reduces the DIFFERENCE, not the base
    attenuated_signal = survivor_heat * attenuation
    
    # Distance attenuation (further from drone = weaker signal)
    dist = math.sqrt((sx - drone_x)**2 + (sy - drone_y)**2)
    distance_factor = max(0.5, 1.0 - dist * 0.1)  # gentle falloff
    attenuated_signal *= distance_factor
    
    # Add Gaussian noise (sensor imprecision)
    noise = random.gauss(0, THERMAL_NOISE_STDDEV)
    
    observed_temp = base_temp + attenuated_signal + noise
    
    return {
        "position": [sx, sy],
        "observed_temp": observed_temp,
        "true_temp": base_temp + survivor_heat,  # ground truth, not exposed to agent
        "terrain": terrain.value,
    }


def classify_heat(temperature: float, terrain) -> dict:
    """Classify a temperature reading into a source type.
    
    Uses the temperature relative to the terrain's base temp to determine
    what's likely causing the heat signature.
    
    Returns:
        dict with type, confidence, and optional count_estimate
    """
    base = TERRAIN_PROPERTIES[terrain]["base_temperature"]
    delta = temperature - base
    
    if delta < 2.0:
        return {"type": "ambient", "confidence": 0.1}
    
    if delta >= 2.0 and delta < 8.0:
        # Could be human (weak signal through attenuation)
        confidence = min(0.6, 0.3 + delta * 0.05)
        return {
            "type": "possible_human",
            "confidence": confidence,
            "count_estimate": 1,
        }
    
    if delta >= 8.0 and delta < 16.0:
        # Strong human signal (group or clear terrain)
        confidence = min(0.95, 0.7 + delta * 0.02)
        count_estimate = max(1, int(delta / 4))
        return {
            "type": "human",
            "confidence": confidence,
            "count_estimate": count_estimate,
        }
    
    if delta >= 16.0:
        # Fire or engine — too hot for human
        return {
            "type": "fire",
            "confidence": 0.9,
            "count_estimate": 0,
        }
    
    return {"type": "unknown", "confidence": 0.2}


def _inject_false_positives(world, hits: list, cells_scanned: list) -> list:
    """Randomly inject false positive readings to simulate real-world noise.
    
    The agent must learn to use confirm_survivor() to verify hits.
    """
    for cell in cells_scanned:
        if random.random() < FALSE_POSITIVE_RATE:
            # Check if there's already a hit here
            if not any(h["position"] == cell for h in hits):
                fake_temp = random.uniform(30.5, 35.0)
                hits.append({
                    "position": cell,
                    "observed_temp": round(fake_temp, 1),
                    "classification": "possible_human",
                    "confidence": round(random.uniform(0.3, 0.55), 2),
                    "count_estimate": 1,
                    # Note: no "_is_false_positive" flag — agent doesn't know
                })
    return hits
```

## Confirmation Scan

When the agent gets a hit with moderate confidence (0.4-0.7), it should send a drone close and run `confirm_survivor()`. This does a high-resolution scan at 1-cell range:

```python
def confirm_scan(world, drone_id: str, target_x: int, target_y: int) -> dict:
    """Close-range confirmation scan. Drone must be adjacent to target."""
    drone = world.drones.get(drone_id)
    if not drone:
        return {"error": f"Drone {drone_id} not found"}
    
    # Check adjacency (drone must be within 1 cell)
    dist = abs(drone.x - target_x) + abs(drone.y - target_y)
    if dist > 1:
        return {"error": f"Drone too far. Distance: {dist}. Must be adjacent (<=1)."}
    
    drone.drain_battery(1)  # lighter than full scan
    
    # Direct reading with minimal noise
    for survivor in world.survivors:
        if survivor.x == target_x and survivor.y == target_y and not survivor.rescued:
            return {
                "confirmed": True,
                "position": [target_x, target_y],
                "count": survivor.count,
                "urgency": survivor.urgency,
                "body_temp": round(survivor.body_temp, 1),
                "confidence": 0.98,
            }
    
    return {
        "confirmed": False,
        "position": [target_x, target_y],
        "note": "No survivor at this location. Likely a false positive.",
        "confidence": 0.95,
    }
```

## Fog of War (sensors/fog_of_war.py)

```python
class FogOfWar:
    """Tracks which cells have been revealed by drone scans."""
    
    def __init__(self, width: int, height: int):
        self.grid = [[False] * width for _ in range(height)]
        self.width = width
        self.height = height
    
    def reveal(self, cx: int, cy: int, radius: int):
        """Reveal cells in a circular radius."""
        revealed = []
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx*dx + dy*dy <= radius * radius:
                    x, y = cx + dx, cy + dy
                    if 0 <= x < self.width and 0 <= y < self.height:
                        if not self.grid[y][x]:
                            self.grid[y][x] = True
                            revealed.append([x, y])
        return revealed
    
    def coverage_percent(self) -> float:
        revealed = sum(1 for row in self.grid for cell in row if cell)
        return round(revealed / (self.width * self.height) * 100, 1)
    
    def unrevealed_sectors(self, sector_size: int = 5) -> list:
        """Get list of sectors (grouped cells) that are mostly unexplored.
        Useful for the agent to decide where to send scouts."""
        sectors = []
        for sy in range(0, self.height, sector_size):
            for sx in range(0, self.width, sector_size):
                total = 0
                revealed = 0
                for dy in range(min(sector_size, self.height - sy)):
                    for dx in range(min(sector_size, self.width - sx)):
                        total += 1
                        if self.grid[sy + dy][sx + dx]:
                            revealed += 1
                if total > 0 and revealed / total < 0.5:
                    sectors.append({
                        "sector_origin": [sx, sy],
                        "coverage": round(revealed / total * 100, 1),
                    })
        return sectors
```

## Tuning Guide

If detection is too easy, increase these:
- `THERMAL_NOISE_STDDEV`: higher = more sensor noise
- `FALSE_POSITIVE_RATE`: higher = more ghost readings
- Lower terrain `thermal_attenuation` for jungle/ash

If detection is too hard, decrease those same values, or:
- Increase `SCAN_RADIUS` from 2 to 3
- Lower `DETECTION_THRESHOLD` from 30C to 28C
- Increase survivor `effective_temp` by raising the group heat bonus

The goal is that the agent needs ~2-3 scans per sector to confidently find survivors, and must use `confirm_survivor()` for ambiguous readings. If every scan finds everyone perfectly, it's too easy. If no scan ever works, it's too hard.
