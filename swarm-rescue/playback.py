# playback.py — Post-mission replay system for Swarm Rescue
#
# Reads a completed mission log JSON file and replays the mission visually
# on the IsometricRenderer, step by step.
#
# Usage:
#   python swarm-rescue/playback.py                           # most recent log
#   python swarm-rescue/playback.py path/to/mission.json     # specific log
#   python swarm-rescue/playback.py --speed 2.0              # faster playback

from __future__ import annotations

import ast
import json
import math
import os
import re
import sys
import time
import argparse
import glob as glob_module
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Path bootstrap — allow running from repo root or swarm-rescue/ directly
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)

from simulation.world import World
from simulation.drone import Drone, DroneRole, DroneStatus
from simulation.survivor import Survivor
from simulation.renderer import IsometricRenderer
from config import BASE_X, BASE_Y, SCAN_RADIUS, NUM_DRONES, NUM_SURVIVORS


# ---------------------------------------------------------------------------
# Scenario → terrain file mapping
# ---------------------------------------------------------------------------

_SCENARIO_TERRAIN = {
    "palu_earthquake":  "terrain_data/sulawesi_earthquake.json",
    "semeru_eruption":  "terrain_data/sulawesi_earthquake.json",
    "jakarta_flood":    "terrain_data/sulawesi_earthquake.json",
    "simple_test":      "terrain_data/simple_test.json",
}

_ROLE_MAP = {
    "scout":  DroneRole.SCOUT,
    "rescue": DroneRole.RESCUE,
    "relay":  DroneRole.RELAY,
    "idle":   DroneRole.IDLE,
}

_STATUS_MAP = {
    "active":    DroneStatus.ACTIVE,
    "returning": DroneStatus.RETURNING,
    "charging":  DroneStatus.CHARGING,
    "offline":   DroneStatus.OFFLINE,
}


# ---------------------------------------------------------------------------
# Parsed event dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PlaybackEvent:
    """One atomic playback action extracted from a log entry."""
    turn: int
    kind: str           # move_step | scan | confirm | role | leader | supply | msg | reasoning
    drone_id: str = ""
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Log parser
# ---------------------------------------------------------------------------

def _try_parse_json(text: str) -> Optional[dict]:
    """Attempt to extract and parse the first JSON object found in text."""
    # Find the outermost { } in the string
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _extract_tool_result_json(content_str: str) -> Optional[dict]:
    """Extract the inner JSON from a tool result content string.

    Tool result messages look like:
        [{'type': 'text', 'text': '{"success":true,...}'}]
    The content_str is already the string value of the 'content' field.
    """
    # The content is a Python list repr; find the 'text' key value
    m = re.search(r"'text':\s*'(.*?)'(?:,|\s*\})", content_str, re.DOTALL)
    if not m:
        # Try double-quoted text
        m = re.search(r'"text":\s*"(.*?)"(?:,|\s*\})', content_str, re.DOTALL)
    if m:
        raw = m.group(1)
        # Unescape single-quote escaped JSON
        raw = raw.replace("\\'", "'").replace("\\\\", "\\")
        return _try_parse_json(raw)
    return _try_parse_json(content_str)


def _parse_message_string(msg_str: str) -> dict:
    """Parse a stringified LangChain message back into a usable dict.

    Messages in the log are stored as Python repr strings like:
        content='...' additional_kwargs={'function_call': ...} ...
    We use regex + AST literal eval on extracted fields.
    """
    result = {"role": "unknown", "content": "", "tool_calls": [], "name": None}

    # Detect role from class hint embedded in the string or from structure
    if "tool_call_id=" in msg_str:
        result["role"] = "tool"
    elif "function_call" in msg_str or "tool_calls=[" in msg_str:
        result["role"] = "ai"
    elif msg_str.startswith("content='") or msg_str.startswith('content="'):
        result["role"] = "human"

    # Extract content
    # Try: content='...' (single-quote delimited, with \' escapes)
    m = re.match(r"content='((?:[^'\\]|\\.)*)'\s", msg_str, re.DOTALL)
    if m:
        result["content"] = m.group(1).replace("\\'", "'").replace("\\n", "\n")
    else:
        m = re.match(r'content="((?:[^"\\]|\\.)*)"', msg_str, re.DOTALL)
        if m:
            result["content"] = m.group(1).replace('\\"', '"').replace("\\n", "\n")

    # Extract tool_calls list for AI messages
    tc_match = re.search(r"tool_calls=(\[.*?\])\s+invalid_tool_calls=", msg_str, re.DOTALL)
    if tc_match:
        try:
            tc_list = ast.literal_eval(tc_match.group(1))
            result["tool_calls"] = tc_list
        except (ValueError, SyntaxError):
            pass

    # Extract tool name for tool result messages
    name_m = re.search(r"\bname='([^']+)'", msg_str)
    if name_m:
        result["name"] = name_m.group(1)

    return result


