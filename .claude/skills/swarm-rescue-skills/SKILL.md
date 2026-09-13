---
name: swarm-rescue
description: Master skill for building a decentralized drone swarm rescue system for ASEAN disaster response. Use this skill whenever the user mentions drone swarm, rescue simulation, MCP drone server, thermal sensor, swarm intelligence, leader election, disaster response AI, or any component of the V HACK 2026 Case Study 3. This skill routes to specialized sub-skills based on what the user is working on. Even if the user only mentions one piece (like "fix my drone movement" or "add thermal scanning"), trigger this skill — it will point to the right sub-skill.
---

# Swarm Rescue — Master Skill

This project builds a **decentralized drone swarm** for post-disaster search-and-rescue in Indonesia, using an AI command agent (Google Gemini) communicating through MCP (Model Context Protocol) to coordinate autonomous drones on a simulated 2D terrain grid.

## Project Architecture (3 layers)

```
┌─────────────────────────────────────────┐
│  Layer 1: AI Command Agent              │
│  (Gemini 2.0 Flash + LangChain)        │
│  Issues strategic goals via MCP         │
└──────────────┬──────────────────────────┘
               │ MCP Protocol (JSON-RPC)
┌──────────────▼──────────────────────────┐
│  Layer 2: MCP Server (FastMCP)          │
│  Exposes drone tools as MCP endpoints   │
│  24 tools across 4 categories           │
└──────────────┬──────────────────────────┘
               │ Python function calls
┌──────────────▼──────────────────────────┐
│  Layer 3: Simulation Engine (Python)    │
│  2D grid, drones, survivors, terrain    │
│  Thermal sensors, fog of war            │
└─────────────────────────────────────────┘
```

## Sub-Skills — Read the right one based on what you're doing

| Working on... | Read this sub-skill |
|---|---|
| Terrain grid, world state, game loop, drone movement physics | `simulation/SKILL.md` |
| FastMCP server, tool definitions, JSON-RPC endpoints | `mcp-server/SKILL.md` |
| LangChain agent, Gemini integration, CoT reasoning loop | `agent/SKILL.md` |
| Heat detection, thermal signatures, sensor noise, fog of war | `thermal-sensor/SKILL.md` |
| Leader election, drone roles, message bus, decentralization | `swarm-comms/SKILL.md` |

Read the relevant sub-skill BEFORE writing any code. If the task spans multiple sub-skills (e.g., "add a new MCP tool that does thermal scanning"), read both.

## Tech Stack (locked in)

- **Python 3.11+** — everything is Python
- **Google Gemini 2.0 Flash** — LLM via `google-generativeai` SDK or LangChain wrapper
- **LangChain + LangGraph** — agent framework with MCP adapter
- **FastMCP** — Python MCP server (`pip install fastmcp`)
- **Pygame** — 3D isometric visualization (IsometricRenderer with elevation-based terrain height)
- **Streamlit** — dashboard and mission log viewer

## File Structure

```
swarm-rescue/
├── simulation/
│   ├── terrain.py          ← 2D grid world with Indonesian terrain types
│   ├── drone.py            ← Drone class (pos, battery, cargo, role, status)
│   ├── survivor.py         ← Survivor spawner with thermal signatures
│   ├── world.py            ← Game loop, tick system, state manager
│   └── renderer.py         ← IsometricRenderer (3D Pygame) + TerminalRenderer (fallback)
├── mcp_server.py           ← FastMCP server exposing all 24 tools
├── agent.py                ← LangChain agent + Gemini + MCP client
├── swarm/
│   ├── leader_election.py  ← Scoring algorithm, failover logic
│   ├── message_bus.py      ← Broadcast, direct, inbox system
│   └── roles.py            ← Scout, Rescue, Relay role definitions
├── sensors/
│   ├── thermal.py          ← Heat sensor model with noise + attenuation
│   └── fog_of_war.py       ← Visibility tracking per cell
├── dashboard.py            ← Streamlit real-time mission viewer
├── terrain_data/
│   └── sulawesi_earthquake.json  ← Sample terrain from AI Studio
├── config.py               ← All constants (grid size, battery drain, etc.)
└── main.py                 ← Entry point, wires everything together
```

## Key Constants (config.py)

```python
GRID_WIDTH = 20
GRID_HEIGHT = 20
NUM_DRONES = 5
NUM_SURVIVORS = 8
DRONE_BATTERY_MAX = 100
BATTERY_DRAIN_PER_MOVE = 2
BATTERY_DRAIN_PER_SCAN = 3
SCAN_RADIUS = 2
COMM_RANGE = 5
LEADER_ELECTION_INTERVAL = 10  # ticks
THERMAL_NOISE_STDDEV = 1.5     # degrees C
DETECTION_THRESHOLD = 30.0     # degrees C
FALSE_POSITIVE_RATE = 0.08
BASE_CHARGING_STATION = (0, 0)
```

## Running the Project

```bash
# 1. Install dependencies
pip install fastmcp langchain langchain-google-genai langgraph streamlit pygame

# 2. Set API key
export GOOGLE_API_KEY="your-key-from-ai-studio"

# 3. Start MCP server (terminal 1)
python mcp_server.py

# 4. Start agent (terminal 2)
python agent.py

# 5. Optional: start dashboard (terminal 3)
streamlit run dashboard.py
```
