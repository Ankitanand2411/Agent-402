"""
Contract tests for routers/tools.py.

The router is mounted on a bare FastAPI app (no lifespan, so no MongoDB
connection or escrow worker). The in-memory registry is populated directly,
MongoDB is a FakeToolsCollection, and the three side-effecting collaborators
(on-chain verification, tool execution, escrow settlement) are monkeypatched
so each test can script them.
"""

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from routers import tools as tools_router
from tests.conftest import ECHO_TOOL_DOC, ESCROW_ADDR, PAYER_ADDR, PROVIDER_ADDR

TX = "0x" + "cd" * 32


ADMIN = {"Authorization": "Bearer test-admin-key"}


@pytest.fixture
def client(monkeypatch, clean_registry, fake_collection, fake_ledger, fake_spend, permissive_urls):
    monkeypatch.setattr(settings, "ESCROW_CONTRACT_ADDRESS", ESCROW_ADDR)
    monkeypatch.setattr(settings, "ESCROW_PRIVATE_KEY", "0x" + "33" * 32)
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "test-admin-key")
    # These tests assert on the final settlement status inline, so they run the
    # original synchronous settlement path. Async mode has its own module.
    monkeypatch.setattr(settings, "SETTLEMENT_MODE", "sync")
    monkeypatch.setattr(settings, "DAILY_SPEND_CAP_UNITS", 0)
    app = FastAPI()
    app.include_router(tools_router.router)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def echo_tool(clean_registry):
    """A registered proxy tool priced at 0.5 USDC."""
    clean_registry.register(ECHO_TOOL_DOC)
    return clean_registry


class Recorder:
    """Async callable that records its calls and returns / raises a scripted value."""

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def collaborators(monkeypatch):
    verify = Recorder(result={"from_addr": PAYER_ADDR, "to_addr": ESCROW_ADDR, "transfer_amount": 500_000})
    execute = Recorder(result={"success": True, "result": "Tool call successful", "data": {"echo": "hi"}})
    release = Recorder(result={"status": "released", "releaseTxHash": "0xrel", "releasedTo": PROVIDER_ADDR})
    refund = Recorder(result={"status": "refunded", "refundTxHash": "0xref", "refundedTo": PAYER_ADDR})
    monkeypatch.setattr(tools_router, "verify_onchain_payment", verify)
    monkeypatch.setattr(tools_router, "execute_proxy_tool", execute)
    monkeypatch.setattr(tools_router, "release_escrow", release)
    monkeypatch.setattr(tools_router, "refund_escrow", refund)
    return {"verify": verify, "execute": execute, "release": release, "refund": refund}


# ─── 402 challenge ────────────────────────────────────────────────────────────

def test_unpaid_call_returns_402_challenge(client, echo_tool):
    r = client.post("/tools/echo", json={"text": "hi"})

    assert r.status_code == 402
    challenge = r.json()["accepts"][0]
    assert challenge["scheme"] == "x402"
    assert challenge["payTo"] == ESCROW_ADDR
    assert challenge["maxAmountRequired"] == "500000"           # 0.5 USDC in atomic units
    assert challenge["asset"] == settings.TOKEN_CONTRACT_ADDR
    assert challenge["network"] == f"eip155:{settings.SEPOLIA_CHAIN_ID}"
    assert challenge["toolProvider"] == PROVIDER_ADDR


def test_challenge_uses_exact_pricing(client, echo_tool):
    echo_tool.get("echo")["price"] = "0.0157"
    r = client.post("/tools/echo", json={})
    assert r.json()["accepts"][0]["maxAmountRequired"] == "15700"  # float math would give 15699


def test_misconfigured_price_is_a_500_not_a_default_charge(client, echo_tool):
    echo_tool.get("echo")["price"] = "free"
    r = client.post("/tools/echo", json={})
    assert r.status_code == 500
    assert "misconfigured price" in r.json()["error"]


