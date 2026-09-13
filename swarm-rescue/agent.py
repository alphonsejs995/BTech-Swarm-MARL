# agent.py
# AI Command Agent — the strategic brain of the drone swarm.
#
# Architecture:
#   LangChain + Google Gemini 2.0 Flash
#   LangGraph ReAct agent with multi-turn mission loop
#   MCP client (langchain-mcp-adapters) connecting to mcp_server.py via stdio
#
# All drone communication goes through MCP tools only.
# No direct imports from simulation/, sensors/, or swarm/.

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Suppress Gemini SDK "additionalProperties not supported" warnings
# ---------------------------------------------------------------------------
warnings.filterwarnings("ignore", message=".*additionalProperties.*")
logging.getLogger("google.generativeai").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# Load environment — GEMINI_API_KEY from ../.env, aliased to GOOGLE_API_KEY
# ---------------------------------------------------------------------------
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if _api_key:
    os.environ["GOOGLE_API_KEY"] = _api_key

# ---------------------------------------------------------------------------
# Lazy imports for all heavy AI/MCP dependencies.
# langchain_google_genai, langchain_mcp_adapters, and langgraph are all
# imported inside run_mission() so that `import agent` succeeds even when
# those packages are not yet installed (e.g. during quick lint/CI checks).
# ---------------------------------------------------------------------------


def _build_llm():
    """Construct and return the Gemini LLM instance."""
    from langchain_google_genai import ChatGoogleGenerativeAI  # lazy

    return ChatGoogleGenerativeAI(
        model="gemini-3.6-flash",
        google_api_key=os.environ.get("GOOGLE_API_KEY", ""),
        temperature=0.2,
        max_output_tokens=4096,
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are the Command Agent for a search-and-rescue drone swarm in post-disaster Indonesia.
All actions go through MCP tool calls. No direct simulation access.

CRITICAL RULES:

1. FIRST ACTION: Call discover_fleet() FIRST. Never assume drone IDs.

2. CHAIN-OF-THOUGHT — MANDATORY every turn, formatted exactly as:
   SITUATION: [coverage %, rescued count, battery levels, known survivors]
   PRIORITIES: [top 3 actions ranked by urgency]
   ACTIONS: [what tools you will call and why each one]
   You MUST write this block before every set of tool calls. Skipping it means mission failure.

3. BATCH TOOL CALLS AGGRESSIVELY: Call as many INDEPENDENT tools as possible in a SINGLE response.
   - After discover_fleet(), call elect_leader() AND assign_role() for ALL drones in ONE response.
   - Move ALL scouts simultaneously — do not wait for one drone before moving the next.
   - Scan with ALL scouts simultaneously — one response, multiple thermal_scan() calls.
   - Confirm ALL ambiguous hits simultaneously — multiple confirm_survivor() in one response.
   Every wasted LLM turn is a survivor left behind. Maximize parallelism.

4. BATTERY: Recall drones below 20% battery immediately. Use get_path_cost() before long moves.
   At 10% battery a drone is in critical danger — recall it NOW.

5. DECENTRALIZED LEADERSHIP — the swarm is self-healing:
   - Call elect_leader() right after discover_fleet(). The swarm also auto-elects if the leader dies.
   - The leader is NOT a central controller — it is a COORDINATOR. Every drone can operate alone.
   - If the leader goes offline, the swarm AUTOMATICALLY holds a re-election (no agent action needed).
   - If a drone becomes isolated (0 neighbors for 3+ ticks), it AUTONOMOUSLY returns toward the swarm
     centroid. This is built-in firmware — you do NOT need to command it. But you should PREVENT it by
     keeping drones close together in the first place.
   - Use send_direct() for leader-to-drone task assignments. Use get_inbox() to read field reports.
   - Use check_network_health() to verify the mesh network has NO partitions before proceeding.

6. COVERAGE STRATEGY — COHESIVE SWARM SWEEP:
   The map is 20x20 = 400 cells. SCAN_RADIUS=3 means each thermal_scan covers a ~7x7 area (49 cells).
   COMM_RANGE is 5 cells — drones MUST stay within 5 cells of at least one other drone at all times.

   SWARM FORMATION — "fan sweep":
     a) The LEADER stays near the swarm centroid and does NOT move far ahead.
     b) SCOUTS fan out in a spread formation: each scout is 3-4 cells apart from each other,
        but NO scout moves more than 4-5 cells away from the nearest other drone.
     c) The swarm moves as a WAVE across the map. Start from base (0,0) and sweep:
        - Phase 1: sweep east along rows 0-6 (scouts at y=1, y=3, y=5 approximately)
        - Phase 2: shift south, sweep rows 7-13
        - Phase 3: shift south, sweep rows 14-19
     d) At each position, ALL scouts scan simultaneously, then the whole group advances ~5 cells.

   BEFORE EVERY MOVEMENT PHASE: call get_swarm_cohesion() to check:
     - If max_spread > 7: STOP. Regroup isolated drones toward the rally_point before continuing.
     - If isolated_drones is not empty: move those drones closer to the centroid FIRST.
     - Only proceed with the sweep once all_connected is True.

   MOVEMENT PATTERN per phase:
     1. Move all scouts to their fan positions (3-4 cells apart, within comm range)
     2. All scouts call thermal_scan() simultaneously
     3. Advance the whole group ~5 cells in the sweep direction
     4. Repeat scan
     5. After covering a row band, shift the group south and sweep back

   Use get_fog_of_war() to find remaining uncovered sectors and redirect the swarm there.
   Priority terrain: urban rubble > coastline > flood zone > jungle > open ground.
   NEVER send a lone drone to a distant corner — always move as a group.

