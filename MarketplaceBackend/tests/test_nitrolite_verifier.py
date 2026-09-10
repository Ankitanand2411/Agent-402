"""
Tests for services.nitrolite_verifier.

Proofs are built the way the JS client builds them and signed with a real
secp256k1 key via eth-account, so signature recovery is exercised for real.
Only the network (Yellow ClearNode) is absent: the "response" messages are
hand-written to look like what the ClearNode returns.
"""

import base64
import json

import pytest
from eth_account import Account
from web3 import Web3

from services import nitrolite_verifier as nv
from tests.conftest import PROVIDER_ADDR

PAYER_KEY = "0x" + "11" * 32
PAYER = Account.from_key(PAYER_KEY).address
OTHER_KEY = "0x" + "22" * 32

SESSION = "0x" + "77" * 32
TOOL = "weather_lookup"
PRICE_UNITS = "500000"


def sign_req(req: list, key: str = PAYER_KEY) -> str:
    """Sign exactly what the JS client signs: keccak256(JSON.stringify(req)), no EIP-191 prefix."""
    digest = Web3.keccak(nv.serialize_request_payload(req))
    return Account._sign_hash(digest, private_key=key).signature.hex()


def submit_req(provider_amount=PRICE_UNITS, asset="usdc", allocations=None, protocol="nitroliterpc"):
    if allocations is None:
        allocations = [
            {"participant": PROVIDER_ADDR, "asset": asset, "amount": provider_amount},
            {"participant": PAYER, "asset": asset, "amount": "9500000"},
        ]
    return [
        1001,
        "submit_app_state",
        {
            "protocol": protocol,
            "app_session_id": SESSION,
            "state": {"version": 3, "allocations": allocations},
        },
        1_725_000_000_000,
    ]


def make_proof(req=None, *, sig=None, response=None, tool_name=TOOL, provider=PROVIDER_ADDR,
               create_session=None, extra=None) -> str:
    req = req if req is not None else submit_req()
    sig = sig if sig is not None else sign_req(req)
    response = response or [1001, "submit_app_state", {"app_session_id": SESSION, "version": 3}, 1_725_000_000_500]
    proof = {
        "submitStateRequest": json.dumps({"req": req, "sig": [sig]}),
        "submitStateResponse": json.dumps({"res": response, "sig": ["0xclearnode"]}),
        "toolName": tool_name,
        "provider": provider,
        "payer": PAYER,
        "appSessionId": SESSION,
        "wsUrl": "wss://clearnet-sandbox.yellow.com/ws",
    }
    if create_session:
        proof.update(create_session)
    if extra:
        proof.update(extra)
    return base64.b64encode(json.dumps(proof).encode()).decode()


def verify(encoded, **overrides):
    kwargs = dict(
        encoded_proof=encoded,
        expected_amount_atomic=PRICE_UNITS,
        expected_provider_wallet=PROVIDER_ADDR,
        expected_tool_name=TOOL,
        expected_payer=PAYER,
    )
    kwargs.update(overrides)
    return nv.verify_nitrolite_proof(**kwargs)


# ─── Happy path ───────────────────────────────────────────────────────────────

def test_valid_proof_is_accepted():
    receipt = verify(make_proof())
    assert receipt["verified"] is True
    assert receipt["payer"] == PAYER
    assert receipt["provider"] == PROVIDER_ADDR
    assert receipt["amount"] == PRICE_UNITS
    assert receipt["appSessionId"] == SESSION
    assert receipt["stateVersion"] == 3
    assert receipt["toolName"] == TOOL


def test_overpayment_is_accepted():
    receipt = verify(make_proof(submit_req(provider_amount="600000")))
    assert receipt["amount"] == "600000"


def test_payer_can_come_from_proof_when_header_missing():
    receipt = verify(make_proof(), expected_payer="")
    assert receipt["payer"] == PAYER


def test_non_ascii_payload_matches_js_serialisation():
    # JSON.stringify leaves "é" as UTF-8; a Python default json.dumps would emit \\u00e9
    # and the recovered signer would not match. serialize_request_payload must agree with JS.
    req = submit_req()
    req[2]["memo"] = "café ☕"
    assert b"\\u00e9" not in nv.serialize_request_payload(req)
    assert verify(make_proof(req))["verified"] is True


# ─── Signature and tampering ──────────────────────────────────────────────────

def test_signature_from_wrong_key_is_rejected():
    req = submit_req()
    with pytest.raises(ValueError, match="payer signature mismatch"):
        verify(make_proof(req, sig=sign_req(req, OTHER_KEY)))


