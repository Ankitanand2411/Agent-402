"""
/agent/runs — server-side agent runs.

    POST /agent/runs                 {task, max_iterations?, max_spend_units?}   → view
    GET  /agent/runs/{id}                                                        → view
    POST /agent/runs/{id}/pay        {payments: {call_id: {method, tx_hash | proof, from}}} → view
    POST /agent/runs/{id}/continue   re-run a step that failed (model outage)   → view

The run is a LangGraph thread. When the plan needs paid tools the run parks at
an interrupt and the view carries `pending_calls` with one payment challenge
each; the client pays with its own wallets and posts the proofs to /pay. The
server never holds a payer key. Run ids are unguessable (128-bit) and act as
the capability to read or advance a run.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent.state import STATUS_AWAITING_PAYMENT, STATUS_PLANNING
from config import settings

router = APIRouter(prefix="/agent", tags=["Agent"])

_MODEL_UNAVAILABLE = "The planner is temporarily unavailable; retry with POST /agent/runs/{id}/continue."


class StartRunRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=4000)
    max_iterations: int | None = Field(None, ge=1, le=20)
    max_spend_units: int | None = Field(None, ge=0)
    # Prior conversation as JS-style turns ({role: user|model, parts: [{text}]}); text parts only.
    history: list[dict[str, Any]] | None = Field(None, max_length=40)


def _clean_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for turn in history or []:
        role = "model" if turn.get("role") in ("model", "assistant") else "user"
        parts = [{"text": str(p.get("text") if isinstance(p, dict) else p)[:4000]}
                 for p in turn.get("parts", []) if (isinstance(p, dict) and p.get("text")) or isinstance(p, str)]
        if parts:
            out.append({"role": role, "parts": parts})
    return out


class PayRequest(BaseModel):
    payments: dict[str, dict[str, Any]]


def get_graph(request: Request):
    graph = getattr(request.app.state, "agent_graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="Agent service is not initialised")
    return graph


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": f"run:{run_id}"}}


def _interrupt(snapshot):
    for task in snapshot.tasks:
        for intr in task.interrupts:
            return intr.value
    return None


def _pending_step(snapshot) -> str | None:
    if _interrupt(snapshot) is not None:
        return None
    return snapshot.next[0] if snapshot.next else None


def view(run_id: str, snapshot) -> dict[str, Any]:
    v = snapshot.values
    intr = _interrupt(snapshot) or {}
    return {
        "run_id": run_id,
        "status": v.get("status", STATUS_PLANNING),
        "iteration": v.get("iteration", 0),
        "max_iterations": v.get("max_iterations"),
        "max_spend_units": v.get("max_spend_units", 0),
        "spend_units": v.get("spend_units", 0),
        "plan_text": v.get("plan_text", ""),
        "pending_calls": intr.get("calls", []) if intr.get("type") == "payment_required" else [],
        "results": v.get("results", []),
        "usage": v.get("usage") or {"prompt_tokens": 0, "candidate_tokens": 0, "llm_calls": 0},
        "final_text": v.get("final_text"),
        "error": v.get("error"),
        "pending_step": _pending_step(snapshot),
        "transcript_length": len(v.get("history", [])),
    }


async def _load(graph, run_id: str):
    snapshot = await graph.aget_state(_config(run_id))
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="Run not found")
    return snapshot


async def _advance(graph, run_id: str, payload) -> dict[str, Any]:
    try:
        await graph.ainvoke(payload, _config(run_id))
    except Exception as e:  # planner or marketplace failure: checkpoint stays parked before the failed node
        raise HTTPException(status_code=503, detail=f"{_MODEL_UNAVAILABLE} ({str(e)[:120]})") from e
    return view(run_id, await graph.aget_state(_config(run_id)))


@router.post("/runs")
async def start_run(request: Request, body: StartRunRequest):
    graph = get_graph(request)
    run_id = uuid.uuid4().hex
    initial = {
        "run_id": run_id,
        "task": body.task,
        "max_iterations": body.max_iterations or settings.AGENT_MAX_ITERATIONS,
        "max_spend_units": body.max_spend_units if body.max_spend_units is not None else settings.AGENT_DEFAULT_MAX_SPEND_UNITS,
        "history": _clean_history(body.history) + [{"role": "user", "parts": [{"text": body.task}]}],
        "iteration": 0,
        "status": STATUS_PLANNING,
        "spend_units": 0,
        "results": [],
        "pending_calls": [],
        "payments": {},
    }
    return await _advance(graph, run_id, initial)


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str):
    graph = get_graph(request)
    return view(run_id, await _load(graph, run_id))


@router.post("/runs/{run_id}/pay")
async def pay_run(request: Request, run_id: str, body: PayRequest):
    graph = get_graph(request)
    snapshot = await _load(graph, run_id)
    if _interrupt(snapshot) is None or snapshot.values.get("status") != STATUS_AWAITING_PAYMENT:
        raise HTTPException(status_code=409, detail="This run is not waiting for payment")
    return await _advance(graph, run_id, Command(resume={"payments": body.payments}))


@router.post("/runs/{run_id}/continue")
async def continue_run(request: Request, run_id: str):
    graph = get_graph(request)
    snapshot = await _load(graph, run_id)
    if not _pending_step(snapshot):
        return view(run_id, snapshot)
    return await _advance(graph, run_id, None)