7. THERMAL SCANNING: Use thermal_scan() at the centre of each sector. Hits with confidence >= 0.7
   MUST be confirmed with confirm_survivor() before deploying rescue. Hits below 0.7 are likely
   false positives — note them but do not send rescue drones without confirmation.

8. RESCUE PIPELINE — follow this EXACT sequence, no shortcuts:
   Step 1: confirm_survivor(drone_id, x, y) — confirm the hit is real
   Step 2: broadcast_msg(leader_id, "SURVIVOR_FOUND at (x,y) urgency=<urgency>") — alert all drones
   Step 3: assign_role(drone_id, 'rescue') — designate the rescue drone
   Step 4: move_to(drone_id, BASE_X, BASE_Y) — return to base to load supplies
   Step 5: load_supplies(drone_id) — REQUIRED before drop_supply, do NOT skip this step
   Step 6: move_to(drone_id, survivor_x, survivor_y) — fly to survivor
   Step 7: drop_supply(drone_id, survivor_x, survivor_y) — deliver supplies
   Step 8: broadcast_msg(leader_id, "SUPPLY_DROPPED at (x,y)") — log the delivery
   WARNING: drop_supply() will fail with "no cargo" if load_supplies() was not called first.
   WARNING: drop_supply() will fail with "too far" if the drone is not adjacent to the survivor.

9. SELF-HEALING MESH NETWORK — the swarm is resilient to failures:
   - BEFORE moving drones: call check_network_health(). If partition_count > 1, the network is SPLIT.
     You must move drones closer together to heal the partition before continuing the mission.
   - The swarm has AUTONOMOUS self-healing: isolated drones auto-return toward the swarm after 3 ticks.
     But this wastes battery and time. PREVENT isolation by keeping max_spread < 7 cells.
   - AFTER any drone goes offline: call check_network_health() to verify the remaining network is intact.
     The swarm auto-elects a new leader if needed, but you should check and adapt your plan.
   - After confirming a survivor: broadcast_msg(leader_id, "SURVIVOR_FOUND at (x,y)")
   - After dropping supplies: broadcast_msg(leader_id, "SUPPLY_DROPPED at (x,y)")
   - Leader to drone task assignment: send_direct(leader_id, target_drone_id, task_message)
   - Periodically: get_inbox(leader_id) to read field reports from scouts
   - For rescue missions far from the swarm: send the rescue drone WITH a relay drone as a pair.
     The relay positions midway to maintain the communication chain. Never send a drone alone.
   - Drones can only communicate within 5-cell radio range. A partitioned network = lost drones.

10. ERROR RECOVERY — READ error messages carefully and take corrective action:
    - "no cargo" error on drop_supply → call load_supplies(drone_id) FIRST, then retry drop_supply
    - "too far" error on drop_supply → call move_to(drone_id, x, y) to get adjacent, then retry
    - "battery critical" error → call recall_drone(drone_id) immediately
    - "drone not found" → call discover_fleet() to re-sync drone IDs
    Do NOT retry the same failing call without fixing the root cause first.

11. USE get_map_state() for full world snapshots. Prefer this over querying drones individually.

