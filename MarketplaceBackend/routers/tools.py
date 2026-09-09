"""
/tools/* endpoints + payment middleware (x402 and Nitrolite).
Replaces the tools-related routes and the /tools middleware in market.js.
"""
import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import database
import registry
from config import settings
from models.tool import ToolCreate
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
async def register_tool(body: ToolCreate):
    if not body.name or not body.price:
        return JSONResponse(status_code=400, content={"success": False, "error": "Missing required fields: name, price"})
    if body.type == "proxy" and not body.targetUrl:
        return JSONResponse(status_code=400, content={"success": False, "error": "Proxy tools require targetUrl"})
    if body.type == "code" and not body.code:
        return JSONResponse(status_code=400, content={"success": False, "error": "Code tools require code"})

    existing = await database.tools_collection.find_one({"name": body.name})
    if existing:
        return JSONResponse(status_code=400, content={"success": False, "error": "Tool with this name already exists"})

    doc = body.model_dump()
    doc["trusted"] = False
    doc["status"] = "pending"
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

@router.post("/tools/{name}/approve")
async def approve_tool(name: str):
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

    server_receipt: dict = {}

    # ---- Nitrolite off-chain payment ----
    if x_payment_method == "nitrolite":
        try:
            nitrolite = verify_nitrolite_proof(
                encoded_proof=x_nitrolite_proof,
                expected_amount_atomic=str(price_units),
                expected_provider_wallet=provider_wallet,
                expected_tool_name=tool_name,
                expected_payer=x_nitrolite_from,
            )
            server_receipt = _build_nitrolite_receipt(tool_name, provider_wallet, price_units, nitrolite)
            logger.info(f"[Nitrolite] ✓ Verified off-chain payment for {tool_name} from {nitrolite['payer']}")
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
        # Verify x402 on-chain payment
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
            from_addr = verified["from_addr"]
            transfer_amount = verified["transfer_amount"]
            logger.info(f"[x402] ✓ Payment verified: {transfer_amount} units from {from_addr}")

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
                "payer": from_addr,
                "transferAmount": str(transfer_amount),
            }
        except ValueError as e:
            return JSONResponse(status_code=400 if "not found" in str(e).lower() else 403, content={"error": str(e)})
        except Exception as e:
            return JSONResponse(status_code=502, content={"error": "Payment Verification Failed", "details": str(e)})

    # ---- Execute the tool ----
    body = await request.json()

    try:
        if tool_config.get("type") == "code":
            result = await execute_code_tool(
                tool_name,
                body,
                trusted=tool_config.get("trusted", False),
            )
        else:
            result = await execute_proxy_tool(tool_config.get("targetUrl", ""), body)

        tool_success = result.get("success", True)
    except Exception as e:
        logger.error(f"[Execution] Error calling {tool_name}: {e}")
        tool_success = False
        result = {"success": False, "result": "Tool execution failed", "error": str(e)}

    # ---- Escrow release / refund ----
    escrow_key = settings.ESCROW_PRIVATE_KEY
    if x_payment_method != "nitrolite" and escrow_key and escrow_contract_addr:
        tx_hash_for_escrow = server_receipt.get("txHash", "")
        try:
            if tool_success:
                provider = server_receipt.get("toolProvider") or provider_wallet
                amount_val = int(server_receipt.get("transferAmount", price_units))
                escrow_info = await release_escrow(tx_hash_for_escrow, provider, amount_val)
            else:
                payer = server_receipt.get("payer", "")
                amount_val = int(server_receipt.get("transferAmount", price_units))
                escrow_info = await refund_escrow(tx_hash_for_escrow, payer, amount_val)
            server_receipt["escrowRelease"] = escrow_info
        except Exception as e:
            logger.warning(f"[x402] Escrow operation failed: {e}")
            server_receipt["escrowRelease"] = {"status": "release-failed", "error": str(e)}
    elif not escrow_key:
        server_receipt["escrowRelease"] = {"status": "no-key", "note": "ESCROW_PRIVATE_KEY not set"}

    # Attach receipt to response body and header
    if isinstance(result, dict):
        if x_payment_method == "nitrolite":
            result["nitroliteReceipt"] = {**server_receipt, "deliveryStatus": 200 if tool_success else 502}
        else:
            result["escrowReceipt"] = server_receipt

    response_headers = {"X-Payment-Receipt": json.dumps(server_receipt)}

    if not tool_success:
        return JSONResponse(status_code=502, content=result, headers=response_headers)

    return JSONResponse(content=result, headers=response_headers)
