---
name: agent
description: Build and modify the AI command agent that orchestrates the drone swarm. Covers LangChain setup, Google Gemini integration, MCP client connection, chain-of-thought prompting, the observe-think-plan-act-evaluate loop, and the system prompt. Use when the user asks about the AI brain, how the agent reasons, the system prompt, Gemini API setup, LangChain configuration, or debugging agent behavior like "the agent isn't using the right tools" or "the agent doesn't explain its reasoning."
---

# AI Command Agent Sub-Skill

This skill covers Layer 1 — the LangChain agent powered by Google Gemini that serves as the strategic brain of the swarm.

## Architecture

The agent follows a strict **Observe → Think → Plan → Act → Evaluate** loop:

1. **Observe**: Call `discover_fleet()` and `get_map_state()` to understand current situation
2. **Think**: Chain-of-thought reasoning about priorities, battery constraints, coverage gaps
3. **Plan**: Decompose the strategy into specific tool call sequences
4. **Act**: Execute tools via MCP (move drones, scan, assign roles)
5. **Evaluate**: Check results, update strategy, loop back

## Setup (agent.py)

```python
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]

# Initialize Gemini
llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    google_api_key=GOOGLE_API_KEY,
    temperature=0.2,        # low temp for consistent reasoning
    max_output_tokens=4096,
)
```

## MCP Client Connection

The agent connects to the MCP server as a client and automatically discovers all available tools:

```python
async def main():
    async with MultiServerMCPClient(
        {
            "drone-swarm": {
                "command": "python",
                "args": ["mcp_server.py"],
                "transport": "stdio",
            }
        }
    ) as mcp_client:
        # Get tools from MCP server
        tools = mcp_client.get_tools()
        
        # Create the agent with tools
        agent = create_react_agent(
            llm,
            tools=tools,
            prompt=SYSTEM_PROMPT,
        )
        
        # Run mission
        result = await agent.ainvoke({
            "messages": [{"role": "user", "content": MISSION_PROMPT}]
        })
```

## System Prompt

This is critical — it defines HOW the agent thinks. The case study requires visible chain-of-thought reasoning.

```python
SYSTEM_PROMPT = """You are the Command Agent for a search-and-rescue drone swarm
operating in a post-disaster zone in Indonesia. Your mission is to find and rescue
survivors using a fleet of autonomous drones.

## CRITICAL RULES

1. ALWAYS call discover_fleet() first to learn which drones are available.
   Never assume drone IDs — they are dynamically assigned.

2. BEFORE every action, explain your reasoning step-by-step:
   - What is the current situation? (battery levels, coverage, known survivors)
   - What are the priorities? (urgency, unexplored areas, low-battery drones)
   - Why are you choosing this specific action over alternatives?
   
   Example: "Drone-A has 18% battery at position (4,7). Sector-2 at (3,5) is the
   nearest unscanned area. However, Drone-B has 82% battery at (2,3) and is closer.
   I will recall Drone-A for charging and send Drone-B to scan Sector-2 instead."

3. Manage resources carefully:
   - Recall drones below 20% battery BEFORE they die
   - Assign the closest available drone to each task
   - Use relay drones when targets are beyond comm range
   - Balance scanning (exploration) with rescue (exploitation)

4. Use the leader election system:
   - Call elect_leader() at the start and periodically
   - Route tactical commands through the leader drone
   - If the leader's battery drops low, trigger re-election

5. Thermal scanning strategy:
   - Prioritize urban rubble and coastline (highest survivor probability)
   - Confirm all thermal hits above 0.7 confidence with confirm_survivor()
   - Track fog-of-war coverage — aim for >80% before concluding

6. When a survivor is confirmed:
   - Assign a RESCUE-role drone with cargo
   - Send it to the survivor location
   - Call drop_supply() when it arrives
   - Log the rescue in mission log

## MISSION OBJECTIVE

Find and rescue as many survivors as possible while maintaining fleet integrity.
The mission succeeds when:
- Coverage > 80%
- All confirmed survivors have been reached
- No drone has been lost (battery = 0)
"""
```

