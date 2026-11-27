"""tools/list must expose all four MCP annotation hints on every tool."""

import asyncio
from pathlib import Path

import server

REQUIRED_HINTS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def test_tools_list_includes_all_four_annotation_hints():
    tools = asyncio.run(server.mcp.list_tools())
    assert tools, "expected at least one tool"

    names = {tool.name for tool in tools}
    assert {"get_trace_schema", "evaluate_trace"} <= names

    for tool in tools:
        payload = tool.model_dump(by_alias=True, mode="json")
        annotations = payload.get("annotations")
        assert isinstance(annotations, dict), f"{tool.name} is missing annotations"
        for hint, expected in REQUIRED_HINTS.items():
            assert hint in annotations, f"{tool.name} is missing {hint}"
            assert annotations[hint] is expected, (
                f"{tool.name}.{hint} should be {expected}, got {annotations[hint]!r}"
            )
            assert isinstance(annotations[hint], bool)


def test_source_writes_annotation_hints_in_camel_case():
    """Static scanners read server.py; literal camelCase keys must be visible in source."""
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    for hint, value in (
        ("readOnlyHint", "True"),
        ("destructiveHint", "False"),
        ("idempotentHint", "True"),
        ("openWorldHint", "False"),
    ):
        needle = f"{hint}={value}"
        assert source.count(needle) >= 2, f"expected {needle} on every tool, found {source.count(needle)}"