def parse_messages_to_events(messages: list[str]) -> list[PlaybackEvent]:
    """Convert the raw messages list from the log file into PlaybackEvent objects.

    Each message string is a repr of a LangChain message. We walk through:
    - AI messages: extract tool_calls (turn increments on human messages)
    - Tool result messages: extract JSON results for moves/scans/etc.
    - Human messages: increment turn counter
    """
    events: list[PlaybackEvent] = []
    turn = 0
    # We need to pair tool calls with their results so we can extract positions.
    # Build a map from tool_call_id -> (tool_name, args) as we encounter AI msgs.
    pending_calls: dict[str, tuple[str, dict]] = {}  # id -> (name, args)

    for raw_msg in messages:
        msg = _parse_message_string(str(raw_msg))
        role = msg["role"]

        if role == "human":
            turn += 1

        elif role == "ai":
            for tc in msg.get("tool_calls", []):
                if not isinstance(tc, dict):
                    continue
                name = tc.get("name", "")
                args = tc.get("args", {})
                tc_id = tc.get("id", "")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                pending_calls[tc_id] = (name, args)

                # emit reasoning events for non-tool content
                content = msg.get("content", "").strip()
                if content and content not in ("", "None"):
                    events.append(PlaybackEvent(
                        turn=turn,
                        kind="reasoning",
                        data={"text": content},
                    ))

        elif role == "tool":
            content = msg.get("content", "")
            tool_name = msg.get("name", "")

            # Match back to the originating call to get args
            # Find tool_call_id in the raw string
            tc_id_m = re.search(r"tool_call_id='([^']+)'", str(raw_msg))
            tc_id = tc_id_m.group(1) if tc_id_m else ""
            orig_name, orig_args = pending_calls.pop(tc_id, (tool_name, {}))

            result_json = _extract_tool_result_json(str(content))

            _process_tool_result(events, turn, orig_name or tool_name, orig_args, result_json)

    return events


def _process_tool_result(
    events: list[PlaybackEvent],
    turn: int,
    tool_name: str,
    args: dict,
    result: Optional[dict],
) -> None:
    """Translate a single tool call + result into PlaybackEvent(s)."""
    if result is None:
        return

    drone_id = args.get("drone_id", "")

    if tool_name == "move_to":
        if not result.get("success"):
            return
        steps = result.get("steps", [])
        from_pos = result.get("from", [BASE_X, BASE_Y])
        new_pos = result.get("new_position", from_pos)
        battery = result.get("battery_remaining", None)

        # Build an ordered step list: from -> step1 -> step2 -> ... -> new_pos
        all_steps = [from_pos] + steps
        if not steps:
            all_steps = [from_pos, new_pos]

        for i, step in enumerate(all_steps[1:], 1):
            events.append(PlaybackEvent(
                turn=turn,
                kind="move_step",
                drone_id=drone_id,
                data={
                    "from": all_steps[i - 1],
                    "to": step,
                    "final": (i == len(all_steps) - 1),
                    "battery": battery if (i == len(all_steps) - 1) else None,
                },
            ))

    elif tool_name == "thermal_scan":
        scan_center = result.get("scan_center", [])
        fog_cleared = result.get("fog_cleared", [])
        if not scan_center and drone_id:
            # scan center not in result? use drone pos
            pass
        events.append(PlaybackEvent(
            turn=turn,
            kind="scan",
            drone_id=drone_id,
            data={
                "center": scan_center,
                "fog_cleared": fog_cleared,
                "battery": result.get("battery_remaining"),
            },
        ))

    elif tool_name == "confirm_survivor":
        confirmed = result.get("confirmed", False)
        pos = result.get("position", [args.get("x", 0), args.get("y", 0)])
        events.append(PlaybackEvent(
            turn=turn,
            kind="confirm",
            drone_id=drone_id,
            data={
                "confirmed": confirmed,
                "x": pos[0] if pos else args.get("x", 0),
                "y": pos[1] if pos else args.get("y", 0),
                "survivor_id": result.get("survivor_id"),
            },
        ))

    elif tool_name == "assign_role":
        new_role = result.get("new_role") or args.get("role", "")
        events.append(PlaybackEvent(
            turn=turn,
            kind="role",
            drone_id=drone_id,
            data={"role": new_role},
        ))

    elif tool_name == "elect_leader":
        leader = result.get("elected_leader", "")
        events.append(PlaybackEvent(
            turn=turn,
            kind="leader",
            drone_id=leader,
            data={"scores": result.get("scores", {})},
        ))

    elif tool_name in ("drop_supply", "load_supplies"):
        events.append(PlaybackEvent(
            turn=turn,
            kind="supply",
            drone_id=drone_id,
            data={
                "action": tool_name,
                "x": args.get("x"),
                "y": args.get("y"),
                "result": result,
            },
        ))

    elif tool_name in ("broadcast_msg", "send_direct"):
        events.append(PlaybackEvent(
            turn=turn,
            kind="msg",
            drone_id=drone_id,
            data={
                "tool": tool_name,
                "target": args.get("target_drone_id", "ALL"),
                "message": args.get("message", ""),
            },
        ))

    elif tool_name == "recall_drone":
        # Set drone to returning status
        events.append(PlaybackEvent(
            turn=turn,
            kind="recall",
            drone_id=drone_id,
            data={"status": "returning"},
        ))

    elif tool_name == "discover_fleet":
        fleet = result.get("fleet", [])
        events.append(PlaybackEvent(
            turn=turn,
            kind="discover",
            data={"fleet": fleet},
        ))


