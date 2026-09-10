"""Replay protection and receipt lifecycle in services.payments_ledger."""

import pytest

from services import payments_ledger as ledger


async def test_claim_then_replay_is_rejected(fake_ledger):
    key = ledger.x402_key("0xABC")
    await ledger.claim_payment(key, rail="x402", tool_name="echo", payer="0xp", provider="0xq", amount_units=5)
    with pytest.raises(ledger.PaymentAlreadyUsed):
        await ledger.claim_payment(key, rail="x402", tool_name="echo", payer="0xp", provider="0xq", amount_units=5)


def test_x402_key_is_case_insensitive():
    assert ledger.x402_key("0xABCdef") == ledger.x402_key("0xabcDEF")


def test_nitrolite_key_uses_session_and_version_else_proof_hash():
    assert ledger.nitrolite_key("0xSESS", 3, "proof") == "nitrolite:0xsess:3"
    a = ledger.nitrolite_key(None, None, "proof-a")
    b = ledger.nitrolite_key(None, None, "proof-b")
    assert a != b and a.startswith("nitrolite:proof:")


async def test_receipt_lifecycle(fake_ledger):
    key = ledger.x402_key("0x1")
    await ledger.claim_payment(key, rail="x402", tool_name="echo", payer="0xp", provider="0xq", amount_units=5)
    receipt = await ledger.get_receipt(key)
    assert receipt["status"] == "processing"
    assert receipt["settlement"] == {"status": "pending"}
    assert receipt["paymentKey"] == key and "_id" not in receipt

    await ledger.record_delivery(key, True, {"status": "pending", "action": "release"})
    assert (await ledger.get_receipt(key))["status"] == "delivered"

    await ledger.record_settlement(key, {"status": "released", "releaseTxHash": "0xrel"})
    receipt = await ledger.get_receipt(key)
    assert receipt["settlement"]["status"] == "released"
    assert isinstance(receipt["created_at"], str)  # JSON-friendly


async def test_missing_receipt_is_none(fake_ledger):
    assert await ledger.get_receipt("x402:nope") is None
