"""Atomic per-wallet daily cap in services.spend_caps."""

import asyncio

from services import spend_caps

PAYER = "0xPayer"
CAP = 1_000_000  # 1 USDC / day


async def test_cap_disabled_allows_everything(fake_spend):
    assert await spend_caps.try_reserve(PAYER, 10**12, cap_units=0) is True


async def test_reservations_accumulate_until_cap(fake_spend):
    assert await spend_caps.try_reserve(PAYER, 400_000, CAP) is True
    assert await spend_caps.try_reserve(PAYER, 400_000, CAP) is True
    assert await spend_caps.spent_today(PAYER) == 800_000
    assert await spend_caps.try_reserve(PAYER, 400_000, CAP) is False   # 1.2M > cap
    assert await spend_caps.spent_today(PAYER) == 800_000               # rejected attempt did not count
    assert await spend_caps.try_reserve(PAYER, 200_000, CAP) is True    # exactly the cap is allowed


async def test_single_payment_above_cap_is_rejected_without_writing(fake_spend):
    assert await spend_caps.try_reserve(PAYER, CAP + 1, CAP) is False
    assert await spend_caps.spent_today(PAYER) == 0


async def test_refund_releases_budget(fake_spend):
    await spend_caps.try_reserve(PAYER, CAP, CAP)
    assert await spend_caps.try_reserve(PAYER, 1, CAP) is False
    await spend_caps.release(PAYER, CAP)
    assert await spend_caps.try_reserve(PAYER, 1, CAP) is True


async def test_caps_are_per_wallet(fake_spend):
    assert await spend_caps.try_reserve("0xA", CAP, CAP) is True
    assert await spend_caps.try_reserve("0xB", CAP, CAP) is True


async def test_concurrent_reservations_never_exceed_cap(fake_spend):
    # Ten simultaneous 300k requests against a 1M cap: exactly three may win.
    results = await asyncio.gather(*(spend_caps.try_reserve(PAYER, 300_000, CAP) for _ in range(10)))
    assert results.count(True) == 3
    assert await spend_caps.spent_today(PAYER) == 900_000