# ---------------------------------------------------------------------------
# Playback state machine
# ---------------------------------------------------------------------------

@dataclass
class PlaybackState:
    """Tracks the current playback position and timing."""
    events: list[PlaybackEvent]
    current_event_idx: int = 0
    paused: bool = False
    speed: float = 1.0                # multiplier (2.0 = twice as fast)
    last_advance_time: float = 0.0

    # Animation sub-state for move events
    animating_move: bool = False
    move_anim_start_time: float = 0.0
    move_anim_duration: float = 0.4   # seconds per cell (at speed 1.0)

    # Confirmed survivor positions collected during playback
    confirmed_positions: list[tuple[int, int]] = field(default_factory=list)

    # Flash effects: list of (x, y, color, expire_time)
    flashes: list[tuple[int, int, tuple, float]] = field(default_factory=list)

    # Communication lines: list of (x0, y0, x1, y1, expire_time)
    comm_lines: list[tuple[int, int, int, int, float]] = field(default_factory=list)

    # HUD overlay text
    current_tool: str = ""
    current_reasoning: str = ""

    # Stats
    total_turns: int = 0
    coverage_pct: float = 0.0
    survivors_found: int = 0
    survivors_rescued: int = 0

    @property
    def done(self) -> bool:
        return self.current_event_idx >= len(self.events)

    def step_back(self) -> None:
        """Step back is approximate — we just rewind the index by some events."""
        # Find the previous turn boundary
        if self.current_event_idx == 0:
            return
        target_turn = self.events[max(0, self.current_event_idx - 1)].turn - 1
        if target_turn < 1:
            target_turn = 1
        # Find first event of that turn
        for i, ev in enumerate(self.events):
            if ev.turn >= target_turn:
                self.current_event_idx = i
                return
        self.current_event_idx = 0

    def peek(self) -> Optional[PlaybackEvent]:
        if self.current_event_idx < len(self.events):
            return self.events[self.current_event_idx]
        return None

    def advance(self) -> Optional[PlaybackEvent]:
        if self.current_event_idx < len(self.events):
            ev = self.events[self.current_event_idx]
            self.current_event_idx += 1
            return ev
        return None

    def add_flash(self, x: int, y: int, color: tuple, duration: float = 1.5) -> None:
        self.flashes.append((x, y, color, time.time() + duration))

    def add_comm_line(self, x0: int, y0: int, x1: int, y1: int, duration: float = 1.0) -> None:
        self.comm_lines.append((x0, y0, x1, y1, time.time() + duration))

    def expire_effects(self) -> None:
        now = time.time()
        self.flashes = [(x, y, c, e) for x, y, c, e in self.flashes if e > now]
        self.comm_lines = [(x0, y0, x1, y1, e) for x0, y0, x1, y1, e in self.comm_lines if e > now]


# ---------------------------------------------------------------------------
# Playback controller
# ---------------------------------------------------------------------------

