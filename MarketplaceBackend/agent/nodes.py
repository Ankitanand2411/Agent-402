"""
Nodes of the agent run graph.

    plan ──(no tool calls)──► finish
      │
      └─(tool calls)─► [await_payment if anything costs money] ─► execute ─► plan …

`plan` asks Gemini what to do with the relevant tools declared. If the model
requests tools that cost money, `await_payment` interrupts with one payment
challenge per call; the browser pays with its own wallets and resumes with
the tx hashes. `execute` then calls POST /tools/{name} on this very service,
so verification, the replay ledger, the spend cap and settlement all apply,
and appends the results as functionResponse parts for the next `plan`.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from langgraph.types import interrupt

import database
import registry
from agent.state import (
    STATUS_AWAITING_PAYMENT,
    STATUS_BUDGET_EXCEEDED,
    STATUS_DONE,
    STATUS_EXECUTING,
    STATUS_ITERATION_CAP,
    STATUS_PLANNING,
    AgentRunState,
)
from config import settings
from services import gemini_agent
from services.marketplace_client import call_tool, payment_challenge
from services.pricing import price_to_units
from services.telemetry import telemetry

logger = logging.getLogger(__name__)


async def plan(state: AgentRunState) -> dict[str, Any]:
    """One Gemini turn. Either a final answer or a set of tool calls to pay for and run."""
    catalog = registry.marketplace_view()
    turn = await gemini_agent.generate(state["history"], catalog)
    usage = {"prompt_tokens": turn["usage"].get("promptTokens") or 0, "candidate_tokens": turn["usage"].get("candidatesTokens") or 0, "llm_calls": 1}
    model_turn = {"role": "model", "parts": turn["parts"]}

    if not turn["function_calls"]:
        return {"history": [model_turn], "plan_text": turn["text"], "final_text": turn["text"], "status": STATUS_DONE, "usage": usage, "resume": None}

    pending = []
    for idx, fc in enumerate(turn["function_calls"]):
        tool = registry.get(fc["name"])
        price_units = price_to_units(tool["price"], settings.TOKEN_DECIMALS) if tool else 0
        pending.append({
            "id": f"{state.get('iteration', 0)}-{idx}",
            "name": fc["name"],
            "args": fc["args"],
            "known": tool is not None,
            "price_units": price_units,
            "challenge": payment_challenge(fc["name"], price_units, (tool or {}).get("walletAddress")) if price_units > 0 else None,
        })

    budget = state.get("max_spend_units") or 0
    projected = state.get("spend_units", 0) + sum(c["price_units"] for c in pending)
    if budget and projected > budget:
        return {
            "history": [model_turn], "plan_text": turn["text"], "pending_calls": pending, "status": STATUS_BUDGET_EXCEEDED,
            "error": f"Run budget of {budget} units would be exceeded ({projected} needed)", "usage": usage, "resume": None,
        }

    needs_payment = any(c["price_units"] > 0 for c in pending)
    return {
        "history": [model_turn], "plan_text": turn["text"], "pending_calls": pending,
        "status": STATUS_AWAITING_PAYMENT if needs_payment else STATUS_EXECUTING, "usage": usage, "resume": None,
    }


def route_after_plan(state: AgentRunState) -> str:
    status = state.get("status")
    if status == STATUS_AWAITING_PAYMENT:
        return "await_payment"
    if status == STATUS_EXECUTING:
        return "execute"
    return "finish"


async def await_payment(state: AgentRunState) -> dict[str, Any]:
    """Pause until the client has paid for every priced call in this step."""
    calls = [c for c in state.get("pending_calls", []) if c["price_units"] > 0]
    payload = interrupt({"type": "payment_required", "run_id": state["run_id"], "calls": calls, "plan_text": state.get("plan_text", "")})
    return {"payments": dict((payload or {}).get("payments") or {}), "resume": payload}


async def execute(state: AgentRunState) -> dict[str, Any]:
    """Run every pending call through the paid endpoint; append results for the model."""
    payments = state.get("payments") or {}
    results, parts, spent = [], [], 0
    for call in state.get("pending_calls", []):
        payment = payments.get(call["id"])
        if call["price_units"] > 0 and not payment:
            outcome = {"status": 402, "body": {"error": "payment missing for this call"}}
        else:
            outcome = await call_tool(call["name"], call["args"], payment)
        ok = outcome["status"] == 200
        if ok:
            spent += call["price_units"]
        record = {"id": call["id"], "name": call["name"], "args": call["args"], "status": outcome["status"], "ok": ok,
                  "price_units": call["price_units"], "response": outcome["body"]}
        results.append(record)
        parts.append({"functionResponse": {"name": call["name"], "response": outcome["body"] if isinstance(outcome["body"], dict) else {"result": outcome["body"]}}})
    return {
        "history": [{"role": "user", "parts": parts}], "results": results, "spend_units": state.get("spend_units", 0) + spent,
        "iteration": state.get("iteration", 0) + 1, "pending_calls": [], "payments": {}, "status": STATUS_PLANNING, "resume": None,
    }


def route_after_execute(state: AgentRunState) -> str:
    return "plan" if state.get("iteration", 0) < state.get("max_iterations", settings.AGENT_MAX_ITERATIONS) else "finish"


async def finish(state: AgentRunState) -> dict[str, Any]:
    status = state.get("status")
    if status == STATUS_PLANNING:          # arrived from the iteration cap
        status = STATUS_ITERATION_CAP
    results = state.get("results", [])
    logger.info("[Agent] run %s finished: %s (iterations=%s, tool calls=%s, spend=%s units)",
                state.get("run_id"), status, state.get("iteration"), len(results), state.get("spend_units", 0))
    telemetry.record_agent_run(status=status, iterations=state.get("iteration", 0), tool_calls=len(results),
                               tool_failures=sum(1 for r in results if not r["ok"]), spend_units=state.get("spend_units", 0))
    await _record_run(state, status)
    return {"status": status, "resume": None}


async def _record_run(state: AgentRunState, status: str) -> None:
    """Compact summary for analytics and evals (the checkpoint has the full transcript). Fail-soft."""
    coll = database.agent_runs_collection
    if coll is None:
        return
    results = state.get("results", [])
    doc = {
        "_id": state["run_id"],
        "task": state.get("task"),
        "status": status,
        "iterations": state.get("iteration", 0),
        "tool_calls": [{"name": r["name"], "ok": r["ok"], "price_units": r["price_units"]} for r in results],
        "spend_units": state.get("spend_units", 0),
        "usage": state.get("usage") or {},
        "final_text": state.get("final_text"),
        "error": state.get("error"),
        "finished_at": datetime.now(timezone.utc),
    }
    try:
        await coll.update_one({"_id": state["run_id"]}, {"$set": doc}, upsert=True)
    except Exception as e:
        logger.warning("[Agent] could not record run %s: %s", state.get("run_id"), e)
