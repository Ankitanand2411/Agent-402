"""
MCP server tests.

Layer 1 exercises the building blocks (tool list, payment header mapping,
result mapping) against the real tools router mounted in-process.

Layer 2 is a genuine MCP client round trip: the Streamable HTTP session
manager is mounted on a FastAPI app, the official client connects through an
in-process ASGI transport, initialises a session, lists tools, calls a paid
tool without payment (gets the 402 challenge), then with payment (gets the
result and receipt), then replays the same payment (gets the ledger's 409).
"""

import json
from contextlib import asynccontextmanager

import httpx
import mcp.types as types
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import mcp_server
from config import settings
from routers import tools as tools_router
from tests.conftest import ECHO_TOOL_DOC, ESCROW_ADDR, PAYER_ADDR, PROVIDER_ADDR
from tests.test_tools_router import Recorder

TX = "0x" + "ee" * 32


@pytest.fixture
def marketplace(monkeypatch, clean_registry, fake_collection, fake_ledger, fake_spend):
    """The real tools router with fakes for chain, execution and settlement, reachable in-process."""
    monkeypatch.setattr(settings, "ESCROW_CONTRACT_ADDRESS", ESCROW_ADDR)
    monkeypatch.setattr(settings, "ESCROW_PRIVATE_KEY", "0x" + "33" * 32)
    monkeypatch.setattr(settings, "SETTLEMENT_MODE", "sync")
    monkeypatch.setattr(settings, "DAILY_SPEND_CAP_UNITS", 0)

    clean_registry.register(ECHO_TOOL_DOC)

    verify = Recorder(result={"from_addr": PAYER_ADDR, "to_addr": ESCROW_ADDR, "transfer_amount": 500_000})
    execute = Recorder(result={"success": True, "result": "Tool call successful", "data": {"echo": "hi"}})
    release = Recorder(result={"status": "released", "releaseTxHash": "0xrel", "releasedTo": PROVIDER_ADDR})
    refund = Recorder(result={"status": "refunded", "refundTxHash": "0xref", "refundedTo": PAYER_ADDR})
    monkeypatch.setattr(tools_router, "verify_onchain_payment", verify)
    monkeypatch.setattr(tools_router, "execute_proxy_tool", execute)
    monkeypatch.setattr(tools_router, "release_escrow", release)
    monkeypatch.setattr(tools_router, "refund_escrow", refund)

    app = FastAPI()
    app.include_router(tools_router.router)
    # The MCP layer calls "itself" over HTTP; point it at this in-process app.
    monkeypatch.setattr(mcp_server, "_client_factory",
                        lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://marketplace"))
    return {"app": app, "execute": execute, "release": release}


# ─── Layer 1: building blocks ─────────────────────────────────────────────────

def test_tool_list_exposes_catalog_with_prices_and_payment_schema(marketplace):
    tools = {t.name: t for t in mcp_server.build_tool_list()}
    assert {"list_marketplace_tools", "get_payment_receipt", "echo"} <= set(tools)
    echo = tools["echo"]
    assert "COSTS 0.5 USDC" in echo.description
    assert echo.inputSchema["properties"]["text"]["type"] == "string"
    assert echo.inputSchema["properties"]["payment"]["properties"]["tx_hash"]["type"] == "string"
    assert echo.inputSchema["required"] == ["text"]


def test_payment_headers_mapping():
    assert mcp_server.payment_headers(None) == {}
    assert mcp_server.payment_headers({"method": "x402", "tx_hash": TX}) == {"X-Payment-Tx": TX}
    assert mcp_server.payment_headers({"tx_hash": TX, "x_payment": "b64"}) == {"X-Payment-Tx": TX, "X-Payment": "b64"}
    assert mcp_server.payment_headers({"method": "nitrolite", "proof": "p", "from": PAYER_ADDR}) == {
        "X-Payment-Method": "nitrolite", "X-Nitrolite-Proof": "p", "X-Nitrolite-From": PAYER_ADDR,
    }
    assert mcp_server.payment_headers({"proof": "p"})["X-Payment-Method"] == "nitrolite"   # inferred


async def test_call_without_payment_returns_challenge(marketplace):
    res = await mcp_server.call_marketplace_tool("echo", {"text": "hi"})
    assert res.isError is False
    body = res.structuredContent
    assert body["payment_required"] is True
    assert body["challenge"]["payTo"] == ESCROW_ADDR
    assert body["challenge"]["maxAmountRequired"] == "500000"
    assert "tx_hash" in body["instructions"]
    assert json.loads(res.content[0].text)["tool"] == "echo"                    # text mirrors structured
    assert marketplace["execute"].calls == []


async def test_call_with_payment_executes_and_returns_receipt(marketplace):
    res = await mcp_server.call_marketplace_tool("echo", {"text": "hi", "payment": {"method": "x402", "tx_hash": TX}})
    assert res.isError is False
    body = res.structuredContent
    assert body["data"] == {"echo": "hi"}
    assert body["escrowReceipt"]["paymentKey"] == f"x402:{TX}"
    assert body["escrowReceipt"]["escrowRelease"]["status"] == "released"
    (args, kwargs), = marketplace["execute"].calls
    assert args[1] == {"text": "hi"}                                             # payment stripped from tool input


async def test_failed_tool_is_an_error_result(marketplace):
    marketplace["execute"].error = RuntimeError("upstream exploded")
    res = await mcp_server.call_marketplace_tool("echo", {"text": "hi", "payment": {"tx_hash": TX}})
    assert res.isError is True
    assert res.structuredContent["tool_failed"] is True
    assert res.structuredContent["escrowReceipt"]["escrowRelease"]["status"] == "refunded"


async def test_marketplace_unreachable_is_an_error_result(monkeypatch, clean_registry):
    class Boom(httpx.AsyncClient):
        async def post(self, *a, **k):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(mcp_server, "_client_factory", lambda: Boom(base_url="http://x"))
    res = await mcp_server.call_marketplace_tool("echo", {})
    assert res.isError is True and "unreachable" in res.structuredContent["error"]


# ─── Layer 2: real MCP client round trip ──────────────────────────────────────

@asynccontextmanager
async def mcp_session():
    """
    A real MCP client connected to the mounted server, all in one task.
    (Not a pytest fixture on purpose: anyio cancel scopes opened here must be
    closed by the same task, and async-generator fixtures tear down elsewhere.)
    """
    manager = mcp_server.build_session_manager(json_response=True)   # JSON (not SSE) so the ASGI transport can buffer it
    host = FastAPI()
    host.mount("/mcp", manager.handle_request)

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://mcp-host",
                                 headers=headers, timeout=timeout or 30, auth=auth)

    async with manager.run():
        async with streamablehttp_client("http://mcp-host/mcp", httpx_client_factory=factory) as (read, write, _sid):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def test_client_can_discover_pay_and_call(marketplace, monkeypatch):
    monkeypatch.setattr(settings, "MCP_ALLOWED_HOSTS", "")
    async with mcp_session() as mcp_client:
        listed = await mcp_client.list_tools()
        names = {t.name for t in listed.tools}
        assert {"echo", "list_marketplace_tools", "get_payment_receipt"} <= names

        catalog = await mcp_client.call_tool("list_marketplace_tools", {})
        assert catalog.isError is False
        assert catalog.structuredContent["tools"][0]["price_usdc"] == "0.5"

        challenge = await mcp_client.call_tool("echo", {"text": "hi"})
        assert challenge.structuredContent["payment_required"] is True
        assert challenge.structuredContent["challenge"]["maxAmountRequired"] == "500000"

        paid = await mcp_client.call_tool("echo", {"text": "hi", "payment": {"method": "x402", "tx_hash": TX}})
        assert paid.isError is False
        assert paid.structuredContent["data"] == {"echo": "hi"}
        key = paid.structuredContent["escrowReceipt"]["paymentKey"]

        receipt = await mcp_client.call_tool("get_payment_receipt", {"payment_key": key})
        assert receipt.structuredContent["status"] == "delivered"
        assert receipt.structuredContent["settlement"]["status"] == "released"

        replay = await mcp_client.call_tool("echo", {"text": "again", "payment": {"tx_hash": TX}})
        assert replay.isError is True
        assert replay.structuredContent["status"] == 409                               # ledger refused the reused payment


async def test_unknown_tool_lists_alternatives(marketplace, monkeypatch):
    monkeypatch.setattr(settings, "MCP_ALLOWED_HOSTS", "")
    async with mcp_session() as mcp_client:
        res = await mcp_client.call_tool("nope", {})
        assert res.isError is True
        assert res.structuredContent["available"] == ["echo"]


def test_result_helper_shapes():
    r = mcp_server._result({"a": 1}, is_error=True)
    assert isinstance(r, types.CallToolResult) and r.isError is True
    assert r.structuredContent == {"a": 1} and json.loads(r.content[0].text) == {"a": 1}
