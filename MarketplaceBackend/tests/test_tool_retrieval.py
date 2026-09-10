"""
Tool retrieval: top-k by similarity, always-include used tools, caching and
persistence, fail-open behaviour, and the query-text extraction rules.

The embedder is a bag-of-words model over a tiny vocabulary: deterministic,
network-free, and similar enough to a real embedding that "weather in Delhi"
lands nearest the weather tool.
"""

import re

import pytest

import database
from config import settings
from services import tool_retrieval as tr

VOCAB = ["weather", "forecast", "rain", "stock", "price", "market", "translate", "language",
         "image", "picture", "generate", "math", "calculate", "sum", "job", "salary", "hire"]


class BagOfWordsEmbedder:
    model = "bow-test"

    def __init__(self):
        self.calls: list[tuple[str, int]] = []   # (task_type, n_texts)
        self.fail = False

    async def embed(self, texts, *, task_type):
        if self.fail:
            raise RuntimeError("embedding service down")
        self.calls.append((task_type, len(texts)))
        out = []
        for text in texts:
            words = re.findall(r"[a-z]+", text.lower())
            out.append([float(sum(1 for w in words if w == v or w.startswith(v))) for v in VOCAB])
        return out


def tool(name, description, **props):
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in props.items()}},
    }


TOOLS = [
    tool("get_weather", "Current weather and forecast for a city", city="City name"),
    tool("stock_price", "Latest stock market price for a ticker", ticker="Ticker symbol"),
    tool("translate_text", "Translate text between languages", text="Text", language="Target language"),
    tool("generate_image", "Generate a picture from a prompt", prompt="Image description"),
    tool("calculator", "Calculate a math expression", expression="Math expression"),
    tool("job_search", "Find job listings and salary data", query="Job title"),
]


class FakeEmbeddingsCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.reads = 0

    async def find_one(self, q):
        self.reads += 1
        return dict(self.docs[q["_id"]]) if q["_id"] in self.docs else None

    async def update_one(self, q, update, upsert=False):
        doc = self.docs.setdefault(q["_id"], {"_id": q["_id"]})
        doc.update(update["$set"])


@pytest.fixture
def store(monkeypatch):
    coll = FakeEmbeddingsCollection()
    monkeypatch.setattr(database, "tool_embeddings_collection", coll)
    return coll


@pytest.fixture
def index(store):
    return tr.ToolIndex(BagOfWordsEmbedder())


# ─── Ranking ──────────────────────────────────────────────────────────────────

async def test_top_k_picks_the_relevant_tools(index):
    chosen = await index.select("What's the weather forecast for Delhi?", TOOLS, k=2)
    names = [t["name"] for t in chosen]
    assert names[0] == "get_weather"
    assert len(names) == 2 and "stock_price" not in names[:1]


async def test_ranking_is_query_dependent(index):
    assert (await index.select("calculate the sum of 3 and 4", TOOLS, k=1))[0]["name"] == "calculator"
    assert (await index.select("translate this to a different language", TOOLS, k=1))[0]["name"] == "translate_text"


async def test_tools_already_used_are_always_included(index):
    chosen = await index.select("now calculate the average salary", TOOLS, k=1, must_include={"job_search"})
    names = {t["name"] for t in chosen}
    assert "job_search" in names                      # chained call keeps its tool
    assert "calculator" in names                      # plus the top-1 by similarity
    assert len(names) == 2


async def test_must_include_does_not_duplicate(index):
    chosen = await index.select("weather please", TOOLS, k=1, must_include={"get_weather"})
    assert [t["name"] for t in chosen] == ["get_weather"]


# ─── Fail open ────────────────────────────────────────────────────────────────

async def test_disabled_or_small_catalog_declares_everything(index):
    assert await index.select("weather", TOOLS, k=0) == TOOLS
    assert await index.select("weather", TOOLS, k=len(TOOLS)) == TOOLS
    assert await index.select("weather", TOOLS[:2], k=5) == TOOLS[:2]
    assert index._embedder.calls == []                # nothing was embedded


async def test_embedding_failure_declares_everything(index):
    index._embedder.fail = True
    assert await index.select("weather", TOOLS, k=2) == TOOLS


async def test_no_embedder_declares_everything(store):
    assert await tr.ToolIndex(None).select("weather", TOOLS, k=2) == TOOLS


