"""
x402 on-chain payment verifier using web3.py.

Verification rules for a payment transaction:
  1. the receipt exists and the transaction succeeded (status == 1)
  2. it contains an ERC-20 `Transfer` event emitted by the USDC contract
  3. the transfer's recipient is the escrow contract
  4. the transferred amount is at least the tool price (in atomic units)
"""
import asyncio

from hexbytes import HexBytes
from web3 import Web3

from config import settings

# keccak256("Transfer(address,address,uint256)") — the topic[0] of every ERC-20 Transfer log.
TRANSFER_TOPIC = HexBytes(Web3.keccak(text="Transfer(address,address,uint256)"))

# Receipt polling. A transaction can take a few seconds to be mined after the
# client sends its hash, so we poll instead of failing on the first miss.
RECEIPT_POLL_ATTEMPTS = 5
RECEIPT_POLL_INTERVAL_SECONDS = 1.0


def _get_w3() -> Web3:
    return Web3(Web3.HTTPProvider(settings.SEPOLIA_RPC))


def _topic_address(topic) -> str:
    """An indexed address topic is 32 bytes, left-padded; the address is the last 20."""
    return Web3.to_checksum_address("0x" + HexBytes(topic).hex()[-40:])


async def _fetch_receipt(w3: Web3, tx_hash: str):
    receipt = None
    for attempt in range(RECEIPT_POLL_ATTEMPTS):
        try:
            receipt = await asyncio.to_thread(w3.eth.get_transaction_receipt, tx_hash)
        except Exception:
            receipt = None
        if receipt:
            return receipt
        if attempt < RECEIPT_POLL_ATTEMPTS - 1:
            await asyncio.sleep(RECEIPT_POLL_INTERVAL_SECONDS)
    return None


async def verify_onchain_payment(
    tx_hash: str,
    escrow_addr: str,
    price_units: int,
) -> dict:
    """
    Verify that a USDC transfer of at least `price_units` was sent to the escrow contract.
    Returns dict with {from_addr, to_addr, transfer_amount} on success.
    Raises ValueError with a descriptive message on failure.
    """
    w3 = _get_w3()

    receipt = await _fetch_receipt(w3, tx_hash)
    if not receipt:
        raise ValueError("Transaction receipt not found on Sepolia Testnet after retries")

    # A reverted transaction emits no logs, but be explicit rather than rely on that.
    if receipt.get("status") is not None and int(receipt["status"]) != 1:
        raise ValueError("Transaction reverted on-chain")

    token_addr = settings.TOKEN_CONTRACT_ADDR
    transfer_log = next(
        (
            log
            for log in receipt["logs"]
            if str(log["address"]).lower() == token_addr.lower()
            and len(log["topics"]) >= 3
            and HexBytes(log["topics"][0]) == TRANSFER_TOPIC
        ),
        None,
    )

    if not transfer_log:
        raise ValueError("No token transfer found in transaction")

    from_addr = _topic_address(transfer_log["topics"][1])
    to_addr = _topic_address(transfer_log["topics"][2])
    transfer_amount = int.from_bytes(HexBytes(transfer_log["data"]), "big")

    if to_addr.lower() != escrow_addr.lower():
        raise ValueError(
            f"Token sent to {to_addr}, expected escrow contract {escrow_addr}"
        )

    if transfer_amount < price_units:
        raise ValueError(
            f"Insufficient payment. Expected {price_units}, got {transfer_amount}"
        )

    return {
        "from_addr": from_addr,
        "to_addr": to_addr,
        "transfer_amount": transfer_amount,
    }