12. FAILURE RECOVERY — the swarm adapts when drones are lost:
    - If a drone goes OFFLINE: immediately call discover_fleet() and check_network_health().
    - Redistribute the dead drone's tasks to remaining drones. If it was a scout, another drone takes
      its sector. If it was the leader, verify auto-election happened and continue.
    - If the swarm loses 2+ drones, tighten the formation — reduce max_spread to 5 cells.
    - The mission can succeed even with only 3 of 5 drones. Adapt the sweep pattern to fewer scouts.
    - NEVER panic — the decentralized design means every drone can take any role. Reassign and continue.

MISSION COMPLETE when: coverage >80%, all confirmed survivors have received supplies, no drones lost.
Declare "MISSION COMPLETE" with a debrief: rescued count, final coverage %, fleet health, key decisions made.
"""

# ---------------------------------------------------------------------------
# Mission prompt templates — Indonesian disaster scenarios
# ---------------------------------------------------------------------------
MISSION_PROMPTS: dict[str, str] = {
    "palu_earthquake": """
MISSION BRIEFING — PALU EARTHQUAKE AND TSUNAMI (Central Sulawesi, Indonesia)
=============================================================================
A magnitude 7.5 earthquake has struck Palu, Central Sulawesi at 18:02 local
time. The earthquake triggered a tsunami that has flooded coastal low-lying
areas. Cell towers across the region are down. Ground rescue teams cannot reach
the affected zones due to road damage and ongoing liquefaction.

SCENARIO DETAILS:
  - Disaster type: Earthquake (M7.5) + tsunami flooding
  - Primary hazard zones: Coastal flood zones, urban rubble in city centre
  - Terrain: Mix of coastline, flood zone, urban rubble, and open ground
  - Survivor distribution: Concentrated near coastline and collapsed structures
  - Visibility: Good — no ash or heavy smoke, but flooding limits ground access
  - Urgency: CRITICAL — water is still rising in low-lying areas

MISSION OBJECTIVES:
  1. Deploy and discover your drone fleet from base station (0, 0)
  2. Elect a mission leader
  3. Assign scouts and form a cohesive fan formation (drones 3-4 cells apart)
  4. Sweep the map as a GROUP — move together, scan together, advance together
  5. Call get_swarm_cohesion() before each movement phase — regroup if spread > 7 cells
  6. Confirm all thermal hits and execute supply drops for confirmed survivors
  7. Achieve >80% map coverage before concluding

CRITICAL: Drones must stay within communication range (5 cells) of each other.
In a real disaster with no cell towers, losing radio contact means losing the drone.
Move as a coordinated swarm, not as isolated individuals.

Begin search-and-rescue operations now. Report your step-by-step reasoning
at every decision point. Lives depend on your efficiency.
""",
    "semeru_eruption": """
MISSION BRIEFING — MOUNT SEMERU VOLCANIC ERUPTION (East Java, Indonesia)
=============================================================================
Mount Semeru has erupted unexpectedly at 09:17 local time. A pyroclastic flow
and heavy volcanic ash cloud have moved southeast, covering villages near the
volcano. Visibility is near zero in ash-heavy zones. Ground access is blocked.

SCENARIO DETAILS:
  - Disaster type: Volcanic eruption with pyroclastic flow and ash fall
  - Primary hazard zones: Southeast quadrant (heavy ash), mountain slopes
  - Terrain: Volcanic ash, jungle, mountain, open clearings
  - Survivor distribution: Scattered village clusters, some on high ground
  - Visibility: Severely degraded in ash zones — thermal sensors less reliable
  - Urgency: HIGH — ash inhalation risk, temperature extremes near flow zones

MISSION OBJECTIVES:
  1. Discover your fleet and elect a leader
  2. Avoid sending drones deep into volcanic ash terrain without confirming
     sensor health — ash degrades sensor accuracy
  3. Prioritize clearings and jungle edges where survivors may have fled
  4. Use confirm_survivor() aggressively — false positive rate is higher near
     hot volcanic debris (ambient temperature elevated)
  5. Achieve >80% coverage of reachable terrain

Begin operations. Reason carefully about sensor reliability in ash zones before
committing drones to high-cost movements in degraded terrain.
""",
    "jakarta_flood": """
MISSION BRIEFING — JAKARTA MONSOON FLOODING (North Jakarta, Indonesia)
=============================================================================
Record monsoon rainfall over the past 48 hours has caused severe flooding in
North Jakarta. Water levels have risen 2 metres above street level in the
Penjaringan, Pademangan, and Tanjung Priok districts. Many residents are
stranded on rooftops. Emergency services are overwhelmed.

