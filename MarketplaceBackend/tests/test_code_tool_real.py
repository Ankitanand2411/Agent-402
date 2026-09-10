"""
Real Node.js subprocess tests for code-tool isolation (skipped where node is
absent). These exist because the faked-subprocess tests cannot catch what
matters here: an rlimit that kills Node at startup, a shim that is a syntax
error, or a secret that leaks through the environment.
"""

import shutil
import time

import pytest

from tool_executor import executor

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

ECHO = "export default async function(input) { return { echoed: input, secret: process.env.ESCROW_PRIVATE_KEY || null, groq: process.env.GROQ_API_KEY || null }; }"
HOG = "export default async function() { const a = []; for (;;) { a.push(new Array(1e6).fill(1)); } }"
SLOW = "export default async function() { await new Promise(r => setTimeout(r, 60000)); return 1; }"


@pytest.fixture
def tools_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(executor, "USER_TOOLS_DIR", tmp_path)
    monkeypatch.setenv("ESCROW_PRIVATE_KEY", "0xsecret")
    monkeypatch.setenv("GROQ_API_KEY", "groq-visible")
    return tmp_path


@pytest.mark.parametrize("trusted", [False, True])
async def test_tool_runs_with_secrets_withheld(tools_dir, trusted):
    (tools_dir / "echo.js").write_text(ECHO, "utf-8")
    result = await executor.execute_code_tool("echo", {"a": 1}, trusted=trusted)
    assert result["success"] is True
    assert result["data"] == {"echoed": {"a": 1}, "secret": None, "groq": "groq-visible"}


async def test_memory_hog_is_killed(tools_dir):
    (tools_dir / "hog.js").write_text(HOG, "utf-8")
    started = time.perf_counter()
    with pytest.raises(RuntimeError):
        await executor.execute_code_tool("hog", {}, trusted=False)
    assert time.perf_counter() - started < 20


async def test_slow_tool_hits_timeout(tools_dir, monkeypatch):
    monkeypatch.setattr(executor.settings, "TOOL_TIMEOUT_SECONDS", 1.0)
    (tools_dir / "slow.js").write_text(SLOW, "utf-8")
    with pytest.raises(RuntimeError, match="timed out"):
        await executor.execute_code_tool("slow", {}, trusted=False)
