"""
In-memory registry of approved tools — shared state between routers.
Replaces the module-level dynamicRoutes, registeredProxies, MARKETPLACE_TOOLS in market.js.
"""
from typing import Dict, Any

# /tools/<name> → {price, description, walletAddress, ...}
dynamic_routes: Dict[str, Dict[str, Any]] = {}

# tool_name → {type, codePath|targetUrl, walletAddress, trusted}
registered_proxies: Dict[str, Dict[str, Any]] = {}

# List of tool definitions served to the Gemini agent
marketplace_tools: list = []
