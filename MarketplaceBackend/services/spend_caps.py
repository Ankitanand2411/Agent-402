"""
Per-wallet daily spend cap, enforced atomically.

Why a server-side cap at all, when the payer is a self-custodial agent wallet?
For the same reason banks have daily limits: it bounds the damage from a
runaway agent loop or a leaked worker key. By the time the server sees a
payment it has already happened on-chain, so the remedy is "do not execute,
refund", which is what the router does when `try_reserve` returns False.

Atomicity: a naive "read today's total, compare, then increment" lets two
concurrent requests both pass the check. Instead one counter document per
(payer, UTC day) is updated with a single find_one_and_update whose FILTER
includes the cap:

    filter: {_id: key, spent: {$lte: cap - amount}}
    update: {$inc: {spent: amount}}, upsert: true

MongoDB applies filter+update as one operation. If the document exists and the
filter fails, the upsert tries to INSERT a new document with the same _id and
hits the unique index -> DuplicateKeyError, which we read as "cap exceeded".
No window exists between the check and the increment.
"""

from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import database


def _day_key(payer: str, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return f"{payer.lower()}:{when.strftime('%Y-%m-%d')}"


def _collection():
    if database.spend_collection is None:
        raise RuntimeError("spend_collection is not initialised; call database.connect_db() first")
    return database.spend_collection


async def try_reserve(payer: str, amount_units: int, cap_units: int) -> bool:
    """Reserve `amount_units` of today's budget for `payer`. False if it would exceed the cap."""
    if cap_units <= 0:
        return True  # cap disabled
    if amount_units > cap_units:
        return False
    try:
        await _collection().find_one_and_update(
            {"_id": _day_key(payer), "spent": {"$lte": cap_units - amount_units}},
            {"$inc": {"spent": amount_units}, "$set": {"payer": payer.lower()}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return True
    except DuplicateKeyError:
        return False


async def release(payer: str, amount_units: int) -> None:
    """Give budget back when a payment is refunded (failed tool)."""
    await _collection().update_one({"_id": _day_key(payer)}, {"$inc": {"spent": -amount_units}})


async def spent_today(payer: str) -> int:
    doc = await _collection().find_one({"_id": _day_key(payer)})
    return int(doc.get("spent", 0)) if doc else 0
