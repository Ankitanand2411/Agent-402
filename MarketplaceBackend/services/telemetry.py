"""
Telemetry: numbers for every request, every Gemini turn, and every settled payment.

- HTTP: per route template, count / p50 / p95 / max latency / 5xx count.
- Gemini turns: latency, prompt/candidate tokens, tools declared vs available
  (the tool-retrieval saving), function calls per turn.
- Settlement: computed from the payment ledger's timestamps rather than from
  memory, so it survives restarts and covers every instance:
      delivery_ms   = delivered_at - created_at   (what the caller waited for)
      settlement_ms = settled_at   - created_at   (when funds actually moved)
  In sync mode the caller waits for settlement; in async mode only for
  delivery. The gap between the two medians is the latency the async change
  removed from the request path.

HTTP and Gemini aggregates are in-memory and per process (they say so).
"""

import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

MAX_SAMPLES = 5000


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def _summary(lat: list[float]) -> dict:
    return {"p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95), "max_ms": max(lat) if lat else None}


@dataclass
class _Series:
    count: int = 0
    errors: int = 0
    latencies_ms: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))


@dataclass
class _GeminiSeries:
    turns: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    usage_reported: int = 0
    tools_declared: int = 0
    tools_available: int = 0
    function_calls: int = 0
    latencies_ms: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))


@dataclass
class _AgentSeries:
    runs: int = 0
    iterations: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    spend_units: int = 0
    by_status: dict = field(default_factory=lambda: defaultdict(int))


class Telemetry:
    def __init__(self):
        self.started_at = datetime.now(timezone.utc)
        self.http: dict[str, _Series] = defaultdict(_Series)
        self.gemini = _GeminiSeries()
        self.agent = _AgentSeries()

    def record_agent_run(self, *, status: str, iterations: int, tool_calls: int, tool_failures: int, spend_units: int) -> None:
        a = self.agent
        a.runs += 1
        a.iterations += iterations
        a.tool_calls += tool_calls
        a.tool_failures += tool_failures
        a.spend_units += spend_units
        a.by_status[status] += 1

    def record_request(self, *, route: str, method: str, status: int, latency_ms: float) -> None:
        s = self.http[f"{method} {route}"]
        s.count += 1
        s.errors += int(status >= 500)
        s.latencies_ms.append(latency_ms)

    def record_gemini_turn(self, *, latency_ms: float, usage: dict, tools_declared: int, tools_available: int,
                           function_calls: int, error: bool = False) -> None:
        g = self.gemini
        g.turns += 1
        g.errors += int(error)
        pt, ct = usage.get("promptTokens"), usage.get("candidatesTokens")
        if pt is not None:
            g.prompt_tokens += int(pt)
            g.usage_reported += 1
        if ct is not None:
            g.candidate_tokens += int(ct)
        g.tools_declared += tools_declared
        g.tools_available += tools_available
        g.function_calls += function_calls
        g.latencies_ms.append(latency_ms)

    def snapshot(self) -> dict:
        g = self.gemini
        turns = g.turns or 1
        return {
            "since": self.started_at.isoformat(),
            "process": os.getpid(),
            "http": {
                route: {"count": s.count, "errors": s.errors, **_summary(list(s.latencies_ms))}
                for route, s in sorted(self.http.items())
            },
            "gemini": {
                "turns": g.turns,
                "errors": g.errors,
                **_summary(list(g.latencies_ms)),
                "prompt_tokens": g.prompt_tokens,
                "candidate_tokens": g.candidate_tokens,
                "avg_prompt_tokens": round(g.prompt_tokens / g.usage_reported, 1) if g.usage_reported else None,
                "usage_reported_ratio": round(g.usage_reported / g.turns, 2) if g.turns else None,
                "avg_tools_declared": round(g.tools_declared / turns, 2) if g.turns else None,
                "avg_tools_available": round(g.tools_available / turns, 2) if g.turns else None,
                "declaration_ratio": round(g.tools_declared / g.tools_available, 3) if g.tools_available else None,
                "function_calls_per_turn": round(g.function_calls / turns, 2) if g.turns else None,
            },
            "agent_runs": {
                "runs": self.agent.runs,
                "by_status": dict(self.agent.by_status),
                "avg_iterations": round(self.agent.iterations / self.agent.runs, 2) if self.agent.runs else None,
                "avg_tool_calls": round(self.agent.tool_calls / self.agent.runs, 2) if self.agent.runs else None,
                "tool_failure_ratio": round(self.agent.tool_failures / self.agent.tool_calls, 3) if self.agent.tool_calls else None,
                "avg_spend_units": round(self.agent.spend_units / self.agent.runs, 1) if self.agent.runs else None,
            },
            "note": "http/gemini/agent_runs are in-memory per process and reset on restart; settlement comes from the ledger.",
        }

    def reset(self) -> None:
        self.http.clear()
        self.gemini = _GeminiSeries()
        self.agent = _AgentSeries()
        self.started_at = datetime.now(timezone.utc)


telemetry = Telemetry()


def settlement_stats(receipts: list[dict]) -> dict:
    """
    Latency statistics from ledger documents (see payments_ledger). Each
    receipt may carry created_at, delivered_at, settled_at (datetimes).
    """
    delivery, settlement, by_status, by_rail = [], [], defaultdict(int), defaultdict(int)
    for r in receipts:
        created, delivered, settled = r.get("created_at"), r.get("delivered_at"), r.get("settled_at")
        by_rail[r.get("rail", "unknown")] += 1
        by_status[(r.get("settlement") or {}).get("status", "unknown")] += 1
        if created and delivered:
            delivery.append((delivered - created).total_seconds() * 1000)
        if created and settled:
            settlement.append((settled - created).total_seconds() * 1000)
    d, s = _summary(delivery), _summary(settlement)
    gap = (s["p50_ms"] - d["p50_ms"]) if (s["p50_ms"] is not None and d["p50_ms"] is not None) else None
    return {
        "receipts": len(receipts),
        "by_rail": dict(by_rail),
        "by_settlement_status": dict(by_status),
        "delivery": {"n": len(delivery), **d},
        "settlement": {"n": len(settlement), **s},
        "request_path_saving_p50_ms": round(gap, 1) if gap is not None else None,
        "explanation": "delivery = time until the tool result was ready; settlement = time until the on-chain release/refund "
                       "confirmed. In sync mode the caller waited for settlement; in async mode only for delivery.",
    }
