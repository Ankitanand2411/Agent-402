"""
Nitrolite off-chain payment verifier.
Uses eth-account for ECDSA signature recovery over the exact payload the client signed.
"""
import base64
import json

from eth_account import Account
from web3 import Web3


def _normalize_address(value: str, field_name: str) -> str:
    if not value or not isinstance(value, str):
        raise ValueError(f"Missing {field_name}")
    return Web3.to_checksum_address(value)


def _parse_json_message(raw: str, field_name: str) -> dict:
    if not raw or not isinstance(raw, str):
        raise ValueError(f"Missing {field_name}")
    try:
        return json.loads(raw)
    except Exception as e:
        raise ValueError(f"Invalid {field_name}: {e}") from e


def _recover_request_signer(request_message: dict) -> str:
    """Recover the signer of a Nitrolite RPC request."""
    req = request_message.get("req")
    if not req or not isinstance(req, list):
        raise ValueError("Nitrolite proof is missing req payload")

    sig_list = request_message.get("sig", [])
    if not sig_list or not sig_list[0]:
        raise ValueError("Nitrolite proof is missing request signature")

    sig = sig_list[0]

    digest = Web3.keccak(serialize_request_payload(req))

    # Recover from the raw 32-byte keccak digest, with no EIP-191 prefix —
    # this matches ethers.recoverAddress on the JS side.
    recovered = Account._recover_hash(digest, signature=sig)
    return Web3.to_checksum_address(recovered)


