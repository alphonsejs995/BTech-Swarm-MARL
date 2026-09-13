# CLAUDE.md — Swarm Rescue: Decentralized Drone Intelligence for Indonesian Disaster Response

## What this project is

A hackathon submission for **V HACK 2026 Case Study 3** (First Responder of the Future — Decentralised Swarm Intelligence). We build a Python simulation of autonomous rescue drones that find survivors in post-disaster Indonesia using AI-powered coordination, thermal sensing, and decentralized swarm intelligence — all communicating through the Model Context Protocol (MCP).

## The problem

When earthquakes or tsunamis hit Indonesia (Ring of Fire, ~17,000 islands), cell towers go down in the critical first 72 hours. Cloud-dependent AI systems become useless. Rescue teams operate blind. People die waiting.

## Our solution

An **AI command agent** (Google Gemini 2.0 Flash) orchestrates a fleet of 3-5 simulated drones through MCP. The drones self-organize using leader election, communicate peer-to-peer within radio range, scan for survivors using simulated thermal sensors, and deliver supplies — all without cloud connectivity. The AI provides strategic reasoning while the swarm handles tactical coordination autonomously.

## Architecture (3 layers)

**Layer 1 — AI Command Agent**: Google Gemini 2.0 Flash via LangChain + LangGraph. Issues high-level goals ("scan the southeast flood zone"), receives reports, makes strategic decisions. Demonstrates chain-of-thought reasoning before every action.

**Layer 2 — MCP Server**: FastMCP (Python) exposes ~24 tools across 4 categories: fleet management (`discover_fleet`, `elect_leader`, `assign_role`), navigation (`move_to`, `recall_drone`, `get_path_cost`), sensors (`thermal_scan`, `confirm_survivor`, `get_fog_of_war`), and swarm communication (`broadcast_msg`, `send_direct`, `get_inbox`). This is the only interface between the AI brain and the simulation. No backdoors.

**Layer 3 — Simulation Engine**: Pure Python 2D grid (20x20) with Indonesian terrain types (jungle, flood zone, urban rubble, mountain, volcanic ash, coastline). Each terrain affects drone movement cost, thermal sensor accuracy, and has an elevation value for 3D visualization. Survivors spawn with realistic heat signatures (36-38°C), sensors have Gaussian noise (±1.5°C) and false positives (8%), and fog of war forces the agent to explore strategically. The simulation is rendered as a **3D isometric view** using Pygame — terrain tiles have elevation-based height (mountains rise, flood zones sink), drones hover with animations, and survivors pulse by urgency.

## Key features

- **Decentralized leader election**: Drones score themselves on battery, sensor health, uptime, and signal strength. Highest score becomes leader. If leader fails, automatic re-election with zero downtime.
- **Drone roles**: Scout (explore), Rescue (deliver supplies), Relay (extend comm range between base and field). Leader dynamically reassigns based on mission state.
- **Thermal sensor simulation**: Realistic heat detection with terrain attenuation (jungle canopy blocks 30% IR), distance falloff, noise, and false positives. Agent must use `confirm_survivor()` for ambiguous readings.
- **Fog of war**: Map starts hidden. Each `thermal_scan()` reveals cells in a radius. Agent must reason about coverage gaps and prioritize unexplored high-probability sectors.
- **Comm range**: Drones can only communicate within 5 cells. Relay drones extend this. Messages outside range are silently dropped.
- **Indonesian disaster scenarios**: Palu earthquake + tsunami, Semeru volcanic eruption, Jakarta monsoon flooding — each with unique terrain and survivor distribution.

## Tech stack

- Python 3.11+
- `fastmcp` — MCP server
- `langchain`, `langchain-google-genai`, `langgraph` — agent framework
- `google-generativeai` — Gemini API (free tier via AI Studio)
- `pygame` — 3D isometric visualization (IsometricRenderer)
- `streamlit` — dashboard and mission log

## Project structure

```
swarm-rescue/
├── simulation/
│   ├── terrain.py        # 2D grid, terrain types, movement costs
│   ├── drone.py          # Drone class with battery, role, status
│   ├── survivor.py       # Survivor spawning, urgency escalation
│   ├── world.py          # Game loop, tick system, state manager
│   └── renderer.py       # 3D isometric Pygame renderer + terminal fallback
├── sensors/
│   ├── thermal.py        # Heat scan, noise, classification, false positives
│   └── fog_of_war.py     # Visibility tracking
├── swarm/
│   ├── leader_election.py  # Scoring, election, failover
│   ├── message_bus.py      # Broadcast, direct, inbox, relay forwarding
│   └── roles.py            # Scout/Rescue/Relay definitions
├── mcp_server.py         # FastMCP server (all 24 tools)
├── agent.py              # LangChain + Gemini + MCP client
├── dashboard.py          # Streamlit viewer
├── config.py             # All tunable constants
├── terrain_data/         # JSON maps from Google AI Studio
└── main.py               # Entry point
```

## How to run

```bash
pip install fastmcp langchain langchain-google-genai langgraph streamlit pygame
export GOOGLE_API_KEY="your-key"
python main.py
```

## Agent skills (for Claude Code)

This project includes 5 specialized sub-skills in `.claude/skills/swarm-rescue-skills/`. Each covers one component:

| Skill | Covers |
|---|---|
| `simulation/` | Terrain, drones, world state, game loop, 3D isometric renderer |
| `mcp-server/` | FastMCP tools, schemas, server lifecycle |
| `agent/` | LangChain, Gemini, CoT prompting, mission loop |
| `thermal-sensor/` | Heat detection, noise, fog of war, classification |
| `swarm-comms/` | Leader election, message bus, roles, failover |

Read the relevant skill before modifying any component. Cross-cutting changes (like adding a new MCP tool that does thermal scanning) require reading multiple skills.

## SDG alignment

- **SDG 9** (Industry, Innovation, Infrastructure) — Target 9.1 & 9.5: resilient infrastructure and enhanced research
- **SDG 3** (Good Health and Well-being) — Target 3.d: early warning and risk reduction for national health emergencies

## Constraints from the case study

- Simulation only — no physical hardware
- All agent-to-drone communication MUST go through MCP. No hard-coded drone movements.
- Agent MUST demonstrate chain-of-thought reasoning before every tool call
- Agent MUST NOT have hard-coded drone IDs — use `discover_fleet()` for dynamic discovery
- Minimum fleet: 3-5 drones
- Must produce a readable mission log showing step-by-step reasoning

## Common commands

```bash
# Run just the simulation (no AI)
python simulation/world.py

# Run MCP server standalone (for testing tools)
python mcp_server.py

# Run full mission with AI agent
python main.py --scenario palu_earthquake

# Open dashboard
streamlit run dashboard.py

# Generate new terrain map
python -c "from terrain_data.generator import generate; generate('jakarta_flood')"
```
