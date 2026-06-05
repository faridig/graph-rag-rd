"""Unit tests for the FastAPI endpoints — Neo4j and LLM mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.config import FALLBACK_MESSAGE
from src.models import QueryResponse, Source


def _mock_record(value: int = 10) -> MagicMock:
    record = MagicMock()
    record.__getitem__ = MagicMock(return_value=value)
    return record


@pytest.fixture()
def client():
    mock_driver = MagicMock()
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session
    mock_session.run.return_value.single.return_value = _mock_record(10)
    mock_session.run.return_value.__iter__ = MagicMock(return_value=iter([]))

    with (
        patch("src.api.GraphDatabase.driver", return_value=mock_driver),
        patch("src.api.OpenAI"),
        patch("src.api.anthropic.Anthropic"),
        patch("src.retrieval.hybrid_retriever.HybridCypherRetriever"),
    ):
        from src.api import app

        with TestClient(app) as c:
            yield c


# ── GET /health ───────────────────────────────────────────────────────────────


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_includes_neo4j_status(client):
    response = client.get("/health")
    assert "neo4j" in response.json()


# ── GET /corpus ───────────────────────────────────────────────────────────────


def test_corpus_returns_sources_list(client):
    response = client.get("/corpus")
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    assert isinstance(data["sources"], list)


def test_corpus_sources_have_run_count(client):
    response = client.get("/corpus")
    for source in response.json()["sources"]:
        assert "run_count" in source
        assert isinstance(source["run_count"], int)


# ── POST /query ───────────────────────────────────────────────────────────────


def test_query_endpoint_returns_fallback(client):
    fallback = QueryResponse(
        answer=FALLBACK_MESSAGE, sources=[], found_in_corpus=False
    )
    with patch("src.api.run_query", return_value=fallback):
        response = client.post("/query", json={"question": "Pisane ES testé ?"})
    assert response.status_code == 200
    body = response.json()
    assert body["found_in_corpus"] is False
    assert body["answer"] == FALLBACK_MESSAGE
    assert body["sources"] == []


def test_query_endpoint_returns_answer_with_sources(client):
    source = Source(run_id="ACE-5:Run:1", experiment_id="ACE-5", source_file="ACE-5_documentation.md", score=0.9)
    found = QueryResponse(
        answer="L'huile améliore la texture [source: ACE-5:Run:1].",
        sources=[source],
        found_in_corpus=True,
    )
    with patch("src.api.run_query", return_value=found):
        response = client.post("/query", json={"question": "Effet de l'huile ?"})
    assert response.status_code == 200
    body = response.json()
    assert body["found_in_corpus"] is True
    assert len(body["sources"]) == 1
    assert body["sources"][0]["run_id"] == "ACE-5:Run:1"


def test_query_endpoint_passes_chantier_filter(client):
    fallback = QueryResponse(answer=FALLBACK_MESSAGE, sources=[], found_in_corpus=False)
    with patch("src.api.run_query", return_value=fallback) as mock_rq:
        client.post("/query", json={"question": "Test ?", "chantier": "Extrusion"})
    _, kwargs = mock_rq.call_args
    assert kwargs.get("chantier") == "Extrusion"
