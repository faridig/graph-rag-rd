"""Unit tests for the RAG pipeline — all external calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.config import FALLBACK_MESSAGE, SCORE_THRESHOLD
from src.generation.rag_pipeline import RAGPipeline, extract_cited_ids, run_query

_FAKE_CHUNK = {
    "run_id": "ACE-5:Run:1",
    "experiment_id": "ACE-5",
    "text": "L'huile améliore la texture de l'extrudat.",
    "score": 0.9,
    "objective": None,
    "synthesis": None,
    "date": None,
    "ingredients": [],
    "chantier": None,
}


def _make_pipeline(dense_score: float = 0.0, llm_answer: str = "") -> RAGPipeline:
    """Build a RAGPipeline with all network calls mocked."""
    mock_driver = MagicMock()
    mock_openai = MagicMock()
    mock_anthropic = MagicMock()

    # OpenAI embeddings (embed_text)
    embed_resp = MagicMock()
    embed_resp.data = [MagicMock(embedding=[0.0] * 1536)]
    mock_openai.embeddings.create.return_value = embed_resp

    # Dense gate: driver.session().__enter__().run().single()["score"]
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session
    mock_record = MagicMock()
    mock_record.__getitem__ = MagicMock(return_value=dense_score)
    mock_session.run.return_value.single.return_value = mock_record

    # Anthropic LLM
    msg = MagicMock()
    msg.content = [MagicMock(text=llm_answer)]
    mock_anthropic.messages.create.return_value = msg

    with patch("src.retrieval.hybrid_retriever.HybridCypherRetriever"):
        pipeline = RAGPipeline(mock_driver, mock_openai, mock_anthropic)
    return pipeline


# ── extract_cited_ids ─────────────────────────────────────────────────────────


def test_extract_cited_ids_parses_markers():
    text = "Résultat [source: ACE-3:Run:1] et [source: ACE-5:Run:2]."
    assert extract_cited_ids(text) == {"ACE-3:Run:1", "ACE-5:Run:2"}


def test_extract_cited_ids_case_insensitive():
    assert "ACE-3:Run:1" in extract_cited_ids("[SOURCE: ACE-3:Run:1]")


def test_extract_cited_ids_trims_whitespace():
    assert "ACE-3:Run:1" in extract_cited_ids("[source:  ACE-3:Run:1  ]")


def test_extract_cited_ids_empty_text():
    assert extract_cited_ids("Aucune source.") == set()


# ── fallback gate (critical anti-hallucination tests) ─────────────────────────


def test_fallback_when_below_threshold_and_no_exact_match():
    """dense_score < SCORE_THRESHOLD + exact_lookup empty → FALLBACK_MESSAGE exactly."""
    pipeline = _make_pipeline(dense_score=SCORE_THRESHOLD - 0.1)
    with patch("src.generation.rag_pipeline.exact_lookup", return_value=[]):
        response = run_query(pipeline, "Pisane ES a-t-il été testé ?")
    assert not response.found_in_corpus
    assert response.answer == FALLBACK_MESSAGE
    assert response.sources == []


def test_fallback_answer_is_exact_constant():
    """Fallback must be the constant, never a paraphrase."""
    pipeline = _make_pipeline(dense_score=0.0)
    with patch("src.generation.rag_pipeline.exact_lookup", return_value=[]):
        response = run_query(pipeline, "Ingrédient inconnu XYZ123")
    assert response.answer == FALLBACK_MESSAGE


def test_no_llm_call_on_fallback():
    """LLM must not be called when corpus returns nothing."""
    pipeline = _make_pipeline(dense_score=0.0)
    with patch("src.generation.rag_pipeline.exact_lookup", return_value=[]):
        run_query(pipeline, "Ingrédient absent")
    pipeline._anthropic.messages.create.assert_not_called()


# ── found-in-corpus path ──────────────────────────────────────────────────────


def test_found_in_corpus_returns_true():
    pipeline = _make_pipeline(
        dense_score=SCORE_THRESHOLD + 0.1,
        llm_answer="L'huile améliore la texture [source: ACE-5:Run:1].",
    )
    pipeline._retriever.search = MagicMock(return_value=[_FAKE_CHUNK])
    response = run_query(pipeline, "Effet de l'huile ?")
    assert response.found_in_corpus


def test_answer_preserves_valid_citation():
    pipeline = _make_pipeline(
        dense_score=SCORE_THRESHOLD + 0.1,
        llm_answer="L'huile améliore la texture [source: ACE-5:Run:1].",
    )
    pipeline._retriever.search = MagicMock(return_value=[_FAKE_CHUNK])
    response = run_query(pipeline, "Effet de l'huile ?")
    assert "[source: ACE-5:Run:1]" in response.answer


def test_sources_list_populated_from_chunks():
    pipeline = _make_pipeline(
        dense_score=SCORE_THRESHOLD + 0.1,
        llm_answer="Résultat [source: ACE-5:Run:1].",
    )
    pipeline._retriever.search = MagicMock(return_value=[_FAKE_CHUNK])
    response = run_query(pipeline, "Test ?")
    assert any(s.run_id == "ACE-5:Run:1" for s in response.sources)


# ── citation verification (hallucination guard) ───────────────────────────────


def test_hallucinated_citation_is_stripped():
    """A citation not backed by a retrieved chunk must be removed."""
    pipeline = _make_pipeline(
        dense_score=SCORE_THRESHOLD + 0.1,
        llm_answer="Résultat [source: ACE-5:Run:1] et faux [source: HALLUCINE:Run:99].",
    )
    pipeline._retriever.search = MagicMock(return_value=[_FAKE_CHUNK])
    response = run_query(pipeline, "Test ?")
    assert "[source: HALLUCINE:Run:99]" not in response.answer
    assert "[source: ACE-5:Run:1]" in response.answer


# ── exact_lookup fallback path ────────────────────────────────────────────────


def test_exact_lookup_found_returns_answer():
    """dense_score < threshold but exact_lookup finds rows → builds response from them."""
    exact_rows = [
        {
            "run_id": "ACE-3:Run:1",
            "experiment_id": "ACE-3",
            "objective": "Tester Nutralys",
            "synthesis": "Bons résultats",
            "ingredient_match": "Nutralys S85F",
        }
    ]
    pipeline = _make_pipeline(
        dense_score=SCORE_THRESHOLD - 0.1,
        llm_answer="Nutralys améliore la texture [source: ACE-3:Run:1].",
    )
    with patch("src.generation.rag_pipeline.exact_lookup", return_value=exact_rows):
        response = run_query(pipeline, "Nutralys S85F résultats ?")
    assert response.found_in_corpus
    assert any(s.run_id == "ACE-3:Run:1" for s in response.sources)


def test_exact_lookup_found_score_is_zero():
    """Sources from exact fallback always have score=0.0."""
    exact_rows = [
        {
            "run_id": "ACE-3:Run:2",
            "experiment_id": "ACE-3",
            "objective": "Test",
            "synthesis": None,
            "ingredient_match": "Nutralys",
        }
    ]
    pipeline = _make_pipeline(
        dense_score=SCORE_THRESHOLD - 0.1,
        llm_answer="Résultat [source: ACE-3:Run:2].",
    )
    with patch("src.generation.rag_pipeline.exact_lookup", return_value=exact_rows):
        response = run_query(pipeline, "Nutralys ?")
    assert all(s.score == 0.0 for s in response.sources)


# ── citation regen ────────────────────────────────────────────────────────────


def test_citation_regen_called_when_no_markers():
    """LLM answer with zero citation markers triggers a second generate call."""
    pipeline = _make_pipeline(
        dense_score=SCORE_THRESHOLD + 0.1,
        llm_answer="Aucune citation dans cette réponse.",
    )
    pipeline._retriever.search = MagicMock(return_value=[_FAKE_CHUNK])
    run_query(pipeline, "Test ?")
    assert pipeline._anthropic.messages.create.call_count == 2


# ── get_dense_score public function ──────────────────────────────────────────


def test_get_dense_score_returns_float():
    from src.generation.rag_pipeline import get_dense_score

    pipeline = _make_pipeline(dense_score=0.75)
    score = get_dense_score(pipeline, "test query")
    assert isinstance(score, float)
    assert score == 0.75
