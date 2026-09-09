"""
Tests for services.payment_verifier.

A fake `Web3` object returns hand-built receipts, so every rule in
verify_onchain_payment is exercised without an RPC node:
  emitter must be USDC, recipient must be the escrow, amount >= price,
  the transaction must have succeeded, and receipt polling must retry.
"""

import pytest
from hexbytes import HexBytes
from web3 import Web3

from config import settings
from services import payment_verifier as pv
from tests.conftest import ESCROW_ADDR, PAYER_ADDR, PROVIDER_ADDR

TX = "0x" + "ab" * 32
PRICE = 500_000  # 0.5 USDC


def _topic_addr(addr: str) -> HexBytes:
    """Encode an address the way an indexed event parameter appears: 32 bytes, left-padded."""
    return HexBytes(bytes(12) + bytes.fromhex(addr[2:]))


def make_receipt(*, emitter=None, to=ESCROW_ADDR, sender=PAYER_ADDR, amount=PRICE, status=1, extra_logs=()):
    emitter = emitter or settings.TOKEN_CONTRACT_ADDR
    transfer_log = {
        "address": Web3.to_checksum_address(emitter),
        "topics": [pv.TRANSFER_TOPIC, _topic_addr(sender), _topic_addr(to)],
        "data": HexBytes(amount.to_bytes(32, "big")),
    }
    return {"status": status, "logs": [*extra_logs, transfer_log]}


class FakeEth:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get_transaction_receipt(self, tx_hash):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeW3:
    def __init__(self, responses):
        self.eth = FakeEth(responses)


@pytest.fixture
def w3(monkeypatch):
    """Install a fake web3 whose receipt responses the test sets via `w3.use([...])`."""
    holder = {}

    def use(responses):
        holder["w3"] = FakeW3(responses)
        return holder["w3"]

    monkeypatch.setattr(pv, "_get_w3", lambda: holder["w3"])
    monkeypatch.setattr(pv, "RECEIPT_POLL_INTERVAL_SECONDS", 0)  # don't sleep in tests
    use.holder = holder
    return use


async def test_valid_payment_is_accepted(w3):
    w3([make_receipt()])
    result = await pv.verify_onchain_payment(TX, ESCROW_ADDR, PRICE)
    assert result == {"from_addr": PAYER_ADDR, "to_addr": ESCROW_ADDR, "transfer_amount": PRICE}


async def test_overpayment_is_accepted(w3):
    w3([make_receipt(amount=PRICE + 1)])
    result = await pv.verify_onchain_payment(TX, ESCROW_ADDR, PRICE)
    assert result["transfer_amount"] == PRICE + 1


async def test_underpayment_is_rejected(w3):
    w3([make_receipt(amount=PRICE - 1)])
    with pytest.raises(ValueError, match="Insufficient payment"):
        await pv.verify_onchain_payment(TX, ESCROW_ADDR, PRICE)


async def test_transfer_to_wrong_recipient_is_rejected(w3):
    w3([make_receipt(to=PROVIDER_ADDR)])  # paid the provider directly, bypassing escrow
    with pytest.raises(ValueError, match="expected escrow contract"):
        await pv.verify_onchain_payment(TX, ESCROW_ADDR, PRICE)


async def test_transfer_from_wrong_token_is_rejected(w3):
    fake_token = "0x000000000000000000000000000000000000dEaD"
    w3([make_receipt(emitter=fake_token)])  # a worthless token with the same event signature
    with pytest.raises(ValueError, match="No token transfer found"):
        await pv.verify_onchain_payment(TX, ESCROW_ADDR, PRICE)


async def test_non_transfer_logs_are_ignored(w3):
    approval_topic = HexBytes(Web3.keccak(text="Approval(address,address,uint256)"))
    decoy = {
        "address": Web3.to_checksum_address(settings.TOKEN_CONTRACT_ADDR),
        "topics": [approval_topic, _topic_addr(PAYER_ADDR), _topic_addr(ESCROW_ADDR)],
        "data": HexBytes((10**12).to_bytes(32, "big")),
    }
    w3([make_receipt(extra_logs=[decoy])])
    result = await pv.verify_onchain_payment(TX, ESCROW_ADDR, PRICE)
    assert result["transfer_amount"] == PRICE  # the decoy's huge amount was not used


async def test_reverted_transaction_is_rejected(w3):
    w3([make_receipt(status=0)])
    with pytest.raises(ValueError, match="reverted"):
        await pv.verify_onchain_payment(TX, ESCROW_ADDR, PRICE)


async def test_receipt_polling_retries_until_found(w3):
    fake = w3([None, Exception("not yet mined"), make_receipt()])
    result = await pv.verify_onchain_payment(TX, ESCROW_ADDR, PRICE)
    assert result["transfer_amount"] == PRICE
    assert fake.eth.calls == 3


async def test_receipt_never_found_gives_up_after_max_attempts(w3):
    fake = w3([None] * pv.RECEIPT_POLL_ATTEMPTS)
    with pytest.raises(ValueError, match="not found"):
        await pv.verify_onchain_payment(TX, ESCROW_ADDR, PRICE)
    assert fake.eth.calls == pv.RECEIPT_POLL_ATTEMPTS


def test_transfer_topic_matches_erc20_signature():
    # Well-known constant; if this changes, every verification would silently fail.
    assert pv.TRANSFER_TOPIC.hex().lower().lstrip("0x") == (
        "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    )
