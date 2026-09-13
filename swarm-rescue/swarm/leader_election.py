# swarm/leader_election.py
# Leader election, scoring, and failover logic for the swarm.

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulation.drone import Drone, DroneStatus


def compute_leader_score(drone: Drone) -> float:
    """Compute a drone's fitness to be leader.

    Scoring breakdown (max 100):
      battery       : up to 40 pts  (battery * 0.4)
      signal/status : up to 30 pts  (100 if ACTIVE else 0, * 0.3)
      sensors       : up to 20 pts  (100 if online else 0, * 0.2)
      uptime        : up to 10 pts  (normalised over 50 ticks, * 0.1)

    Non-active drones immediately score 0 — they cannot be leader.
    """
    if drone.status != DroneStatus.ACTIVE:
        return 0.0

    battery_score = drone.battery  # 0-100

    # Signal is a proxy for reachability; ACTIVE drones have full signal.
    signal_score = 100.0 if drone.status == DroneStatus.ACTIVE else 0.0

    sensor_score = 100.0 if drone.sensors_online else 0.0

    uptime_score = min(drone.uptime_ticks / 50.0, 1.0) * 100.0

    return (
        battery_score * 0.4
        + signal_score * 0.3
        + sensor_score * 0.2
        + uptime_score * 0.1
    )


def run_election(drones: dict) -> dict:
    """Execute a leader election across all drones.

    Process:
    1. Each drone computes its score via compute_leader_score().
    2. All scores are compared.
    3. Highest score wins.
    4. Previous leader is demoted (is_leader = False).
    5. New leader is promoted (is_leader = True).

    Args:
        drones: dict mapping drone_id (str) -> Drone instance.

    Returns:
        JSON-serialisable dict with:
          elected_leader  : str   — drone_id of the winner
          scores          : dict  — {drone_id: score} for all drones
          leader_battery  : float — winning drone's battery level
          leader_position : list  — [x, y] of the winning drone
    """
    if not drones:
        return {"error": "No drones available for election"}

    scores: dict[str, float] = {
        drone_id: round(compute_leader_score(drone), 2)
        for drone_id, drone in drones.items()
    }

    # Pick the drone with the highest score. If all score 0 (e.g. all
    # offline), still pick one to avoid an unrecoverable leaderless state.
    winner_id = max(scores, key=lambda k: scores[k])

    # Demote any previous leader(s) that are not the winner.
    for drone in drones.values():
        if drone.is_leader and drone.drone_id != winner_id:
            drone.is_leader = False

    # Promote winner.
    drones[winner_id].is_leader = True

    winner = drones[winner_id]
    return {
        "elected_leader": winner_id,
        "scores": scores,
        "leader_battery": round(winner.battery, 1),
        "leader_position": [winner.x, winner.y],
    }


def check_failover(drones: dict, threshold: float = 20.0) -> dict | None:
    """Check if the current leader needs to be replaced.

    Triggers re-election if:
    - Leader battery < threshold
    - Leader goes offline (status != ACTIVE)
    - Leader sensors failed

    If no leader exists at all, an emergency election is held immediately.

    Args:
        drones   : dict mapping drone_id -> Drone.
        threshold: battery level (%) below which the leader must step down.

    Returns:
        Election result dict (with added failover_reason and previous_leader
        keys) if a failover occurred, or None if the leader is healthy.
    """
    # Find current leader.
    current_leader: Drone | None = None
    for drone in drones.values():
        if drone.is_leader:
            current_leader = drone
            break

    # No leader found — run an emergency election.
    if current_leader is None:
        result = run_election(drones)
        result["failover_reason"] = "no_leader"
        result["previous_leader"] = None
        return result

    # Evaluate leader health.
    low_battery = current_leader.battery < threshold
    offline = current_leader.status != DroneStatus.ACTIVE
    sensor_failure = not current_leader.sensors_online

    needs_reelection = low_battery or offline or sensor_failure

    if not needs_reelection:
        return None  # leader is healthy, no failover

    # Determine the most critical reason.
    if offline:
        reason = "offline"
    elif sensor_failure:
        reason = "sensor_failure"
    else:
        reason = "low_battery"

    previous_id = current_leader.drone_id

    result = run_election(drones)
    result["failover_reason"] = reason
    result["previous_leader"] = previous_id
    return result
