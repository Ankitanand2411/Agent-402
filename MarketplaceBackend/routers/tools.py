"""
/tools/* endpoints + payment middleware (x402 and Nitrolite).
Replaces the tools-related routes and the /tools middleware in market.js.
"""
import asyncio
import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import database
import registry
from config import settings
from models.tool import ToolCreate
from services import payments_ledger as ledger
from services import spend_caps
from services.auth import require_admin, verify_provider_signature
from services.escrow_service import refund_escrow, release_escrow
from services.nitrolite_verifier import verify_nitrolite_proof
from services.payment_verifier import verify_onchain_payment
from services.pricing import price_to_units
from tool_executor.executor import execute_code_tool, execute_proxy_tool, normalize_tool_code

logger = logging.getLogger(__name__)
router = APIRouter()

USER_TOOLS_DIR = Path(__file__).parent.parent / "user_tools"
os.makedirs(USER_TOOLS_DIR, exist_ok=True)

TOKEN_CONTRACT_ADDR = settings.TOKEN_CONTRACT_ADDR
TOKEN_DECIMALS = settings.TOKEN_DECIMALS
SEPOLIA_CHAIN_ID = settings.SEPOLIA_CHAIN_ID


# ---------------------------------------------------------------------------
# Startup tool loader — replaces loadTools()
# ---------------------------------------------------------------------------

async def load_tools():
    """Load all approved tools from MongoDB into the in-memory registry."""
    registry.dynamic_routes.clear()
    registry.registered_proxies.clear()
    registry.marketplace_tools.clear()

    tools = await database.tools_collection.find({"status": "approved"}).to_list(None)

    for tool in tools:
        route_path = f"/tools/{tool['name']}"
        registry.dynamic_routes[route_path] = {
            "price": tool["price"],
            "asset": "native",
            "description": tool["description"],
            "mimeType": "application/json",
            "maxTimeoutSeconds": 300,
            "walletAddress": tool.get("walletAddress", ""),
        }

        if tool.get("type") == "code":
            code_path = USER_TOOLS_DIR / f"{tool['name']}.js"
            code_path.write_text(normalize_tool_code(tool["name"], tool.get("code", "")), "utf-8")
            registry.registered_proxies[tool["name"]] = {
                "type": "code",
                "codePath": str(code_path),
                "walletAddress": tool.get("walletAddress", ""),
                "trusted": tool.get("trusted", False),
            }
        else:
            registry.registered_proxies[tool["name"]] = {
                "type": "proxy",
                "targetUrl": tool.get("targetUrl", ""),
                "method": "POST",
                "walletAddress": tool.get("walletAddress", ""),
            }

        tool_def = {
            "name": tool["name"],
            "description": re.sub(r"\s*COSTS:.*$", "", tool["description"], flags=re.IGNORECASE).strip(),
            "price": tool["price"],
            "parameters": tool.get("parameters"),
        }
        existing = next((i for i, t in enumerate(registry.marketplace_tools) if t["name"] == tool["name"]), -1)
        if existing >= 0:
            registry.marketplace_tools[existing] = tool_def
        else:
            registry.marketplace_tools.append(tool_def)

    logger.info(f"[Persistence] Loaded {len(tools)} custom tools from MongoDB.")
    return registry.marketplace_tools


# ---------------------------------------------------------------------------
# Payment helpers
# ---------------------------------------------------------------------------

def _price_units(price_str: str) -> int:
    """
    Tool price → USDC atomic units, exactly (Decimal, not float).
    Raises ValueError for a price that cannot be represented; callers turn
    that into an explicit error instead of silently charging a default.
    """
    return price_to_units(price_str, TOKEN_DECIMALS)


def _build_nitrolite_receipt(tool_name: str, provider_wallet: str, price_units: int, nitrolite: dict) -> dict:
    return {
        "verified": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "toolName": tool_name,
        "toolProvider": provider_wallet,
        "amount": str(price_units),
        "asset": "usdc",
        "chain": "yellow-network",
        "chainId": SEPOLIA_CHAIN_ID,
        "verifiedBy": "yellow-nitrolite",
        "payer": nitrolite.get("payer"),
        "provider": nitrolite.get("provider"),
        "appSessionId": nitrolite.get("appSessionId"),
        "protocol": nitrolite.get("protocol"),
        "wsUrl": nitrolite.get("wsUrl"),
    }


