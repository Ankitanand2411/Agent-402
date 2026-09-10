"""Telemetry aggregates, settlement statistics from ledger timestamps, and the admin /metrics endpoint."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from routers import metrics as metrics_router
from routers import tools as tools_router
from services import payments_ledger as ledger
from services import telemetry as tm
from tests.conftest import PAYER_ADDR, PROVIDER_ADDR

T0 = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
ADMIN = {"Authorization": "Bearer test-admin-key"}


@pytest.fixture(autouse=True)
def fresh():
    tm.telemetry.reset()
    yield
    tm.telemetry.reset()


# ─── Aggregates ───────────────────────────────────────────────────────────────

def test_percentile_nearest_rank():
    assert tm.percentile([], 95) is None
    assert tm.percentile([3, 1, 2], 50) == 2
    assert tm.percentile(list(range(1, 101)), 95) == 95


def test_gemini_turn_aggregates():
    tm.telemetry.record_gemini_turn(latency_ms=900, usage={"promptTokens": 3000, "candidatesTokens": 100},
                                    tools_declared=8, tools_available=40, function_calls=2)
    tm.telemetry.record_gemini_turn(latency_ms=1100, usage={}, tools_declared=8, tools_available=40, function_calls=0)
    tm.telemetry.record_gemini_turn(latency_ms=50, usage={}, tools_declared=0, tools_available=0, function_calls=0, error=True)
    g = tm.telemetry.snapshot()["gemini"]
    assert g["turns"] == 3 and g["errors"] == 1
    assert g["prompt_tokens"] == 3000 and g["avg_prompt_tokens"] == 3000.0
    assert g["usage_reported_ratio"] == pytest.approx(1 / 3, abs=0.01)
    assert g["avg_tools_declared"] == pytest.approx(16 / 3, abs=0.01)
    assert g["declaration_ratio"] == pytest.approx(16 / 80)
    assert g["p95_ms"] == 1100


def test_http_aggregates_by_route_template():
    for status in (200, 404, 500):
        tm.telemetry.record_request(route="/tools/{tool_name}", method="POST", status=status, latency_ms=20)
    s = tm.telemetry.snapshot()["http"]["POST /tools/{tool_name}"]
    assert s["count"] == 3 and s["errors"] == 1 and s["p50_ms"] == 20


# ─── Settlement stats from the ledger ─────────────────────────────────────────

def receipt(i, *, delivered_ms, settled_ms=None, status="released", rail="x402"):
    created = T0 + timedelta(seconds=i)
    doc = {"_id": f"x402:0x{i}", "rail": rail, "created_at": created, "settlement": {"status": status},
           "delivered_at": created + timedelta(milliseconds=delivered_ms)}
    if settled_ms is not None:
        doc["settled_at"] = created + timedelta(milliseconds=settled_ms)
    return doc


def test_settlement_stats_measure_request_path_saving():
    receipts = [
        receipt(1, delivered_ms=800, settled_ms=12800),
        receipt(2, delivered_ms=1200, settled_ms=13200),
        receipt(3, delivered_ms=1000, settled_ms=14000, status="refunded"),
        receipt(4, delivered_ms=900, status="pending"),                       # not yet settled
        receipt(5, delivered_ms=700, rail="nitrolite", status="not-applicable"),
    ]
    stats = tm.settlement_stats(receipts)
    assert stats["receipts"] == 5
    assert stats["by_rail"] == {"x402": 4, "nitrolite": 1}
    assert stats["by_settlement_status"] == {"released": 2, "refunded": 1, "pending": 1, "not-applicable": 1}
    assert stats["delivery"]["n"] == 5 and stats["delivery"]["p50_ms"] == 900
    assert stats["settlement"]["n"] == 3 and stats["settlement"]["p50_ms"] == 13200
    assert stats["request_path_saving_p50_ms"] == pytest.approx(13200 - 900)


def test_settlement_stats_empty():
    stats = tm.settlement_stats([])
    assert stats["receipts"] == 0 and stats["request_path_saving_p50_ms"] is None


async def test_ledger_records_delivery_and_settlement_timestamps(fake_ledger):
    key = ledger.x402_key("0xabc")
    await ledger.claim_payment(key, rail="x402", tool_name="echo", payer=PAYER_ADDR, provider=PROVIDER_ADDR, amount_units=1)
    await ledger.record_delivery(key, True, {"status": "pending"})
    await ledger.record_settlement(key, {"status": "released"})
    [doc] = await ledger.recent_receipts(10)
    assert doc["created_at"] <= doc["delivered_at"] <= doc["settled_at"]
    view = await ledger.get_receipt(key)
    assert isinstance(view["delivered_at"], str) and isinstance(view["settled_at"], str)


# ─── /metrics endpoint ────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, fake_ledger):
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "test-admin-key")
    app = FastAPI()
    app.include_router(metrics_router.router)
    app.include_router(tools_router.router)
    return TestClient(app), fake_ledger


def test_metrics_requires_admin(client):
    c, _ = client
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_metrics_includes_ledger_settlement_stats(client):
    c, fake = client
    fake.docs.update({d["_id"]: d for d in [receipt(1, delivered_ms=800, settled_ms=12800), receipt(2, delivered_ms=1000, settled_ms=13000)]})
    r = c.get("/metrics?receipts=50", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["settlement"]["receipts"] == 2
    assert body["settlement"]["request_path_saving_p50_ms"] == pytest.approx(12000)
    assert "gemini" in body and "http" in body


def test_metrics_survives_ledger_failure(client, monkeypatch):
    c, _ = client

    async def boom(limit=200):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(ledger, "recent_receipts", boom)
    r = c.get("/metrics", headers=ADMIN)
    assert r.status_code == 200 and "error" in r.json()["settlement"]
