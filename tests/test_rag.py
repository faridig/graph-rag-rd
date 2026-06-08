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
    mock_llm = MagicMock()

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

    # DeepSeek LLM (OpenAI-compatible)
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = llm_answer
    completion.usage.prompt_tokens = 0
    completion.usage.completion_tokens = 0
    mock_llm.chat.completions.create.return_value = completion

    with patch("src.retrieval.hybrid_retriever.HybridCypherRetriever"):
        pipeline = RAGPipeline(mock_driver, mock_openai, mock_llm)
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
    pipeline._llm.chat.completions.create.assert_not_called()


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
    assert pipeline._llm.chat.completions.create.call_count == 2


# ── get_dense_score public function ──────────────────────────────────────────


def test_get_dense_score_returns_float():
    from src.generation.rag_pipeline import get_dense_score

    pipeline = _make_pipeline(dense_score=0.75)
    score = get_dense_score(pipeline, "test query")
    assert isinstance(score, float)
    assert score == 0.75


# ── _is_no_data_response ──────────────────────────────────────────────────────


def test_no_data_response_pure_refusal():
    """Refus pur sans citation → True."""
    from src.generation.rag_pipeline import _is_no_data_response

    assert _is_no_data_response("Je ne suis pas en mesure de répondre à cette question.")


def test_no_data_response_with_citation_false():
    """Réponse avec citation valide → False même si elle mentionne une limite."""
    from src.generation.rag_pipeline import _is_no_data_response

    assert not _is_no_data_response(
        "Le SME est 42,96 Wh/kg [source: ACE-3:Run:1]. "
        "Le run 2 ne figure pas dans le contexte."
    )


def test_no_data_response_partial_answer_false():
    """Réponse partielle avec données et source → False."""
    from src.generation.rag_pipeline import _is_no_data_response

    assert not _is_no_data_response(
        "Seul l'Essai 1 est documenté : SME = 42,96 Wh/kg [source: X:Run:1]."
    )


def test_no_data_response_fallback_message_no_trigger():
    """Le FALLBACK_MESSAGE lui-même ne doit pas déclencher le détecteur."""
    from src.config import FALLBACK_MESSAGE
    from src.generation.rag_pipeline import _is_no_data_response

    assert not _is_no_data_response(FALLBACK_MESSAGE)


def test_no_data_response_aucune_donnee_sur():
    """'aucune donnée sur X' sans citation → True (pattern ajouté 2026-06-08)."""
    from src.generation.rag_pipeline import _is_no_data_response

    assert _is_no_data_response(
        "Non, aucune donnée sur l'utilisation de protéines de lupin "
        "en extrusion haute humidité n'est présente dans le contexte fourni."
    )


# ── _apply_augmentation — RÉPERTOIRE coverage bug ────────────────────────────


def test_apply_augmentation_repertoire_not_counted_as_covered():
    """Chunks RÉPERTOIRE ne doivent pas marquer l'expérience cible comme couverte.

    Régression : RÉPERTOIRE-RD-2025-2026:Run:ACE-5 a 'ace-5' comme dernier
    composant du run_id. Sans le filtre non_rep_chunks, 'ACE-5' était considéré
    couvert et l'augmentation sautait l'expérience.
    """
    from unittest.mock import MagicMock, patch
    from src.generation.rag_pipeline import RAGPipeline

    # Résultats hybrides : ACE-4 direct + RÉPERTOIRE pointant vers ACE-5
    hybrid_chunks = [
        {"run_id": "ACE-4:Run:1", "experiment_id": "ACE-4", "text": "données ACE-4 run 1"},
        {"run_id": "ACE-4:Run:2", "experiment_id": "ACE-4", "text": "données ACE-4 run 2"},
        {
            "run_id": "REPERTOIRE-RD-2025-2026:Run:ACE-5",
            "experiment_id": "REPERTOIRE-RD-2025-2026",
            "text": "entrée répertoire pour ACE-5",
        },
    ]

    # Chunk ACE-5 retourné par l'augmentation Cypher
    ace5_chunk = {
        "run_id": "ACE-5:Run:11",
        "experiment_id": "ACE-5",
        "text": "données ACE-5 run 11 résultats SME",
    }

    driver_mock = MagicMock()
    openai_mock = MagicMock()
    llm_mock = MagicMock()

    # Augmentation Cypher → retourne ace5_chunk
    driver_mock.session.return_value.__enter__.return_value.run.return_value.data.return_value = [
        ace5_chunk
    ]

    # Patch HybridNeo4jRetriever + _load_experiment_ids + _load_ingredient_tokens
    # pour éviter la validation pydantic du driver réel
    with patch("src.generation.rag_pipeline.HybridNeo4jRetriever"), \
         patch("src.generation.rag_pipeline._load_experiment_ids", return_value=(
             frozenset({"ACE-4", "ACE-5"}), frozenset({"ACE"}), frozenset()
         )), \
         patch("src.generation.rag_pipeline._load_ingredient_tokens", return_value=frozenset()), \
         patch("src.generation.rag_pipeline._load_absent_topics", return_value=frozenset()):
        pipeline = RAGPipeline(driver_mock, openai_mock, llm_mock)

    question = "Comparer ACE-4 et ACE-5 sur la SME"
    result = pipeline._apply_augmentation(hybrid_chunks, question, top_k=6)

    run_ids = [c.get("run_id") for c in result]
    assert "ACE-5:Run:11" in run_ids, (
        f"ACE-5 chunk doit être dans le résultat après augmentation. Got: {run_ids}"
    )
    # Le chunk ACE-5 doit être en tête (ajouté avant les chunks hybrides)
    assert result[0]["run_id"] == "ACE-5:Run:11", (
        f"ACE-5 chunk doit être en position 0. Got: {result[0]['run_id']}"
    )
