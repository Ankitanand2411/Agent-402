"""
Authentication for the marketplace's two trust boundaries.

1. Admin approval (`/tools/{name}/approve`).
   Approving a tool makes provider-submitted code executable on this server,
   so it is gated by a static bearer token from ADMIN_API_KEY. Comparison uses
   hmac.compare_digest, which takes the same time whether the first or the last
   byte differs, so an attacker cannot recover the key one byte at a time by
   measuring response latency (a timing attack). If no key is configured the
   endpoint fails CLOSED with 503 rather than open.

2. Provider identity on registration (`/tools/register`).
   A provider proves control of the payout wallet by signing a fixed message
   with that wallet (EIP-191 `personal_sign`, the standard "Sign this message"
   prompt every wallet supports). The server recovers the signer address from
   the signature and compares it to `walletAddress`. This is a different
   scheme from Nitrolite, which signs a raw keccak digest with no prefix; the
   EIP-191 prefix exists precisely so a signed message can never be mistaken
   for a transaction.
"""

import hmac

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import HTTPException, Request
from web3 import Web3

from config import settings


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-admin-key")


async def require_admin(request: Request) -> None:
    """FastAPI dependency: allow the request only with the configured admin key."""
    configured = settings.ADMIN_API_KEY
    if not configured:
        raise HTTPException(status_code=503, detail="Admin API key is not configured on this server")
    presented = _extract_bearer(request)
    if not presented or not hmac.compare_digest(presented.encode(), configured.encode()):
        raise HTTPException(status_code=401, detail="Admin authentication required")


def registration_message(tool_name: str, wallet_address: str) -> str:
    """The exact text a provider signs. Binding name + wallet stops a signature being reused for another tool."""
    return f"Agent402 tool registration\ntool: {tool_name}\nwallet: {Web3.to_checksum_address(wallet_address)}"


def verify_provider_signature(tool_name: str, wallet_address: str, signature: str) -> bool:
    """True if `signature` is an EIP-191 signature of registration_message() by `wallet_address`."""
    try:
        message = encode_defunct(text=registration_message(tool_name, wallet_address))
        recovered = Account.recover_message(message, signature=signature)
        return recovered.lower() == Web3.to_checksum_address(wallet_address).lower()
    except Exception:
        return False
