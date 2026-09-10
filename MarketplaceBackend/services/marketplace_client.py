"""
Calling this service's own paid endpoint from inside the process (used by the
MCP server and the agent graph). One code path for payments regardless of
who the caller is.
"""

from typing import Any

import httpx

from config import settings

TOKEN_CONTRACT_ADDR = settings.TOKEN_CONTRACT_ADDR


def client_factory() -> httpx.AsyncClient:
    """HTTP client to this service. Tests replace it with an in-process ASGI client."""
    base = settings.SELF_BASE_URL or f"http://127.0.0.1:{settings.PORT}"
    return httpx.AsyncClient(base_url=base, timeout=310.0)


def payment_headers(payment: dict[str, Any] | None) -> dict[str, str]:
    if not payment:
        return {}
    method = (payment.get("method") or ("nitrolite" if payment.get("proof") else "x402")).lower()
    if method == "nitrolite":
        headers = {"X-Payment-Method": "nitrolite"}
        if payment.get("proof"):
            headers["X-Nitrolite-Proof"] = str(payment["proof"])
        if payment.get("from"):
            headers["X-Nitrolite-From"] = str(payment["from"])
        return headers
    headers = {}
    if payment.get("tx_hash"):
        headers["X-Payment-Tx"] = str(payment["tx_hash"])
    if payment.get("x_payment"):
        headers["X-Payment"] = str(payment["x_payment"])
    return headers


def payment_challenge(tool_name: str, price_units: int, provider_wallet: str | None = None) -> dict[str, Any]:
    """The same challenge POST /tools/{name} returns with 402, computed without a round trip."""
    return {
        "toolProvider": provider_wallet or settings.DEFAULT_EVM_WALLET or "",
        "scheme": "x402",
        "payTo": settings.ESCROW_CONTRACT_ADDRESS,
        "maxAmountRequired": str(price_units),
        "asset": TOKEN_CONTRACT_ADDR,
        "network": f"eip155:{settings.SEPOLIA_CHAIN_ID}",
        "escrowContract": settings.ESCROW_CONTRACT_ADDRESS,
        "tokenDecimals": settings.TOKEN_DECIMALS,
        "tool": tool_name,
    }


async def call_tool(name: str, arguments: dict[str, Any], payment: dict[str, Any] | None) -> dict[str, Any]:
    """POST /tools/{name} with the payment as headers. Returns {status, body}."""
    async with client_factory() as client:
        try:
            resp = await client.post(f"/tools/{name}", json=arguments, headers=payment_headers(payment))
        except httpx.HTTPError as e:
            return {"status": 0, "body": {"error": f"marketplace unreachable: {e}"}}
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    return {"status": resp.status_code, "body": body}
