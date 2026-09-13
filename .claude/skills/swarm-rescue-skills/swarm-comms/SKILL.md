---
name: swarm-comms
description: Build and modify the decentralized swarm communication system. Covers leader election algorithm, drone-to-drone message bus, role assignment (scout/rescue/relay), comm range limitations, failover and re-election, heartbeat protocol, and the peer-to-peer mesh network. Use when the user asks about leader election, drone roles, how drones communicate, message passing, failover scenarios, decentralization, or why a drone didn't receive a command. Also use when adding new message types or modifying the election scoring formula.
---

# Swarm Communication Sub-Skill

This skill covers the decentralized coordination layer — how drones self-organize, elect leaders, communicate, and handle failures without centralized control.

## Core Concept: Why Decentralize?

The case study scenario is a post-disaster zone where cell towers are down. The AI agent (Gemini) gives high-level strategic goals, but the drones must coordinate tactically among themselves. If the "leader drone" fails (battery dies, sensors break), the swarm must continue operating seamlessly through automatic re-election.

The hierarchy is:

```
AI Agent (Gemini)
    │ talks to via MCP
    ▼
Leader Drone (elected)
    │ broadcasts TASK_ASSIGN messages
    ▼
Worker Drones (scout / rescue / relay)
    │ send SENSOR_REPORT messages back
    ▼
Leader aggregates and reports to Agent
```

## Leader Election (swarm/leader_election.py)

```python
from simulation.drone import Drone, DroneStatus

def compute_leader_score(drone: Drone) -> float:
    """Compute a drone's fitness to be leader.
    
    Weights:
    - battery (40%): Leaders need endurance
    - signal/status (30%): Must be active and reachable
    - sensors (20%): Leader should have working sensors for oversight
    - uptime (10%): Stability bonus — longer uptime = more reliable
    """
    if drone.status != DroneStatus.ACTIVE:
        return 0.0  # non-active drones can't be leader
    
    battery_score = drone.battery  # 0-100
    
    signal_score = 100.0 if drone.status == DroneStatus.ACTIVE else 0.0
    
    sensor_score = 100.0 if drone.sensors_online else 0.0
    
    uptime_score = min(drone.uptime_ticks / 50, 1.0) * 100.0
    
    return (
        battery_score * 0.4 +
        signal_score * 0.3 +
        sensor_score * 0.2 +
        uptime_score * 0.1
    )


def run_election(drones: dict) -> dict:
    """Execute a leader election across all drones.
    
    Process:
    1. Each drone computes its score
    2. All scores are compared
    3. Highest score wins
    4. Previous leader is demoted to worker
    5. New leader is promoted
    
    Returns election results including all scores and the winner.
    """
    scores = {}
    for drone_id, drone in drones.items():
        scores[drone_id] = round(compute_leader_score(drone), 2)
    
    # Find winner
    if not scores:
        return {"error": "No drones available for election"}
    
    winner_id = max(scores, key=scores.get)
    
    # Demote old leader
    for drone in drones.values():
        if drone.is_leader and drone.drone_id != winner_id:
            drone.is_leader = False
    
    # Promote new leader
    drones[winner_id].is_leader = True
    
    return {
        "elected_leader": winner_id,
        "scores": scores,
        "leader_battery": drones[winner_id].battery,
        "leader_position": [drones[winner_id].x, drones[winner_id].y],
    }


def check_failover(drones: dict, threshold: float = 20.0) -> dict | None:
    """Check if the current leader needs to be replaced.
    
    Triggers re-election if:
    - Leader battery drops below threshold
    - Leader goes offline
    - Leader sensors fail
    
    Call this every LEADER_ELECTION_INTERVAL ticks.
    """
    current_leader = None
    for drone in drones.values():
        if drone.is_leader:
            current_leader = drone
            break
    
    if current_leader is None:
        return run_election(drones)
    
    needs_reelection = (
        current_leader.battery < threshold or
        current_leader.status != DroneStatus.ACTIVE or
        not current_leader.sensors_online
    )
    
    if needs_reelection:
        reason = "low_battery"
        if current_leader.status != DroneStatus.ACTIVE:
            reason = "offline"
        elif not current_leader.sensors_online:
            reason = "sensor_failure"
        
        result = run_election(drones)
        result["failover_reason"] = reason
        result["previous_leader"] = current_leader.drone_id
        return result
    
    return None  # no failover needed
```

## Message Bus (swarm/message_bus.py)

Drones communicate through a simple message system with comm range limitations.

