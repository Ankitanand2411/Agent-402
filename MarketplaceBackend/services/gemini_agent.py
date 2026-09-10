"""
Gemini planning call shared by the per-turn endpoint (/gemini/chat) and the
server-side agent graph (agent/). One place for the system prompt, schema
sanitisation, history conversion and response parsing.
"""

import asyncio
import re
from typing import Any

from google import genai
from google.genai import types as genai_types

from config import settings
from services import tool_retrieval
from services.telemetry import telemetry

TOOL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
SAFE_TYPES = {"string", "number", "integer", "boolean", "array", "object"}

SYSTEM_PROMPT = """You are an autonomous agent that can use MCP tools from a paid marketplace.

IMPORTANT RULES:
1. ALWAYS EXPLAIN YOUR PLAN: Before calling any tools, provide a short 1-sentence explanation of what you are about to do and why. This is CRITICAL.
2. PLAN AHEAD: If you need to call multiple tools that are independent, output ALL tool calls in a single turn.
3. CHAINING: If a tool output is needed for the next step, call the first tool, wait for the result, then call the next.
4. If multiple tools provide the same capability, ALWAYS choose the lowest-cost tool.
5. Only call a tool if it is absolutely necessary to answer the question.
6. Construct arguments exactly according to the tool parameter schema.
7. After receiving tool results, synthesize a final, complete answer for the user.
8. Never fabricate tool results. If a tool failed, say so and decide whether to retry, use another tool, or stop.
"""


# ─── Schema sanitisation ──────────────────────────────────────────────────────

def sanitize_parameters(params: Any) -> dict:
    """Coerce a provider-submitted parameter schema into the subset Gemini accepts."""
    if not isinstance(params, dict):
        return {"type": "object", "properties": {}}
    properties = params.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    safe_props = {}
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            safe_props[key] = {"type": "string"}
            continue
        prop_type = prop.get("type") if prop.get("type") in SAFE_TYPES else "string"
        safe = {"type": prop_type}
        if isinstance(prop.get("description"), str):
            safe["description"] = prop["description"]
        if isinstance(prop.get("enum"), list) and all(isinstance(v, (str, int, float, bool)) for v in prop["enum"]):
            safe["enum"] = prop["enum"]
        if prop_type == "array" and isinstance(prop.get("items"), dict):
            safe["items"] = sanitize_parameters(prop["items"]) if prop["items"].get("type") == "object" else {"type": prop["items"].get("type", "string") if prop["items"].get("type") in SAFE_TYPES else "string"}
        safe_props[key] = safe
    out = {"type": "object", "properties": safe_props}
    required = [r for r in params.get("required", []) if isinstance(r, str) and r in safe_props]
    if required:
        out["required"] = required
    return out


def sanitize_tools(tools: list) -> list:
    """Drop malformed tools; normalise names and descriptions."""
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not isinstance(name, str) or not TOOL_NAME_RE.match(name):
            continue
        description = t.get("description")
        if not isinstance(description, str) or not description.strip():
            description = f"Tool {name}"
        out.append({"name": name, "description": description.strip(), "parameters": sanitize_parameters(t.get("parameters"))})
    return out


def build_tool_declarations(sanitized_tools: list) -> list[genai_types.FunctionDeclaration]:
    return [
        genai_types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["parameters"])
        for t in sanitized_tools
    ]


# ─── History conversion ───────────────────────────────────────────────────────

def _part(p: Any) -> genai_types.Part | None:
    if isinstance(p, str):
        return genai_types.Part(text=p)
    if not isinstance(p, dict):
        return None
    if "text" in p:
        return genai_types.Part(text=p["text"])
    if "functionCall" in p:
        fc = p["functionCall"]
        return genai_types.Part(function_call=genai_types.FunctionCall(name=fc.get("name", ""), args=fc.get("args", {})))
    if "functionResponse" in p:
        fr = p["functionResponse"]
        return genai_types.Part(function_response=genai_types.FunctionResponse(
            name=fr.get("name", ""), response=fr.get("response") or {"result": "No content"},
        ))
    return None


def to_contents(history: list) -> list[genai_types.Content]:
    """JS-style turns ({role, parts}) → genai Content list."""
    contents = []
    for turn in history or []:
        parts = [x for x in (_part(p) for p in turn.get("parts", [])) if x is not None]
        if parts:
            role = "model" if turn.get("role", "user") in ("assistant", "model") else turn.get("role", "user")
            contents.append(genai_types.Content(role=role, parts=parts))
    return contents


def message_to_parts(message: Any) -> list[dict]:
    """Normalise an incoming message (string or {parts:[...]}) into JS-style parts."""
    if isinstance(message, str):
        return [{"text": message}]
    if isinstance(message, dict) and "parts" in message:
        return [({"text": p} if isinstance(p, str) else p) for p in message["parts"]]
    return [{"text": str(message)}]


def usage_of(response: Any) -> dict:
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return {}
    return {
        "promptTokens": getattr(meta, "prompt_token_count", None),
        "candidatesTokens": getattr(meta, "candidates_token_count", None),
        "totalTokens": getattr(meta, "total_token_count", None),
    }


# ─── The planning call ────────────────────────────────────────────────────────

async def generate(history: list[dict], tools: list[dict]) -> dict:
    """
    One Gemini turn over `history` (JS-style turns, last one from the user) with
    the relevant subset of `tools` declared. Returns
      {text, function_calls: [{name, args}], parts: [...], usage, tools_declared, tools_available}
    Records the turn in telemetry. Raises on API failure.
    """
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    sanitized = sanitize_tools(tools)
    last_user = next((t for t in reversed(history) if t.get("role") == "user"), None)
    selected = await tool_retrieval.select_for_request(sanitized, history[:-1] if last_user is history[-1] else history, last_user or "")
    declarations = build_tool_declarations(selected) if selected else []

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    config = genai_types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[genai_types.Tool(function_declarations=declarations)] if declarations else None,
    )
    import time
    started = time.perf_counter()
    try:
        response = await asyncio.to_thread(client.models.generate_content, model=settings.AGENT_MODEL, contents=to_contents(history), config=config)
    except Exception:
        telemetry.record_gemini_turn(latency_ms=(time.perf_counter() - started) * 1000, usage={}, tools_declared=len(selected),
                                     tools_available=len(sanitized), function_calls=0, error=True)
        raise

    text = ""
    try:
        text = response.text or ""
    except Exception:
        pass
    function_calls, parts_out = [], []
    try:
        for p in response.candidates[0].content.parts:
            if getattr(p, "function_call", None) and p.function_call.name:
                fc = {"name": p.function_call.name, "args": dict(p.function_call.args or {})}
                function_calls.append(fc)
                parts_out.append({"functionCall": fc})
            elif getattr(p, "text", None):
                parts_out.append({"text": p.text})
    except Exception:
        pass
    usage = usage_of(response)
    telemetry.record_gemini_turn(latency_ms=(time.perf_counter() - started) * 1000, usage=usage, tools_declared=len(selected),
                                 tools_available=len(sanitized), function_calls=len(function_calls))
    return {
        "text": text, "function_calls": function_calls, "parts": parts_out or ([{"text": text}] if text else []),
        "usage": usage, "tools_declared": len(selected), "tools_available": len(sanitized),
    }
