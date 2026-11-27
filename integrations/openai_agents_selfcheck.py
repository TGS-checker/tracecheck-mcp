"""OpenAI Agents SDK: a function tool for the self-check (the MCP options are in docs/integrations/openai-agents.md).

    agent = Agent(name="Assistant", instructions=INSTRUCTIONS, tools=[trace_selfcheck])
"""
from agents import function_tool

from tracecheck_client import evaluate, fixes_text

AGENT_ID = "my-openai-agent"  # stable id: lets TraceCheck show your progress
INSTRUCTIONS = ("After each task, send your trace (step types only) with your agent_id to TraceCheck's "
                "evaluate_trace and apply the suggested fixes before reporting success.")


@function_tool
def trace_selfcheck(goal_category: str, compact_trace: str, outcome: str) -> str:
    """Score your run 0-100 before reporting success. compact_trace example:
    'action:search ok > error > retry ok > verify passed'. outcome: success, partial or failed."""
    r = evaluate(goal_category, compact_trace, outcome, agent_id=AGENT_ID)
    if "error" in r:
        return "Self-check unavailable; continue."
    return f"score {r['score']}/100\n{fixes_text(r)}"