```python
from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict
import math

@dataclass
class DroneMessage:
    sender_id: str
    message_type: str    # HEARTBEAT, TASK_ASSIGN, SENSOR_REPORT, etc.
    payload: dict
    tick: int
    recipient_id: str | None = None  # None = broadcast

# Global inbox storage
_inboxes: dict[str, list[DroneMessage]] = defaultdict(list)


def _in_comm_range(drone_a, drone_b, comm_range: int) -> bool:
    """Check if two drones can communicate (within comm_range cells)."""
    dist = math.sqrt(
        (drone_a.x - drone_b.x)**2 + 
        (drone_a.y - drone_b.y)**2
    )
    return dist <= comm_range


def broadcast(world, sender_id: str, message_type: str, payload: dict) -> dict:
    """Broadcast a message from one drone to all drones in comm range.
    
    Messages only reach drones within COMM_RANGE cells.
    Relay drones extend this range — if Drone-A can reach Drone-R (relay),
    and Drone-R can reach Drone-C, then A's message reaches C through R.
    """
    from config import COMM_RANGE
    
    sender = world.drones.get(sender_id)
    if not sender or sender.status != DroneStatus.ACTIVE:
        return {"error": f"Sender {sender_id} not active"}
    
    msg = DroneMessage(
        sender_id=sender_id,
        message_type=message_type,
        payload=payload,
        tick=world.tick_count,
    )
    
    delivered_to = []
    
    # Direct delivery to drones in range
    for drone_id, drone in world.drones.items():
        if drone_id == sender_id:
            continue
        if drone.status != DroneStatus.ACTIVE:
            continue
        if _in_comm_range(sender, drone, COMM_RANGE):
            _inboxes[drone_id].append(msg)
            delivered_to.append(drone_id)
    
    # Relay extension: check if any relay drone can forward
    relay_drones = [
        d for d in world.drones.values()
        if d.role == DroneRole.RELAY and d.status == DroneStatus.ACTIVE
    ]
    for relay in relay_drones:
        if relay.drone_id in delivered_to or relay.drone_id == sender_id:
            continue
        if not _in_comm_range(sender, relay, COMM_RANGE):
            continue
        # Relay received it — now forward to drones in relay's range
        for drone_id, drone in world.drones.items():
            if drone_id in delivered_to or drone_id == sender_id:
                continue
            if _in_comm_range(relay, drone, COMM_RANGE):
                _inboxes[drone_id].append(msg)
                delivered_to.append(drone_id)
    
    return {
        "sent": True,
        "sender": sender_id,
        "message_type": message_type,
        "delivered_to": delivered_to,
        "total_reached": len(delivered_to),
    }


def send_direct(world, from_id: str, to_id: str, 
                message_type: str, payload: dict) -> dict:
    """Send a message directly to one drone. Must be in comm range."""
    from config import COMM_RANGE
    
    sender = world.drones.get(from_id)
    receiver = world.drones.get(to_id)
    
    if not sender or not receiver:
        return {"error": "Invalid drone ID"}
    
    if not _in_comm_range(sender, receiver, COMM_RANGE):
        return {
            "delivered": False,
            "reason": "out_of_range",
            "distance": round(math.sqrt(
                (sender.x - receiver.x)**2 + (sender.y - receiver.y)**2
            ), 1),
            "comm_range": COMM_RANGE,
        }
    
    msg = DroneMessage(
        sender_id=from_id,
        message_type=message_type,
        payload=payload,
        tick=world.tick_count,
        recipient_id=to_id,
    )
    _inboxes[to_id].append(msg)
    
    return {"delivered": True, "to": to_id, "message_type": message_type}


def get_inbox(drone_id: str) -> dict:
    """Retrieve and clear all pending messages for a drone."""
    messages = _inboxes.pop(drone_id, [])
    return {
        "drone_id": drone_id,
        "message_count": len(messages),
        "messages": [
            {
                "from": m.sender_id,
                "type": m.message_type,
                "payload": m.payload,
                "tick": m.tick,
            }
            for m in messages
        ],
    }
```

## Message Types

Define these as constants and use consistently:

```python
class MessageType:
    HEARTBEAT = "HEARTBEAT"
    # Sent every 2 ticks by all drones
    # Payload: {"battery": float, "position": [x,y], "role": str, "score": float}
    
    TASK_ASSIGN = "TASK_ASSIGN"
    # Leader → worker
    # Payload: {"task": "scan"|"rescue"|"relay"|"return", 
    #           "target": [x,y], "priority": int}
    
    SENSOR_REPORT = "SENSOR_REPORT"
    # Worker → leader
    # Payload: {"hits": [...], "cells_scanned": int, "coverage_patch": [...]}
    
    SURVIVOR_FOUND = "SURVIVOR_FOUND"
    # Any drone → broadcast (priority)
    # Payload: {"position": [x,y], "confidence": float, 
    #           "count_estimate": int, "urgency": str}
    
    ELECTION = "ELECTION"
    # Broadcast during election
    # Payload: {"candidate_id": str, "score": float}
    
    SUPPLY_DROPPED = "SUPPLY_DROPPED"
    # Rescue drone → broadcast
    # Payload: {"position": [x,y], "survivor_id": str}
    
    LOW_BATTERY_ALERT = "LOW_BATTERY_ALERT"
    # Any drone → leader
    # Payload: {"battery": float, "position": [x,y], "need_replacement": bool}
```