SCENARIO DETAILS:
  - Disaster type: Urban flash flood (residential and commercial areas)
  - Primary hazard zones: Residential flood zones, low-elevation streets
  - Terrain: Flood zone, urban rubble, some elevated structures
  - Survivor distribution: Rooftop clusters, upper floors of buildings
  - Visibility: Moderate — overcast skies, light rain reducing thermal contrast
  - Urgency: HIGH — exposure, dehydration, risk of structure collapse

MISSION OBJECTIVES:
  1. Discover fleet and elect a leader
  2. Assign scouts to systematically cover residential flood zones
  3. Be thorough — flood water suppresses thermal contrast, so survivors may
     read lower than normal body temperature. Lower your confirmation threshold.
  4. Multiple survivors are likely clustered per building rooftop — check
     survivor count in each confirmation result
  5. Prioritize CRITICAL urgency survivors (longest time stranded)
  6. Achieve >80% coverage before concluding

Begin operations. Rooftop survivors in North Jakarta are counting on you.
Reason carefully and coordinate your fleet efficiently.
""",
    "simple_test": """
MISSION BRIEFING — SIMPLE DETECTION TEST (10x10 Grid)
======================================================
A small 10x10 area needs to be searched for 3 survivors. Your fleet has
3 drones. This is a DETECTION-ONLY mission — find and confirm all survivors
using thermal scans. No supply drops required.

SCENARIO DETAILS:
  - Grid size: 10x10 (100 cells total)
  - Fleet: 3 drones
  - Survivors: 3 (locations unknown)
  - Mission type: DETECTION ONLY — no supply drops needed
  - SCAN_RADIUS=3 means a single well-placed scan covers roughly 49 cells.
    2-3 scans from spread positions should cover the entire 10x10 grid.

MISSION OBJECTIVES:
  1. Discover fleet and elect a leader
  2. Assign all 3 drones as scouts and spread them across the grid
  3. Thermal scan systematically to achieve >80% map coverage
     (spread drones to (2,2), (7,2), (5,7) or similar non-overlapping positions)
  4. Confirm all thermal hits with confirm_survivor()
  5. Use broadcast_msg() to report each SURVIVOR_FOUND
  6. Declare MISSION COMPLETE when coverage >80% and all survivors confirmed

MISSION COMPLETE when: coverage >80% AND all thermal hits have been confirmed.
Supply drops are NOT required for this mission.

