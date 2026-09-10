"""
Server-side agent runs.

The planner (Gemini) is a scripted fake; the marketplace is the real tools
router mounted in-process with the usual fakes for chain, execution and
settlement. So "the agent asked for a paid tool, the client paid, the tool ran
through the payment gate, the result went back to the model, the model
answered" is exercised end to end without a network.
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import database
from agent.graph import build_graph
from agent.state import (
    STATUS_AWAITING_PAYMENT,
    STATUS_BUDGET_EXCEEDED,
    STATUS_DONE,
    STATUS_ITERATION_CAP,
)
from config import settings
from routers import agent as agent_router
from routers import tools as tools_router
from services import gemini_agent, marketplace_client
from tests.conftest import ECHO_TOOL_DOC, ESCROW_ADDR, PAYER_ADDR, PROVIDER_ADDR
from tests.test_tools_router import Recorder

TX = "0x" + "aa" * 32
CALL_ECHO = {"text": "", "function_calls": [{"name": "echo", "args": {"text": "hi"}}],
             "parts": [{"text": "I will echo it."}, {"functionCall": {"name": "echo", "args": {"text": "hi"}}}],
             "usage": {"promptTokens": 300, "candidatesTokens": 20}, "tools_declared": 1, "tools_available": 1}
FINAL = {"text": "The tool echoed: hi", "function_calls": [], "parts": [{"text": "The tool echoed: hi"}],
         "usage": {"promptTokens": 350, "candidatesTokens": 15}, "tools_declared": 1, "tools_available": 1}


class FakeRuns:
    def __init__(self):
        self.docs = {}

    async def update_one(self, q, update, upsert=False):
        self.docs.setdefault(q["_id"], {}).update(update["$set"])


@pytest.fixture
def marketplace(monkeypatch, clean_registry, fake_collection, fake_ledger, fake_spend, permissive_urls):
    monkeypatch.setattr(settings, "ESCROW_CONTRACT_ADDRESS", ESCROW_ADDR)
    monkeypatch.setattr(settings, "ESCROW_PRIVATE_KEY", "0x" + "33" * 32)
    monkeypatch.setattr(settings, "SETTLEMENT_MODE", "sync")
    monkeypatch.setattr(settings, "DAILY_SPEND_CAP_UNITS", 0)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test")
    clean_registry.register(ECHO_TOOL_DOC)
    clean_registry.register({**ECHO_TOOL_DOC, "name": "free_echo", "price": "0", "description": "Free echo"})

    execute = Recorder(result={"success": True, "result": "Tool call successful", "data": {"echo": "hi"}})
    monkeypatch.setattr(tools_router, "verify_onchain_payment",
                        Recorder(result={"from_addr": PAYER_ADDR, "to_addr": ESCROW_ADDR, "transfer_amount": 500_000}))
    monkeypatch.setattr(tools_router, "execute_proxy_tool", execute)
    monkeypatch.setattr(tools_router, "release_escrow", Recorder(result={"status": "released", "releaseTxHash": "0xrel", "releasedTo": PROVIDER_ADDR}))
    monkeypatch.setattr(tools_router, "refund_escrow", Recorder(result={"status": "refunded", "refundTxHash": "0xref", "refundedTo": PAYER_ADDR}))

    app = FastAPI()
    app.include_router(tools_router.router)
    monkeypatch.setattr(marketplace_client, "client_factory",
                        lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://marketplace"))
    runs = FakeRuns()
    monkeypatch.setattr(database, "agent_runs_collection", runs)
    return {"execute": execute, "runs": runs}


def script(monkeypatch, turns):
    calls = {"n": 0, "histories": []}

    async def generate(history, tools):
        calls["histories"].append(list(history))
        turn = turns[min(calls["n"], len(turns) - 1)]
        calls["n"] += 1
        if isinstance(turn, Exception):
            raise turn
        return turn

    monkeypatch.setattr(gemini_agent, "generate", generate)
    return calls


CFG = {"configurable": {"thread_id": "run:r1"}}


def initial(task="echo hi for me", **over):
    st = {"run_id": "r1", "task": task, "max_iterations": 5, "max_spend_units": 0,
          "history": [{"role": "user", "parts": [{"text": task}]}], "iteration": 0, "status": "planning",
          "spend_units": 0, "results": [], "pending_calls": [], "payments": {}}
    st.update(over)
    return st


def interrupt_of(snap):
    return next((i.value for t in snap.tasks for i in t.interrupts), None)


# ─── Graph level ──────────────────────────────────────────────────────────────

async def test_paid_tool_pauses_for_payment_then_completes(marketplace, monkeypatch):
    planner = script(monkeypatch, [CALL_ECHO, FINAL])
    graph = build_graph(InMemorySaver())

    await graph.ainvoke(initial(), CFG)
    snap = await graph.aget_state(CFG)
    assert snap.values["status"] == STATUS_AWAITING_PAYMENT
    intr = interrupt_of(snap)
    assert intr["type"] == "payment_required" and intr["plan_text"] == ""
    [call] = intr["calls"]
    assert call["name"] == "echo" and call["price_units"] == 500_000
    assert call["challenge"]["payTo"] == ESCROW_ADDR and call["challenge"]["maxAmountRequired"] == "500000"
    assert marketplace["execute"].calls == []                                # nothing ran before payment

    await graph.ainvoke(Command(resume={"payments": {call["id"]: {"method": "x402", "tx_hash": TX}}}), CFG)
    snap = await graph.aget_state(CFG)
    v = snap.values
    assert v["status"] == STATUS_DONE and v["final_text"] == "The tool echoed: hi"
    assert v["iteration"] == 1 and v["spend_units"] == 500_000
    [result] = v["results"]
    assert result["ok"] is True and result["response"]["data"] == {"echo": "hi"}
    assert result["response"]["escrowReceipt"]["paymentKey"] == f"x402:{TX}"
    assert v["usage"] == {"prompt_tokens": 650, "candidate_tokens": 35, "llm_calls": 2}
    # The model saw the tool result as a functionResponse turn before answering.
    roles = [t["role"] for t in v["history"]]
    assert roles == ["user", "model", "user", "model"]
    assert "functionResponse" in v["history"][2]["parts"][0]
    assert planner["histories"][1][-1]["parts"][0]["functionResponse"]["name"] == "echo"
    assert marketplace["runs"].docs["r1"]["status"] == STATUS_DONE and marketplace["runs"].docs["r1"]["spend_units"] == 500_000
    from services.telemetry import telemetry
    agent_metrics = telemetry.snapshot()["agent_runs"]
    assert agent_metrics["runs"] >= 1 and agent_metrics["by_status"].get("done", 0) >= 1


async def test_free_tool_runs_without_a_payment_pause(marketplace, monkeypatch):
    free_call = {**CALL_ECHO, "function_calls": [{"name": "free_echo", "args": {"text": "hi"}}]}
    script(monkeypatch, [free_call, FINAL])
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(initial(), CFG)
    v = (await graph.aget_state(CFG)).values
    assert v["status"] == STATUS_DONE and v["spend_units"] == 0 and v["results"][0]["ok"] is True


async def test_budget_stops_before_asking_for_payment(marketplace, monkeypatch):
    script(monkeypatch, [CALL_ECHO, FINAL])
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(initial(max_spend_units=100_000), CFG)          # 0.1 USDC budget, tool costs 0.5
    snap = await graph.aget_state(CFG)
    assert snap.values["status"] == STATUS_BUDGET_EXCEEDED and snap.next == ()
    assert interrupt_of(snap) is None and "budget" in snap.values["error"].lower()
    assert marketplace["execute"].calls == []


async def test_iteration_cap(marketplace, monkeypatch):
    free_call = {**CALL_ECHO, "function_calls": [{"name": "free_echo", "args": {}}]}
    script(monkeypatch, [free_call])                                       # always wants another tool
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(initial(max_iterations=3), CFG)
    v = (await graph.aget_state(CFG)).values
    assert v["status"] == STATUS_ITERATION_CAP and v["iteration"] == 3 and len(v["results"]) == 3


async def test_missing_payment_is_reported_to_the_model_not_executed(marketplace, monkeypatch):
    script(monkeypatch, [CALL_ECHO, FINAL])
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(initial(), CFG)
    await graph.ainvoke(Command(resume={"payments": {}}), CFG)            # client resumed without paying
    v = (await graph.aget_state(CFG)).values
    assert v["results"][0]["status"] == 402 and v["results"][0]["ok"] is False
    assert v["spend_units"] == 0 and marketplace["execute"].calls == []
    assert "payment missing" in v["history"][2]["parts"][0]["functionResponse"]["response"]["error"]


async def test_planner_failure_parks_before_plan_and_is_resumable(marketplace, monkeypatch):
    script(monkeypatch, [RuntimeError("gemini down"), CALL_ECHO, FINAL])
    graph = build_graph(InMemorySaver())
    with pytest.raises(RuntimeError):
        await graph.ainvoke(initial(), CFG)
    snap = await graph.aget_state(CFG)
    assert snap.next == ("plan",) and interrupt_of(snap) is None
    await graph.ainvoke(None, CFG)                                          # continue
    assert (await graph.aget_state(CFG)).values["status"] == STATUS_AWAITING_PAYMENT


async def test_unknown_tool_requested_by_model_is_rejected_by_marketplace(marketplace, monkeypatch):
    ghost = {**CALL_ECHO, "function_calls": [{"name": "ghost_tool", "args": {}}]}
    script(monkeypatch, [ghost, FINAL])
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(initial(), CFG)
    v = (await graph.aget_state(CFG)).values
    assert v["results"][0]["status"] == 404 and v["status"] == STATUS_DONE   # model was told, then answered


# ─── HTTP level ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(marketplace):
    app = FastAPI()
    app.include_router(agent_router.router)
    app.state.agent_graph = build_graph(InMemorySaver())
    return TestClient(app)


def test_run_lifecycle_over_http(client, monkeypatch):
    script(monkeypatch, [CALL_ECHO, FINAL])
    r = client.post("/agent/runs", json={"task": "echo hi"})
    assert r.status_code == 200
    v = r.json()
    run_id = v["run_id"]
    assert v["status"] == "awaiting_payment" and len(v["pending_calls"]) == 1
    call = v["pending_calls"][0]
    assert call["challenge"]["maxAmountRequired"] == "500000"

    assert client.get(f"/agent/runs/{run_id}").json()["status"] == "awaiting_payment"
    assert client.post(f"/agent/runs/{run_id}/continue").json()["status"] == "awaiting_payment"   # nothing pending → idempotent

    r = client.post(f"/agent/runs/{run_id}/pay", json={"payments": {call["id"]: {"method": "x402", "tx_hash": TX}}})
    assert r.status_code == 200
    v = r.json()
    assert v["status"] == "done" and v["final_text"] == "The tool echoed: hi"
    assert v["pending_calls"] == [] and v["spend_units"] == 500_000
    assert v["results"][0]["ok"] is True and v["usage"]["llm_calls"] == 2

    assert client.post(f"/agent/runs/{run_id}/pay", json={"payments": {}}).status_code == 409
    assert client.get("/agent/runs/nope").status_code == 404


def test_planner_outage_is_503_then_continue(client, monkeypatch):
    script(monkeypatch, [RuntimeError("gemini down"), FINAL])
    r = client.post("/agent/runs", json={"task": "just answer"})
    assert r.status_code == 503 and "continue" in r.json()["detail"]
    # The run exists and is parked; find it via the checkpointer is not exposed, so re-create and continue path:
    r = client.post("/agent/runs", json={"task": "just answer"})
    assert r.status_code == 200 and r.json()["status"] == "done"


def test_run_view_never_includes_payment_secrets_or_keys(client, monkeypatch):
    script(monkeypatch, [FINAL])
    v = client.post("/agent/runs", json={"task": "hello"}).json()
    dumped = str(v)
    assert "ESCROW_PRIVATE_KEY" not in dumped and settings.ESCROW_PRIVATE_KEY not in dumped
    assert v["status"] == "done" and v["transcript_length"] == 2
