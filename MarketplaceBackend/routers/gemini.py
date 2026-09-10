"""
POST /gemini/chat — one planning turn for the browser-side agent loop.

The model call itself lives in services.gemini_agent so the server-side agent
graph (agent/) uses exactly the same prompt, sanitisation and retrieval.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import settings
from models.tool import GeminiChatRequest
from services import gemini_agent
from services.gemini_agent import (  # re-exported for existing importers
    SYSTEM_PROMPT,
    TOOL_NAME_RE,
    build_tool_declarations,
    sanitize_parameters,
    sanitize_tools,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Backwards-compatible aliases (tests and mcp_server import these names).
_sanitize_parameters = sanitize_parameters
_sanitize_tools = sanitize_tools
_build_tool_declarations = build_tool_declarations
__all__ = ["router", "SYSTEM_PROMPT", "TOOL_NAME_RE", "_sanitize_parameters", "_sanitize_tools", "_build_tool_declarations"]


@router.post("/gemini/chat")
async def gemini_chat(body: GeminiChatRequest):
    if not settings.GEMINI_API_KEY:
        return JSONResponse(status_code=500, content={"success": False, "error": "GEMINI_API_KEY not configured"})

    history = list(body.history or []) + [{"role": "user", "parts": gemini_agent.message_to_parts(body.message)}]
    try:
        turn = await gemini_agent.generate(history, body.tools or [])
    except Exception as e:
        logger.exception("Gemini API Error")
        return JSONResponse(status_code=500, content={"success": False, "error": f"Gemini API error: {e}"})

    logger.info(
        "[Gemini] tools declared=%d/%d prompt_tokens=%s total_tokens=%s",
        turn["tools_declared"], turn["tools_available"], turn["usage"].get("promptTokens"), turn["usage"].get("totalTokens"),
    )
    return {
        "success": True,
        "text": turn["text"],
        "functionCalls": turn["function_calls"],
        "parts": turn["parts"],
        "toolsDeclared": turn["tools_declared"],
        "toolsAvailable": turn["tools_available"],
        "usage": turn["usage"],
    }
