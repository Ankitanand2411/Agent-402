"""
Payment ledger: replay protection and settlement receipts, in one collection.

Every verified payment is written to `payment_receipts` BEFORE the tool runs,
keyed by a deterministic payment key:

    x402:      "x402:<tx_hash>"
    nitrolite: "nitrolite:<app_session_id>:<state_version>"

The key is stored in `_id`, and MongoDB enforces uniqueness on `_id` with an
index every collection has by default. A second request carrying the same proof
therefore fails the insert with DuplicateKeyError, which the router turns into
HTTP 409. That is what makes tool execution idempotent per payment: the same
payment can never buy two executions, no matter how many times it is replayed
or how concurrently.

The same document is then updated with the delivery outcome and, once the
background settlement finishes, the escrow release/refund result. `GET
/receipts/{payment_key}` serves it, so a client that received
`settlement: pending` can poll for the final on-chain state.
"""

import hashlib
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

import database


class PaymentAlreadyUsed(Exception):
    """The payment behind this request has already bought an execution."""


def x402_key(tx_hash: str) -> str:
    return f"x402:{tx_hash.strip().lower()}"


def nitrolite_key(app_session_id: str | None, state_version, encoded_proof: str | None) -> str:
    """
    A Nitrolite payment is a specific signed state (session + version). If the
    proof somehow lacks a version, fall back to a hash of the whole proof so a
    byte-identical replay is still caught.
    """
    if app_session_id and state_version is not None:
        return f"nitrolite:{str(app_session_id).lower()}:{state_version}"
    digest = hashlib.sha256((encoded_proof or "").encode("utf-8")).hexdigest()
    return f"nitrolite:proof:{digest}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _collection():
    if database.payments_collection is None:
        raise RuntimeError("payments_collection is not initialised; call database.connect_db() first")
    return database.payments_collection


async def claim_payment(payment_key: str, **fields) -> dict:
    """
    Atomically claim a payment for execution. Raises PaymentAlreadyUsed if the
    key exists. Must be called before the tool runs.
    """
    doc = {
        "_id": payment_key,
        "status": "processing",
        "settlement": {"status": "pending"},
        "created_at": _now(),
        "updated_at": _now(),
        **fields,
    }
    try:
        await _collection().insert_one(doc)
    except DuplicateKeyError as e:
        raise PaymentAlreadyUsed(payment_key) from e
    return doc


async def record_delivery(payment_key: str, tool_success: bool, settlement: dict) -> None:
    await _collection().update_one(
        {"_id": payment_key},
        {"$set": {
            "status": "delivered" if tool_success else "failed",
            "settlement": settlement,
            "delivered_at": _now(),
            "updated_at": _now(),
        }},
    )


async def record_settlement(payment_key: str, settlement: dict) -> None:
    await _collection().update_one(
        {"_id": payment_key},
        {"$set": {"settlement": settlement, "settled_at": _now(), "updated_at": _now()}},
    )


async def recent_receipts(limit: int = 200) -> list[dict]:
    """Most recent ledger documents, for settlement statistics."""
    cursor = _collection().find({}).sort("created_at", -1).limit(limit)
    return [dict(d) for d in await cursor.to_list(limit)]


async def get_receipt(payment_key: str) -> dict | None:
    doc = await _collection().find_one({"_id": payment_key})
    if doc is None:
        return None
    doc = dict(doc)
    doc["paymentKey"] = doc.pop("_id")
    for k in ("created_at", "updated_at", "delivered_at", "settled_at"):
        if isinstance(doc.get(k), datetime):
            doc[k] = doc[k].isoformat()
    return doc