def serialize_request_payload(req: list) -> bytes:
    """
    Byte-for-byte reproduction of the JS `JSON.stringify(req)` the client signed.

    JSON.stringify emits compact JSON with no spaces and leaves non-ASCII
    characters unescaped (UTF-8). Python's json.dumps must therefore use
    compact separators AND ensure_ascii=False; with the default
    ensure_ascii=True a payload containing "é" would serialise to "\\u00e9",
    hash differently, and every signature over it would fail to recover.
    """
    return json.dumps(req, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _assert_rpc_method(message: dict, expected_method: str, field_name: str):
    req = message.get("req", [])
    res = message.get("res", [])
    actual_method = req[1] if len(req) > 1 else (res[1] if len(res) > 1 else None)
    if res and actual_method == "error":
        # An RPC error response is handled by _get_response_params, which
        # raises with the ClearNode's own error text — more useful than
        # reporting a method mismatch here.
        return
    if actual_method != expected_method:
        raise ValueError(
            f"Expected {field_name} method {expected_method}, got {actual_method or 'unknown'}"
        )


def _get_response_params(response_message: dict) -> dict:
    res = response_message.get("res")
    if not res or not isinstance(res, list):
        raise ValueError("Nitrolite response is missing res payload")
    if res[1] == "error":
        detail = (res[2] or {}).get("error") or json.dumps(res[2] or {})
        raise ValueError(f"Nitrolite rejected payment: {detail}")
    return res[2] if len(res) > 2 else {}


def _find_allocation(allocations: list, participant: str) -> dict | None:
    normalized = Web3.to_checksum_address(participant)
    for entry in allocations or []:
        try:
            if Web3.to_checksum_address(entry.get("participant", "")) == normalized:
                return entry
        except Exception:
            continue
    return None


def verify_nitrolite_proof(
    encoded_proof: str,
    expected_amount_atomic: str,
    expected_provider_wallet: str,
    expected_tool_name: str,
    expected_payer: str,
) -> dict:
    """
    Verify a Nitrolite off-chain payment proof.
    Returns a receipt dict on success, raises ValueError on failure.
    """
    if not encoded_proof:
        raise ValueError("Missing X-Nitrolite-Proof header")

    try:
        proof = json.loads(base64.b64decode(encoded_proof).decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Invalid X-Nitrolite-Proof header: {e}") from e

    submit_request = _parse_json_message(proof.get("submitStateRequest", ""), "submitStateRequest")
    submit_response = _parse_json_message(proof.get("submitStateResponse", ""), "submitStateResponse")

    _assert_rpc_method(submit_request, "submit_app_state", "submitStateRequest")
    _assert_rpc_method(submit_response, "submit_app_state", "submitStateResponse")

    req_params = submit_request.get("req", [None, None, {}])[2] or {}
    submit_state = req_params.get("state", {})
    submit_allocations = submit_state.get("allocations", [])

    if req_params.get("protocol") != "nitroliterpc":
        raise ValueError(f"Unexpected Nitrolite protocol {req_params.get('protocol', 'unknown')}")

    recovered_payer = _recover_request_signer(submit_request)
    expected_payer_address = _normalize_address(
        expected_payer or proof.get("payer", ""), "payer"
    )
    if recovered_payer != expected_payer_address:
        raise ValueError(
            f"Nitrolite payer signature mismatch: expected {expected_payer_address}, got {recovered_payer}"
        )

    provider_address = _normalize_address(expected_provider_wallet, "provider")
    provider_allocation = _find_allocation(submit_allocations, provider_address)
    if not provider_allocation:
        raise ValueError("Nitrolite state does not allocate funds to the tool provider")

    payer_allocation = _find_allocation(submit_allocations, expected_payer_address)
    if not payer_allocation:
        raise ValueError("Nitrolite state does not include the payer allocation")

    provider_amount = int(provider_allocation.get("amount") or 0)
    expected_amount = int(expected_amount_atomic)
    if provider_amount < expected_amount:
        raise ValueError(
            f"Nitrolite payment is insufficient: expected {expected_amount}, got {provider_amount}"
        )

    if (provider_allocation.get("asset") or "").lower() != "usdc":
        raise ValueError(f"Unexpected Nitrolite asset {provider_allocation.get('asset', 'unknown')}")

    if proof.get("toolName") and proof["toolName"] != expected_tool_name:
        raise ValueError(
            f"Nitrolite proof tool mismatch: expected {expected_tool_name}, got {proof['toolName']}"
        )

    if proof.get("provider") and _normalize_address(proof["provider"], "proof.provider") != provider_address:
        raise ValueError("Nitrolite proof provider mismatch")

    # Validate session ID
    submit_response_params = _get_response_params(submit_response)
    response_session_id = (
        submit_response_params.get("app_session_id")
        or req_params.get("app_session_id")
        or proof.get("appSessionId")
    )
    if not response_session_id:
        raise ValueError("Nitrolite response is missing app_session_id")

    # Optional: validate create-session proof
    if proof.get("createSessionRequest") and proof.get("createSessionResponse"):
        create_request = _parse_json_message(proof["createSessionRequest"], "createSessionRequest")
        create_response = _parse_json_message(proof["createSessionResponse"], "createSessionResponse")
        _assert_rpc_method(create_request, "create_app_session", "createSessionRequest")
        _assert_rpc_method(create_response, "create_app_session", "createSessionResponse")

        create_signer = _recover_request_signer(create_request)
        if create_signer != expected_payer_address:
            raise ValueError("Nitrolite create-session signer does not match payer")

        create_params = (create_request.get("req") or [None, None, {}])[2] or {}
        definition = create_params.get("definition", {})
        participants = definition.get("participants", [])

        if not any(Web3.to_checksum_address(p) == expected_payer_address for p in participants):
            raise ValueError("Nitrolite app session does not include the payer")
        if not any(Web3.to_checksum_address(p) == provider_address for p in participants):
            raise ValueError("Nitrolite app session does not include the tool provider")

        create_response_params = _get_response_params(create_response)
        created_session_id = (
            create_response_params.get("app_session_id")
            or (create_response_params[0].get("app_session_id") if isinstance(create_response_params, list) and create_response_params else None)
        )
        if created_session_id and created_session_id != response_session_id:
            raise ValueError("Nitrolite create-session and submit-state session IDs do not match")

    return {
        "verified": True,
        "paymentMethod": "yellow-nitrolite",
        "protocol": "nitrolite-erc7824",
        "payer": expected_payer_address,
        "provider": provider_address,
        "appSessionId": response_session_id,
        "amount": str(provider_amount),
        "asset": "usdc",
        "toolName": expected_tool_name,
        "wsUrl": proof.get("wsUrl"),
        "stateVersion": submit_state.get("version"),
        "proof": proof,
    }
