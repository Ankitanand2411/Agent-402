"""
Tool executor — runs registered tools in two modes:
  1. proxy  → HTTP POST to a remote URL
  2. code   → executes JS files via a Node.js subprocess
"""
import asyncio
import json
import logging
import os
import resource
import tempfile
from pathlib import Path

import httpx

from config import settings
from services.url_policy import validate_target_url

logger = logging.getLogger(__name__)

USER_TOOLS_DIR = Path(__file__).parent.parent / "user_tools"


# ---------------------------------------------------------------------------
# Code-tool normalisation
# ---------------------------------------------------------------------------

def normalize_tool_code(raw_code: str) -> str:
    """Normalise line endings. Tool-specific rewrites belong in the stored code, not here."""
    if not isinstance(raw_code, str):
        return raw_code
    return raw_code.replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# Proxy tool executor
# ---------------------------------------------------------------------------

async def execute_proxy_tool(target_url: str, body: dict) -> dict:
    """
    Forward the request body to the tool provider's URL.

    The URL is re-validated at call time (its DNS may have changed since
    approval), redirects are never followed, and the response is capped so a
    provider cannot make this server buffer gigabytes.
    """
    target_url = validate_target_url(target_url)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        async with client.stream("POST", target_url, json=body, headers={"Content-Type": "application/json"}) as response:
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > settings.PROXY_MAX_RESPONSE_BYTES:
                    raise RuntimeError(f"upstream response exceeded {settings.PROXY_MAX_RESPONSE_BYTES} bytes")
            try:
                data = json.loads(bytes(raw)) if raw else None
            except json.JSONDecodeError:
                data = bytes(raw).decode("utf-8", errors="replace")
            return {
                "success": response.is_success,
                "result": "Tool call successful" if response.is_success else "Tool call failed upstream",
                "data": data,
            }


# ---------------------------------------------------------------------------
# Code tool executor (JS via Node.js subprocess)
# ---------------------------------------------------------------------------

_JS_RUNNER_TEMPLATE = """
import {{ createRequire }} from 'module';
import {{ fileURLToPath }} from 'url';
import path from 'path';

// Minimal env injection for tool files
const toolInput = {tool_input_json};

const mod = await import('./{tool_name}.js?t=' + Date.now());
const fn = mod.default;
if (typeof fn !== 'function') {{
  console.error(JSON.stringify({{ error: 'Tool does not export a default function' }}));
  process.exit(1);
}}
const result = await fn(toolInput);
console.log(JSON.stringify(result));
"""

# Sandboxed runner (no module imports — for untrusted tools)
# `exports` is already a parameter of Node's CommonJS wrapper, so a shim that
# declared `const exports` was a SyntaxError and every untrusted tool failed to
# load. Use a private identifier instead.
_JS_SANDBOX_TEMPLATE = """
const __tool_exports = {{}};
{tool_code}
const fn = __tool_exports.default;
if (typeof fn !== 'function') {{
  process.stderr.write('Sandboxed code did not export a default function');
  process.exit(1);
}}
fn({tool_input_json}).then(r => console.log(JSON.stringify(r))).catch(e => {{
  process.stderr.write(e.message);
  process.exit(1);
}});
"""


def tool_environment() -> dict[str, str]:
    """
    The environment a tool subprocess gets: a minimal base plus the allowlisted
    variables. The server's own secrets are never in it.
    """
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR", "NODE_PATH") if k in os.environ}
    for name in (n.strip() for n in settings.TOOL_ENV_ALLOWLIST.split(",")):
        if name and name in os.environ:
            env[name] = os.environ[name]
    return env


def _limit_resources() -> None:
    """
    Runs in the child before exec: cap memory and CPU so a tool cannot exhaust the host.

    RLIMIT_DATA, not RLIMIT_AS: V8 reserves gigabytes of virtual address space
    (its code range) at startup without touching it, so an address-space limit
    of a few hundred MB kills Node immediately. RLIMIT_DATA counts memory that
    is actually allocated, which is what we want to bound; --max-old-space-size
    caps the JS heap on top.
    """
    mem = settings.TOOL_MAX_MEMORY_MB * 1024 * 1024
    cpu = settings.TOOL_MAX_CPU_SECONDS
    for kind, value in ((resource.RLIMIT_DATA, (mem, mem)), (resource.RLIMIT_CPU, (cpu, cpu)), (resource.RLIMIT_NPROC, (64, 64))):
        try:
            resource.setrlimit(kind, value)
        except (ValueError, OSError):
            pass  # some platforms/containers refuse; best effort


async def execute_code_tool(tool_name: str, body: dict, trusted: bool = False) -> dict:
    """
    Execute a JS tool file using a Node.js subprocess with a minimal environment
    and resource limits. Trusted tools use ESM dynamic import; untrusted tools
    use a CJS shim. Neither is real isolation (see README); the environment
    allowlist and rlimits bound the damage.
    """
    code_path = USER_TOOLS_DIR / f"{tool_name}.js"
    if not code_path.exists():
        raise FileNotFoundError(f"Tool file not found at {code_path}")

    source_code = code_path.read_text("utf-8")
    normalized_code = normalize_tool_code(source_code)

    # Write normalised code back so the subprocess picks it up
    runtime_path = USER_TOOLS_DIR / f"{tool_name}.runtime.mjs"
    runtime_path.write_text(normalized_code, "utf-8")

    tool_input_json = json.dumps(body)

    if trusted:
        # Use a tiny ESM runner that imports the tool file
        runner_content = _JS_RUNNER_TEMPLATE.format(
            tool_name=tool_name,
            tool_input_json=tool_input_json,
        )
        runner_ext = ".mjs"
    else:
        # CJS shim (not real isolation; see README known limitations)
        sandboxed_code = normalized_code.replace("export default ", "__tool_exports.default = ")
        runner_content = _JS_SANDBOX_TEMPLATE.format(
            tool_code=sandboxed_code,
            tool_input_json=tool_input_json,
        )
        runner_ext = ".cjs"

    with tempfile.NamedTemporaryFile(
        suffix=runner_ext, delete=False, dir=USER_TOOLS_DIR, mode="w", encoding="utf-8"
    ) as f:
        f.write(runner_content)
        runner_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "node", f"--max-old-space-size={settings.TOOL_MAX_MEMORY_MB // 2}", runner_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(USER_TOOLS_DIR),
            env=tool_environment(),
            preexec_fn=_limit_resources,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=settings.TOOL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise RuntimeError(f"Tool {tool_name} timed out after {settings.TOOL_TIMEOUT_SECONDS:g}s") from e
    finally:
        try:
            os.unlink(runner_path)
        except Exception:
            pass

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Tool {tool_name} failed: {err}")

    raw = stdout.decode("utf-8", errors="replace").strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = raw  # some tools return plain text

    label = "Tool executed successfully" if trusted else "Tool executed successfully (Sandboxed)"
    return {"success": True, "result": label, "data": result}
