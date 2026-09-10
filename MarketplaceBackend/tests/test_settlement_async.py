"""
Async settlement: the response returns as soon as the tool result is ready,
the receipt says `pending`, and the ledger is updated when the background
task finishes. Uses httpx's ASGI transport so the test shares the app's event
loop and can await drain_settlements() deterministically.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from config import settings
from routers import tools as tools_router
from tests.conftest import ECHO_TOOL_DOC, ESCROW_ADDR, PAYER_ADDR, PROVIDER_ADDR

TX = "0x" + "aa" * 32


class SlowRecorder:
    """Awaits a gate before returning, so the test controls when settlement completes."""

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []
        self.gate = asyncio.Event()

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        await self.gate.wait()
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
async def env(monkeypatch, clean_registry, fake_ledger, fake_spend):
    monkeypatch.setattr(settings, "ESCROW_CONTRACT_ADDRESS", ESCROW_ADDR)
    monkeypatch.setattr(settings, "ESCROW_PRIVATE_KEY", "0x" + "33" * 32)
    monkeypatch.setattr(settings, "SETTLEMENT_MODE", "async")
    monkeypatch.setattr(settings, "DAILY_SPEND_CAP_UNITS", 0)

    clean_registry.register(ECHO_TOOL_DOC)

    async def verify(*a, **k):
        return {"from_addr": PAYER_ADDR, "to_addr": ESCROW_ADDR, "transfer_amount": 500_000}

    execute_result = {"success": True, "result": "ok", "data": {"echo": 1}}

    async def execute(*a, **k):
        if execute_result.get("_raise"):
            raise RuntimeError("tool exploded")
        return execute_result

    release = SlowRecorder(result={"status": "released", "releaseTxHash": "0xrel", "releasedTo": PROVIDER_ADDR})
    refund = SlowRecorder(result={"status": "refunded", "refundTxHash": "0xref", "refundedTo": PAYER_ADDR})
    monkeypatch.setattr(tools_router, "verify_onchain_payment", verify)
    monkeypatch.setattr(tools_router, "execute_proxy_tool", execute)
    monkeypatch.setattr(tools_router, "release_escrow", release)
    monkeypatch.setattr(tools_router, "refund_escrow", refund)

    app = FastAPI()
    app.include_router(tools_router.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "release": release, "refund": refund, "execute_result": execute_result, "ledger": fake_ledger}
    await tools_router.drain_settlements(timeout=1)


async def test_response_does_not_wait_for_settlement(env):
    r = await env["client"].post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})

    assert r.status_code == 200                         # returned while release is still blocked on the gate
    release = r.json()["escrowReceipt"]["escrowRelease"]
    assert release["status"] == "pending"
    assert release["action"] == "release"
    assert release["receiptId"] == f"x402:{TX}"
    assert release["poll"] == f"/receipts/x402:{TX}"

    # create_task() schedules the coroutine; it starts on the next loop
    # iteration. Yield once so the background task reaches the escrow call.
    await asyncio.sleep(0)
    assert len(env["release"].calls) == 1               # settlement started, still blocked on the gate

    # Ledger shows delivered + pending until the chain confirms.
    rec = (await env["client"].get(f"/receipts/x402:{TX}")).json()
    assert rec["status"] == "delivered"
    assert rec["settlement"]["status"] == "pending"

    env["release"].gate.set()
    await tools_router.drain_settlements(timeout=1)

    rec = (await env["client"].get(f"/receipts/x402:{TX}")).json()
    assert rec["settlement"]["status"] == "released"
    assert rec["settlement"]["releaseTxHash"] == "0xrel"


async def test_failed_tool_schedules_refund(env):
    env["execute_result"]["_raise"] = True
    r = await env["client"].post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})

    assert r.status_code == 502
    assert r.json()["escrowReceipt"]["escrowRelease"] == {
        "status": "pending", "action": "refund", "receiptId": f"x402:{TX}", "poll": f"/receipts/x402:{TX}",
    }
    await asyncio.sleep(0)
    assert env["release"].calls == []
    assert env["refund"].calls == [(TX, PAYER_ADDR, 500_000)]

    env["refund"].gate.set()
    await tools_router.drain_settlements(timeout=1)
    rec = (await env["client"].get(f"/receipts/x402:{TX}")).json()
    assert rec["status"] == "failed"
    assert rec["settlement"]["status"] == "refunded"


async def test_settlement_error_is_recorded_not_lost(env):
    env["release"].error = RuntimeError("nonce too low")
    await env["client"].post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})
    env["release"].gate.set()
    await tools_router.drain_settlements(timeout=1)

    rec = (await env["client"].get(f"/receipts/x402:{TX}")).json()
    assert rec["settlement"]["status"] == "release-failed"
    assert "nonce too low" in rec["settlement"]["error"]


async def test_drain_waits_for_in_flight_tasks(env):
    await env["client"].post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})
    assert len(tools_router._settlement_tasks) == 1
    env["release"].gate.set()
    await tools_router.drain_settlements(timeout=1)
    assert tools_router._settlement_tasks == set()
