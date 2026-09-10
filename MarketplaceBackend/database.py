from motor.motor_asyncio import AsyncIOMotorClient

from config import settings

client: AsyncIOMotorClient = None
db = None
tools_collection = None
payments_collection = None   # payment ledger: replay protection + settlement receipts
spend_collection = None      # per-wallet daily spend counters


async def connect_db():
    global client, db, tools_collection, payments_collection, spend_collection
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    # Use explicit database name — Mongoose defaults to the DB in the URI path;
    # the Atlas URI has no DB name, so we default to "agent402" (same as Mongoose model pluralisation)
    try:
        db = client.get_default_database()
    except Exception:
        db = client["test"]  # Mongoose default when no DB name in URI
    # Use the same collection Mongoose used (pluralized lowercase model name = "tools")
    tools_collection = db["tools"]
    payments_collection = db["payment_receipts"]
    spend_collection = db["spend_counters"]
    print("[MongoDB] Connected successfully")


async def close_db():
    if client:
        client.close()
        print("[MongoDB] Connection closed")
