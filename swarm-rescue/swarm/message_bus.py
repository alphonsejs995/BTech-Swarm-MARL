# swarm/message_bus.py
# Drone-to-drone message bus with comm-range gating and relay forwarding.

from __future__ import annotations

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict

from simulation.drone import DroneRole, DroneStatus


# ---------------------------------------------------------------------------
# Message type constants
# ---------------------------------------------------------------------------

class MessageType:
    """All valid message type strings used in the swarm."""

    # Sent every 2 ticks by every drone.
    # Payload: {"battery": float, "position": [x, y], "role": str, "score": float}
    HEARTBEAT = "HEARTBEAT"

    # Leader -> worker: assign a specific task.
    # Payload: {"task": "scan"|"rescue"|"relay"|"return", "target": [x, y], "priority": int}
    TASK_ASSIGN = "TASK_ASSIGN"

    # Worker -> leader: report what the sensor saw.
    # Payload: {"hits": [...], "cells_scanned": int, "coverage_patch": [...]}
    SENSOR_REPORT = "SENSOR_REPORT"

    # Any drone -> broadcast (highest priority).
    # Payload: {"position": [x, y], "confidence": float,
    #           "count_estimate": int, "urgency": str}
    SURVIVOR_FOUND = "SURVIVOR_FOUND"

    # Broadcast during leader election.
    # Payload: {"candidate_id": str, "score": float}
    ELECTION = "ELECTION"

    # Rescue drone -> broadcast after a supply drop.
    # Payload: {"position": [x, y], "survivor_id": str}
    SUPPLY_DROPPED = "SUPPLY_DROPPED"

    # Any drone -> leader when battery is critically low.
    # Payload: {"battery": float, "position": [x, y], "need_replacement": bool}
    LOW_BATTERY_ALERT = "LOW_BATTERY_ALERT"


# ---------------------------------------------------------------------------
# Message dataclass
# ---------------------------------------------------------------------------

@dataclass
class DroneMessage:
    """A single message passed between drones."""

    sender_id: str
    message_type: str       # one of the MessageType constants
    payload: dict           # message-type-specific data
    tick: int               # simulation tick when the message was created
    recipient_id: str | None = None  # None means broadcast


# ---------------------------------------------------------------------------
# Module-level inbox storage
# ---------------------------------------------------------------------------

# Maps drone_id -> list of pending DroneMessage objects.
_inboxes: dict[str, list[DroneMessage]] = defaultdict(list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _in_comm_range(drone_a, drone_b, comm_range: int) -> bool:
    """Return True if two drones are within Euclidean comm_range of each other."""
    dist = math.sqrt(
        (drone_a.x - drone_b.x) ** 2 + (drone_a.y - drone_b.y) ** 2
    )
    return dist <= comm_range


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def broadcast(world, sender_id: str, message_type: str, payload: dict) -> dict:
    """Broadcast a message from one drone to all reachable active drones.

    Delivery rules:
    1. Direct reach: any ACTIVE drone within COMM_RANGE of the sender receives
       the message immediately.
    2. Relay extension: if an ACTIVE RELAY drone received the message in step 1,
       it forwards the message to any ACTIVE drone within its own COMM_RANGE
       that was not already reached in step 1.

    The sender itself never receives its own broadcast.

    Args:
        world        : World instance (must have .drones dict and .tick_count).
        sender_id    : drone_id of the broadcasting drone.
        message_type : one of the MessageType constants.
        payload      : JSON-serialisable dict.

    Returns:
        JSON-serialisable dict with sent, sender, message_type,
        delivered_to (list of drone_ids), and total_reached (int).
    """
    from config import COMM_RANGE

    sender = world.drones.get(sender_id)
    if sender is None:
        return {"error": f"Drone {sender_id!r} not found"}
    if sender.status != DroneStatus.ACTIVE:
        return {"error": f"Sender {sender_id!r} is not active"}

    msg = DroneMessage(
        sender_id=sender_id,
        message_type=message_type,
        payload=payload,
        tick=world.tick_count,
    )

    delivered_to: list[str] = []

    # --- Step 1: Direct delivery to drones in sender's range ---
    for drone_id, drone in world.drones.items():
        if drone_id == sender_id:
            continue
        if drone.status != DroneStatus.ACTIVE:
            continue
        if _in_comm_range(sender, drone, COMM_RANGE):
            _inboxes[drone_id].append(msg)
            delivered_to.append(drone_id)

    # --- Step 2: Relay forwarding ---
    # Any relay drone that received the message (in step 1) re-broadcasts it
    # to drones within the relay's own COMM_RANGE that weren't already reached.
    relay_drones = [
        d
        for d in world.drones.values()
        if d.role == DroneRole.RELAY
        and d.status == DroneStatus.ACTIVE
        and d.drone_id != sender_id
        and d.drone_id in delivered_to  # relay must have received it first
    ]

    for relay in relay_drones:
        for drone_id, drone in world.drones.items():
            if drone_id == sender_id or drone_id in delivered_to:
                continue
            if drone.status != DroneStatus.ACTIVE:
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


def send_direct(
    world,
    from_id: str,
    to_id: str,
    message_type: str,
    payload: dict,
) -> dict:
    """Send a point-to-point message from one drone to another.

    The message is only delivered if both drones exist and the recipient is
    within COMM_RANGE of the sender.

    Args:
        world        : World instance.
        from_id      : sender drone_id.
        to_id        : recipient drone_id.
        message_type : one of the MessageType constants.
        payload      : JSON-serialisable dict.

    Returns:
        JSON-serialisable dict.
        On success : {"delivered": True, "to": to_id, "message_type": ...}
        On failure : {"delivered": False, "reason": str, ...extra context...}
    """
    from config import COMM_RANGE

    sender = world.drones.get(from_id)
    receiver = world.drones.get(to_id)

    if sender is None:
        return {"delivered": False, "reason": "sender_not_found", "from": from_id}
    if receiver is None:
        return {"delivered": False, "reason": "receiver_not_found", "to": to_id}

    distance = math.sqrt(
        (sender.x - receiver.x) ** 2 + (sender.y - receiver.y) ** 2
    )

    if not _in_comm_range(sender, receiver, COMM_RANGE):
        return {
            "delivered": False,
            "reason": "out_of_range",
            "distance": round(distance, 1),
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

    return {
        "delivered": True,
        "to": to_id,
        "message_type": message_type,
    }


def get_inbox(drone_id: str) -> dict:
    """Retrieve and clear all pending messages for a drone (consume on read).

    Args:
        drone_id: the drone whose inbox to drain.

    Returns:
        JSON-serialisable dict with drone_id, message_count, and messages list.
        Each message entry contains: from, type, payload, tick, and
        optionally recipient_id for direct messages.
    """
    messages: list[DroneMessage] = _inboxes.pop(drone_id, [])

    serialised = []
    for m in messages:
        entry: dict[str, Any] = {
            "from": m.sender_id,
            "type": m.message_type,
            "payload": m.payload,
            "tick": m.tick,
        }
        if m.recipient_id is not None:
            entry["recipient_id"] = m.recipient_id
        serialised.append(entry)

    return {
        "drone_id": drone_id,
        "message_count": len(serialised),
        "messages": serialised,
    }
