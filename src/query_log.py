"""Monitoring de la pertinence du RAG en production.

Journalise une ligne JSONL par requête dans data/query_log.jsonl : signaux de
retrieval (dense_score), fallback (raison de la gate déclenchée), ancrage
(couverture citations), coût (tokens) et retour utilisateur (👍/👎).

100 % local, aucun envoi externe — respecte la règle « zéro fuite de données ».
Env-gated par QUERY_LOG_ENABLED. Analyse : `python -m src.query_log --days 7`.

Deux types de lignes partagent le même `query_id` :
- {"type": "query", ...}    écrit à la fin de chaque requête
- {"type": "feedback", ...} écrit quand l'utilisateur clique 👍/👎
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from src.config import QUERY_LOG_ENABLED, QUERY_LOG_PATH, SCORE_THRESHOLD

_log = logging.getLogger(__name__)

_LOG_FILE = Path(QUERY_LOG_PATH)
_lock = threading.Lock()

# Raisons de fallback (found_in_corpus=False) — alignées sur les gates du pipeline.
FALLBACK_REASONS: tuple[str, ...] = (
    "absent_topic",  # question matche data/absent_topics.txt
    "dense_gate_no_exact",  # dense_score < seuil ET aucun exact_lookup
    "absent_experiment",  # essai nommé absent du corpus (ex. ACE-8)
    "no_chunks_or_topic_mismatch",  # 0 chunk, ou acronyme de la question absent
    "llm_declined",  # le LLM a répondu « information absente »
)


def _now() -> datetime:
    return datetime.now(UTC)


def _append(record: dict[str, Any]) -> None:
    """Append atomique d'une ligne JSON. Ne lève jamais (le monitoring ne doit
    pas casser une requête utilisateur)."""
    if not QUERY_LOG_ENABLED:
        return
    try:
        with _lock:
            _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        _log.warning("query_log: écriture échouée (%s)", exc)


def log_query(
    *,
    question: str,
    found_in_corpus: bool,
    dense_score: float | None = None,
    fallback_reason: str | None = None,
    n_chunks: int | None = None,
    n_sources: int = 0,
    n_cited: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int | None = None,
    chantier: str | None = None,
    user: str | None = None,
) -> str:
    """Journalise une requête RAG et retourne son query_id (à joindre au feedback).

    `n_cited` = nombre d'IDs de sources effectivement cités dans la réponse ;
    couverture = n_cited / n_sources (proxy d'ancrage réponse↔sources).
    """
    query_id = uuid.uuid4().hex
    _append(
        {
            "type": "query",
            "query_id": query_id,
            "ts": _now().isoformat(),
            "question": question,
            "chantier": chantier or None,
            "user": user or None,
            "found_in_corpus": found_in_corpus,
            "fallback_reason": fallback_reason,
            "dense_score": dense_score,
            "score_threshold": SCORE_THRESHOLD,
            "n_chunks": n_chunks,
            "n_sources": n_sources,
            "n_cited": n_cited,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
        }
    )
    return query_id


def record_feedback(query_id: str, value: int, comment: str | None = None) -> None:
    """Enregistre un retour utilisateur : value=1 (👍) ou value=0 (👎)."""
    _append(
        {
            "type": "feedback",
            "query_id": query_id,
            "ts": _now().isoformat(),
            "value": int(value),
            "comment": comment or None,
        }
    )


# ── Analyse ──────────────────────────────────────────────────────────────────


def _read_records(since: datetime | None = None) -> list[dict[str, Any]]:
    """Lit toutes les lignes JSONL, filtrées par date (ts >= since)."""
    if not _LOG_FILE.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(_LOG_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is not None:
                try:
                    ts = datetime.fromisoformat(rec["ts"])
                except (KeyError, ValueError):
                    continue
                if ts < since:
                    continue
            out.append(rec)
    return out


def compute_metrics(days: int | None = 7) -> dict[str, Any]:
    """Agrège les métriques de pertinence sur une fenêtre glissante (jours)."""
    since = _now() - timedelta(days=days) if days else None
    records = _read_records(since)
    queries = [r for r in records if r.get("type") == "query"]
    feedback = [r for r in records if r.get("type") == "feedback"]

    n = len(queries)
    present = [q for q in queries if q.get("found_in_corpus")]
    fallbacks = [q for q in queries if not q.get("found_in_corpus")]

    reason_counts = Counter(q.get("fallback_reason") or "unspecified" for q in fallbacks)

    dense_present = [
        q["dense_score"] for q in present if isinstance(q.get("dense_score"), (int, float))
    ]
    # Couverture citations : sur les réponses trouvées ayant des sources.
    with_sources = [q for q in present if (q.get("n_sources") or 0) > 0]
    cited_cov = [
        (q.get("n_cited") or 0) / q["n_sources"] for q in with_sources if q.get("n_sources")
    ]
    uncited = [q for q in with_sources if not (q.get("n_cited") or 0)]

    latencies = [q["latency_ms"] for q in queries if isinstance(q.get("latency_ms"), (int, float))]

    # Feedback : dernière valeur par query_id (l'utilisateur peut changer d'avis).
    fb_by_query: dict[str, int] = {}
    for f in feedback:
        qid = f.get("query_id")
        if qid is not None and isinstance(f.get("value"), int):
            fb_by_query[qid] = f["value"]
    up = sum(1 for v in fb_by_query.values() if v == 1)
    down = sum(1 for v in fb_by_query.values() if v == 0)

    return {
        "window_days": days,
        "n_queries": n,
        "n_present": len(present),
        "n_fallback": len(fallbacks),
        "fallback_rate": len(fallbacks) / n if n else 0.0,
        "fallback_reasons": dict(reason_counts),
        "dense_score_avg": sum(dense_present) / len(dense_present) if dense_present else None,
        "dense_score_median": median(dense_present) if dense_present else None,
        "citation_coverage_avg": sum(cited_cov) / len(cited_cov) if cited_cov else None,
        "n_uncited_answers": len(uncited),
        "latency_ms_median": median(latencies) if latencies else None,
        "total_input_tokens": sum(q.get("input_tokens") or 0 for q in queries),
        "total_output_tokens": sum(q.get("output_tokens") or 0 for q in queries),
        "feedback_up": up,
        "feedback_down": down,
        "feedback_total": up + down,
        "satisfaction_rate": up / (up + down) if (up + down) else None,
    }


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:.1f}%" if x is not None else "—"


def _fmt_num(x: float | None, digits: int = 3) -> str:
    return f"{x:.{digits}f}" if x is not None else "—"


def format_summary(days: int | None = 7) -> str:
    """Résumé texte lisible des métriques (pour CLI ou affichage chat)."""
    m = compute_metrics(days)
    window = f"{days} derniers jours" if days else "tout l'historique"
    lines = [
        f"── Monitoring RAG ({window}) ──",
        f"Requêtes                : {m['n_queries']}",
        f"  trouvées / fallback   : {m['n_present']} / {m['n_fallback']}",
        f"Taux de fallback        : {_fmt_pct(m['fallback_rate'])}",
    ]
    if m["fallback_reasons"]:
        lines.append("  raisons :")
        for reason, cnt in sorted(
            m["fallback_reasons"].items(), key=lambda kv: kv[1], reverse=True
        ):
            lines.append(f"    - {reason:<28} {cnt}")
    lines += [
        f"dense_score (moy/méd)   : {_fmt_num(m['dense_score_avg'])} / "
        f"{_fmt_num(m['dense_score_median'])}  (seuil {SCORE_THRESHOLD:.4f})",
        f"Couverture citations    : {_fmt_pct(m['citation_coverage_avg'])} "
        f"({m['n_uncited_answers']} réponse(s) sans citation)",
        f"Latence médiane         : "
        f"{int(m['latency_ms_median']) if m['latency_ms_median'] is not None else '—'} ms",
        f"Tokens (in/out)         : {m['total_input_tokens']} / {m['total_output_tokens']}",
        f"Feedback 👍/👎          : {m['feedback_up']} / {m['feedback_down']}",
        f"Satisfaction            : {_fmt_pct(m['satisfaction_rate'])} "
        f"({m['feedback_total']} vote(s))",
    ]
    return "\n".join(lines)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Monitoring pertinence RAG (logs locaux).")
    parser.add_argument(
        "--days", type=int, default=7, help="Fenêtre en jours (0 = tout l'historique)."
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute.")
    args = parser.parse_args()
    days = args.days or None
    if args.json:
        print(json.dumps(compute_metrics(days), ensure_ascii=False, indent=2))
    else:
        print(format_summary(days))


if __name__ == "__main__":
    _main()
