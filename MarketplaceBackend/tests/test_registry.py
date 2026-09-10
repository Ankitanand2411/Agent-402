"""The single in-memory tool registry shared by the routers and the MCP server."""

import registry
from tests.conftest import ECHO_TOOL_DOC


def test_register_and_view(clean_registry):
    rec = registry.register(ECHO_TOOL_DOC)
    assert rec["type"] == "proxy" and rec["targetUrl"] == "http://tool.local/echo" and rec["codePath"] is None
    assert registry.get("echo") is rec
    [view] = registry.marketplace_view()
    assert view == {"name": "echo", "description": "Echoes its input", "price": "0.5", "parameters": ECHO_TOOL_DOC["parameters"]}


def test_register_replaces_existing(clean_registry):
    registry.register(ECHO_TOOL_DOC)
    registry.register({**ECHO_TOOL_DOC, "price": "0.75"})
    assert len(registry.tools) == 1 and registry.get("echo")["price"] == "0.75"


def test_code_tool_keeps_its_path_and_trust(clean_registry):
    rec = registry.register({**ECHO_TOOL_DOC, "name": "js", "type": "code", "trusted": True}, code_path="/x/js.js")
    assert rec["codePath"] == "/x/js.js" and rec["trusted"] is True


def test_clear(clean_registry):
    registry.register(ECHO_TOOL_DOC)
    registry.clear()
    assert registry.tools == {} and registry.get("echo") is None
