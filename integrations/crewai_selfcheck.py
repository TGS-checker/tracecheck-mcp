"""CrewAI: a self-check tool. Give it to the agent and add to its task:
"Before you report success, call trace_selfcheck with your step types and apply the fixes."
"""
from crewai.tools import tool

from tracecheck_client import evaluate, fixes_text

AGENT_ID = "my-crewai-agent"  # stable id: lets TraceCheck show your progress


@tool("trace_selfcheck")
def trace_selfcheck(goal_category: str, compact_trace: str, outcome: str) -> str:
    """Score your run 0-100 before reporting success. compact_trace example:
    'action:search ok > error > retry ok > verify passed'. outcome: success, partial or failed."""
    r = evaluate(goal_category, compact_trace, outcome, agent_id=AGENT_ID)
    if "error" in r:
        return "Self-check unavailable; continue."
    return f"score {r['score']}/100\n{fixes_text(r)}"
