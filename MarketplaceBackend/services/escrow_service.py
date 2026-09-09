"""
Escrow release/refund service with a serial asyncio queue.
Replaces the NonceQueue class in market.js — guarantees no nonce collisions.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable

from eth_account import Account
from web3 import Web3

from config import settings

logger = logging.getLogger(__name__)

RELEASE_ABI = [
    {
        "name": "releasePaymentByTxHash",
        "type": "function",
        "inputs": [
            {"name": "txHash", "type": "string"},
            {"name": "toolProvider", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    }
]

REFUND_ABI = [
    {
        "name": "refundPayment",
        "type": "function",
        "inputs": [
            {"name": "txHash", "type": "string"},
            {"name": "payer", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    }
]

# Single asyncio queue — worker processes one tx at a time (no nonce races)
_queue: asyncio.Queue = asyncio.Queue()
_worker_task: asyncio.Task | None = None


async def _worker():
    """Background worker that serially processes escrow transactions."""
    while True:
        fn, future = await _queue.get()
        try:
            result = await fn()
            if not future.done():
                future.set_result(result)
        except Exception as e:
            if not future.done():
                future.set_exception(e)
        finally:
            _queue.task_done()


async def start_worker():
    global _worker_task
    _worker_task = asyncio.create_task(_worker())
    logger.info("[EscrowService] Background nonce queue worker started")


async def stop_worker():
    if _worker_task:
        _worker_task.cancel()


async def _enqueue(fn: Callable[[], Awaitable[Any]]) -> Any:
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    await _queue.put((fn, future))
    return await future


def _get_w3():
    return Web3(Web3.HTTPProvider(settings.SEPOLIA_RPC))


async def release_escrow(tx_hash: str, provider_wallet: str, amount: int) -> dict:
    """Release payment from escrow to tool provider."""
    async def _do():
        w3 = _get_w3()
        account = Account.from_key(settings.ESCROW_PRIVATE_KEY)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(settings.ESCROW_CONTRACT_ADDRESS),
            abi=RELEASE_ABI,
        )
        nonce = await asyncio.to_thread(w3.eth.get_transaction_count, account.address, "pending")
        tx = await asyncio.to_thread(
            contract.functions.releasePaymentByTxHash(
                tx_hash,
                Web3.to_checksum_address(provider_wallet),
                amount,
            ).build_transaction,
            {"from": account.address, "nonce": nonce},
        )
        signed = account.sign_transaction(tx)
        tx_sent = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)
        receipt = await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, tx_sent)
        return receipt["transactionHash"].hex()

    release_tx_hash = await _enqueue(_do)
    logger.info(f"[Escrow] Released to {provider_wallet} (tx: {release_tx_hash})")
    return {"status": "released", "releaseTxHash": release_tx_hash, "releasedTo": provider_wallet}


async def refund_escrow(tx_hash: str, payer_address: str, amount: int) -> dict:
    """Refund payment from escrow back to the payer."""
    async def _do():
        w3 = _get_w3()
        account = Account.from_key(settings.ESCROW_PRIVATE_KEY)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(settings.ESCROW_CONTRACT_ADDRESS),
            abi=REFUND_ABI,
        )
        nonce = await asyncio.to_thread(w3.eth.get_transaction_count, account.address, "pending")
        tx = await asyncio.to_thread(
            contract.functions.refundPayment(
                tx_hash,
                Web3.to_checksum_address(payer_address),
                amount,
            ).build_transaction,
            {"from": account.address, "nonce": nonce},
        )
        signed = account.sign_transaction(tx)
        tx_sent = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)
        receipt = await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, tx_sent)
        return receipt["transactionHash"].hex()

    refund_tx_hash = await _enqueue(_do)
    logger.info(f"[Escrow] Refunded to {payer_address} (tx: {refund_tx_hash})")
    return {"status": "refunded", "refundTxHash": refund_tx_hash, "refundedTo": payer_address}
