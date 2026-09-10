"""Admin bearer auth and EIP-191 provider signatures in services.auth."""

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from config import settings
from services import auth

PROVIDER_KEY = "0x" + "44" * 32
PROVIDER = Account.from_key(PROVIDER_KEY).address


@pytest.fixture
def admin_app(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "s3cret-admin")
    app = FastAPI()

    @app.post("/admin-only", dependencies=[Depends(auth.require_admin)])
    async def admin_only():
        return {"ok": True}

    return TestClient(app)


def test_admin_key_via_bearer_or_header(admin_app):
    assert admin_app.post("/admin-only", headers={"Authorization": "Bearer s3cret-admin"}).status_code == 200
    assert admin_app.post("/admin-only", headers={"X-Admin-Key": "s3cret-admin"}).status_code == 200


def test_missing_or_wrong_admin_key_is_401(admin_app):
    assert admin_app.post("/admin-only").status_code == 401
    assert admin_app.post("/admin-only", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert admin_app.post("/admin-only", headers={"Authorization": "Bearer s3cret-admi"}).status_code == 401  # prefix


def test_unconfigured_admin_key_fails_closed(admin_app, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "")
    r = admin_app.post("/admin-only", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 503


def _sign(tool: str, wallet: str, key: str = PROVIDER_KEY) -> str:
    msg = encode_defunct(text=auth.registration_message(tool, wallet))
    return Account.sign_message(msg, private_key=key).signature.hex()


def test_valid_provider_signature_is_accepted():
    assert auth.verify_provider_signature("weather", PROVIDER, _sign("weather", PROVIDER)) is True
    # Address casing must not matter.
    assert auth.verify_provider_signature("weather", PROVIDER.lower(), _sign("weather", PROVIDER)) is True


def test_signature_by_other_key_is_rejected():
    assert auth.verify_provider_signature("weather", PROVIDER, _sign("weather", PROVIDER, "0x" + "55" * 32)) is False


def test_signature_is_bound_to_tool_name():
    sig_for_weather = _sign("weather", PROVIDER)
    assert auth.verify_provider_signature("other_tool", PROVIDER, sig_for_weather) is False


def test_garbage_signature_is_rejected_not_raised():
    assert auth.verify_provider_signature("weather", PROVIDER, "0xdeadbeef") is False
    assert auth.verify_provider_signature("weather", "not-an-address", "0x00") is False


def test_registration_message_is_stable_and_readable():
    assert auth.registration_message("weather", PROVIDER.lower()) == (
        f"Agent402 tool registration\ntool: weather\nwallet: {PROVIDER}"
    )
