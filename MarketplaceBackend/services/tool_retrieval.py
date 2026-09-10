"""
Tool retrieval: declare only the tools that matter for this request.

Why
───
Every function declaration sent to Gemini costs prompt tokens on every turn,
and providers cap how many tools a request may declare. Declaring the whole
catalog is fine at ten tools and fails at a few hundred. The standard fix is
the same one used for documents (RAG): embed each tool's description once,
embed the incoming request, and declare the top-k most similar tools.

How
───
1. `tool_text()` turns a tool into the text we embed: name, description and
   parameter names/descriptions. A SHA-256 of that text is the cache key, so a
   tool is re-embedded only when its description actually changes.
2. `ToolIndex` keeps vectors in memory and mirrors them to the
   `tool_embeddings` collection, so a restart does not re-embed the catalog.
3. `select()` ranks by cosine similarity and returns the top-k, plus any tool
   that already appears as a functionCall in the conversation history, so a
   chained call (tool B needs tool A's output) is never cut off mid-plan.
4. Anything going wrong (no API key, embedding error, empty query) fails OPEN:
   we declare all tools, exactly as before retrieval existed. Retrieval is an
   optimisation; it must never make the agent less capable than it was.

Embeddings use asymmetric task types: documents (tool descriptions) are
embedded as RETRIEVAL_DOCUMENT and the request as RETRIEVAL_QUERY, which is
how retrieval-tuned embedding models are meant to be used.
"""

import asyncio
import hashlib
import logging
import math
from typing import Any, Iterable

import database
from config import settings

logger = logging.getLogger(__name__)


# ─── Text + hashing ───────────────────────────────────────────────────────────

def tool_text(tool: dict[str, Any]) -> str:
    props = ((tool.get("parameters") or {}).get("properties") or {})
    param_bits = [f"{k}: {v.get('description', '')}".strip(": ") for k, v in props.items() if isinstance(v, dict)]
    parts = [tool.get("name", ""), tool.get("description", "")]
    if param_bits:
        parts.append("parameters: " + "; ".join(param_bits))
    return "\n".join(p for p in parts if p)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ─── Embedders ────────────────────────────────────────────────────────────────

class GeminiEmbedder:
    """Embeds text with the Gemini embedding model. Documents and queries use different task types."""

    def __init__(self, api_key: str, model: str, dimensions: int):
        self._api_key, self.model, self.dimensions = api_key, model, dimensions

    async def embed(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=self._api_key)
        result = await asyncio.to_thread(
            client.models.embed_content,
            model=self.model,
            contents=texts,
            config=genai_types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dimensions),
        )
        return [list(e.values) for e in result.embeddings]


def default_embedder():
    if not settings.GEMINI_API_KEY:
        return None
    return GeminiEmbedder(settings.GEMINI_API_KEY, settings.TOOL_EMBED_MODEL, settings.TOOL_EMBED_DIMENSIONS)


# ─── Index ────────────────────────────────────────────────────────────────────