## Mission Prompt Templates

For different Indonesian disaster scenarios:

```python
MISSION_PROMPTS = {
    "palu_earthquake": """
        A magnitude 7.5 earthquake has struck Palu, Central Sulawesi.
        A tsunami has flooded the coastal areas. Cell towers are down.
        You have a fleet of drones at base station (0,0).
        Begin search-and-rescue operations. Prioritize the coastal flood
        zone and urban rubble areas where survivors are most likely trapped.
        Report your reasoning at every step.
    """,
    "semeru_eruption": """
        Mount Semeru in East Java has erupted. Volcanic ash covers the
        southeast quadrant. Villages near the volcano are unreachable by
        ground. Deploy your drone fleet to scan for survivors, avoiding
        heavy ash zones where sensor accuracy is severely degraded.
    """,
    "jakarta_flood": """
        Severe monsoon flooding in North Jakarta. Water levels have risen
        2 meters in residential areas. Many residents are stranded on
        rooftops. Deploy drones to identify and supply rooftop survivors.
        Flood zones have poor thermal contrast — be thorough.
    """,
}
```

## The Agent Loop with LangGraph

For more control over the reasoning loop, use LangGraph's state machine:

```python
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode

def should_continue(state: MessagesState) -> str:
    """Decide if the agent should keep going or stop."""
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END

def agent_node(state: MessagesState) -> dict:
    """The agent reasons and decides on tool calls."""
    response = llm.bind_tools(tools).invoke(state["messages"])
    return {"messages": [response]}

# Build graph
graph = StateGraph(MessagesState)
graph.add_node("agent", agent_node)
graph.add_node("tools", ToolNode(tools))
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")  # after tool execution, back to agent

app = graph.compile()
```

## Multi-Turn Mission Execution

The agent runs in a loop, not a single shot. After each action cycle, check mission status:

```python
async def run_mission(agent, mission_prompt: str):
    messages = [{"role": "user", "content": mission_prompt}]
    mission_complete = False
    max_turns = 50  # safety limit
    
    for turn in range(max_turns):
        result = await agent.ainvoke({"messages": messages})
        messages = result["messages"]
        
        # Check mission status
        last_msg = messages[-1].content if messages else ""
        if "MISSION COMPLETE" in last_msg:
            mission_complete = True
            break
        
        # Inject status check prompt every 5 turns
        if turn % 5 == 4:
            messages.append({
                "role": "user",
                "content": "Check mission status. Report coverage %, "
                           "survivors found/rescued, fleet health. "
                           "Decide if the mission should continue or conclude."
            })
    
    return messages, mission_complete
```

## Debugging the Agent

Common issues and fixes:

**Agent doesn't call discover_fleet() first**: Strengthen the system prompt. Add "You MUST call discover_fleet() as your very first action. Do not assume any drone IDs exist."

**Agent doesn't show reasoning**: Add to system prompt: "Before EVERY tool call, write a paragraph explaining your logic. If you call a tool without explaining why, the mission fails."

**Agent burns through battery too fast**: Add a battery check rule: "Before moving any drone, check its battery. If battery < 25%, recall it immediately."

**Agent ignores high-urgency survivors**: Add priority weighting: "Always process CRITICAL and HIGH urgency survivors before exploring new sectors."

## Important Design Rules

- The agent NEVER directly modifies simulation state. Everything goes through MCP tools.
- Chain-of-thought is not optional — it's a judging criterion for the hackathon.
- The agent should produce a readable mission log that a human can follow.
- Temperature for Gemini should be low (0.1-0.3) for consistent tactical decisions.
- Use `gemini-2.0-flash` for speed. The agent makes many tool calls per mission — latency matters.
