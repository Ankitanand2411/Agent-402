from motor.motor_asyncio import AsyncIOMotorClient

from config import settings

client: AsyncIOMotorClient = None
db = None
tools_collection = None
payments_collection = None         # payment ledger: replay protection + settlement receipts
spend_collection = None            # per-wallet daily spend counters
tool_embeddings_collection = None  # cached embeddings of tool descriptions


async def connect_db():
    global client, db, tools_collection, payments_collection, spend_collection, tool_embeddings_collection
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB_NAME]
    tools_collection = db["tools"]
    payments_collection = db["payment_receipts"]
    spend_collection = db["spend_counters"]
    tool_embeddings_collection = db["tool_embeddings"]
    print(f"[MongoDB] Connected (database: {settings.MONGODB_DB_NAME})")


async def close_db():
    if client:
        client.close()
        print("[MongoDB] Connection closed")