# ---------------------------------------------------------------------------
# GET /tools — list marketplace tools
# ---------------------------------------------------------------------------

@router.get("/tools")
async def list_tools():
    logger.info("[Server] Fetching marketplace tools list")
    if not registry.marketplace_tools:
        await load_tools()
    return registry.marketplace_tools


@router.get("/tools/info")
async def tools_info():
    return registry.marketplace_tools


# ---------------------------------------------------------------------------
# POST /tools/register
# ---------------------------------------------------------------------------

@router.post("/tools/register")
async def register_tool(body: ToolCreate, request: Request):
    """
    Submit a tool for approval. Registration is open by design (pending tools
    are inert until an admin approves them), but a provider can prove control
    of the payout wallet by sending `X-Provider-Signature`: an EIP-191 signature
    of services.auth.registration_message(name, walletAddress). The signature is
    verified whenever present and required when REQUIRE_PROVIDER_SIGNATURE is on.
    """
    if not body.name or not body.price:
        return JSONResponse(status_code=400, content={"success": False, "error": "Missing required fields: name, price"})
    if body.type == "proxy" and not body.targetUrl:
        return JSONResponse(status_code=400, content={"success": False, "error": "Proxy tools require targetUrl"})
    if body.type == "code" and not body.code:
        return JSONResponse(status_code=400, content={"success": False, "error": "Code tools require code"})

    signature = request.headers.get("x-provider-signature")
    provider_verified = False
    if signature:
        if not body.walletAddress:
            return JSONResponse(status_code=400, content={"success": False, "error": "walletAddress is required to verify a provider signature"})
        if not verify_provider_signature(body.name, body.walletAddress, signature):
            return JSONResponse(status_code=401, content={"success": False, "error": "Provider signature does not match walletAddress"})
        provider_verified = True
    elif settings.REQUIRE_PROVIDER_SIGNATURE:
        return JSONResponse(status_code=401, content={"success": False, "error": "X-Provider-Signature from the payout wallet is required"})

    existing = await database.tools_collection.find_one({"name": body.name})
    if existing:
        return JSONResponse(status_code=400, content={"success": False, "error": "Tool with this name already exists"})

    doc = body.model_dump()
    doc["trusted"] = False
    doc["status"] = "pending"
    doc["providerVerified"] = provider_verified
    doc["createdAt"] = datetime.now(timezone.utc)

    await database.tools_collection.insert_one(doc)
    logger.info(f"[Registry] Registered new tool: {body.name} (PENDING APPROVAL)")

    return {
        "success": True,
        "message": "Tool registered successfully. Status is PENDING approval.",
        "tool": {
            "name": body.name,
            "description": body.description,
            "price": body.price,
            "parameters": body.parameters or {"type": "object", "properties": {}},
        },
    }


# ---------------------------------------------------------------------------
# POST /tools/{name}/approve
# ---------------------------------------------------------------------------

@router.post("/tools/{name}/approve", dependencies=[Depends(require_admin)])
async def approve_tool(name: str):
    """Admin only: approving makes the tool callable (and, for code tools, executable on this server)."""
    tool = await database.tools_collection.find_one({"name": name})
    if not tool:
        return JSONResponse(status_code=404, content={"success": False, "error": "Tool not found"})
    if tool.get("status") == "approved":
        return JSONResponse(status_code=400, content={"success": False, "error": "Tool already approved"})

    await database.tools_collection.update_one({"name": name}, {"$set": {"status": "approved"}})

    # Hot-load into registry
    route_path = f"/tools/{name}"
    registry.dynamic_routes[route_path] = {
        "price": tool["price"],
        "asset": "native",
        "description": tool["description"],
        "mimeType": "application/json",
        "maxTimeoutSeconds": 300,
        "walletAddress": tool.get("walletAddress", ""),
    }

    if tool.get("type") == "code":
        code_path = USER_TOOLS_DIR / f"{name}.js"
        code_path.write_text(normalize_tool_code(name, tool.get("code", "")), "utf-8")
        registry.registered_proxies[name] = {
            "type": "code",
            "codePath": str(code_path),
            "walletAddress": tool.get("walletAddress", ""),
            "trusted": tool.get("trusted", False),
        }
    else:
        registry.registered_proxies[name] = {
            "type": "proxy",
            "targetUrl": tool.get("targetUrl", ""),
            "method": "POST",
            "walletAddress": tool.get("walletAddress", ""),
        }

    tool_def = {"name": name, "description": tool["description"], "price": tool["price"], "parameters": tool.get("parameters")}
    existing = next((i for i, t in enumerate(registry.marketplace_tools) if t["name"] == name), -1)
    if existing >= 0:
        registry.marketplace_tools[existing] = tool_def
    else:
        registry.marketplace_tools.append(tool_def)

    logger.info(f"[Approval] Approved and loaded tool: {name}")
    return {"success": True, "message": "Tool approved and live"}