def test_unknown_tool_is_404(client):
    assert client.post("/tools/nope", json={}).status_code == 404


def test_402_when_escrow_not_configured_is_500(client, echo_tool, monkeypatch):
    monkeypatch.setattr(settings, "ESCROW_CONTRACT_ADDRESS", "")
    r = client.post("/tools/echo", json={})
    assert r.status_code == 500


# ─── x402: verify → execute → release ─────────────────────────────────────────

def test_paid_call_executes_and_releases_escrow(client, echo_tool, collaborators):
    r = client.post("/tools/echo", json={"text": "hi"}, headers={"X-Payment-Tx": TX})

    assert r.status_code == 200
    body = r.json()
    assert body["data"] == {"echo": "hi"}
    receipt = body["escrowReceipt"]
    assert receipt["verified"] is True
    assert receipt["txHash"] == TX
    assert receipt["payer"] == PAYER_ADDR
    assert receipt["escrowRelease"]["status"] == "released"

    # Receipt is also exposed as a header for non-JSON-aware clients.
    assert json.loads(r.headers["X-Payment-Receipt"])["txHash"] == TX

    # Verification was asked for the right amount, and release went to the provider.
    (args, _), = collaborators["verify"].calls
    assert args == (TX, ESCROW_ADDR, 500_000)
    (args, _), = collaborators["release"].calls
    assert args == (TX, PROVIDER_ADDR, 500_000)
    assert collaborators["refund"].calls == []


def test_tx_hash_can_come_from_base64_x_payment_header(client, echo_tool, collaborators):
    payload = base64.b64encode(json.dumps({"txHash": TX, "from": PAYER_ADDR, "amount": 500000}).encode()).decode()
    r = client.post("/tools/echo", json={}, headers={"X-Payment": payload})
    assert r.status_code == 200
    (args, _), = collaborators["verify"].calls
    assert args[0] == TX


def test_failed_tool_returns_502_and_refunds_payer(client, echo_tool, collaborators):
    collaborators["execute"].error = RuntimeError("upstream exploded")

    r = client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})

    assert r.status_code == 502
    body = r.json()
    assert body["success"] is False
    assert body["escrowReceipt"]["escrowRelease"]["status"] == "refunded"
    (args, _), = collaborators["refund"].calls
    assert args == (TX, PAYER_ADDR, 500_000)
    assert collaborators["release"].calls == []


def test_upstream_failure_flag_also_refunds(client, echo_tool, collaborators):
    collaborators["execute"].result = {"success": False, "result": "Tool call failed upstream", "data": {}}
    r = client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})
    assert r.status_code == 502
    assert len(collaborators["refund"].calls) == 1


def test_unverifiable_payment_is_400_when_receipt_missing(client, echo_tool, collaborators):
    collaborators["verify"].error = ValueError("Transaction receipt not found on Sepolia Testnet after retries")
    r = client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})
    assert r.status_code == 400
    assert collaborators["execute"].calls == []          # tool never ran


def test_invalid_payment_is_403(client, echo_tool, collaborators):
    collaborators["verify"].error = ValueError("Insufficient payment. Expected 500000, got 1")
    r = client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})
    assert r.status_code == 403
    assert collaborators["execute"].calls == []


def test_settlement_failure_is_reported_not_fatal(client, echo_tool, collaborators):
    collaborators["release"].error = RuntimeError("nonce too low")
    r = client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})
    assert r.status_code == 200
    assert r.json()["escrowReceipt"]["escrowRelease"]["status"] == "release-failed"


# ─── Nitrolite ────────────────────────────────────────────────────────────────

def test_bad_nitrolite_proof_is_403_and_never_executes(client, echo_tool, collaborators):
    r = client.post(
        "/tools/echo", json={},
        headers={"X-Payment-Method": "nitrolite", "X-Nitrolite-Proof": "garbage", "X-Nitrolite-From": PAYER_ADDR},
    )
    assert r.status_code == 403
    assert r.json()["error"] == "Nitrolite payment verification failed"
    assert collaborators["execute"].calls == []


