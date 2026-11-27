"""Public texts describe WHAT TraceCheck does, never HOW (no weights, thresholds, detection rules)."""
import json
import pathlib
import re

import server

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEAKS = [r"weight\s*0\.\d", r"\bweights?\b", r"capped at", r"~\s*60", r"real repeat", r"benefit of the doubt"]


def _check(text: str, where: str):
    for pat in LEAKS:
        assert not re.search(pat, text, re.I), f"{where} leaks scoring detail: {pat}"


def test_schema_has_no_weights_or_rules():
    _check(json.dumps(server.get_trace_schema()), "get_trace_schema")


def test_tool_descriptions_and_instructions():
    _check(server.EVALUATE_TRACE_DESCRIPTION, "evaluate_trace description")
    _check(server.mcp.instructions or "", "instructions")


def test_public_files():
    for name in ["README.md", "llms.txt", "agent-card.json", "server.json", "docs/self-check.md"]:
        _check((ROOT / name).read_text(), name)


def test_registry_description_short():
    desc = json.loads((ROOT / "server.json").read_text())["description"]
    assert len(desc) <= 100
