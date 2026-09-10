"""
GET /metrics — admin-only numbers: HTTP routes, Gemini turns, and settlement
latency derived from the payment ledger.
"""

from fastapi import APIRouter, Depends, Query

from services import payments_ledger as ledger
from services.auth import require_admin
from services.telemetry import settlement_stats, telemetry

router = APIRouter()


@router.get("/metrics", dependencies=[Depends(require_admin)])
async def get_metrics(receipts: int = Query(200, ge=1, le=2000)):
    snapshot = telemetry.snapshot()
    try:
        snapshot["settlement"] = settlement_stats(await ledger.recent_receipts(receipts))
    except Exception as e:  # ledger unavailable (e.g. no Mongo in dev) must not hide the rest
        snapshot["settlement"] = {"error": str(e)}
    return snapshot