## Drone Roles (swarm/roles.py)

```python
from simulation.drone import DroneRole

ROLE_DESCRIPTIONS = {
    DroneRole.SCOUT: {
        "description": "Explore uncharted areas using thermal_scan()",
        "priority_actions": ["move_to unscanned sectors", "thermal_scan", "report findings"],
        "battery_threshold": 25,  # recall below this
    },
    DroneRole.RESCUE: {
        "description": "Deliver supplies to confirmed survivor locations",
        "priority_actions": ["move_to survivor", "drop_supply", "confirm_survivor"],
        "battery_threshold": 20,  # needs more reserve for return trip
    },
    DroneRole.RELAY: {
        "description": "Position between base and field to extend comm range",
        "priority_actions": ["move_to relay_point", "maintain position"],
        "battery_threshold": 30,  # relays need to stay up longer
    },
    DroneRole.IDLE: {
        "description": "No assignment. Available for tasking.",
        "priority_actions": ["await orders"],
        "battery_threshold": 15,
    },
}


def suggest_role_assignment(drones: dict, world) -> dict:
    """Suggest optimal role assignments based on current fleet state.
    
    Logic:
    - High battery drones → Scout (need endurance for exploration)
    - Drones with cargo → Rescue
    - Drones at mid-range positions → Relay (if comm gaps exist)
    - Low battery → recall to charge, assign IDLE
    
    This is a suggestion — the agent makes the final call.
    """
    suggestions = {}
    active = [d for d in drones.values() if d.status == DroneStatus.ACTIVE]
    
    # Sort by battery descending
    active.sort(key=lambda d: d.battery, reverse=True)
    
    scouts_needed = 2
    rescue_needed = 1
    relay_needed = 1 if _has_comm_gap(drones, world) else 0
    
    for drone in active:
        if drone.battery < 20:
            suggestions[drone.drone_id] = "idle"  # should recall
        elif drone.cargo and rescue_needed > 0:
            suggestions[drone.drone_id] = "rescue"
            rescue_needed -= 1
        elif relay_needed > 0 and not drone.is_leader:
            suggestions[drone.drone_id] = "relay"
            relay_needed -= 1
        elif scouts_needed > 0:
            suggestions[drone.drone_id] = "scout"
            scouts_needed -= 1
        else:
            suggestions[drone.drone_id] = "idle"
    
    return {"suggested_roles": suggestions}


def _has_comm_gap(drones: dict, world) -> bool:
    """Check if any active drone is out of comm range from the leader."""
    from config import COMM_RANGE
    leader = next((d for d in drones.values() if d.is_leader), None)
    if not leader:
        return False
    for drone in drones.values():
        if drone.drone_id == leader.drone_id:
            continue
        if drone.status != DroneStatus.ACTIVE:
            continue
        dist = math.sqrt((leader.x - drone.x)**2 + (leader.y - drone.y)**2)
        if dist > COMM_RANGE:
            return True
    return False
```

## Integrating with the Agent

The agent should follow this pattern for decentralized operations:

```
1. elect_leader() → know who's in charge
2. discover_fleet() → know who's available
3. Use suggest_role_assignment logic (or implement own) 
4. assign_role() for each drone
5. For scout tasks: send TASK_ASSIGN via leader → worker
6. Workers execute and send SENSOR_REPORT back
7. Agent reads reports via get_inbox(leader_id) 
8. Every LEADER_ELECTION_INTERVAL ticks: check_failover()
9. If failover triggered: re-elect, reassign roles, continue
```

## Important Design Rules

- The leader is NOT a permanent role — it shifts based on fleet health.
- Comm range (5 cells default) means distant drones CANNOT hear broadcasts. Relay drones solve this.
- Messages are consumed on read (get_inbox clears them). Process them promptly.
- The agent should check the leader's inbox regularly — that's where all field reports arrive.
- Heartbeats are simulated (you can auto-send them in the world tick loop) or explicit (agent triggers them). Choose one approach and be consistent.
- When a drone goes offline (battery = 0), it stops receiving and sending messages. The swarm must detect this via missing heartbeats and adapt.