def test_valid_nitrolite_proof_executes_without_escrow(client, echo_tool, collaborators, monkeypatch):
    monkeypatch.setattr(tools_router, "verify_nitrolite_proof", lambda **kw: {
        "payer": PAYER_ADDR, "provider": PROVIDER_ADDR, "appSessionId": "0xsess",
        "protocol": "nitrolite-erc7824", "wsUrl": "wss://x",
    })
    r = client.post(
        "/tools/echo", json={},
        headers={"X-Payment-Method": "nitrolite", "X-Nitrolite-Proof": "ok", "X-Nitrolite-From": PAYER_ADDR},
    )
    assert r.status_code == 200
    receipt = r.json()["nitroliteReceipt"]
    assert receipt["verifiedBy"] == "yellow-nitrolite"
    assert receipt["deliveryStatus"] == 200
    assert collaborators["release"].calls == []           # off-chain rail: no escrow tx
    assert collaborators["refund"].calls == []


# ─── Registration and approval ────────────────────────────────────────────────

def test_register_creates_pending_tool(client, fake_collection):
    r = client.post("/tools/register", json={
        "name": "weather", "description": "Weather lookup", "price": "0.25",
        "type": "proxy", "targetUrl": "http://w.example/run", "walletAddress": PROVIDER_ADDR,
    })
    assert r.status_code == 200
    assert r.json()["success"] is True
    [doc] = fake_collection.inserted
    assert doc["status"] == "pending"
    assert doc["trusted"] is False


def test_register_rejects_duplicate_name(client, fake_collection):
    fake_collection.docs.append({"name": "weather", "status": "approved"})
    r = client.post("/tools/register", json={
        "name": "weather", "description": "d", "price": "1", "type": "proxy", "targetUrl": "http://x",
    })
    assert r.status_code == 400
    assert "already exists" in r.json()["error"]


def test_register_rejects_unrepresentable_price(client, fake_collection):
    r = client.post("/tools/register", json={
        "name": "w", "description": "d", "price": "0.0000001", "type": "proxy", "targetUrl": "http://x",
    })
    assert r.status_code == 422
    assert fake_collection.inserted == []


@pytest.mark.parametrize(
    "body",
    [
        {"name": "w", "description": "d", "price": "1", "type": "proxy"},            # proxy needs targetUrl
        {"name": "w", "description": "d", "price": "1", "type": "code"},             # code needs code
    ],
)
def test_register_validates_type_specific_fields(client, body):
    assert client.post("/tools/register", json=body).status_code == 400


def test_approve_hot_loads_tool_into_registry(client, fake_collection, clean_registry):
    fake_collection.docs.append({
        "name": "weather", "description": "Weather lookup COSTS: 0.25 USDC", "price": "0.25",
        "type": "proxy", "targetUrl": "http://w.example/run", "walletAddress": PROVIDER_ADDR, "status": "pending",
    })

    r = client.post("/tools/weather/approve", headers=ADMIN)

    assert r.status_code == 200
    assert fake_collection.updates == [({"name": "weather"}, {"$set": {"status": "approved"}})]
    weather = clean_registry.get("weather")
    assert weather["price"] == "0.25" and weather["targetUrl"] == "http://w.example/run"
    assert [t["name"] for t in clean_registry.marketplace_view()] == ["weather"]
    assert clean_registry.marketplace_view()[0]["description"] == "Weather lookup"     # COSTS suffix stripped

    # And it is immediately callable: unpaid call gets a 402 for 0.25 USDC.
    r = client.post("/tools/weather", json={})
    assert r.status_code == 402
    assert r.json()["accepts"][0]["maxAmountRequired"] == "250000"


def test_approve_unknown_tool_is_404(client, fake_collection):
    assert client.post("/tools/ghost/approve", headers=ADMIN).status_code == 404


