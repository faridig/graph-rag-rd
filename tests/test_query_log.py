"""Tests unitaires du monitoring RAG (src/query_log.py) — I/O sur fichier temporaire."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src import query_log


@pytest.fixture()
def logfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirige le log JSONL vers un fichier temporaire et active l'écriture."""
    target = tmp_path / "query_log.jsonl"
    monkeypatch.setattr(query_log, "_LOG_FILE", target)
    monkeypatch.setattr(query_log, "QUERY_LOG_ENABLED", True)
    return target


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_log_query_writes_line_and_returns_id(logfile: Path) -> None:
    qid = query_log.log_query(
        question="Effet de l'huile sur M03 ?",
        found_in_corpus=True,
        dense_score=0.81,
        n_chunks=4,
        n_sources=3,
        n_cited=2,
        input_tokens=100,
        output_tokens=50,
        latency_ms=1200,
    )
    rows = _lines(logfile)
    assert len(rows) == 1
    rec = rows[0]
    assert rec["type"] == "query"
    assert rec["query_id"] == qid
    assert rec["found_in_corpus"] is True
    assert rec["dense_score"] == 0.81
    assert rec["n_cited"] == 2
    assert rec["fallback_reason"] is None


def test_record_feedback_appends(logfile: Path) -> None:
    qid = query_log.log_query(question="q", found_in_corpus=True)
    query_log.record_feedback(qid, 1, comment="parfait")
    rows = _lines(logfile)
    assert len(rows) == 2
    fb = rows[1]
    assert fb["type"] == "feedback"
    assert fb["query_id"] == qid
    assert fb["value"] == 1
    assert fb["comment"] == "parfait"


def test_disabled_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "query_log.jsonl"
    monkeypatch.setattr(query_log, "_LOG_FILE", target)
    monkeypatch.setattr(query_log, "QUERY_LOG_ENABLED", False)
    query_log.log_query(question="q", found_in_corpus=True)
    assert not target.exists()


def test_compute_metrics_aggregates(logfile: Path) -> None:
    # 2 réponses trouvées, 2 fallbacks (raisons distinctes)
    q1 = query_log.log_query(
        question="q1", found_in_corpus=True, dense_score=0.80, n_sources=2, n_cited=2
    )
    query_log.log_query(
        question="q2", found_in_corpus=True, dense_score=0.70, n_sources=2, n_cited=1
    )
    query_log.log_query(
        question="q3", found_in_corpus=False, fallback_reason="absent_experiment"
    )
    query_log.log_query(
        question="q4", found_in_corpus=False, fallback_reason="dense_gate_no_exact"
    )
    query_log.record_feedback(q1, 1)

    m = query_log.compute_metrics(days=7)
    assert m["n_queries"] == 4
    assert m["n_present"] == 2
    assert m["n_fallback"] == 2
    assert m["fallback_rate"] == pytest.approx(0.5)
    assert m["fallback_reasons"] == {
        "absent_experiment": 1,
        "dense_gate_no_exact": 1,
    }
    # dense_score moyen sur les présentes seulement : (0.80 + 0.70) / 2
    assert m["dense_score_avg"] == pytest.approx(0.75)
    # couverture citations : (2/2 + 1/2) / 2 = 0.75
    assert m["citation_coverage_avg"] == pytest.approx(0.75)
    assert m["feedback_up"] == 1
    assert m["feedback_down"] == 0
    assert m["satisfaction_rate"] == pytest.approx(1.0)


def test_last_feedback_wins(logfile: Path) -> None:
    qid = query_log.log_query(question="q", found_in_corpus=True)
    query_log.record_feedback(qid, 1)
    query_log.record_feedback(qid, 0)  # l'utilisateur change d'avis
    m = query_log.compute_metrics(days=7)
    assert m["feedback_up"] == 0
    assert m["feedback_down"] == 1


def test_windowing_excludes_old_records(logfile: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Enregistrement daté d'il y a 30 jours (écrit directement)
    old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with open(logfile, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "query", "ts": old_ts, "found_in_corpus": True}) + "\n")
    query_log.log_query(question="récent", found_in_corpus=False, fallback_reason="absent_topic")

    m7 = query_log.compute_metrics(days=7)
    assert m7["n_queries"] == 1  # l'ancien est exclu
    m_all = query_log.compute_metrics(days=None)
    assert m_all["n_queries"] == 2  # sans fenêtre, les deux comptent


def test_format_summary_smoke(logfile: Path) -> None:
    query_log.log_query(question="q", found_in_corpus=True, dense_score=0.8)
    out = query_log.format_summary(days=7)
    assert "Monitoring RAG" in out
    assert "Taux de fallback" in out
