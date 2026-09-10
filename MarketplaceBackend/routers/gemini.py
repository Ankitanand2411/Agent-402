"""
/gemini/chat endpoint — uses the new google-genai SDK (google.genai).

"""
import asyncio
import logging
import re
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types as genai_types

from config import settings
from models.tool import GeminiChatRequest
from services import tool_retrieval
from services.telemetry import telemetry

logger = logging.getLogger(__name__)
router = APIRouter()

SYSTEM_PROMPT = """You are an autonomous agent that can use MCP tools from a paid marketplace.

IMPORTANT RULES:
1. ALWAYS EXPLAIN YOUR PLAN: Before calling any tools, provide a short 1-sentence explanation of what you are about to do and why. This is CRITICAL.
2. PLAN AHEAD: If you need to call multiple tools that are independent, output ALL tool calls in a single turn.
3. CHAINING: If a tool output is needed for the next step, call the first tool, wait for the result, then call the next.
4. If multiple tools provide the same capability, ALWAYS choose the lowest-cost tool.
5. Only call a tool if it is absolutely necessary to answer the question.
6. Construct arguments exactly according to the tool parameter schema.
7. Be concise and helpful in your responses.

Each tool has a monetary cost stated in its description. Consider cost when selecting tools."""

SAFE_TYPES = {"string", "number", "integer", "boolean", "array", "object"}
TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _sanitize_parameters(params: Any) -> dict:
    """Coerce a provider-submitted parameter schema into the subset Gemini accepts."""
    if not params or not isinstance(params, dict):
        return {"type": "object", "properties": {}}

    properties = {}
    for key, value in (params.get("properties") or {}).items():
        if not key or not value or not isinstance(value, dict):
            continue
        prop_type = value.get("type") if value.get("type") in SAFE_TYPES else "string"
        prop: dict = {"type": prop_type}
        if isinstance(value.get("description"), str) and value["description"].strip():
            prop["description"] = value["description"].strip()
        if isinstance(value.get("enum"), list) and all(
            isinstance(i, (str, int, float)) for i in value["enum"]
        ):
            prop["enum"] = value["enum"]
        properties[key] = prop

    required_raw = params.get("required", [])
    required = [r for r in required_raw if isinstance(r, str) and r in properties]

    result = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def _sanitize_tools(tools: list) -> list:
    """Drop malformed tools; normalise names and descriptions."""
    if not isinstance(tools, list):
        return []
    sanitized = []
    for tool in tools:
        if not tool or not isinstance(tool, dict):
            continue
        name = tool.get("name", "")
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not TOOL_NAME_RE.match(name):
            continue
        description = tool.get("description", "")
        if not isinstance(description, str) or not description.strip():
            description = f"Tool {name}"
        sanitized.append({
            "name": name,
            "description": description.strip(),
            "parameters": _sanitize_parameters(tool.get("parameters")),
        })
    return sanitized


def _build_tool_declarations(sanitized_tools: list) -> list[genai_types.FunctionDeclaration]:
    """Convert sanitized tool dicts to genai FunctionDeclaration objects."""
    declarations = []
    for t in sanitized_tools:
        params = t["parameters"]
        props = {}
        for k, v in params.get("properties", {}).items():
            type_map = {
                "string": genai_types.Type.STRING,
                "number": genai_types.Type.NUMBER,
                "integer": genai_types.Type.INTEGER,
                "boolean": genai_types.Type.BOOLEAN,
                "array": genai_types.Type.ARRAY,
                "object": genai_types.Type.OBJECT,
            }
            props[k] = genai_types.Schema(
                type=type_map.get(v.get("type", "string"), genai_types.Type.STRING),
                description=v.get("description", ""),
            )

        declarations.append(
            genai_types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=genai_types.Schema(
                    type=genai_types.Type.OBJECT,
                    properties=props,
                    required=params.get("required", []),
                ),
            )
        )
    return declarations


