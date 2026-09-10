"""
State of one server-side agent run.

`history` is the Gemini conversation in JS-style turns ({role, parts}); the
model's own turns (text + functionCall parts) and our functionResponse turns
are appended by the nodes, so the checkpoint is a complete record of what the
agent saw and decided. That record is what makes runs replayable and evaluable.
"""

import operator
from typing import Annotated, Any, TypedDict

STATUS_PLANNING = "planning"
STATUS_AWAITING_PAYMENT = "awaiting_payment"
STATUS_EXECUTING = "executing"
STATUS_DONE = "done"
STATUS_ITERATION_CAP = "iteration_cap"
STATUS_BUDGET_EXCEEDED = "budget_exceeded"


def add_usage(a: dict | None, b: dict | None) -> dict:
    a, b = a or {}, b or {}
    return {k: (a.get(k) or 0) + (b.get(k) or 0) for k in ("prompt_tokens", "candidate_tokens", "llm_calls")}


class AgentRunState(TypedDict, total=False):
    run_id: str
    task: str
    max_iterations: int
    max_spend_units: int                     # 0 = unlimited

    history: Annotated[list[dict[str, Any]], operator.add]
    iteration: int
    status: str
    plan_text: str

    pending_calls: list[dict[str, Any]]      # [{id, name, args, price_units, challenge}]
    payments: dict[str, Any]                 # call id -> {method, tx_hash | proof, from}
    results: Annotated[list[dict[str, Any]], operator.add]
    spend_units: int
    usage: Annotated[dict[str, Any], add_usage]

    final_text: str | None
    error: str | None
    resume: dict[str, Any] | None