def test_approve_twice_is_400(client, fake_collection):
    fake_collection.docs.append({"name": "w", "description": "d", "price": "1", "status": "approved"})
    assert client.post("/tools/w/approve", headers=ADMIN).status_code == 400


def test_list_tools_loads_approved_from_db(client, fake_collection, clean_registry):
    fake_collection.docs += [
        {"name": "a", "description": "A COSTS: 1 USDC", "price": "1", "type": "proxy", "targetUrl": "http://a", "status": "approved"},
        {"name": "b", "description": "B", "price": "2", "type": "proxy", "targetUrl": "http://b", "status": "pending"},
    ]
    r = client.get("/tools")
    assert r.status_code == 200
    assert [t["name"] for t in r.json()] == ["a"]                 # pending tools are not served
    assert r.json()[0]["description"] == "A"                      # COSTS suffix stripped for the agent


# ─── Admin auth on approval ───────────────────────────────────────────────────

def test_approve_without_admin_key_is_401_and_changes_nothing(client, fake_collection, clean_registry):
    fake_collection.docs.append({"name": "w", "description": "d", "price": "1", "type": "proxy", "targetUrl": "http://x", "status": "pending"})
    assert client.post("/tools/w/approve").status_code == 401
    assert client.post("/tools/w/approve", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert fake_collection.updates == []
    assert clean_registry.tools == {}


def test_approve_fails_closed_when_admin_key_unconfigured(client, fake_collection, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "")
    fake_collection.docs.append({"name": "w", "description": "d", "price": "1", "status": "pending"})
    assert client.post("/tools/w/approve", headers=ADMIN).status_code == 503


# ─── Provider signature on registration ───────────────────────────────────────

def _provider_sig(tool: str, wallet: str, key: str) -> str:
    from eth_account import Account
    from eth_account.messages import encode_defunct

    from services.auth import registration_message
    return Account.sign_message(encode_defunct(text=registration_message(tool, wallet)), private_key=key).signature.hex()


def test_register_with_valid_signature_marks_provider_verified(client, fake_collection):
    from eth_account import Account
    key = "0x" + "66" * 32
    wallet = Account.from_key(key).address
    r = client.post(
        "/tools/register",
        json={"name": "signed_tool", "description": "d", "price": "1", "type": "proxy", "targetUrl": "http://x", "walletAddress": wallet},
        headers={"X-Provider-Signature": _provider_sig("signed_tool", wallet, key)},
    )
    assert r.status_code == 200
    assert fake_collection.inserted[0]["providerVerified"] is True


def test_register_with_wrong_signature_is_401(client, fake_collection):
    from eth_account import Account
    wallet = Account.from_key("0x" + "66" * 32).address
    r = client.post(
        "/tools/register",
        json={"name": "t", "description": "d", "price": "1", "type": "proxy", "targetUrl": "http://x", "walletAddress": wallet},
        headers={"X-Provider-Signature": _provider_sig("t", wallet, "0x" + "77" * 32)},
    )
    assert r.status_code == 401
    assert fake_collection.inserted == []


def test_register_without_signature_is_open_unless_required(client, fake_collection, monkeypatch):
    body = {"name": "t", "description": "d", "price": "1", "type": "proxy", "targetUrl": "http://x", "walletAddress": PROVIDER_ADDR}
    assert client.post("/tools/register", json=body).status_code == 200
    assert fake_collection.inserted[-1]["providerVerified"] is False

    monkeypatch.setattr(settings, "REQUIRE_PROVIDER_SIGNATURE", True)
    r = client.post("/tools/register", json={**body, "name": "t2"})
    assert r.status_code == 401


# ─── Replay protection ────────────────────────────────────────────────────────

def test_same_tx_hash_cannot_buy_two_executions(client, echo_tool, collaborators, fake_ledger):
    first = client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})
    assert first.status_code == 200
    assert first.json()["escrowReceipt"]["paymentKey"] == f"x402:{TX}"

    replay = client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX.upper().replace("0X", "0x")})
    assert replay.status_code == 409
    assert replay.json()["paymentKey"] == f"x402:{TX}"          # case-normalised key
    assert len(collaborators["execute"].calls) == 1              # tool ran once
    assert len(collaborators["release"].calls) == 1              # escrow released once


