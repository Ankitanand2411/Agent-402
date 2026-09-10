"""
MCP server for the Agent402 marketplace.

Any MCP client (Claude Desktop, Cursor, an agent framework) can connect to
/mcp, discover the approved tools with their prices, and call them. Calls are
forwarded to this same service's POST /tools/{name}, so the x402 payment gate,
replay ledger, spend cap and settlement apply exactly as they do for the web
agent. The MCP layer adds no trust: it never holds a payer key.

Paying works the same way it does over HTTP, expressed as tool arguments:

  1. Call a tool without `payment`  → result {"payment_required": true,
     "challenge": {payTo, maxAmountRequired, asset, network, …}}
  2. The client's agent pays on-chain (or via Nitrolite), then calls again with
     payment = {"method": "x402", "tx_hash": "0x…"}
     or        {"method": "nitrolite", "proof": "<base64>", "from": "0x…"}
  3. The result carries the tool output and the escrow/nitrolite receipt; the
     receipt's paymentKey can be polled with the `get_payment_receipt` tool.

Two meta tools help discovery: `list_marketplace_tools` (catalog with prices
and these instructions) and `get_payment_receipt`.
"""

import json
import logging
from typing import Any

import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

import registry
from config import settings
from routers.gemini import _sanitize_parameters
from services import payments_ledger as ledger

logger = logging.getLogger(__name__)

SERVER_NAME = "agent402-marketplace"
META_TOOLS = {"list_marketplace_tools", "get_payment_receipt"}

PAYMENT_SCHEMA = {
    "type": "object",
    "description": (
        "Proof of payment. Omit on the first call to receive the payment challenge. "
        "x402: {method: 'x402', tx_hash: '<USDC transfer to the escrow>'}. "
        "Nitrolite: {method: 'nitrolite', proof: '<base64 proof>', from: '<payer address>'}."
    ),
    "properties": {
        "method": {"type": "string", "enum": ["x402", "nitrolite"]},
        "tx_hash": {"type": "string", "description": "x402: hash of the USDC transfer to the escrow contract"},
        "x_payment": {"type": "string", "description": "x402: optional base64 X-Payment payload instead of tx_hash"},
        "proof": {"type": "string", "description": "nitrolite: base64 X-Nitrolite-Proof"},
        "from": {"type": "string", "description": "nitrolite: payer wallet address"},
    },
}

PAYMENT_INSTRUCTIONS = (
    "Call a tool without `payment` to get its challenge (payTo, maxAmountRequired in USDC atomic units, asset, "
    "network). Pay exactly that on-chain, then call again with payment={method:'x402', tx_hash}. Each payment "
    "buys one execution; reusing a tx_hash returns an error."
)


# ─── Catalog ──────────────────────────────────────────────────────────────────

def _client_factory() -> httpx.AsyncClient:
    """HTTP client to this service. Tests replace it with an in-process ASGI client."""
    base = settings.SELF_BASE_URL or f"http://127.0.0.1:{settings.PORT}"
    return httpx.AsyncClient(base_url=base, timeout=310.0)


def _marketplace_tools() -> list[dict[str, Any]]:
    return registry.marketplace_view()


def tool_input_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """The tool's own parameters (sanitised) plus the optional payment object."""
    schema = _sanitize_parameters(tool.get("parameters"))
    schema["properties"] = {**schema.get("properties", {}), "payment": PAYMENT_SCHEMA}
    return schema


def build_tool_list() -> list[types.Tool]:
    tools = [
        types.Tool(
            name="list_marketplace_tools",
            description="List the paid tools available on this marketplace with their prices in USDC, and how to pay.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_payment_receipt",
            description="Look up the ledger receipt for a payment key (returned in a tool result as paymentKey) to see settlement status.",
            inputSchema={
                "type": "object",
                "properties": {"payment_key": {"type": "string"}},
                "required": ["payment_key"],
            },
        ),
    ]
    for t in _marketplace_tools():
        name = t.get("name", "")
        if not name or name in META_TOOLS:
            continue
        tools.append(
            types.Tool(
                name=name,
                description=f"{t.get('description', '')} COSTS {t.get('price', '?')} USDC per call (paid via x402 escrow or Nitrolite).",
                inputSchema=tool_input_schema(t),
            )
        )
    return tools


