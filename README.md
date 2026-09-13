# 🚁 Swarm Disaster Agents - MARL Framework

<div align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python Version">
  <img src="https://img.shields.io/badge/AI-Gemini%203.6%20Flash-orange.svg" alt="Gemini AI">
  <img src="https://img.shields.io/badge/Architecture-LangChain%20%7C%20MCP-green.svg" alt="Architecture">
  <img src="https://img.shields.io/badge/Simulation-PyGame%203D-purple.svg" alt="Simulation">
</div>

<br>

Welcome to the **Swarm Disaster Agents** framework! This repository contains a fully functioning **Multi-Agent Reinforcement Learning (MARL)** environment designed for orchestrating autonomous drone swarms in post-disaster Search & Rescue (SAR) operations. 

Powered by **Google's Gemini 3.6 Flash** and orchestrated via **LangChain** and the **Model Context Protocol (MCP)**, this simulation creates a self-healing, intelligent mesh network of drones that actively search for survivors, confirm thermal signatures, and deliver medical supplies.

---

## 🏗️ System Architecture

Our framework bridges the gap between simulated disaster physics and state-of-the-art Large Language Models (LLMs).

\\\mermaid
graph TD
    subgraph AI Orchestration Layer
        Gemini[🧠 Gemini 3.6 Flash]
        LangChain[⛓️ LangChain Agent]
    end

    subgraph Communication Bridge
        MCP((🔌 FastMCP Server))
    end

    subgraph 3D Physics & Simulation Engine
        World[🌍 World State Manager]
        Renderer[🎮 Pygame 3D Isometric View]
        
        subgraph Drones
            Scout[🚁 Scout Drones]
            Rescue[🚁 Rescue Drones]
            Relay[🚁 Relay Drones]
        end
        
        Sensors[🌡️ Thermal & Fog of War]
    end

    Gemini <--> |Reasons & Chooses Tools| LangChain
    LangChain <--> |JSON-RPC Calls| MCP
    MCP <--> |Executes Actions| World
    World --> Drones
    World --> Sensors
    World --> Renderer
\\\

## 🚀 Key Features

* **Intelligent Auto-Orchestration**: The AI dynamically elects swarm leaders and assigns roles (\scout\, \escue\, \elay\) based on the map size and disaster type.
* **Physics & Environmental Constraints**: Drones consume battery based on distance, and thermal scanners are affected by ambient temperature, volcanic ash, and water.
* **Self-Healing Mesh Network**: Drones must stay within 5 cells of each other. If the radio network splits, the AI automatically detects partitions and regroups.
* **Full 3D Isometric Playback**: Watch the AI's real-time decision making rendered in an interactive Pygame 3D viewer.

## 🛠️ Quick Start

### 1. Setup the Environment
\\\ash
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install strictly locked dependencies (Prevents MCP v2 crashes)
pip install -r requirements.txt
\\\

### 2. Configure API Keys
Add your Google AI API key to the environment. The project uses this key to run the LangChain Agent.
\\\ash
set GEMINI_API_KEY="your_api_key_here"
\\\

### 3. Run a Mission
We have provided easy-to-use batch scripts for Windows:
* \un_ai_mission.bat\ - Runs the full AI simulation with LangChain making real-time decisions.
* \un_sim_only.bat\ - Runs the Pygame simulation visually without the AI logic (for testing map generation).

Alternatively, use the command line for specific scenarios:
\\\ash
# Run the Palu Earthquake tsunami scenario with 5 drones
python main.py --scenario palu_earthquake --drones 5 --max-turns 40

# Run headless (no GUI)
python main.py --no-render
\\\

### 4. Watch the Replay
Every mission generates a detailed JSON log of the AI's reasoning. You can watch the playback in 3D!
\\\ash
python playback.py
\\\

---

## 🗺️ Disaster Scenarios

| Scenario | Terrain Challenge | Visual |
|---|---|---|
| **Palu Earthquake (M7.5)** | Flooded east coast, dense rubble in city center | High risk of false thermal positives |
| **Semeru Eruption** | Volcanic ash zones, near-zero visibility | Ash blocks 70% of thermal signals |
| **Jakarta Floods** | Urban flooding, survivors stranded on rooftops | Cold water actively masks heat signatures |

## 🎓 Academic Deliverables (B.Tech Project)

1. **The Orchestrator:** Located in \gent.py\. Handles the LangChain ReAct loop.
2. **MCP Server Integration:** Located in \mcp_server.py\. Exposes 22 discrete tool functions to the LLM.
3. **Mission Logs:** Found in \mission_logs/\. Extremely detailed logs of every Chain-of-Thought (CoT) reasoning step taken by the swarm.

---
*Built for B.Tech Research in Multi-Agent Reinforcement Learning (MARL).*