def test_replay_is_checked_before_execution_even_on_other_tool(client, echo_tool, collaborators, clean_registry):
    clean_registry.register({**ECHO_TOOL_DOC, "name": "other"})
    assert client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX}).status_code == 200
    assert client.post("/tools/other", json={}, headers={"X-Payment-Tx": TX}).status_code == 409
    assert len(collaborators["execute"].calls) == 1


def test_nitrolite_state_cannot_be_replayed(client, echo_tool, collaborators, monkeypatch):
    monkeypatch.setattr(tools_router, "verify_nitrolite_proof", lambda **kw: {
        "payer": PAYER_ADDR, "provider": PROVIDER_ADDR, "appSessionId": "0xsess", "stateVersion": 7, "amount": "500000",
    })
    headers = {"X-Payment-Method": "nitrolite", "X-Nitrolite-Proof": "ok", "X-Nitrolite-From": PAYER_ADDR}
    assert client.post("/tools/echo", json={}, headers=headers).status_code == 200
    r = client.post("/tools/echo", json={}, headers=headers)
    assert r.status_code == 409
    assert r.json()["paymentKey"] == "nitrolite:0xsess:7"


# ─── Receipts endpoint ────────────────────────────────────────────────────────

def test_receipt_is_retrievable_after_call(client, echo_tool, collaborators):
    client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})
    r = client.get(f"/receipts/x402:{TX}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "delivered"
    assert body["tool_name"] == "echo"
    assert body["payer"] == PAYER_ADDR
    assert body["settlement"]["status"] == "released"


def test_unknown_receipt_is_404(client):
    assert client.get("/receipts/x402:0xnothing").status_code == 404


# ─── Spend cap ────────────────────────────────────────────────────────────────

def test_spend_cap_refunds_instead_of_executing(client, echo_tool, collaborators, monkeypatch, fake_spend):
    monkeypatch.setattr(settings, "DAILY_SPEND_CAP_UNITS", 700_000)    # 0.7 USDC/day; tool costs 0.5

    ok = client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX})
    assert ok.status_code == 200

    tx2 = "0x" + "ef" * 32
    capped = client.post("/tools/echo", json={}, headers={"X-Payment-Tx": tx2})
    assert capped.status_code == 429
    body = capped.json()
    assert "spend cap" in body["error"]
    assert body["escrowReceipt"]["escrowRelease"]["status"] == "refunded"
    assert len(collaborators["execute"].calls) == 1               # second call never ran the tool
    (args, _) = collaborators["refund"].calls[-1]
    assert args == (tx2, PAYER_ADDR, 500_000)

    # The refunded payment is recorded with the reason, and did not consume budget.
    receipt = client.get(f"/receipts/x402:{tx2}").json()
    assert receipt["status"] == "failed"
    assert receipt["settlement"]["reason"] == "spend-cap"


def test_failed_tool_refund_gives_budget_back(client, echo_tool, collaborators, monkeypatch, fake_spend):
    monkeypatch.setattr(settings, "DAILY_SPEND_CAP_UNITS", 500_000)    # exactly one call per day
    collaborators["execute"].error = RuntimeError("boom")
    assert client.post("/tools/echo", json={}, headers={"X-Payment-Tx": TX}).status_code == 502   # refunded

    collaborators["execute"].error = None
    tx2 = "0x" + "ef" * 32
    assert client.post("/tools/echo", json={}, headers={"X-Payment-Tx": tx2}).status_code == 200  # budget was released