def test_tampering_with_signed_amount_is_rejected():
    req = submit_req(provider_amount="1")           # payer signed a 1-unit allocation...
    sig = sign_req(req)
    tampered = submit_req(provider_amount=PRICE_UNITS)  # ...then someone edits it to the full price
    with pytest.raises(ValueError, match="payer signature mismatch"):
        verify(make_proof(tampered, sig=sig))


def test_missing_signature_is_rejected():
    req = submit_req()
    encoded = base64.b64encode(json.dumps({
        "submitStateRequest": json.dumps({"req": req, "sig": []}),
        "submitStateResponse": json.dumps({"res": [1, "submit_app_state", {"app_session_id": SESSION}, 2]}),
    }).encode()).decode()
    with pytest.raises(ValueError, match="missing request signature"):
        verify(encoded)


# ─── Allocation checks ────────────────────────────────────────────────────────

def test_insufficient_allocation_is_rejected():
    with pytest.raises(ValueError, match="insufficient"):
        verify(make_proof(submit_req(provider_amount="499999")))


def test_wrong_asset_is_rejected():
    with pytest.raises(ValueError, match="Unexpected Nitrolite asset"):
        verify(make_proof(submit_req(asset="weth")))


def test_state_without_provider_allocation_is_rejected():
    only_payer = [{"participant": PAYER, "asset": "usdc", "amount": "10000000"}]
    with pytest.raises(ValueError, match="does not allocate funds to the tool provider"):
        verify(make_proof(submit_req(allocations=only_payer)))


def test_state_without_payer_allocation_is_rejected():
    only_provider = [{"participant": PROVIDER_ADDR, "asset": "usdc", "amount": PRICE_UNITS}]
    with pytest.raises(ValueError, match="does not include the payer allocation"):
        verify(make_proof(submit_req(allocations=only_provider)))


# ─── Binding to the request ───────────────────────────────────────────────────

def test_proof_for_another_tool_is_rejected():
    with pytest.raises(ValueError, match="tool mismatch"):
        verify(make_proof(tool_name="other_tool"))


def test_proof_for_another_provider_is_rejected():
    other = "0x000000000000000000000000000000000000dEaD"
    with pytest.raises(ValueError, match="provider mismatch"):
        verify(make_proof(provider=other))


def test_wrong_protocol_is_rejected():
    with pytest.raises(ValueError, match="Unexpected Nitrolite protocol"):
        verify(make_proof(submit_req(protocol="something_else")))


def test_wrong_rpc_method_is_rejected():
    req = submit_req()
    req[1] = "close_app_session"
    with pytest.raises(ValueError, match="Expected submitStateRequest method submit_app_state"):
        verify(make_proof(req))


# ─── ClearNode response ───────────────────────────────────────────────────────

def test_clearnode_error_response_is_rejected():
    error = [1001, "error", {"error": "insufficient channel balance"}, 1]
    with pytest.raises(ValueError, match="rejected payment: insufficient channel balance"):
        verify(make_proof(response=error))


def test_missing_session_id_is_rejected():
    req = submit_req()
    del req[2]["app_session_id"]
    encoded = make_proof(req, response=[1001, "submit_app_state", {}, 1], extra={"appSessionId": None})
    with pytest.raises(ValueError, match="missing app_session_id"):
        verify(encoded)


# ─── Optional create-session proof ────────────────────────────────────────────

def _create_session_messages(signer_key=PAYER_KEY, participants=None, session_id=SESSION):
    participants = participants or [PAYER, PROVIDER_ADDR]
    create_req = [900, "create_app_session", {"definition": {"participants": participants}}, 1]
    return {
        "createSessionRequest": json.dumps({"req": create_req, "sig": [sign_req(create_req, signer_key)]}),
        "createSessionResponse": json.dumps({"res": [900, "create_app_session", {"app_session_id": session_id}, 2]}),
    }


def test_valid_create_session_proof_is_accepted():
    assert verify(make_proof(create_session=_create_session_messages()))["verified"] is True


def test_create_session_signed_by_someone_else_is_rejected():
    with pytest.raises(ValueError, match="create-session signer does not match payer"):
        verify(make_proof(create_session=_create_session_messages(signer_key=OTHER_KEY)))


def test_create_session_without_provider_participant_is_rejected():
    with pytest.raises(ValueError, match="does not include the tool provider"):
        verify(make_proof(create_session=_create_session_messages(participants=[PAYER])))


def test_create_session_for_different_session_is_rejected():
    with pytest.raises(ValueError, match="session IDs do not match"):
        verify(make_proof(create_session=_create_session_messages(session_id="0x" + "99" * 32)))


# ─── Malformed input ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "not-base64!!", base64.b64encode(b"not json").decode()])
def test_malformed_header_is_rejected(bad):
    with pytest.raises(ValueError):
        verify(bad)