async def test_blank_query_declares_everything(index):
    assert await index.select("   ", TOOLS, k=2) == TOOLS


# ─── Caching and persistence ──────────────────────────────────────────────────

async def test_documents_are_embedded_once_and_queries_every_time(index):
    await index.select("weather", TOOLS, k=2)
    await index.select("stock price", TOOLS, k=2)
    doc_calls = [c for c in index._embedder.calls if c[0] == "RETRIEVAL_DOCUMENT"]
    query_calls = [c for c in index._embedder.calls if c[0] == "RETRIEVAL_QUERY"]
    assert doc_calls == [("RETRIEVAL_DOCUMENT", len(TOOLS))]  # one batch for all tools, once
    assert len(query_calls) == 2


async def test_changed_description_re_embeds_only_that_tool(index):
    await index.select("weather", TOOLS, k=2)
    changed = [dict(t) for t in TOOLS]
    changed[1] = tool("stock_price", "Latest stock market price AND market news for a ticker", ticker="Ticker")
    await index.select("weather", changed, k=2)
    doc_calls = [c for c in index._embedder.calls if c[0] == "RETRIEVAL_DOCUMENT"]
    assert doc_calls == [("RETRIEVAL_DOCUMENT", len(TOOLS)), ("RETRIEVAL_DOCUMENT", 1)]


async def test_embeddings_are_persisted_and_reloaded_without_re_embedding(store):
    first = tr.ToolIndex(BagOfWordsEmbedder())
    await first.select("weather", TOOLS, k=2)
    assert set(store.docs) == {t["name"] for t in TOOLS}
    assert store.docs["get_weather"]["model"] == "bow-test"

    second = tr.ToolIndex(BagOfWordsEmbedder())          # "after a restart"
    chosen = await second.select("weather", TOOLS, k=1)
    assert chosen[0]["name"] == "get_weather"
    assert [c for c in second._embedder.calls if c[0] == "RETRIEVAL_DOCUMENT"] == []   # loaded from Mongo


async def test_persisted_vectors_from_another_model_are_ignored(store):
    store.docs["get_weather"] = {"_id": "get_weather", "text_hash": tr.text_hash(tr.tool_text(TOOLS[0])),
                                 "embedding": [9.0] * len(VOCAB), "model": "some-other-model"}
    index = tr.ToolIndex(BagOfWordsEmbedder())
    await index.select("weather", TOOLS, k=2)
    assert store.docs["get_weather"]["model"] == "bow-test"       # replaced, not trusted


async def test_index_works_without_a_collection(monkeypatch):
    monkeypatch.setattr(database, "tool_embeddings_collection", None)
    index = tr.ToolIndex(BagOfWordsEmbedder())
    assert (await index.select("weather", TOOLS, k=1))[0]["name"] == "get_weather"


# ─── Request helpers ──────────────────────────────────────────────────────────

def test_tool_text_includes_parameters():
    text = tr.tool_text(TOOLS[0])
    assert "get_weather" in text and "forecast" in text and "city: City name" in text


def test_query_text_prefers_current_message_then_last_user_text():
    assert tr.extract_query_text([], "hello") == "hello"
    assert tr.extract_query_text([], {"parts": [{"text": "a"}, "b"]}) == "a b"
    history = [
        {"role": "user", "parts": [{"text": "find me a job"}]},
        {"role": "model", "parts": [{"functionCall": {"name": "job_search", "args": {}}}]},
    ]
    tool_result = {"parts": [{"functionResponse": {"name": "job_search", "response": {"jobs": []}}}]}
    assert tr.extract_query_text(history, tool_result) == "find me a job"
    assert tr.extract_query_text([], tool_result) == ""


def test_tools_used_in_history():
    history = [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"functionCall": {"name": "job_search", "args": {}}}, {"text": "..."}]},
        {"role": "model", "parts": [{"functionCall": {"name": "calculator", "args": {}}}]},
    ]
    assert tr.tools_used_in(history) == {"job_search", "calculator"}
    assert tr.tools_used_in(None) == set()


def test_default_embedder_requires_api_key(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    assert tr.default_embedder() is None
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k")
    emb = tr.default_embedder()
    assert emb.model == settings.TOOL_EMBED_MODEL and emb.dimensions == settings.TOOL_EMBED_DIMENSIONS