def _clean_history_for_genai(history: list) -> list[genai_types.Content]:
    """
    Convert JS-style history dicts to genai Content objects.
    Handles both text turns and functionResponse turns.
    """
    contents = []
    for turn in history or []:
        role = turn.get("role", "user")
        parts_raw = turn.get("parts", [])
        parts = []

        for p in parts_raw:
            if isinstance(p, str):
                parts.append(genai_types.Part(text=p))
            elif isinstance(p, dict):
                if "text" in p:
                    parts.append(genai_types.Part(text=p["text"]))
                elif "functionCall" in p:
                    fc = p["functionCall"]
                    parts.append(genai_types.Part(
                        function_call=genai_types.FunctionCall(
                            name=fc.get("name", ""),
                            args=fc.get("args", {}),
                        )
                    ))
                elif "functionResponse" in p:
                    fr = p["functionResponse"]
                    response = fr.get("response") or {"result": "No content"}
                    parts.append(genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=fr.get("name", ""),
                            response=response,
                        )
                    ))

        if parts:
            # genai uses "model" not "assistant"
            genai_role = "model" if role in ("assistant", "model") else role
            contents.append(genai_types.Content(role=genai_role, parts=parts))

    return contents


def _message_to_parts(message: Any) -> list[genai_types.Part]:
    """Convert the incoming message (string or {parts:[...]}) to genai Part list."""
    if isinstance(message, str):
        return [genai_types.Part(text=message)]

    if isinstance(message, dict) and "parts" in message:
        parts = []
        for p in message["parts"]:
            if isinstance(p, str):
                parts.append(genai_types.Part(text=p))
            elif isinstance(p, dict):
                if "text" in p:
                    parts.append(genai_types.Part(text=p["text"]))
                elif "functionResponse" in p:
                    fr = p["functionResponse"]
                    response = fr.get("response") or {"result": "No content"}
                    parts.append(genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=fr.get("name", ""),
                            response=response,
                        )
                    ))
        return parts

    # Fallback
    return [genai_types.Part(text=str(message))]


def _usage_of(response: Any) -> dict:
    """Token accounting from the SDK response; the before/after number for tool retrieval."""
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return {}
    return {
        "promptTokens": getattr(meta, "prompt_token_count", None),
        "candidatesTokens": getattr(meta, "candidates_token_count", None),
        "totalTokens": getattr(meta, "total_token_count", None),
    }


@router.post("/gemini/chat")
async def gemini_chat(body: GeminiChatRequest):
    if not settings.GEMINI_API_KEY:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "GEMINI_API_KEY is not configured in .env"},
        )

    sanitized_tools = _sanitize_tools(body.tools or [])
    # Retrieval: declare only the tools relevant to this request (plus any already in use).
    selected_tools = await tool_retrieval.select_for_request(sanitized_tools, body.history, body.message)
    declarations = _build_tool_declarations(selected_tools) if selected_tools else []

    started = time.perf_counter()
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)

        # Build history + new message
        history_contents = _clean_history_for_genai(body.history)
        new_parts = _message_to_parts(body.message)
        history_contents.append(genai_types.Content(role="user", parts=new_parts))

        config = genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[genai_types.Tool(function_declarations=declarations)] if declarations else None,
        )

        # Run synchronous SDK call in thread pool to avoid blocking
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=history_contents,
            config=config,
        )

        # Extract text
        text = ""
        try:
            text = response.text or ""
        except Exception:
            pass

        # Extract function calls
        function_calls = []
        parts_out = []

        try:
            candidate = response.candidates[0]
            for p in candidate.content.parts:
                if p.function_call and p.function_call.name:
                    fc_dict = {
                        "name": p.function_call.name,
                        "args": dict(p.function_call.args),
                    }
                    function_calls.append(fc_dict)
                    parts_out.append({"functionCall": fc_dict})
                elif p.text:
                    parts_out.append({"text": p.text})
        except Exception:
            pass

        usage = _usage_of(response)
        telemetry.record_gemini_turn(
            latency_ms=(time.perf_counter() - started) * 1000, usage=usage,
            tools_declared=len(selected_tools), tools_available=len(sanitized_tools),
            function_calls=len(function_calls),
        )
        logger.info(
            "[Gemini] tools declared=%d/%d prompt_tokens=%s total_tokens=%s",
            len(selected_tools), len(sanitized_tools), usage.get("promptTokens"), usage.get("totalTokens"),
        )
        return {
            "success": True,
            "text": text,
            "functionCalls": function_calls,
            "parts": parts_out,
            "toolsDeclared": len(selected_tools),
            "toolsAvailable": len(sanitized_tools),
            "usage": usage,
        }

    except Exception as e:
        telemetry.record_gemini_turn(
            latency_ms=(time.perf_counter() - started) * 1000, usage={},
            tools_declared=len(selected_tools), tools_available=len(sanitized_tools), function_calls=0, error=True,
        )
        logger.exception("Gemini API Error")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )
