"""
In-memory registry of approved tools, shared by the routers and the MCP server.

One record per tool. `register()` takes a tool document (the MongoDB shape) and
returns the record; `marketplace_view()` is the agent-facing catalog. The
registry is populated at startup from MongoDB and updated when a tool is
approved; it is per process (see README, known limitations).
"""

import re
from typing import Any

_COSTS_SUFFIX = re.compile(r"\s*COSTS:.*$", re.IGNORECASE)

tools: dict[str, dict[str, Any]] = {}


def clear() -> None:
    tools.clear()


def register(doc: dict[str, Any], *, code_path: str | None = None) -> dict[str, Any]:
    """Add or replace a tool from its stored document."""
    record = {
        "name": doc["name"],
        "description": doc.get("description", ""),
        "price": doc.get("price", "1"),
        "parameters": doc.get("parameters"),
        "type": doc.get("type", "proxy"),
        "targetUrl": doc.get("targetUrl", ""),
        "codePath": code_path,
        "walletAddress": doc.get("walletAddress", ""),
        "trusted": bool(doc.get("trusted", False)),
    }
    tools[record["name"]] = record
    return record


def get(name: str) -> dict[str, Any] | None:
    return tools.get(name)


def marketplace_view() -> list[dict[str, Any]]:
    """What the agent and MCP clients see: name, description (price suffix stripped), price, parameters."""
    return [
        {
            "name": t["name"],
            "description": _COSTS_SUFFIX.sub("", t["description"]).strip(),
            "price": t["price"],
            "parameters": t["parameters"],
        }
        for t in tools.values()
    ]
