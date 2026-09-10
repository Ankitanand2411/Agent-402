"""
Security: outbound URL policy (SSRF) and code-tool isolation.

DNS is faked so each address class can be tested deterministically; the Node
subprocess is faked so the environment and limits passed to it can be asserted
without running JavaScript.
"""

import asyncio
import ipaddress
import json
import socket
from types import SimpleNamespace

import pytest

from config import settings
from services import url_policy
from tests.conftest import ECHO_TOOL_DOC
from tool_executor import executor

PUBLIC = ipaddress.ip_address("93.184.216.34")


def resolver(*ips):
    return lambda host: [ipaddress.ip_address(ip) for ip in ips]


# ─── URL policy ───────────────────────────────────────────────────────────────

def test_public_https_is_allowed(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve", resolver("93.184.216.34"))
    assert url_policy.validate_target_url("  https://api.provider.example/run  ") == "https://api.provider.example/run"


@pytest.mark.parametrize("ip", [
    "127.0.0.1",          # loopback: this server's own routes
    "10.1.2.3",           # RFC1918
    "172.16.5.5",
    "192.168.1.10",
    "169.254.169.254",    # cloud metadata
    "0.0.0.0",
    "::1",
    "fd00::1",            # IPv6 unique-local
    "fe80::1",            # IPv6 link-local
    "::ffff:127.0.0.1",   # IPv4-mapped loopback
    "224.0.0.1",          # multicast
])
def test_non_public_addresses_are_rejected(monkeypatch, ip):
    monkeypatch.setattr(url_policy, "resolve", resolver(ip))
    with pytest.raises(url_policy.UnsafeURL, match="non-public"):
        url_policy.validate_target_url("https://innocent-looking.example/hook")


def test_any_private_record_among_several_rejects(monkeypatch):
    # A host with one public and one private A record is still dangerous.
    monkeypatch.setattr(url_policy, "resolve", resolver("93.184.216.34", "10.0.0.5"))
    with pytest.raises(url_policy.UnsafeURL):
        url_policy.validate_target_url("https://split-horizon.example/")


@pytest.mark.parametrize("url", [
    "https://localhost/x",
    "https://foo.localhost/x",
    "https://db.internal/x",
    "https://printer.local/x",
    "https://metadata.google.internal/computeMetadata/v1/",
])
def test_local_style_hostnames_are_rejected_before_dns(monkeypatch, url):
    monkeypatch.setattr(url_policy, "resolve", lambda host: pytest.fail("must not resolve blocked names"))
    with pytest.raises(url_policy.UnsafeURL, match="not allowed"):
        url_policy.validate_target_url(url)


def test_literal_ips_are_checked_without_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("no DNS for literals"))
    with pytest.raises(url_policy.UnsafeURL):
        url_policy.validate_target_url("https://169.254.169.254/latest/meta-data/")
    with pytest.raises(url_policy.UnsafeURL):
        url_policy.validate_target_url("https://[::1]/")
    assert url_policy.validate_target_url("https://93.184.216.34/") == "https://93.184.216.34/"


