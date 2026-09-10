"""
Tool executor — runs registered tools in two modes:
  1. proxy  → HTTP POST to a remote URL
  2. code   → executes JS files via a Node.js subprocess
"""
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import httpx

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
    """Forward the request body to the tool provider's URL."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            target_url,
            json=body,
            headers={"Content-Type": "application/json"},
        )
        data = response.json()
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
_JS_SANDBOX_TEMPLATE = """
const exports = {{}};
{tool_code}
const fn = exports.default;
if (typeof fn !== 'function') {{
  process.stderr.write('Sandboxed code did not export a default function');
  process.exit(1);
}}
fn({tool_input_json}).then(r => console.log(JSON.stringify(r))).catch(e => {{
  process.stderr.write(e.message);
  process.exit(1);
}});
"""


async def execute_code_tool(tool_name: str, body: dict, trusted: bool = False) -> dict:
    """
    Execute a JS tool file using a Node.js subprocess.
    Trusted tools use ESM dynamic import; untrusted tools use a sandboxed CJS shim.
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
        sandboxed_code = normalized_code.replace("export default ", "exports.default = ")
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
            "node", runner_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(USER_TOOLS_DIR),
            env={**os.environ},  # pass full env so tools can read GROQ_API_KEY etc.
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise RuntimeError(f"Tool {tool_name} timed out after 30s") from e
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
