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
from pymongo.errors import DuplicateKeyError

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


def _apply_update(doc: dict, update: dict) -> None:
    for k, v in update.get("$inc", {}).items():
        doc[k] = doc.get(k, 0) + v
    doc.update(update.get("$set", {}))


class FakeLedgerCollection:
    """
    Stand-in for `payment_receipts`. Mirrors the one MongoDB guarantee the
    ledger relies on: `_id` is unique, so a second insert_one with the same
    `_id` raises DuplicateKeyError.
    """

    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def insert_one(self, doc: dict):
        if doc["_id"] in self.docs:
            raise DuplicateKeyError("E11000 duplicate key error (fake)")
        self.docs[doc["_id"]] = copy.deepcopy(doc)

    async def update_one(self, query: dict, update: dict):
        doc = self.docs.get(query["_id"])
        if doc is not None:
            _apply_update(doc, update)

    async def find_one(self, query: dict):
        doc = self.docs.get(query["_id"])
        return copy.deepcopy(doc) if doc is not None else None

    def find(self, query: dict):
        return _LedgerCursor(list(self.docs.values()))


class _LedgerCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction):
        self._docs = sorted(self._docs, key=lambda d: d.get(key), reverse=(direction == -1))
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, _n=None):
        return [copy.deepcopy(d) for d in self._docs]


class FakeSpendCollection:
    """
    Stand-in for `spend_counters`. Reproduces find_one_and_update semantics for
    the filter shape spend_caps uses ({_id, spent: {$lte: N}}) including the
    upsert behaviour: if the document exists but the filter does not match,
    the upsert tries to insert a duplicate `_id` and raises DuplicateKeyError.
    The method body has no awaits, so it is atomic with respect to the event
    loop, just as the real operation is atomic on the server.
    """

    def __init__(self):
        self.docs: dict[str, dict] = {}

    @staticmethod
    def _matches(doc: dict, flt: dict) -> bool:
        cond = flt.get("spent")
        if isinstance(cond, dict) and "$lte" in cond:
            return doc.get("spent", 0) <= cond["$lte"]
        return True

    async def find_one_and_update(self, flt: dict, update: dict, upsert: bool = False, return_document=None):
        key = flt["_id"]
        doc = self.docs.get(key)
        if doc is not None and self._matches(doc, flt):
            _apply_update(doc, update)
            return copy.deepcopy(doc)
        if doc is None and upsert:
            new = {"_id": key}
            _apply_update(new, update)
            self.docs[key] = new
            return copy.deepcopy(new)
        if doc is not None and upsert:
            raise DuplicateKeyError("E11000 duplicate key error (fake upsert)")
        return None

    async def update_one(self, flt: dict, update: dict):
        doc = self.docs.get(flt["_id"])
        if doc is not None:
            _apply_update(doc, update)

    async def find_one(self, flt: dict):
        doc = self.docs.get(flt["_id"])
        return copy.deepcopy(doc) if doc is not None else None


@pytest.fixture
def fake_ledger(monkeypatch):
    import database

    coll = FakeLedgerCollection()
    monkeypatch.setattr(database, "payments_collection", coll)
    return coll


@pytest.fixture
def fake_spend(monkeypatch):
    import database

    coll = FakeSpendCollection()
    monkeypatch.setattr(database, "spend_collection", coll)
    return coll
