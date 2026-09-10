"""
Shared fixtures for the MarketplaceBackend test suite.

Nothing here touches the network: MongoDB is replaced by an in-memory fake
collection, the Sepolia RPC by a fake web3 object, Gemini is never called, and
the escrow settlement functions are monkeypatched in the router tests.

`config.Settings` has defaults for every field, so no environment variables
are required. Tests that need an escrow address set it with `monkeypatch`.
"""

import copy

import pytest

# Deterministic test addresses (valid checksums, no real funds).
ESCROW_ADDR = "0x14b848bE61C159908C0F1127C53Aa70dD0F2cBed"
PROVIDER_ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
PAYER_ADDR = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, _length=None):
        return [copy.deepcopy(d) for d in self._docs]


class FakeToolsCollection:
    """Minimal async stand-in for a Motor collection, enough for the tools router."""

    def __init__(self, docs=None):
        self.docs: list[dict] = [copy.deepcopy(d) for d in (docs or [])]
        self.inserted: list[dict] = []
        self.updates: list[tuple[dict, dict]] = []

    def _match(self, query: dict):
        return [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]

    async def find_one(self, query: dict):
        matches = self._match(query)
        return copy.deepcopy(matches[0]) if matches else None

    def find(self, query: dict):
        return _Cursor(self._match(query))

    async def insert_one(self, doc: dict):
        self.docs.append(copy.deepcopy(doc))
        self.inserted.append(copy.deepcopy(doc))

    async def update_one(self, query: dict, update: dict):
        self.updates.append((query, update))
        for d in self._match(query):
            d.update(update.get("$set", {}))


@pytest.fixture
def fake_collection(monkeypatch):
    import database

    coll = FakeToolsCollection()
    monkeypatch.setattr(database, "tools_collection", coll)
    return coll


@pytest.fixture
def clean_registry():
    import registry

    registry.dynamic_routes.clear()
    registry.registered_proxies.clear()
    registry.marketplace_tools.clear()
    yield registry
    registry.dynamic_routes.clear()
    registry.registered_proxies.clear()
    registry.marketplace_tools.clear()