class ToolIndex:
    """Embedding cache for tool descriptions, in memory and mirrored to MongoDB."""

    def __init__(self, embedder=None):
        self._embedder = embedder
        self._cache: dict[str, tuple[str, list[float]]] = {}   # name -> (text hash, vector)
        self.document_embed_calls = 0                           # for tests / metrics

    def _collection(self):
        return database.tool_embeddings_collection

    async def _load_persisted(self, names: Iterable[str]) -> None:
        coll = self._collection()
        if coll is None:
            return
        for name in names:
            try:
                doc = await coll.find_one({"_id": name})
            except Exception as e:  # persistence is an optimisation; keep going
                logger.warning(f"[ToolRetrieval] Could not read cached embedding for {name}: {e}")
                return
            if doc and doc.get("model") == getattr(self._embedder, "model", None):
                self._cache[name] = (doc["text_hash"], list(doc["embedding"]))

    async def _persist(self, name: str, h: str, vec: list[float]) -> None:
        coll = self._collection()
        if coll is None:
            return
        try:
            await coll.update_one(
                {"_id": name},
                {"$set": {"text_hash": h, "embedding": vec, "model": getattr(self._embedder, "model", None)}},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"[ToolRetrieval] Could not persist embedding for {name}: {e}")

    async def vectors_for(self, tools: list[dict[str, Any]]) -> dict[str, list[float]]:
        """Return a vector per tool name, embedding only tools whose text is new or changed."""
        wanted = {t["name"]: tool_text(t) for t in tools}
        hashes = {name: text_hash(text) for name, text in wanted.items()}

        missing = [n for n, h in hashes.items() if self._cache.get(n, ("", None))[0] != h]
        if missing:
            await self._load_persisted(missing)
            missing = [n for n in missing if self._cache.get(n, ("", None))[0] != hashes[n]]

        if missing:
            self.document_embed_calls += 1
            vectors = await self._embedder.embed([wanted[n] for n in missing], task_type="RETRIEVAL_DOCUMENT")
            for name, vec in zip(missing, vectors, strict=True):
                self._cache[name] = (hashes[name], vec)
                await self._persist(name, hashes[name], vec)

        return {name: self._cache[name][1] for name in wanted}

    async def select(
        self,
        query: str,
        tools: list[dict[str, Any]],
        k: int,
        must_include: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Top-k tools by similarity to `query`, plus `must_include`. Fails open to all tools."""
        must_include = must_include or set()
        if k <= 0 or len(tools) <= k or self._embedder is None or not query.strip():
            return tools
        try:
            vectors = await self.vectors_for(tools)
            [qvec] = await self._embedder.embed([query], task_type="RETRIEVAL_QUERY")
        except Exception as e:
            logger.warning(f"[ToolRetrieval] Embedding failed; declaring all {len(tools)} tools: {e}")
            return tools

        ranked = sorted(tools, key=lambda t: cosine(vectors[t["name"]], qvec), reverse=True)
        chosen = ranked[:k]
        chosen_names = {t["name"] for t in chosen}
        for t in tools:
            if t["name"] in must_include and t["name"] not in chosen_names:
                chosen.append(t)
                chosen_names.add(t["name"])
        return chosen


# ─── Request helpers ──────────────────────────────────────────────────────────

def _text_of(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        bits = []
        for p in message.get("parts", []):
            if isinstance(p, str):
                bits.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                bits.append(p["text"])
        return " ".join(bits)
    return ""


def extract_query_text(history: list | None, message: Any) -> str:
    """
    The text to retrieve against: the current message if it has text, else
    the most recent user text in history (the current message may be a bare
    functionResponse when the agent is feeding a tool result back).
    """
    text = _text_of(message).strip()
    if text:
        return text
    for turn in reversed(history or []):
        if isinstance(turn, dict) and turn.get("role") == "user":
            text = _text_of(turn).strip()
            if text:
                return text
    return ""


def tools_used_in(history: list | None) -> set[str]:
    used: set[str] = set()
    for turn in history or []:
        for p in (turn.get("parts", []) if isinstance(turn, dict) else []):
            if isinstance(p, dict):
                fc = p.get("functionCall") or {}
                if fc.get("name"):
                    used.add(fc["name"])
    return used


# Process-wide index. Created lazily so tests can install their own embedder.
_index: ToolIndex | None = None


def get_index() -> ToolIndex:
    global _index
    if _index is None:
        _index = ToolIndex(default_embedder())
    return _index


async def select_for_request(tools: list[dict[str, Any]], history: list | None, message: Any) -> list[dict[str, Any]]:
    """Entry point used by the chat route."""
    return await get_index().select(
        extract_query_text(history, message),
        tools,
        settings.TOOL_RETRIEVAL_TOP_K,
        must_include=tools_used_in(history),
    )
