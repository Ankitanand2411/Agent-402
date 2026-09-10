"""
main.py — FastAPI application entry point.
Replaces market.js. Run with:
  venv/bin/uvicorn main:app --host 0.0.0.0 --port 3000 --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

import database
from config import settings
from routers import gemini, info, tools
from routers.tools import load_tools
from services import escrow_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle — replaces top-level awaits in market.js."""
    # Connect MongoDB
    await database.connect_db()

    # Start serial escrow nonce queue worker
    await escrow_service.start_worker()

    # Load approved tools into memory
    await load_tools()

    logger.info(f"🚀 MCP Tool Server running on port {settings.PORT}")
    logger.info(f"🔗 Chain: Ethereum Sepolia Testnet (Chain ID: {settings.SEPOLIA_CHAIN_ID})")
    logger.info(f"💰 Token: {settings.TOKEN_CONTRACT_ADDR}")
    logger.info(
        f"🔒 Escrow Contract: {settings.ESCROW_CONTRACT_ADDRESS or 'NOT SET — deploy via Remix and set ESCROW_CONTRACT_ADDRESS in .env'}"
    )

    if not settings.ADMIN_API_KEY:
        logger.warning("ADMIN_API_KEY is not set — /tools/{name}/approve will refuse all requests (503)")
    logger.info(f"⚙️  Settlement mode: {settings.SETTLEMENT_MODE}; daily spend cap: {settings.DAILY_SPEND_CAP_UNITS or 'off'}")

    yield  # Server runs here

    # Shutdown: let in-flight escrow releases/refunds finish before the worker stops
    await tools.drain_settlements(timeout=60)
    await escrow_service.stop_worker()
    await database.close_db()


app = FastAPI(
    title="Agent402 MCP Marketplace",
    description="AI Tool Marketplace with x402 and Nitrolite micropayments",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS — same origins as market.js
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://localhost:5173",
        "https://agent402-skale.vercel.app",
        "https://agent402-goodvibes.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Payment-Receipt"],
)


# Global request logger — mirrors the debug middleware in market.js
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.debug(f"[DEBUG] Incoming Request: {request.method} {request.url.path}")
    return await call_next(request)


# Register routers
app.include_router(info.router)
app.include_router(gemini.router)
app.include_router(tools.router)