# ---------------------------------------------------------------------------
# Settlement (release / refund) — runs off the request path by default
# ---------------------------------------------------------------------------

# Strong references to in-flight settlement tasks. asyncio only keeps weak
# references to tasks, so without this set a task could be garbage-collected
# mid-flight. main.py drains it on shutdown; tests drain it for determinism.
_settlement_tasks: set[asyncio.Task] = set()


async def _settle(payment_key: str, tool_success: bool, tx_hash: str, provider: str, payer: str, amount: int) -> dict:
    """Release to the provider on success, refund the payer on failure. Records the outcome in the ledger."""
    try:
        if tool_success:
            escrow_info = await release_escrow(tx_hash, provider, amount)
        else:
            escrow_info = await refund_escrow(tx_hash, payer, amount)
            if settings.DAILY_SPEND_CAP_UNITS > 0 and payer:
                await spend_caps.release(payer, amount)  # a refunded payment does not count against the cap
    except Exception as e:
        logger.warning(f"[x402] Escrow operation failed for {payment_key}: {e}")
        escrow_info = {"status": "release-failed" if tool_success else "refund-failed", "error": str(e)}
    try:
        await ledger.record_settlement(payment_key, escrow_info)
    except Exception as e:  # ledger failure must not hide the settlement result
        logger.error(f"[Ledger] Could not record settlement for {payment_key}: {e}")
    return escrow_info


def _schedule_settlement(*args) -> None:
    task = asyncio.create_task(_settle(*args))
    _settlement_tasks.add(task)
    task.add_done_callback(_settlement_tasks.discard)


async def drain_settlements(timeout: float | None = None) -> None:
    """Wait for in-flight settlements (used on shutdown and in tests)."""
    if not _settlement_tasks:
        return
    await asyncio.wait(set(_settlement_tasks), timeout=timeout)


async def _start_settlement(payment_key: str, tool_success: bool, tx_hash: str, provider: str, payer: str, amount: int) -> dict:
    """
    Kick off release (tool succeeded) or refund (tool failed) and return the
    settlement record to put in the receipt.

      no escrow key configured -> {"status": "no-key"} (nothing to settle)
      SETTLEMENT_MODE == "sync" -> awaits the on-chain tx; final status
      otherwise                 -> schedules a background task; "pending" +
                                   the /receipts URL the client can poll
    """
    if not (settings.ESCROW_PRIVATE_KEY and settings.ESCROW_CONTRACT_ADDRESS):
        return {"status": "no-key", "note": "ESCROW_PRIVATE_KEY not set"}
    if settings.SETTLEMENT_MODE == "sync":
        return await _settle(payment_key, tool_success, tx_hash, provider, payer, amount)
    _schedule_settlement(payment_key, tool_success, tx_hash, provider, payer, amount)
    return {
        "status": "pending",
        "action": "release" if tool_success else "refund",
        "receiptId": payment_key,
        "poll": f"/receipts/{payment_key}",
    }


# ---------------------------------------------------------------------------
# GET /receipts/{payment_key} — settlement status for a paid call
# ---------------------------------------------------------------------------

@router.get("/receipts/{payment_key:path}")
async def get_receipt(payment_key: str):
    doc = await ledger.get_receipt(payment_key)
    if not doc:
        return JSONResponse(status_code=404, content={"success": False, "error": "Receipt not found"})
    return doc