# ─── Forwarding ───────────────────────────────────────────────────────────────

def payment_headers(payment: dict[str, Any] | None) -> dict[str, str]:
    if not payment:
        return {}
    method = (payment.get("method") or ("nitrolite" if payment.get("proof") else "x402")).lower()
    if method == "nitrolite":
        headers = {"X-Payment-Method": "nitrolite"}
        if payment.get("proof"):
            headers["X-Nitrolite-Proof"] = str(payment["proof"])
        if payment.get("from"):
            headers["X-Nitrolite-From"] = str(payment["from"])
        return headers
    headers = {}
    if payment.get("tx_hash"):
        headers["X-Payment-Tx"] = str(payment["tx_hash"])
    if payment.get("x_payment"):
        headers["X-Payment"] = str(payment["x_payment"])
    return headers


def _result(payload: dict[str, Any], *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))],
        structuredContent=payload,
        isError=is_error,
    )


async def call_marketplace_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    payment = arguments.pop("payment", None)
    async with _client_factory() as client:
        try:
            resp = await client.post(f"/tools/{name}", json=arguments, headers=payment_headers(payment))
        except httpx.HTTPError as e:
            return _result({"error": f"marketplace unreachable: {e}"}, is_error=True)
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}

    if resp.status_code == 402:
        challenge = (body.get("accepts") or [{}])[0]
        return _result({
            "payment_required": True,
            "tool": name,
            "challenge": challenge,
            "instructions": PAYMENT_INSTRUCTIONS,
        })
    if resp.status_code == 200:
        return _result({"tool": name, **body})
    if resp.status_code == 502:
        return _result({"tool": name, "tool_failed": True, **body}, is_error=True)
    return _result({"tool": name, "status": resp.status_code, **(body if isinstance(body, dict) else {"body": body})}, is_error=True)


# ─── Server ───────────────────────────────────────────────────────────────────

def build_server() -> Server:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return build_tool_list()

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        arguments = dict(arguments or {})
        if name == "list_marketplace_tools":
            catalog = [
                {"name": t["name"], "description": t.get("description", ""), "price_usdc": t.get("price"),
                 "parameters": _sanitize_parameters(t.get("parameters"))}
                for t in _marketplace_tools()
            ]
            return _result({"tools": catalog, "count": len(catalog), "payment": PAYMENT_INSTRUCTIONS})
        if name == "get_payment_receipt":
            key = str(arguments.get("payment_key", ""))
            try:
                doc = await ledger.get_receipt(key)
            except Exception as e:
                return _result({"error": f"ledger unavailable: {e}"}, is_error=True)
            if not doc:
                return _result({"error": "Receipt not found", "payment_key": key}, is_error=True)
            return _result(doc)
        known = {t.get("name") for t in _marketplace_tools()}
        if name not in known:
            return _result({"error": f"Unknown tool '{name}'", "available": sorted(n for n in known if n)}, is_error=True)
        return await call_marketplace_tool(name, arguments)

    return server


def build_session_manager(*, json_response: bool = False) -> StreamableHTTPSessionManager:
    """
    Stateless Streamable HTTP: every request is self-contained, which suits a
    public tool server (no per-client session to lose on restart).
    """
    hosts = [h.strip() for h in settings.MCP_ALLOWED_HOSTS.split(",") if h.strip()]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(hosts),
        allowed_hosts=hosts,
        allowed_origins=[],
    )
    return StreamableHTTPSessionManager(
        app=build_server(),
        stateless=True,
        json_response=json_response,
        security_settings=security,
    )
