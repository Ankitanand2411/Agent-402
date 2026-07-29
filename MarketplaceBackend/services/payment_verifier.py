"""
x402 on-chain payment verifier using web3.py.
Replaces the ethers.js payment verification block in market.js.
"""
import asyncio
from web3 import Web3
from config import settings

TRANSFER_EVENT_SIG = Web3.keccak(text="Transfer(address,address,uint256)").hex()


def _get_w3() -> Web3:
    return Web3(Web3.HTTPProvider(settings.SEPOLIA_RPC))


async def verify_onchain_payment(
    tx_hash: str,
    escrow_addr: str,
    price_units: int,
) -> dict:
    """
    Verify that a USDC transfer was sent to the escrow contract.
    Returns dict with {from_addr, to_addr, transfer_amount} on success.
    Raises ValueError with a descriptive message on failure.
    """
    w3 = _get_w3()

    receipt = None
    for _ in range(5):
        try:
            receipt = await asyncio.to_thread(w3.eth.get_transaction_receipt, tx_hash)
        except Exception:
            pass
        if receipt:
            break
        await asyncio.sleep(1)

    if not receipt:
        raise ValueError("Transaction receipt not found on Sepolia Testnet after retries")

    token_addr = settings.TOKEN_CONTRACT_ADDR
    transfer_log = next(
        (
            log
            for log in receipt["logs"]
            if log["address"].lower() == token_addr.lower()
            and log["topics"][0].hex() == TRANSFER_EVENT_SIG
        ),
        None,
    )

    if not transfer_log:
        raise ValueError("No token transfer found in transaction")

    to_addr = Web3.to_checksum_address("0x" + transfer_log["topics"][2].hex()[-40:])
    from_addr = Web3.to_checksum_address("0x" + transfer_log["topics"][1].hex()[-40:])
    transfer_amount = int(transfer_log["data"].hex(), 16)

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