# ---------------------------------------------------------------------------
# POST /tools/{tool_name} — payment gate + execution
# ---------------------------------------------------------------------------

@router.post("/tools/{tool_name}")
async def call_tool(tool_name: str, request: Request):
    full_path = f"/tools/{tool_name}"
    specific_route = registry.dynamic_routes.get(full_path)

    if not specific_route:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Tool '{tool_name}' not found"})

    tool_config = registry.registered_proxies.get(tool_name, {})
    provider_wallet = tool_config.get("walletAddress") or settings.DEFAULT_EVM_WALLET or ""
    escrow_contract_addr = settings.ESCROW_CONTRACT_ADDRESS
    try:
        price_units = _price_units(specific_route.get("price", "1"))
    except ValueError as e:
        logger.error(f"[Pricing] Tool '{tool_name}' has an invalid price: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": f"Tool '{tool_name}' has a misconfigured price"})

    # Parse payment headers
    x_payment = request.headers.get("x-payment")
    x_payment_tx = request.headers.get("x-payment-tx")
    x_payment_method = request.headers.get("x-payment-method")
    x_nitrolite_proof = request.headers.get("x-nitrolite-proof")
    x_nitrolite_from = request.headers.get("x-nitrolite-from")

    is_nitrolite = x_payment_method == "nitrolite"
    server_receipt: dict = {}
    payment_key = ""
    payer_addr = ""
    paid_amount = price_units

    # ---- Nitrolite off-chain payment ----
    if is_nitrolite:
        try:
            nitrolite = verify_nitrolite_proof(
                encoded_proof=x_nitrolite_proof,
                expected_amount_atomic=str(price_units),
                expected_provider_wallet=provider_wallet,
                expected_tool_name=tool_name,
                expected_payer=x_nitrolite_from,
            )
            server_receipt = _build_nitrolite_receipt(tool_name, provider_wallet, price_units, nitrolite)
            payer_addr = nitrolite.get("payer") or ""
            paid_amount = int(nitrolite.get("amount") or price_units)
            payment_key = ledger.nitrolite_key(nitrolite.get("appSessionId"), nitrolite.get("stateVersion"), x_nitrolite_proof)
            logger.info(f"[Nitrolite] ✓ Verified off-chain payment for {tool_name} from {payer_addr}")
        except Exception as e:
            logger.error(f"[Nitrolite] Verification failed: {e}")
            return JSONResponse(status_code=403, content={"error": "Nitrolite payment verification failed", "details": str(e)})

    # ---- x402 on-chain payment ----
    elif not x_payment and not x_payment_tx:
        if not escrow_contract_addr:
            return JSONResponse(status_code=500, content={"error": "Escrow contract not configured. Set ESCROW_CONTRACT_ADDRESS in .env"})
        logger.info(f"[x402] No payment for {tool_name}. Issuing 402")
        return JSONResponse(status_code=402, content={
            "accepts": [{
                "scheme": "x402",
                "payTo": escrow_contract_addr,
                "maxAmountRequired": str(price_units),
                "asset": TOKEN_CONTRACT_ADDR,
                "network": f"eip155:{SEPOLIA_CHAIN_ID}",
                "escrowContract": escrow_contract_addr,
                "toolProvider": provider_wallet,
                "description": f"Payment for {tool_name} (via custom escrow on Sepolia Testnet)",
                "tokenDecimals": TOKEN_DECIMALS,
            }]
        })
    else:
        if not escrow_contract_addr:
            return JSONResponse(status_code=500, content={"error": "Escrow contract not configured"})

        tx_hash = x_payment_tx

        # The X-Payment header may also carry "from" and "amount", but those are
        # client claims. Only the tx hash is taken from it; payer and amount are
        # read from the on-chain receipt in verify_onchain_payment.
        if x_payment:
            try:
                payload = json.loads(base64.b64decode(x_payment).decode("utf-8"))
                tx_hash = tx_hash or payload.get("txHash")
            except Exception as e:
                logger.warning(f"[x402] Could not parse X-Payment header: {e}")

        if not tx_hash:
            return JSONResponse(status_code=400, content={"error": "Missing payment transaction hash"})

        try:
            verified = await verify_onchain_payment(tx_hash, escrow_contract_addr, price_units)
        except ValueError as e:
            return JSONResponse(status_code=400 if "not found" in str(e).lower() else 403, content={"error": str(e)})
        except Exception as e:
            return JSONResponse(status_code=502, content={"error": "Payment Verification Failed", "details": str(e)})

        payer_addr = verified["from_addr"]
        paid_amount = verified["transfer_amount"]
        payment_key = ledger.x402_key(tx_hash)
        logger.info(f"[x402] ✓ Payment verified: {paid_amount} units from {payer_addr}")

        server_receipt = {
            "verified": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "txHash": tx_hash,
            "payTo": escrow_contract_addr,
            "toolProvider": provider_wallet,
            "amount": str(price_units),
            "asset": TOKEN_CONTRACT_ADDR,
            "chain": "sepolia-testnet",
            "chainId": SEPOLIA_CHAIN_ID,
            "toolName": tool_name,
            "verifiedBy": "x402-bnb-escrow",
            "payer": payer_addr,
            "transferAmount": str(paid_amount),
        }

    # ---- Replay protection: claim the payment before doing any work ----
    try:
        await ledger.claim_payment(
            payment_key,
            rail="nitrolite" if is_nitrolite else "x402",
            tool_name=tool_name,
            payer=payer_addr,
            provider=provider_wallet,
            amount_units=paid_amount,
        )
    except ledger.PaymentAlreadyUsed:
        logger.warning(f"[Ledger] Replay rejected for {payment_key}")
        return JSONResponse(status_code=409, content={
            "success": False,
            "error": "This payment has already been used for a tool call",
            "paymentKey": payment_key,
        })
    server_receipt["paymentKey"] = payment_key

    # ---- Guardrail: per-wallet daily spend cap (x402 only — on-chain funds can be refunded) ----
    cap = settings.DAILY_SPEND_CAP_UNITS
    if cap > 0 and not is_nitrolite and payer_addr:
        if not await spend_caps.try_reserve(payer_addr, paid_amount, cap):
            logger.warning(f"[Guardrail] Daily spend cap reached for {payer_addr}; refunding {paid_amount}")
            server_receipt["escrowRelease"] = await _start_settlement(
                payment_key, False, server_receipt.get("txHash", ""), provider_wallet, payer_addr, paid_amount
            )
            await ledger.record_delivery(payment_key, False, {**server_receipt["escrowRelease"], "reason": "spend-cap"})
            return JSONResponse(status_code=429, content={
                "success": False,
                "error": f"Daily spend cap of {cap} units reached for wallet {payer_addr}; payment is being refunded",
                "escrowReceipt": server_receipt,
            }, headers={"X-Payment-Receipt": json.dumps(server_receipt)})

    # ---- Execute the tool ----
    body = await request.json()

    try:
        if tool_config.get("type") == "code":
            result = await execute_code_tool(tool_name, body, trusted=tool_config.get("trusted", False))
        else:
            result = await execute_proxy_tool(tool_config.get("targetUrl", ""), body)
        tool_success = result.get("success", True)
    except Exception as e:
        logger.error(f"[Execution] Error calling {tool_name}: {e}")
        tool_success = False
        result = {"success": False, "result": "Tool execution failed", "error": str(e)}

    # ---- Settlement ----
    if is_nitrolite:
        settlement = {"status": "not-applicable", "note": "off-chain rail settles in the state channel"}
    else:
        settlement = await _start_settlement(
            payment_key, tool_success, server_receipt.get("txHash", ""), provider_wallet, payer_addr, paid_amount
        )
        server_receipt["escrowRelease"] = settlement

    await ledger.record_delivery(payment_key, tool_success, settlement)

    # Attach receipt to response body and header
    if isinstance(result, dict):
        if is_nitrolite:
            result["nitroliteReceipt"] = {**server_receipt, "deliveryStatus": 200 if tool_success else 502}
        else:
            result["escrowReceipt"] = server_receipt

    response_headers = {"X-Payment-Receipt": json.dumps(server_receipt)}

    if not tool_success:
        return JSONResponse(status_code=502, content=result, headers=response_headers)

    return JSONResponse(content=result, headers=response_headers)