def test_scheme_credentials_and_empty_host(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve", resolver("93.184.216.34"))
    monkeypatch.setattr(settings, "ALLOW_INSECURE_TOOL_URLS", False)
    with pytest.raises(url_policy.UnsafeURL, match="scheme"):
        url_policy.validate_target_url("http://api.example/run")            # http only in dev
    with pytest.raises(url_policy.UnsafeURL, match="scheme"):
        url_policy.validate_target_url("file:///etc/passwd")
    with pytest.raises(url_policy.UnsafeURL, match="credentials"):
        url_policy.validate_target_url("https://user:pw@api.example/run")
    with pytest.raises(url_policy.UnsafeURL):
        url_policy.validate_target_url("https:///run")
    with pytest.raises(url_policy.UnsafeURL):
        url_policy.validate_target_url("")


def test_http_allowed_only_in_dev_mode(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve", resolver("93.184.216.34"))
    monkeypatch.setattr(settings, "ALLOW_INSECURE_TOOL_URLS", True)
    assert url_policy.validate_target_url("http://api.example/run")


def test_unresolvable_host_is_rejected(monkeypatch):
    def gai(*a, **k):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", gai)
    with pytest.raises(url_policy.UnsafeURL, match="does not resolve"):
        url_policy.validate_target_url("https://does-not-exist.example/")


# ─── Enforcement points ───────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, clean_registry, fake_collection, fake_ledger, fake_spend):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import tools as tools_router

    monkeypatch.setattr(settings, "ADMIN_API_KEY", "k")
    monkeypatch.setattr(settings, "ALLOW_INSECURE_TOOL_URLS", False)
    app = FastAPI()
    app.include_router(tools_router.router)
    return TestClient(app), fake_collection, clean_registry


def test_register_rejects_internal_target(client, monkeypatch):
    c, coll, _ = client
    monkeypatch.setattr(url_policy, "resolve", resolver("10.0.0.7"))
    r = c.post("/tools/register", json={"name": "evil", "description": "d", "price": "1", "type": "proxy",
                                        "targetUrl": "https://intranet.example/admin"})
    assert r.status_code == 400 and "targetUrl rejected" in r.json()["error"]
    assert coll.inserted == []


def test_register_rejects_metadata_endpoint_and_localhost(client, monkeypatch):
    c, coll, _ = client
    for url in ("https://169.254.169.254/latest/meta-data/", "https://localhost:3000/tools/x/approve"):
        r = c.post("/tools/register", json={"name": "evil", "description": "d", "price": "1", "type": "proxy", "targetUrl": url})
        assert r.status_code == 400
    assert coll.inserted == []


def test_approve_revalidates_stored_url(client, monkeypatch):
    c, coll, reg = client
    coll.docs.append({**ECHO_TOOL_DOC, "name": "stale", "targetUrl": "https://provider.example/run", "status": "pending"})
    monkeypatch.setattr(url_policy, "resolve", resolver("192.168.0.9"))     # DNS changed since registration
    r = c.post("/tools/stale/approve", headers={"Authorization": "Bearer k"})
    assert r.status_code == 400 and reg.get("stale") is None
    assert coll.updates == []


def test_approve_accepts_public_url(client, monkeypatch):
    c, coll, reg = client
    coll.docs.append({**ECHO_TOOL_DOC, "name": "ok", "targetUrl": "https://provider.example/run", "status": "pending"})
    monkeypatch.setattr(url_policy, "resolve", resolver("93.184.216.34"))
    assert c.post("/tools/ok/approve", headers={"Authorization": "Bearer k"}).status_code == 200
    assert reg.get("ok")["targetUrl"] == "https://provider.example/run"


async def test_proxy_call_revalidates_and_never_follows_redirects(monkeypatch):
    """Call-time check catches DNS rebinding after approval; redirects are disabled."""
    monkeypatch.setattr(url_policy, "resolve", resolver("127.0.0.1"))
    with pytest.raises(url_policy.UnsafeURL):
        await executor.execute_proxy_tool("https://rebound.example/run", {})

    captured = {}

    class FakeStream:
        def __init__(self, status=200, chunks=(b'{"ok": true}',)):
            self.status_code, self._chunks = status, chunks
            self.is_success = status < 400

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_bytes(self):
            for c in self._chunks:
                yield c

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, **kw):
            captured["url"] = url
            return FakeStream()

    monkeypatch.setattr(url_policy, "resolve", resolver("93.184.216.34"))
    monkeypatch.setattr(executor.httpx, "AsyncClient", FakeClient)
    result = await executor.execute_proxy_tool("https://provider.example/run", {"q": 1})
    assert result == {"success": True, "result": "Tool call successful", "data": {"ok": True}}
    assert captured["follow_redirects"] is False


async def test_proxy_response_size_is_capped(monkeypatch):
    monkeypatch.setattr(url_policy, "resolve", resolver("93.184.216.34"))
    monkeypatch.setattr(settings, "PROXY_MAX_RESPONSE_BYTES", 10)

    class FakeStream:
        status_code, is_success = 200, True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_bytes(self):
            yield b"x" * 8
            yield b"x" * 8

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return FakeStream()

    monkeypatch.setattr(executor.httpx, "AsyncClient", FakeClient)
    with pytest.raises(RuntimeError, match="exceeded"):
        await executor.execute_proxy_tool("https://provider.example/run", {})


# ─── Code-tool isolation ──────────────────────────────────────────────────────

def test_tool_environment_withholds_server_secrets(monkeypatch):
    for k, v in {
        "PATH": "/usr/bin", "ESCROW_PRIVATE_KEY": "0xsecret", "MONGODB_URI": "mongodb://x", "GEMINI_API_KEY": "g",
        "ADMIN_API_KEY": "a", "GROQ_API_KEY": "groq", "ADZUNA_APP_ID": "id", "RANDOM_OTHER": "z",
    }.items():
        monkeypatch.setenv(k, v)
    env = executor.tool_environment()
    assert env["PATH"] == "/usr/bin"
    assert env["GROQ_API_KEY"] == "groq" and env["ADZUNA_APP_ID"] == "id"        # allowlisted
    for secret in ("ESCROW_PRIVATE_KEY", "MONGODB_URI", "GEMINI_API_KEY", "ADMIN_API_KEY", "RANDOM_OTHER"):
        assert secret not in env


def test_tool_environment_allowlist_is_configurable(monkeypatch):
    monkeypatch.setenv("MY_TOOL_TOKEN", "t")
    monkeypatch.setenv("GROQ_API_KEY", "groq")
    monkeypatch.setattr(settings, "TOOL_ENV_ALLOWLIST", "MY_TOOL_TOKEN")
    env = executor.tool_environment()
    assert env["MY_TOOL_TOKEN"] == "t" and "GROQ_API_KEY" not in env


async def test_code_tool_runs_with_minimal_env_and_limits(monkeypatch, tmp_path):
    monkeypatch.setattr(executor, "USER_TOOLS_DIR", tmp_path)
    (tmp_path / "echo.js").write_text("export default async function(i) { return i; }", "utf-8")
    monkeypatch.setenv("ESCROW_PRIVATE_KEY", "0xsecret")
    monkeypatch.setenv("GROQ_API_KEY", "groq")
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"], captured["kwargs"] = args, kwargs

        async def communicate():
            return json.dumps({"echo": True}).encode(), b""

        return SimpleNamespace(communicate=communicate, returncode=0, kill=lambda: None)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await executor.execute_code_tool("echo", {"a": 1}, trusted=False)

    assert result["data"] == {"echo": True}
    env = captured["kwargs"]["env"]
    assert "ESCROW_PRIVATE_KEY" not in env and env.get("GROQ_API_KEY") == "groq"
    assert captured["kwargs"]["preexec_fn"] is executor._limit_resources
    assert any(a.startswith("--max-old-space-size=") for a in captured["args"])


def test_limit_resources_sets_rlimits(monkeypatch):
    calls = []
    monkeypatch.setattr(executor.resource, "setrlimit", lambda kind, value: calls.append((kind, value)))
    monkeypatch.setattr(settings, "TOOL_MAX_MEMORY_MB", 256)
    monkeypatch.setattr(settings, "TOOL_MAX_CPU_SECONDS", 12)
    executor._limit_resources()
    kinds = {k for k, _ in calls}
    assert executor.resource.RLIMIT_DATA in kinds and executor.resource.RLIMIT_CPU in kinds
    assert executor.resource.RLIMIT_AS not in kinds                      # would kill Node at startup (V8 code range)
    assert (executor.resource.RLIMIT_DATA, (256 * 1024 * 1024, 256 * 1024 * 1024)) in calls
    assert (executor.resource.RLIMIT_CPU, (12, 12)) in calls