class PlaybackController:
    """Orchestrates the replay: mutates world state based on events,
    drives IsometricRenderer, handles keyboard controls."""

    # How long to dwell between events (seconds, at speed 1.0)
    _DWELL_PER_TURN = 0.8       # pause between LLM turns
    _DWELL_PER_EVENT = 0.15     # pause between individual events within a turn
    _MOVE_CELL_DURATION = 0.25  # seconds to animate one cell movement
    _SCAN_DURATION = 0.6        # seconds to show scan flash

    def __init__(self, world: World, events: list[PlaybackEvent],
                 speed: float = 1.0, log_meta: dict = None) -> None:
        self.world = world
        self.state = PlaybackState(events=events, speed=speed)
        self.log_meta = log_meta or {}

        # Compute max turn
        self.state.total_turns = max((e.turn for e in events), default=0)

        # Renderer
        self.renderer = IsometricRenderer(world)
        self.renderer.running = True

        # Track drone positions for comm lines
        self._drone_positions: dict[str, list[int]] = {
            did: [BASE_X, BASE_Y] for did in world.drones
        }

        # Track survivors we've placed (for confirmed positions)
        self._placed_survivors: dict[tuple[int, int], str] = {}  # (x,y) -> survivor_id

        # Last event dwell timer
        self._last_event_time = time.time()
        self._current_dwell = 0.0
        self._move_queue: list[tuple[str, list[int], list[int]]] = []  # (drone_id, from, to)
        self._move_start = 0.0
        self._move_dur = 0.0

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, record: bool = False) -> None:
        pg = self.renderer._pygame
        FPS = self.renderer.FPS

        if record:
            self.renderer.start_recording()

        while self.renderer.running:
            # Handle pygame events (camera controls + playback controls)
            if not self._handle_all_events():
                break

            now = time.time()

            # Advance playback if not paused and dwell has elapsed
            if not self.state.paused and not self.state.done:
                if now - self._last_event_time >= self._current_dwell / self.state.speed:
                    self._process_next_events()

            # Expire visual effects
            self.state.expire_effects()

            # Render
            self._draw_frame()

            # Capture frame for video recording
            if self.renderer._ffmpeg_proc is not None:
                self.renderer._capture_frame()

            self.renderer.clock.tick(FPS)

        # Stop recording on exit
        if self.renderer._ffmpeg_proc is not None:
            self.renderer.stop_recording()

        pg.quit()

    def _handle_all_events(self) -> bool:
        """Handle both renderer camera events and playback keyboard controls."""
        pg = self.renderer._pygame

        for event in pg.event.get():
            if event.type == pg.QUIT:
                self.renderer.running = False
                return False

            # Renderer camera controls
            if event.type == pg.KEYDOWN:
                if event.key == pg.K_r:
                    self.renderer._reset_camera()
                elif event.key == pg.K_g:
                    self.renderer.show_grid = not self.renderer.show_grid

                # Playback controls
                elif event.key == pg.K_SPACE:
                    self.state.paused = not self.state.paused

                elif event.key == pg.K_RIGHT:
                    # Step forward one turn
                    self._step_forward_one_turn()

                elif event.key == pg.K_LEFT:
                    # Step back is not easily reversible — restart and fast-forward
                    # For simplicity, just rewind index and signal a rebuild
                    self._restart_to_turn(
                        max(1, self._current_turn() - 1)
                    )

                elif event.key == pg.K_EQUALS or event.key == pg.K_PLUS:
                    self.state.speed = min(8.0, self.state.speed * 1.5)

                elif event.key == pg.K_MINUS:
                    self.state.speed = max(0.25, self.state.speed / 1.5)

                elif event.key == pg.K_F9:
                    # Toggle video recording
                    if self.renderer._ffmpeg_proc is not None:
                        self.renderer.stop_recording()
                    else:
                        self.renderer.start_recording()

                elif event.key == pg.K_ESCAPE:
                    self.renderer.running = False
                    return False

            if event.type == pg.MOUSEWHEEL:
                self.renderer._handle_zoom(event.y)

            # Right-click drag
            if event.type == pg.MOUSEBUTTONDOWN and event.button == 3:
                self.renderer._rotating = True
                self.renderer._rotate_last_x = event.pos[0]
            if event.type == pg.MOUSEBUTTONUP and event.button == 3:
                self.renderer._rotating = False
            if event.type == pg.MOUSEMOTION and self.renderer._rotating:
                dx = event.pos[0] - self.renderer._rotate_last_x
                self.renderer.cam_angle += dx * 0.005
                self.renderer._rotate_last_x = event.pos[0]

        # Held-key camera panning (WASD + Q/E always work; arrow keys only pan
        # when paused so they don't conflict with the step controls above)
        keys = pg.key.get_pressed()
        sp = self.renderer.CAMERA_SPEED
        if keys[pg.K_a]: self.renderer.camera_x += sp
        if keys[pg.K_d]: self.renderer.camera_x -= sp
        if keys[pg.K_w]: self.renderer.camera_y += sp
        if keys[pg.K_s]: self.renderer.camera_y -= sp
        if keys[pg.K_q]: self.renderer.cam_angle -= 0.02
        if keys[pg.K_e]: self.renderer.cam_angle += 0.02
        # Arrow keys pan only when paused (during playback they step turns via KEYDOWN)
        if self.state.paused:
            if keys[pg.K_LEFT]:  self.renderer.camera_x += sp
            if keys[pg.K_RIGHT]: self.renderer.camera_x -= sp
            if keys[pg.K_UP]:    self.renderer.camera_y += sp
            if keys[pg.K_DOWN]:  self.renderer.camera_y -= sp

        return True

    def _current_turn(self) -> int:
        ev = self.state.peek()
        if ev:
            return ev.turn
        if self.state.events:
            return self.state.events[-1].turn
        return 0

    def _step_forward_one_turn(self) -> None:
        """Process all remaining events of the current turn immediately."""
        if self.state.done:
            return
        current = self._current_turn()
        while not self.state.done:
            ev = self.state.peek()
            if ev is None or ev.turn > current:
                break
            ev = self.state.advance()
            self._apply_event(ev)
        self._last_event_time = time.time()
        self._current_dwell = self._DWELL_PER_TURN

    def _restart_to_turn(self, target_turn: int) -> None:
        """Restart simulation from scratch and fast-forward to target_turn."""
        # Reset world state
        self._rebuild_world()
        self.state.survivors_found = 0
        self.state.survivors_rescued = 0
        self.state.coverage_pct = 0.0
        self.state.confirmed_positions.clear()
        self.state.flashes.clear()
        self.state.comm_lines.clear()

        # Apply all events up to (but not including) target_turn silently
        new_idx = len(self.state.events)  # default: end of list
        for i, ev in enumerate(self.state.events):
            if ev.turn > target_turn:
                new_idx = i
                break
            self._apply_event(ev)

        self.state.current_event_idx = new_idx
        self._last_event_time = time.time()
        self._current_dwell = self._DWELL_PER_TURN

    def _rebuild_world(self) -> None:
        """Reset drone positions to base and clear fog."""
        from config import BASE_X, BASE_Y
        for drone in self.world.drones.values():
            drone.x = BASE_X
            drone.y = BASE_Y
            drone.battery = 100.0
            drone.role = DroneRole.IDLE
            drone.status = DroneStatus.ACTIVE
            drone.is_leader = False
            drone.cargo = None
        for s in self.world.survivors:
            s.rescued = False
        # Reset fog
        for row in self.world.fog_of_war.grid:
            for i in range(len(row)):
                row[i] = False
        self.world.fog_of_war.reveal(BASE_X, BASE_Y, SCAN_RADIUS)
        self.world.rescued_count = 0
        self._drone_positions = {did: [BASE_X, BASE_Y] for did in self.world.drones}

    # ------------------------------------------------------------------
    # Event processing
    # ------------------------------------------------------------------

    def _process_next_events(self) -> None:
        """Process the next batch of events, respecting dwell times."""
        ev = self.state.peek()
        if ev is None:
            return

        self._apply_event(self.state.advance())
        self._last_event_time = time.time()

        # Determine dwell based on event type
        next_ev = self.state.peek()
        if next_ev is None:
            self._current_dwell = self._DWELL_PER_TURN
        elif next_ev.turn != ev.turn:
            # Turn boundary — longer pause
            self._current_dwell = self._DWELL_PER_TURN
        else:
            self._current_dwell = self._DWELL_PER_EVENT

    def _apply_event(self, ev: PlaybackEvent) -> None:
        """Apply a single PlaybackEvent to the world state."""
        self.state.current_tool = ev.kind
        now = time.time()

        if ev.kind == "move_step":
            drone_id = ev.drone_id
            to = ev.data.get("to", [0, 0])
            battery = ev.data.get("battery")

            if drone_id in self.world.drones:
                drone = self.world.drones[drone_id]
                drone.x = int(to[0])
                drone.y = int(to[1])
                if battery is not None:
                    drone.battery = float(battery)
                self._drone_positions[drone_id] = [drone.x, drone.y]

        elif ev.kind == "scan":
            drone_id = ev.drone_id
            fog_cleared = ev.data.get("fog_cleared", [])
            battery = ev.data.get("battery")
            center = ev.data.get("center", [])

            # Reveal fog cells
            for cell in fog_cleared:
                if isinstance(cell, (list, tuple)) and len(cell) >= 2:
                    cx, cy = int(cell[0]), int(cell[1])
                    if 0 <= cx < self.world.terrain.width and 0 <= cy < self.world.terrain.height:
                        self.world.fog_of_war.grid[cy][cx] = True

            # If no explicit fog_cleared, use radius reveal
            if not fog_cleared and center and len(center) >= 2:
                self.world.fog_of_war.reveal(int(center[0]), int(center[1]), SCAN_RADIUS)

            if battery is not None and drone_id in self.world.drones:
                self.world.drones[drone_id].battery = float(battery)

            # Flash effect on scan center
            if center and len(center) >= 2:
                self.state.add_flash(int(center[0]), int(center[1]),
                                     (100, 200, 255), self._SCAN_DURATION / self.state.speed)

            self.state.coverage_pct = self.world.fog_of_war.coverage_percent()
            self.state.current_tool = f"thermal_scan ({drone_id})"

        elif ev.kind == "confirm":
            x, y = int(ev.data.get("x", 0)), int(ev.data.get("y", 0))
            confirmed = ev.data.get("confirmed", False)

            if confirmed:
                # Place a survivor at this position if not already there
                pos_key = (x, y)
                if pos_key not in self._placed_survivors:
                    # Find an unplaced survivor slot
                    for s in self.world.survivors:
                        if not s.rescued and (s.x, s.y) == (BASE_X, BASE_Y):
                            s.x = x
                            s.y = y
                            self._placed_survivors[pos_key] = s.survivor_id
                            break
                    else:
                        # All survivors are placed; add confirmation flash only
                        pass
                self.state.survivors_found += 1
                self.state.add_flash(x, y, (255, 80, 80), 2.0)
                self.state.current_tool = f"confirm_survivor FOUND at ({x},{y})"
            else:
                self.state.add_flash(x, y, (200, 200, 50), 0.8)
                self.state.current_tool = f"confirm_survivor FALSE_POS at ({x},{y})"

        elif ev.kind == "role":
            drone_id = ev.drone_id
            role_str = ev.data.get("role", "idle")
            if drone_id in self.world.drones:
                self.world.drones[drone_id].role = _ROLE_MAP.get(role_str, DroneRole.IDLE)

        elif ev.kind == "leader":
            leader_id = ev.drone_id
            for did, drone in self.world.drones.items():
                drone.is_leader = (did == leader_id)
            self.state.current_tool = f"elect_leader -> {leader_id}"

        elif ev.kind == "supply":
            action = ev.data.get("action", "")
            drone_id = ev.drone_id
            x, y = ev.data.get("x"), ev.data.get("y")

            if action == "load_supplies":
                if drone_id in self.world.drones:
                    self.world.drones[drone_id].cargo = "supplies"
                self.state.current_tool = f"load_supplies ({drone_id})"

            elif action == "drop_supply":
                if drone_id in self.world.drones:
                    self.world.drones[drone_id].cargo = None
                # Mark survivor rescued if at this position
                if x is not None and y is not None:
                    for s in self.world.survivors:
                        if not s.rescued and s.x == int(x) and s.y == int(y):
                            s.rescued = True
                            self.world.rescued_count += 1
                            self.state.survivors_rescued += 1
                            break
                    if x is not None:
                        self.state.add_flash(int(x), int(y), (100, 255, 100), 2.0)
                self.state.current_tool = f"drop_supply at ({x},{y})"

        elif ev.kind == "msg":
            drone_id = ev.drone_id
            target = ev.data.get("target", "ALL")

            # Draw a communication line between sender and target
            if drone_id in self._drone_positions:
                sx, sy = self._drone_positions[drone_id]
                if target != "ALL" and target in self._drone_positions:
                    tx, ty = self._drone_positions[target]
                    self.state.add_comm_line(sx, sy, tx, ty, 1.2)
                else:
                    # Broadcast: flash the sender
                    self.state.add_flash(sx, sy, (255, 255, 100), 0.6)

            self.state.current_tool = f"{ev.data.get('tool')} from {drone_id}"

        elif ev.kind == "recall":
            drone_id = ev.drone_id
            if drone_id in self.world.drones:
                self.world.drones[drone_id].status = DroneStatus.RETURNING

        elif ev.kind == "discover":
            fleet = ev.data.get("fleet", [])
            # Sync initial positions (all at base, battery 100)
            for entry in fleet:
                did = entry.get("drone_id", "")
                if did and did not in self.world.drones:
                    # This shouldn't happen but handle gracefully
                    pass

        elif ev.kind == "reasoning":
            text = ev.data.get("text", "")
            # Show first 200 chars of reasoning in HUD
            self.state.current_reasoning = text[:200].replace("\n", " ")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _draw_frame(self) -> None:
        """Render the world + overlays via the IsometricRenderer."""
        pg = self.renderer._pygame

        # Standard renderer frame
        self.renderer._cos_a = math.cos(self.renderer.cam_angle)
        self.renderer._sin_a = math.sin(self.renderer.cam_angle)
        self.renderer._south_vis = (
            math.cos(self.renderer.cam_angle) - math.sin(self.renderer.cam_angle)
        ) > 0
        self.renderer._east_vis = (
            math.cos(self.renderer.cam_angle) + math.sin(self.renderer.cam_angle)
        ) > 0

        # Background
        for y_band in range(0, self.renderer.SCREEN_H, 4):
            t = abs(y_band - self.renderer.SCREEN_H // 2) / max(1, self.renderer.SCREEN_H // 2)
            v = int(15 + (1.0 - t) * 10)
            pg.draw.rect(self.renderer.screen, (v, v, v + 8),
                         (0, y_band, self.renderer.SCREEN_W, 4))

        # Entity lookup for renderer
        drone_cells: dict[tuple[int, int], list] = {}
        for drone in self.world.drones.values():
            drone_cells.setdefault((drone.x, drone.y), []).append(drone)
        survivor_cells: dict[tuple[int, int], list] = {}
        for s in self.world.survivors:
            if not s.rescued:
                survivor_cells.setdefault((s.x, s.y), []).append(s)

        # Sort tiles back-to-front
        w = self.world.terrain.width
        h = self.world.terrain.height
        tiles = [(x, y) for y in range(h) for x in range(w)]
        tiles.sort(key=lambda t: self.renderer._tile_depth(t[0], t[1]))

        for gx, gy in tiles:
            self.renderer._draw_tile(gx, gy, drone_cells, survivor_cells)

        # Draw communication lines
        self._draw_comm_lines()

        # Draw flash effects
        self._draw_flashes()

        # Draw playback HUD (replaces normal HUD)
        self._draw_playback_hud()

        pg.display.flip()
        self.renderer._frame += 1

    def _project(self, wx: float, wy: float, wz: float = 0) -> tuple[int, int]:
        """Delegate projection to renderer."""
        return self.renderer._project(wx, wy, wz)

    def _draw_comm_lines(self) -> None:
        """Draw communication lines between drones."""
        pg = self.renderer._pygame
        now = time.time()
        for x0, y0, x1, y1, expire in self.state.comm_lines:
            alpha = min(1.0, (expire - now) / 1.0)
            color = tuple(int(c * alpha) for c in (255, 255, 100))
            # Get terrain elevations for projection
            elev0 = self.world.terrain.elevation(x0, y0)
            elev1 = self.world.terrain.elevation(x1, y1)
            p0 = self._project(x0 + 0.5, y0 + 0.5, elev0 + 1.5)
            p1 = self._project(x1 + 0.5, y1 + 0.5, elev1 + 1.5)
            if alpha > 0.05:
                pg.draw.line(self.renderer.screen, color, p0, p1, 2)

    def _draw_flashes(self) -> None:
        """Draw pulsing highlight circles on flash positions."""
        pg = self.renderer._pygame
        now = time.time()
        for x, y, color, expire in self.state.flashes:
            alpha = min(1.0, (expire - now) / max(0.001, expire - (now - 1.5)))
            # Clamp alpha
            alpha = max(0.0, min(1.0, alpha))
            radius = int(12 * self.renderer.zoom * (0.8 + 0.2 * math.sin(now * 8)))
            elev = self.world.terrain.elevation(x, y)
            sx, sy = self._project(x + 0.5, y + 0.5, elev + 0.5)
            draw_color = tuple(min(255, int(c * alpha + 50 * (1 - alpha))) for c in color)
            if radius > 0:
                # Draw outer ring
                pg.draw.circle(self.renderer.screen, draw_color, (sx, sy), radius, 2)
                # Draw inner dot
                pg.draw.circle(self.renderer.screen, draw_color, (sx, sy), max(3, radius // 3))

    def _draw_playback_hud(self) -> None:
        """Draw the playback-specific HUD overlay."""
        pg = self.renderer._pygame
        font_sm = self.renderer._font_sm
        font_md = self.renderer._font_md
        W = self.renderer.SCREEN_W
        H = self.renderer.SCREEN_H

        PAD = 10
        PANEL_W = 320
        PANEL_H = 200
        LINE_H = 18

        # Semi-transparent panel (draw filled rect with lower alpha via surface)
        panel_surf = pg.Surface((PANEL_W, PANEL_H), pg.SRCALPHA)
        panel_surf.fill((15, 15, 30, 210))
        self.renderer.screen.blit(panel_surf, (PAD, PAD))

        # Title
        scenario = self.log_meta.get("scenario_name", self.log_meta.get("scenario", ""))
        title_text = font_md.render(f"REPLAY: {scenario}", True, (200, 220, 255))
        self.renderer.screen.blit(title_text, (PAD + 8, PAD + 6))

        # Progress bar
        ev_total = len(self.state.events)
        ev_done = self.state.current_event_idx
        progress = ev_done / max(1, ev_total)
        bar_x = PAD + 8
        bar_y = PAD + 28
        bar_w = PANEL_W - 16
        bar_h = 8
        pg.draw.rect(self.renderer.screen, (50, 50, 80), (bar_x, bar_y, bar_w, bar_h))
        pg.draw.rect(self.renderer.screen, (80, 180, 80),
                     (bar_x, bar_y, int(bar_w * progress), bar_h))

        # Turn / event info
        current_turn = self._current_turn() if not self.state.done else self.state.total_turns
        lines = [
            f"Turn: {current_turn} / {self.state.total_turns}",
            f"Event: {ev_done} / {ev_total}",
            f"Speed: {self.state.speed:.1f}x  {'[PAUSED]' if self.state.paused else '[PLAYING]'}",
            f"Coverage: {self.state.coverage_pct:.1f}%",
            f"Survivors: found={self.state.survivors_found}  "
            f"rescued={self.world.rescued_count}",
            f"Tool: {self.state.current_tool[:40]}",
        ]

        y_off = PAD + 44
        for line in lines:
            surf = font_sm.render(line, True, (180, 200, 220))
            self.renderer.screen.blit(surf, (PAD + 8, y_off))
            y_off += LINE_H

        # Controls hint at the bottom of the panel
        hint = font_sm.render(
            "SPACE=pause  ARROWS=step/seek  +/-=speed  R=reset cam  ESC=quit",
            True, (120, 130, 150),
        )
        self.renderer.screen.blit(hint, (PAD + 8, PAD + PANEL_H - 20))

        # Reasoning text panel (bottom of screen)
        if self.state.current_reasoning:
            reason_surf = pg.Surface((W - PAD * 2, 40), pg.SRCALPHA)
            reason_surf.fill((10, 10, 20, 190))
            self.renderer.screen.blit(reason_surf, (PAD, H - 50))
            r_text = font_sm.render(
                f"AI: {self.state.current_reasoning[:120]}",
                True, (180, 255, 180),
            )
            self.renderer.screen.blit(r_text, (PAD + 6, H - 42))

        # "DONE" banner when playback completes
        if self.state.done:
            done_surf = pg.Surface((400, 60), pg.SRCALPHA)
            done_surf.fill((10, 80, 10, 220))
            dx = W // 2 - 200
            dy = H // 2 - 30
            self.renderer.screen.blit(done_surf, (dx, dy))
            result_text = "SUCCESS" if self.log_meta.get("result", {}).get("success") else "MISSION ENDED"
            done_label = font_md.render(
                f"REPLAY COMPLETE — {result_text}",
                True, (100, 255, 100),
            )
            self.renderer.screen.blit(done_label, (dx + 20, dy + 10))
            stats_label = font_sm.render(
                f"Rescued: {self.world.rescued_count}/{len(self.world.survivors)}  "
                f"Coverage: {self.state.coverage_pct:.1f}%  "
                f"Turns: {self.state.total_turns}",
                True, (200, 230, 200),
            )
            self.renderer.screen.blit(stats_label, (dx + 20, dy + 34))


# ---------------------------------------------------------------------------
# Log file loading
# ---------------------------------------------------------------------------

def load_log_file(path: str) -> dict:
    """Load and return a mission log JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_most_recent_log(log_dir: str) -> Optional[str]:
    """Return the path to the most recently modified mission log JSON."""
    # Use os.listdir instead of glob to avoid issues with special chars
    # like square brackets in directory paths
    try:
        files = [
            os.path.join(log_dir, f)
            for f in os.listdir(log_dir)
            if f.startswith("mission_") and f.endswith(".json")
        ]
    except FileNotFoundError:
        files = []
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def build_world_for_log(log_data: dict) -> World:
    """Create a fresh World instance configured for the given log."""
    scenario = log_data.get("scenario", "palu_earthquake")
    num_drones = log_data.get("drones", NUM_DRONES)
    num_survivors = log_data.get("survivors", NUM_SURVIVORS)

    terrain_rel = _SCENARIO_TERRAIN.get(scenario, _SCENARIO_TERRAIN["palu_earthquake"])
    # Build absolute terrain path relative to this file
    terrain_path = os.path.join(_THIS_DIR, terrain_rel)

    if not os.path.exists(terrain_path):
        # Fallback
        terrain_path = os.path.join(_THIS_DIR, "terrain_data", "sulawesi_earthquake.json")

    # Create world — this spawns random survivors we will override
    world = World(
        terrain_path=terrain_path,
        num_drones=num_drones,
        num_survivors=num_survivors,
    )

    # Override: move all spawned survivors to base (hidden) so they only
    # appear when confirmed via the log replay
    for s in world.survivors:
        s.x = BASE_X
        s.y = BASE_Y
        s.rescued = False

    # Reset fog to fully opaque (only base area revealed)
    for row in world.fog_of_war.grid:
        for i in range(len(row)):
            row[i] = False
    world.fog_of_war.reveal(BASE_X, BASE_Y, SCAN_RADIUS)

    return world


def extract_events_from_log(log_data: dict) -> list[PlaybackEvent]:
    """Extract PlaybackEvent list from a log file dict."""
    result = log_data.get("result", {})
    messages = result.get("messages", [])

    if not messages:
        print("Warning: no messages found in log file result.")
        return []

    events = parse_messages_to_events(messages)
    print(f"Parsed {len(events)} playback events from {len(messages)} messages.")
    return events


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay a completed Swarm Rescue mission log visually."
    )
    parser.add_argument(
        "log_file",
        nargs="?",
        default=None,
        help="Path to mission log JSON. Defaults to most recent in mission_logs/.",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed multiplier (default 1.0; 2.0 = twice as fast).",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Directory to search for log files. Defaults to swarm-rescue/mission_logs/.",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Record the playback to an MP4 video (requires ffmpeg).",
    )
    args = parser.parse_args()

    # Resolve log file path
    log_path = args.log_file
    if log_path is None:
        log_dir = args.log_dir or os.path.join(_THIS_DIR, "mission_logs")
        log_path = find_most_recent_log(log_dir)
        if log_path is None:
            print(f"No mission log files found in {log_dir!r}.")
            print("Run a mission first: python main.py --scenario palu_earthquake")
            sys.exit(1)

    log_path = os.path.abspath(log_path)
    if not os.path.exists(log_path):
        print(f"Log file not found: {log_path!r}")
        sys.exit(1)

    print(f"Loading mission log: {log_path}")
    log_data = load_log_file(log_path)

    scenario = log_data.get("scenario", "unknown")
    scenario_name = log_data.get("scenario_name", scenario)
    drones = log_data.get("drones", 0)
    survivors = log_data.get("survivors", 0)
    success = log_data.get("result", {}).get("success", False)

    print(f"Scenario: {scenario_name}")
    print(f"Fleet: {drones} drones  |  Survivors: {survivors}")
    print(f"Outcome: {'SUCCESS' if success else 'MISSION ENDED'}")
    print()

    # Parse events
    events = extract_events_from_log(log_data)
    if not events:
        print("No playback events could be extracted from this log.")
        print("The log may be from an incompatible version.")
        sys.exit(1)

    # Build world
    print("Initialising world...")
    world = build_world_for_log(log_data)
    print(f"World: {world.terrain.width}x{world.terrain.height} grid  "
          f"|  {len(world.drones)} drones  |  {len(world.survivors)} survivor slots")
    print()
    print("Controls:")
    print("  SPACE       — pause / resume")
    print("  RIGHT arrow — step forward one turn")
    print("  LEFT arrow  — step back one turn (rebuilds from start)")
    print("  +/-         — increase / decrease playback speed")
    print("  WASD/arrows — pan camera (while NOT paused arrows also step turns)")
    print("  Scroll      — zoom in / out")
    print("  Right-drag  — rotate camera")
    print("  R           — reset camera")
    print("  G           — toggle grid overlay")
    print("  F9          — toggle video recording")
    print("  ESC         — quit")
    print()
    print("Starting replay...")

    controller = PlaybackController(
        world=world,
        events=events,
        speed=args.speed,
        log_meta={
            "scenario": scenario,
            "scenario_name": scenario_name,
            "result": log_data.get("result", {}),
        },
    )
    controller.run(record=args.record)


if __name__ == "__main__":
    main()
