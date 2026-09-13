#!/usr/bin/env python3
"""
main.py — Entry point for the Swarm Rescue drone simulation.

Run modes:
    python main.py                              # Full AI mission (default: palu_earthquake)
    python main.py --scenario semeru_eruption   # Specific scenario
    python main.py --sim-only                   # Simulation + 3D renderer, no AI
    python main.py --no-render                  # AI mission without Pygame window
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure swarm-rescue is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

# Load API key from .env (one level up)
load_dotenv(Path(__file__).parent.parent / ".env")
api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if api_key:
    os.environ["GOOGLE_API_KEY"] = api_key


SCENARIOS = {
    "palu_earthquake": {
        "terrain": "terrain_data/sulawesi_earthquake.json",
        "name": "Palu Earthquake & Tsunami (Sulawesi)",
        "description": (
            "A 7.5 magnitude earthquake struck Palu, Sulawesi, triggering a tsunami. "
            "The east coast is flooded, the city centre is rubble. Survivors are trapped "
            "in collapsed buildings and stranded along the coastline."
        ),
    },
    "semeru_eruption": {
        "terrain": "terrain_data/sulawesi_earthquake.json",  # reuse for now
        "name": "Mount Semeru Eruption (East Java)",
        "description": (
            "Mount Semeru erupted covering villages in volcanic ash. Visibility is near zero "
            "in ash zones. Thermal sensors are severely attenuated. Survivors are scattered "
            "across the affected area, many on higher ground."
        ),
    },
    "jakarta_flood": {
        "terrain": "terrain_data/sulawesi_earthquake.json",  # reuse for now
        "name": "Jakarta Monsoon Flooding",
        "description": (
            "Extreme monsoon rainfall has flooded residential areas of Jakarta. Survivors "
            "are stranded on rooftops and upper floors. Cold floodwater masks heat signatures, "
            "making thermal detection challenging."
        ),
    },
    "simple_test": {
        "terrain": "terrain_data/simple_test.json",
        "name": "Simple Detection Test (10x10)",
        "description": (
            "A small 10x10 test area with 3 survivors. Detection-only mission — "
            "find and confirm survivors using thermal scans. No supply drops needed."
        ),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Swarm Rescue — Decentralized Drone Intelligence for Indonesian Disaster Response"
    )
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        default="palu_earthquake",
        help="Disaster scenario to simulate (default: palu_earthquake)",
    )
    parser.add_argument(
        "--drones",
        type=int,
        default=5,
        help="Number of drones in the fleet (default: 5)",
    )
    parser.add_argument(
        "--survivors",
        type=int,
        default=8,
        help="Number of survivors to place (default: 8)",
    )
    parser.add_argument(
        "--sim-only",
        action="store_true",
        help="Run the 3D simulation viewer only (no AI agent)",
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="Run AI mission without the Pygame renderer",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=20,
        help="Maximum agent turns before stopping (default: 20)",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Record the simulation to an MP4 video (requires ffmpeg)",
    )
    return parser.parse_args()


def run_sim_only(args: argparse.Namespace) -> None:
    """Launch the 3D isometric renderer without the AI agent."""
    from simulation.world import World
    from simulation.renderer import IsometricRenderer

    scenario = SCENARIOS[args.scenario]
    terrain_path = str(Path(__file__).parent / scenario["terrain"])

    print(f"Scenario: {scenario['name']}")
    print(f"Description: {scenario['description']}")
    print(f"Fleet: {args.drones} drones | Survivors: {args.survivors}")
    print(f"Terrain: {terrain_path}")
    print()
    print("Launching 3D isometric viewer...")
    print("Controls: Arrow/WASD = pan | Scroll = zoom | R = reset | Close window to exit")
    print()

    world = World(
        terrain_path=terrain_path,
        num_drones=args.drones,
        num_survivors=args.survivors,
    )

    renderer = IsometricRenderer(world)
    renderer.run(record=args.record)


async def run_ai_mission(args: argparse.Namespace) -> None:
    """Run the full AI agent mission."""
    scenario = SCENARIOS[args.scenario]

    print("=" * 70)
    print("  SWARM RESCUE — AI Mission")
    print("=" * 70)
    print(f"  Scenario:   {scenario['name']}")
    print(f"  Fleet:      {args.drones} drones")
    print(f"  Survivors:  {args.survivors}")
    print(f"  Max turns:  {args.max_turns}")
    print("=" * 70)
    print()

    if not api_key:
        print("ERROR: No API key found.")
        print("Set GEMINI_API_KEY in ../.env or export GOOGLE_API_KEY")
        sys.exit(1)

    # Enable 3D renderer inside the MCP server subprocess (unless --no-render)
    if not args.no_render:
        os.environ["SWARM_RENDER"] = "1"
    if args.record:
        os.environ["SWARM_RECORD"] = "1"

    # Import agent module
    try:
        from agent import run_mission
    except ImportError as e:
        print(f"ERROR: Could not import agent module: {e}")
        print("Make sure all dependencies are installed: pip install -r requirements.txt")
        sys.exit(1)

    # Run the mission
    print("Starting AI mission...")
    print()

    result = await run_mission(scenario=args.scenario, max_turns=args.max_turns)

    # Print results
    print()
    print("=" * 70)
    print("  MISSION COMPLETE")
    print("=" * 70)

    if isinstance(result, dict):
        if "error" in result:
            print(f"  Error: {result['error']}")
        else:
            print(f"  Status:    {'SUCCESS' if result.get('success') else 'INCOMPLETE'}")
            print(f"  Turns:     {result.get('turns', '?')}")
            print(f"  Rescued:   {result.get('rescued', '?')}")
            print(f"  Coverage:  {result.get('coverage', '?')}%")
    print("=" * 70)

    # Save mission log
    log_dir = Path(__file__).parent / "mission_logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"mission_{args.scenario}_{timestamp}.json"

    log_data = {
        "scenario": args.scenario,
        "scenario_name": scenario["name"],
        "timestamp": timestamp,
        "drones": args.drones,
        "survivors": args.survivors,
        "result": result if isinstance(result, dict) else str(result),
    }

    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2, default=str)

    print(f"\n  Mission log saved to: {log_path}")


def main() -> None:
    args = parse_args()

    # Apply scenario-specific defaults that override the general CLI defaults.
    if args.scenario == "simple_test":
        # Only override if the user did not explicitly pass their own values.
        # argparse has no built-in "was this flag set by the user" check, so we
        # compare against the known defaults and leave intentional overrides alone.
        if args.drones == 5:
            args.drones = 3
        if args.survivors == 8:
            args.survivors = 3

    if args.sim_only:
        run_sim_only(args)
    else:
        asyncio.run(run_ai_mission(args))


if __name__ == "__main__":
    main()
