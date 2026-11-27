"""LangGraph: a self-check node that runs before the final answer.

    graph.add_node("selfcheck", selfcheck_node)
    graph.add_edge("agent", "selfcheck")   # then route to END or back to "agent" on a low score

State keys used: goal_category (str), steps (list of {type, tool?, status?}), outcome (str).
"""
from tracecheck_client import evaluate, fixes_text

AGENT_ID = "my-langgraph-agent"  # stable id: lets TraceCheck show your progress


def selfcheck_node(state: dict) -> dict:
    r = evaluate(state.get("goal_category", "other"), state.get("steps", []), state.get("outcome", "success"),
                 agent_id=AGENT_ID, previous_rating=state.get("previous_rating"),
                 previous_need_id=state.get("tracecheck_need_id"))
    if "error" in r:
        return {}  # never block the run on the self-check
    return {"tracecheck_score": r["score"], "tracecheck_fixes": fixes_text(r), "tracecheck_need_id": r["need_id"]}
