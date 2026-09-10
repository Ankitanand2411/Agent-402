"""
One-off: apply the old `get_audio` hot-patch to the STORED tool code, once.

Until this cleanup, tool_executor.normalize_tool_code rewrote the `get_audio`
tool's source on every load (renamed a deprecated TTS model and voice, added
guard clauses). That rewrite now lives here so it can be applied to the code
in MongoDB a single time, after which the executor runs stored code as-is.

    python scripts/patch_stored_tool_code.py            # dry run: prints a diff
    python scripts/patch_stored_tool_code.py --apply    # writes the patched code back
"""

import argparse
import asyncio
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database  # noqa: E402

TOOL_NAME = "get_audio"


def legacy_normalise(code: str) -> str:
    code = code.replace("\r\n", "\n")
    if not re.search(r"if\s*\(\s*!apiKey\s*\)\s*\{", code) and "GROQ_API_KEY not configured" in code:
        code = re.sub(
            r"(\s*const apiKey = process\.env\.GROQ_API_KEY;\s*)\n\s*throw new Error\(",
            r"\1\n  if (!apiKey) {\n    throw new Error(",
            code,
        )
    code = re.sub(r"^\s*\}\s*$", "  }", code, flags=re.MULTILINE)
    code = re.sub(
        r"export\s+default\s+async\s+function\s*\(\s*\{\s*text\s*,\s*voice\s*\}\s*\)\s*\{",
        "export default async function({ text, voice = 'hannah' }) {",
        code,
    )
    code = re.sub(r"(['\"])playai-tts\1", "'canopylabs/orpheus-v1-english'", code)
    code = re.sub(r"(['\"])Fritz-PlayAI\1", "'hannah'", code)
    if not re.search(r"if\s*\(\s*!text\??\s*\)\s*\{", code):
        code = re.sub(
            r"(const apiKey = process\.env\.GROQ_API_KEY;\s*\n(?:\s*if\s*\(\s*!apiKey\s*\)\s*\{[\s\S]*?\}\s*\n)?)",
            lambda m: m.group(0) + "\n  if (!text || !String(text).trim()) {\n    throw new Error('Text is required to generate audio');\n  }\n",
            code,
        )
    return code


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    await database.connect_db()
    doc = await database.tools_collection.find_one({"name": TOOL_NAME})
    if not doc:
        print(f"no tool named {TOOL_NAME!r}; nothing to do")
        return
    before = doc.get("code", "") or ""
    after = legacy_normalise(before)
    if before == after:
        print("stored code already normalised; nothing to do")
        return
    print("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "stored", "patched")))
    if args.apply:
        await database.tools_collection.update_one({"name": TOOL_NAME}, {"$set": {"code": after}})
        print("applied")
    else:
        print("\n(dry run; pass --apply to write)")
    await database.close_db()


if __name__ == "__main__":
    asyncio.run(main())
