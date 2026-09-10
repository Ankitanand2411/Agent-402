"""
/health, /escrow-info, /ping utility endpoints.

"""
from fastapi import APIRouter

from config import settings

router = APIRouter()

TOKEN_CONTRACT_ADDR = settings.TOKEN_CONTRACT_ADDR
TOKEN_DECIMALS = settings.TOKEN_DECIMALS
SEPOLIA_CHAIN_ID = settings.SEPOLIA_CHAIN_ID


@router.get("/ping")
async def ping():
    return "pong"


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "message": "MCP Tool Server is running",
        "chain": "Ethereum Sepolia Testnet",
        "chainId": SEPOLIA_CHAIN_ID,
        "escrowContract": settings.ESCROW_CONTRACT_ADDRESS or "NOT SET",
        "tokenContract": TOKEN_CONTRACT_ADDR,
    }


@router.get("/escrow-info")
async def escrow_info():
    return {
        "escrowContract": settings.ESCROW_CONTRACT_ADDRESS,
        "tokenContract": TOKEN_CONTRACT_ADDR,
        "tokenDecimals": TOKEN_DECIMALS,
        "chain": "Ethereum Sepolia Testnet",
        "chainId": SEPOLIA_CHAIN_ID,
        "rpc": settings.SEPOLIA_RPC,
        "explorer": "https://sepolia.etherscan.io",
    }