Base station is at (0,0). Begin operations.
""",
}

# ---------------------------------------------------------------------------
# Mission log extraction helper
# ---------------------------------------------------------------------------


def extract_mission_log(messages: list) -> str:
    """Parse the agent message history and return a clean, readable mission log
    showing each decision and the reasoning behind it.

    The log format is:
      [TURN N] [TYPE] Content
    """
    lines: list[str] = [
        "=" * 72,
        "SWARM RESCUE MISSION LOG",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "=" * 72,
        "",
    ]

    turn = 0
    for msg in messages:
        # Handle both dict-style and LangChain message objects
        if isinstance(msg, dict):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
        elif hasattr(msg, "type"):
            role = msg.type  # "human", "ai", "tool"
            content = msg.content or ""
        elif hasattr(msg, "role"):
            role = msg.role
            content = msg.content or ""
        else:
            continue

        if role in ("human", "user"):
            turn += 1
            lines.append(f"[TURN {turn}] [PROMPT]")
            lines.append(str(content).strip())
            lines.append("")
        elif role in ("ai", "assistant"):
            # Check for tool calls
            tool_calls = []
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = msg.tool_calls
            elif hasattr(msg, "additional_kwargs"):
                tool_calls = msg.additional_kwargs.get("tool_calls", [])

            if content:
                lines.append(f"[TURN {turn}] [REASONING]")
                lines.append(str(content).strip())
                lines.append("")

            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn_name = tc.get("name") or tc.get("function", {}).get(
                        "name", "unknown"
                    )
                    fn_args = tc.get("args") or tc.get("function", {}).get(
                        "arguments", "{}"
                    )
                else:
                    fn_name = getattr(tc, "name", "unknown")
                    fn_args = getattr(tc, "args", {})

                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except json.JSONDecodeError:
                        pass

                lines.append(f"[TURN {turn}] [TOOL CALL] {fn_name}")
                lines.append(f"  Args: {json.dumps(fn_args, ensure_ascii=False)}")
                lines.append("")

        elif role == "tool":
            # Tool result
            tool_name = getattr(msg, "name", "tool")
            lines.append(f"[TURN {turn}] [TOOL RESULT] {tool_name}")
            result_str = str(content).strip()
            # Truncate very long results for readability
            if len(result_str) > 600:
                result_str = result_str[:597] + "..."
            lines.append(f"  {result_str}")
            lines.append("")

    lines.append("=" * 72)
    lines.append("END OF MISSION LOG")
    lines.append("=" * 72)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Status check prompt (injected every 5 turns)
# ---------------------------------------------------------------------------
STATUS_CHECK_PROMPT = (
    "STATUS CHECK: Call get_map_state(), report coverage/rescued/battery. "
    "If mission criteria met (>80% coverage, all survivors supplied), declare MISSION COMPLETE. "
    "Otherwise state top 3 priorities and continue. Batch your next tool calls."
)

# ---------------------------------------------------------------------------
# End-of-mission nudge prompt (injected at max_turns - 5)
# ---------------------------------------------------------------------------
FINAL_PUSH_PROMPT = (
    "You have 5 turns remaining. If coverage >80% and all confirmed survivors are supplied, "
    "declare MISSION COMPLETE with your debrief now. "
    "Otherwise, focus exclusively on the most critical remaining objectives — "
    "do not start new tasks you cannot finish. Batch all remaining tool calls aggressively."
)


# ---------------------------------------------------------------------------
# Core mission runner
# ---------------------------------------------------------------------------


async def run_mission(scenario: str = "palu_earthquake", max_turns: int = 20) -> dict:
    """Connect to the MCP server, build the agent, and run the rescue mission.

    Args:
        scenario: One of 'palu_earthquake', 'semeru_eruption', 'jakarta_flood'.

    Returns:
        A dict containing:
          - success (bool): whether MISSION COMPLETE was declared
          - messages (list): full conversation history
          - mission_log (str): human-readable step-by-step log
          - turns (int): number of agent turns executed
          - scenario (str): the scenario name
    """
    mission_prompt = MISSION_PROMPTS.get(scenario, MISSION_PROMPTS["palu_earthquake"])

    print(f"\n{'=' * 72}")
    print(f"SWARM RESCUE MISSION — Scenario: {scenario.upper()}")
    print(f"{'=' * 72}\n")
    print(mission_prompt.strip())
    print(f"\n{'=' * 72}\n")

    # Lazy imports — only resolved when actually running a mission
    from langchain_core.messages import (  # noqa: PLC0415
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )
    from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: PLC0415
    from langchain_mcp_adapters.tools import load_mcp_tools  # noqa: PLC0415

    llm = _build_llm()

    SCENARIO_CONFIG = {
        "palu_earthquake": {"terrain": "terrain_data/sulawesi_earthquake.json", "drones": 5, "survivors": 8},
        "semeru_eruption": {"terrain": "terrain_data/sulawesi_earthquake.json", "drones": 5, "survivors": 8},
        "jakarta_flood": {"terrain": "terrain_data/sulawesi_earthquake.json", "drones": 5, "survivors": 8},
        "simple_test": {"terrain": "terrain_data/simple_test.json", "drones": 3, "survivors": 3},
    }

    scenario_cfg = SCENARIO_CONFIG.get(scenario, SCENARIO_CONFIG["palu_earthquake"])
    mcp_env = dict(os.environ)
    mcp_env["SWARM_TERRAIN"] = scenario_cfg["terrain"]
    mcp_env["SWARM_DRONES"] = str(scenario_cfg["drones"])
    mcp_env["SWARM_SURVIVORS"] = str(scenario_cfg["survivors"])

    mcp_server_path = str(Path(__file__).parent / "mcp_server.py")

    mcp_client = MultiServerMCPClient(
        {
            "drone-swarm": {
                "command": sys.executable,
                "args": [mcp_server_path],
                "transport": "stdio",
                "env": mcp_env,
            }
        }
    )

    # Use a persistent session so the MCP server starts ONCE for the whole mission
    async with mcp_client.session("drone-swarm") as session:
        tools = await load_mcp_tools(session)

        print(f"[AGENT] Connected to MCP server. {len(tools)} tools available:")
        for t in tools:
            print(f"  - {t.name}")
        print()

        # Build a tool lookup for executing tool calls locally
        tool_map = {t.name: t for t in tools}

        # Bind tools to the LLM so it can generate tool_calls
        llm_with_tools = llm.bind_tools(tools)

        # Message history: system prompt + mission briefing
        messages: list = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=mission_prompt),
        ]

        mission_complete = False
        api_calls = 0
        # Track whether the final-push nudge has already been injected
        final_push_injected = False

        for turn in range(max_turns):
            print(f"\n{'─' * 60}")
            print(
                f"[AGENT TURN {turn + 1}/{max_turns}] (API calls so far: {api_calls})"
            )
            print(f"{'─' * 60}")

            # ── Step 1: ONE LLM call ──────────────────────────────
            try:
                ai_msg: AIMessage = await llm_with_tools.ainvoke(messages)
                api_calls += 1
                await asyncio.sleep(12)
            except Exception as exc:
                exc_str = str(exc)
                print(f"[ERROR] LLM call failed on turn {turn + 1}: {exc_str}")

                if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str:
                    print("[RATE LIMIT] Waiting 35s for Gemini free-tier reset...")
                    await asyncio.sleep(35)
                    # Add a clean retry message — do NOT dump the raw error JSON
                    messages.append(
                        HumanMessage(
                            content=(
                                "System: Rate limited, retrying. "
                                "Continue your mission plan."
                            )
                        )
                    )
                else:
                    messages.append(
                        HumanMessage(
                            content=(
                                "System error encountered. "
                                "Continue the mission. Batch multiple tool calls."
                            )
                        )
                    )
                continue

            messages.append(ai_msg)

            # Print reasoning
            if ai_msg.content:
                print(f"\n[REASONING]\n{ai_msg.content}")

            # ── Step 2: Execute ALL tool calls locally (no LLM cost)
            if ai_msg.tool_calls:
                for tc in ai_msg.tool_calls:
                    tc_name = tc["name"]
                    tc_args = tc["args"]
                    tc_id = tc["id"]
                    print(
                        f"\n[TOOL CALL] {tc_name}({json.dumps(tc_args, ensure_ascii=False)})"
                    )

                    try:
                        tool = tool_map.get(tc_name)
                        if tool:
                            result = await tool.ainvoke(tc_args)
                            result_str = str(result)
                        else:
                            result_str = json.dumps(
                                {"error": f"Unknown tool: {tc_name}"}
                            )
                    except Exception as tool_exc:
                        result_str = json.dumps({"error": str(tool_exc)})

                    display = (
                        result_str[:300] + "..."
                        if len(result_str) > 300
                        else result_str
                    )
                    print(f"[TOOL RESULT] {display}")

                    messages.append(
                        ToolMessage(
                            content=result_str,
                            tool_call_id=tc_id,
                            name=tc_name,
                        )
                    )

            # ── Step 3: Check for mission complete ────────────────
            last_content = str(ai_msg.content or "")
            if "MISSION COMPLETE" in last_content.upper():
                print(f"\n[AGENT] Mission complete declared on turn {turn + 1}.")
                mission_complete = True
                break

            # Final-push nudge at max_turns - 5
            if (turn + 1) == (max_turns - 5) and not final_push_injected and not mission_complete:
                print(f"\n[SYSTEM] Injecting final-push prompt at turn {turn + 1}.")
                messages.append(HumanMessage(content=FINAL_PUSH_PROMPT))
                final_push_injected = True

            # Periodic status check nudge (every 5 turns)
            elif (turn + 1) % 5 == 0 and not mission_complete:
                print(f"\n[SYSTEM] Injecting status check prompt at turn {turn + 1}.")
                messages.append(HumanMessage(content=STATUS_CHECK_PROMPT))

        if not mission_complete:
            print(
                f"\n[AGENT] Max turns ({max_turns}) reached without MISSION COMPLETE."
            )

        print(
            f"\n[STATS] Total API calls: {api_calls} over {min(turn + 1, max_turns)} turns"
        )

        # Build the readable mission log
        mission_log = extract_mission_log(messages)

        print(f"\n{'=' * 72}")
        print("MISSION LOG SUMMARY")
        print(f"{'=' * 72}")
        print(mission_log)

        return {
            "success": mission_complete,
            "messages": messages,
            "mission_log": mission_log,
            "turns": api_calls,
            "scenario": scenario,
        }


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    scenario_arg = sys.argv[1] if len(sys.argv) > 1 else "palu_earthquake"

    valid_scenarios = list(MISSION_PROMPTS.keys())
    if scenario_arg not in valid_scenarios:
        print(
            f"Unknown scenario '{scenario_arg}'. "
            f"Valid options: {valid_scenarios}\n"
            "Defaulting to 'palu_earthquake'."
        )
        scenario_arg = "palu_earthquake"

    asyncio.run(run_mission(scenario_arg))

