"""
/gemini/chat with a fake SDK client: retrieval narrows the declared tools,
the response carries token usage and declaration counts, and function calls
are surfaced.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from config import settings
from routers import gemini as gemini_module
from services import telemetry as tm
from services import tool_retrieval as tr
from tests.test_tool_retrieval import TOOLS, BagOfWordsEmbedder, FakeEmbeddingsCollection


class FakeModels:
    def __init__(self, recorder):
        self._rec = recorder

    def generate_content(self, *, model, contents, config):
        declared = config.tools[0].function_declarations if config.tools else []
        self._rec["declared"] = [d.name for d in declared]
        self._rec["model"] = model
        part = SimpleNamespace(function_call=SimpleNamespace(name="get_weather", args={"city": "Delhi"}), text=None)
        return SimpleNamespace(
            text="Let me check the weather.",
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
            usage_metadata=SimpleNamespace(prompt_token_count=321, candidates_token_count=12, total_token_count=333),
        )


@pytest.fixture
def env(monkeypatch):
    rec = {}
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "TOOL_RETRIEVAL_TOP_K", 2)
    monkeypatch.setattr(database, "tool_embeddings_collection", FakeEmbeddingsCollection())
    monkeypatch.setattr(tr, "_index", tr.ToolIndex(BagOfWordsEmbedder()))
    monkeypatch.setattr(gemini_module.genai, "Client", lambda api_key: SimpleNamespace(models=FakeModels(rec)))
    app = FastAPI()
    app.include_router(gemini_module.router)
    return TestClient(app), rec


def test_chat_declares_only_relevant_tools_and_reports_usage(env):
    client, rec = env
    r = client.post("/gemini/chat", json={"message": "What's the weather forecast in Delhi?", "history": [], "tools": TOOLS})

    assert r.status_code == 200
    body = r.json()
    assert body["toolsAvailable"] == len(TOOLS) and body["toolsDeclared"] == 2
    assert rec["declared"][0] == "get_weather" and len(rec["declared"]) == 2
    assert body["functionCalls"] == [{"name": "get_weather", "args": {"city": "Delhi"}}]
    assert body["usage"] == {"promptTokens": 321, "candidatesTokens": 12, "totalTokens": 333}

    g = tm.telemetry.snapshot()["gemini"]
    assert g["turns"] >= 1 and g["prompt_tokens"] >= 321 and g["avg_tools_declared"] is not None


def test_tool_result_turn_keeps_the_tool_in_use(env):
    client, rec = env
    history = [
        {"role": "user", "parts": [{"text": "find me data science jobs"}]},
        {"role": "model", "parts": [{"functionCall": {"name": "job_search", "args": {"query": "data science"}}}]},
    ]
    message = {"parts": [{"functionResponse": {"name": "job_search", "response": {"jobs": ["x"]}}}]}
    r = client.post("/gemini/chat", json={"message": message, "history": history, "tools": TOOLS})
    assert r.status_code == 200
    assert "job_search" in rec["declared"]                       # never cut off mid-plan


def test_retrieval_disabled_declares_all(env, monkeypatch):
    client, rec = env
    monkeypatch.setattr(settings, "TOOL_RETRIEVAL_TOP_K", 0)
    r = client.post("/gemini/chat", json={"message": "weather", "history": [], "tools": TOOLS})
    assert r.json()["toolsDeclared"] == len(TOOLS)
    assert len(rec["declared"]) == len(TOOLS)


def test_missing_api_key_is_500(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    app = FastAPI()
    app.include_router(gemini_module.router)
    r = TestClient(app).post("/gemini/chat", json={"message": "hi", "tools": []})
    assert r.status_code == 500
