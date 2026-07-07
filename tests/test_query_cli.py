"""Unit tests for the query CLI — build_pipeline and run_query mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.config import FALLBACK_MESSAGE
from src.models import QueryResponse, Source


def _make_fallback_response() -> QueryResponse:
    return QueryResponse(answer=FALLBACK_MESSAGE, sources=[], found_in_corpus=False)


def _make_found_response() -> QueryResponse:
    return QueryResponse(
        answer="L'huile améliore la texture [source: ACE-5:Run:1].",
        sources=[
            Source(
                run_id="ACE-5:Run:1",
                experiment_id="ACE-5",
                source_file="ACE-5_documentation.md",
                score=0.85,
            )
        ],
        found_in_corpus=True,
    )


def test_cli_prints_fallback(capsys):
    mock_pipeline = MagicMock()
    with (
        patch("sys.argv", ["query.py", "Pisane ES testé ?"]),
        patch("src.query.build_pipeline", return_value=mock_pipeline),
        patch("src.query.run_query", return_value=_make_fallback_response()),
    ):
        from src.query import main

        main()
    captured = capsys.readouterr()
    assert FALLBACK_MESSAGE in captured.out


def test_cli_prints_answer_and_sources(capsys):
    mock_pipeline = MagicMock()
    with (
        patch("sys.argv", ["query.py", "Effet de l'huile ?"]),
        patch("src.query.build_pipeline", return_value=mock_pipeline),
        patch("src.query.run_query", return_value=_make_found_response()),
    ):
        from src.query import main

        main()
    captured = capsys.readouterr()
    assert "ACE-5:Run:1" in captured.out


def test_cli_passes_chantier_option():
    mock_pipeline = MagicMock()
    with (
        patch("sys.argv", ["query.py", "Test ?", "--chantier", "Extrusion"]),
        patch("src.query.build_pipeline", return_value=mock_pipeline),
        patch("src.query.run_query", return_value=_make_fallback_response()) as mock_rq,
    ):
        from src.query import main

        main()
    _, kwargs = mock_rq.call_args
    assert kwargs.get("chantier") == "Extrusion"


def test_cli_passes_top_k_option():
    mock_pipeline = MagicMock()
    with (
        patch("sys.argv", ["query.py", "Test ?", "--top-k", "5"]),
        patch("src.query.build_pipeline", return_value=mock_pipeline),
        patch("src.query.run_query", return_value=_make_fallback_response()) as mock_rq,
    ):
        from src.query import main

        main()
    _, kwargs = mock_rq.call_args
    assert kwargs.get("top_k") == 5
