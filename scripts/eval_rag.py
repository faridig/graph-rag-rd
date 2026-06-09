"""RAG evaluation — métriques custom + Ragas.

Usage:
    python scripts/eval_rag.py                              # métriques custom uniquement
    python scripts/eval_rag.py --ragas                      # + Ragas sans gold
    python scripts/eval_rag.py --testset data/testset.json  # gold → métriques référencées
    python scripts/eval_rag.py --testset data/testset.json --ragas  # tout
    python scripts/eval_rag.py --save results/eval_2026-06-05.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Corpus de test (fallback quand aucun testset fourni) ──────────────────────

QUESTIONS_PRESENT: list[str] = [
    "Quel est l'impact de l'huile sur l'anisotropie et la texture dans les essais ACE ?",
    "Quels arômes ont été retenus pour la boulette kefta végétale et pourquoi ?",
    "Quel conservateur remplace le Provian NDV et avec quels résultats ?",
    "Quelle est la formulation finale des boulettes kefta référence ?",
    "Quelles fibres ont été testées pour améliorer la jutosité de la P01 ?",
    "Quels essais de marinade ont été réalisés sur les strips végétaux ?",
    "Quel est le nutriscore obtenu pour la variante thaï des strips MDD ?",
    "Résultats des essais de vieillissement J25 sur les émincés avec Flavoset ?",
    "Comment a évolué la texture en fonction du taux de psyllium ?",
    "Quels sont les résultats de STRIP-18 sur l'incorporation d'épices ?",
    "Quelle est la teneur en vitamine B12 dans la formulation finale des steaks burger MDD ?",
]

# Questions dont les réponses sont ABSENTES du corpus.
# In-domain (food tech, même vocabulaire) pour stresser réellement le dense gate.
QUESTIONS_ABSENT: list[str] = [
    "Quels sont les résultats des essais sur le bœuf angus ?",
    "Quels essais d'extrusion HME ont été conduits avec la protéine de chanvre ?",
    "Les essais sur le lait de soja ont-ils donné de bons résultats ?",
    "Quel est l'impact du sel de mer sur le haché de champignon ?",
]

_CITATION_RE = re.compile(r"\[source:\s*([^\]]+)\]", re.IGNORECASE)
_FALLBACK_SIGNAL = "Information absente du corpus"


# ── Types ─────────────────────────────────────────────────────────────────────


@dataclass
class EvalResult:
    question: str
    answer: str
    found_in_corpus: bool
    sources: list[dict]
    input_tokens: int
    output_tokens: int
    latency_s: float
    ground_truth: str = ""
    experiment_id: str = ""
    retrieved_contexts: list[str] = field(default_factory=list)
    ref_traversal_fired: bool = False
    cited_ids: set[str] = field(default_factory=set)
    valid_cited_ids: set[str] = field(default_factory=set)
    dense_gate_score: float = 0.0
    post_llm_detected: bool = False


# ── Answer cache ──────────────────────────────────────────────────────────────

# Absolute paths so the hash is correct regardless of the working directory.
_PROJECT_ROOT = Path(__file__).parent.parent
_PIPELINE_SOURCES = [
    _PROJECT_ROOT / "src/generation/rag_pipeline.py",
    _PROJECT_ROOT / "src/generation/prompt_fr.py",
    _PROJECT_ROOT / "src/retrieval/hybrid_retriever.py",
    _PROJECT_ROOT / "src/config.py",
]


def _pipeline_hash() -> str:
    h = hashlib.md5()
    for p in _PIPELINE_SOURCES:
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


class _AnswerCache:
    """Persist EvalResult objects keyed by (pipeline_hash, question).

    Cache hit = pipeline code unchanged for that question → reuse answer + contexts,
    Ragas DiskCache then handles scoring for free (same inputs → same cache key).
    Entries from old pipeline versions are pruned on each save to bound file size.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self.pipeline_hash = _pipeline_hash()
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                # Load only current-version entries; stale ones are silently dropped.
                self._data = {
                    k: v for k, v in raw.items()
                    if v.get("pipeline_hash") == self.pipeline_hash
                }
            except Exception:
                pass
        self._hits = 0
        self._misses = 0

    def _key(self, question: str) -> str:
        # Separator ":" prevents key collisions from hash/question boundary ambiguity.
        return hashlib.md5(f"{self.pipeline_hash}:{question}".encode()).hexdigest()

    def get(self, question: str) -> EvalResult | None:
        entry = self._data.get(self._key(question))
        if entry is None:
            self._misses += 1
            return None
        self._hits += 1
        d = entry["result"]
        return EvalResult(
            question=d["question"],
            answer=d["answer"],
            found_in_corpus=d["found_in_corpus"],
            sources=d["sources"],
            input_tokens=d["input_tokens"],
            output_tokens=d["output_tokens"],
            latency_s=d["latency_s"],
            ground_truth=d.get("ground_truth", ""),
            experiment_id=d.get("experiment_id", ""),
            retrieved_contexts=d["retrieved_contexts"],
            ref_traversal_fired=d["ref_traversal_fired"],
            cited_ids=set(d["cited_ids"]),
            valid_cited_ids=set(d["valid_cited_ids"]),
            dense_gate_score=d["dense_gate_score"],
            post_llm_detected=d["post_llm_detected"],
        )

    def put(self, question: str, result: EvalResult) -> None:
        self._data[self._key(question)] = {
            "pipeline_hash": self.pipeline_hash,
            "result": {
                "question": result.question,
                "answer": result.answer,
                "found_in_corpus": result.found_in_corpus,
                "sources": result.sources,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_s": result.latency_s,
                "ground_truth": result.ground_truth,
                "experiment_id": result.experiment_id,
                "retrieved_contexts": result.retrieved_contexts,
                "ref_traversal_fired": result.ref_traversal_fired,
                "cited_ids": list(result.cited_ids),
                "valid_cited_ids": list(result.valid_cited_ids),
                "dense_gate_score": result.dense_gate_score,
                "post_llm_detected": result.post_llm_detected,
            },
        }
        # Save after each new entry so a Ctrl+C doesn't lose completed work.
        self._flush()

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def save(self) -> None:
        self._flush()

    def stats(self) -> str:
        total = self._hits + self._misses
        pct = f"{100 * self._hits // total}%" if total else "—"
        return f"{self._hits}/{total} hits ({pct}) — pipeline_hash={self.pipeline_hash}"


# ── Custom metrics ─────────────────────────────────────────────────────────────


def _is_fallback(r: EvalResult) -> bool:
    return not r.found_in_corpus or _FALLBACK_SIGNAL in r.answer


def _compute_custom_metrics(
    present_results: list[EvalResult],
    absent_results: list[EvalResult],
) -> dict[str, Any]:
    all_results = present_results + absent_results

    absent_fallback_rate = (
        sum(1 for r in absent_results if _is_fallback(r)) / len(absent_results)
        if absent_results
        else 0.0
    )
    present_fallback_rate = (
        sum(1 for r in present_results if _is_fallback(r)) / len(present_results)
        if present_results
        else 0.0
    )

    answered = [r for r in present_results if r.found_in_corpus]
    citation_coverage = sum(1 for r in answered if r.cited_ids) / len(answered) if answered else 0.0

    total_cited = sum(len(r.cited_ids) for r in answered)
    total_valid = sum(len(r.valid_cited_ids) for r in answered)
    citation_validity = total_valid / total_cited if total_cited else 0.0

    mean_sources_cited = statistics.mean(len(r.cited_ids) for r in answered) if answered else 0.0
    ref_traversal_rate = (
        sum(1 for r in present_results if r.ref_traversal_fired) / len(present_results)
        if present_results
        else 0.0
    )

    gated = [r for r in present_results if r.dense_gate_score > 0]
    mean_dense_gate_score = statistics.mean(r.dense_gate_score for r in gated) if gated else 0.0

    latencies = [r.latency_s for r in all_results]
    mean_latency = statistics.mean(latencies) if latencies else 0.0
    total_in = sum(r.input_tokens for r in all_results)
    total_out = sum(r.output_tokens for r in all_results)

    post_llm_fallback_rate = (
        sum(1 for r in present_results if r.post_llm_detected) / len(present_results)
        if present_results
        else 0.0
    )

    return {
        "n_present": len(present_results),
        "n_absent": len(absent_results),
        "absent_fallback_rate": round(absent_fallback_rate, 3),
        "present_fallback_rate": round(present_fallback_rate, 3),
        "post_llm_fallback_rate": round(post_llm_fallback_rate, 3),
        "citation_coverage": round(citation_coverage, 3),
        "citation_validity": round(citation_validity, 3),
        "mean_sources_cited": round(mean_sources_cited, 2),
        "ref_traversal_rate": round(ref_traversal_rate, 3),
        "mean_dense_gate_score": round(mean_dense_gate_score, 4),
        "mean_latency_s": round(mean_latency, 2),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
    }


# ── Ragas metrics ─────────────────────────────────────────────────────────────


_RAGAS_COST_PER_Q_PER_METRIC = 0.10  # $USD estimé par question par métrique (Sonnet 4.6)

ALL_RAGAS_METRICS = [
    "faithfulness",
    "context_utilization",
    "answer_relevancy",
    "response_groundedness",
    "context_precision",
    "context_recall",
    "factual_correctness",
]


def _compute_ragas_metrics(
    results: list[EvalResult],
    has_ground_truth: bool,
    selected_metrics: list[str] | None = None,
) -> dict[str, Any]:
    import asyncio
    import os

    try:
        from openai import AsyncOpenAI
        from ragas.embeddings.base import embedding_factory
        from ragas.llms import llm_factory
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextUtilization,
            Faithfulness,
            ResponseGroundedness,
        )
    except ImportError as e:
        print(f"[WARN] dépendance manquante : {e}\n       pip install ragas")
        return {}

    answered = [r for r in results if r.found_in_corpus and r.retrieved_contexts]
    if not answered:
        return {}

    # Cache disque — les reruns sur les mêmes données sont gratuits.
    # Stocké dans .ragas_cache/ (gitignored). Désactiver avec RAG_NO_CACHE=1.
    cache = None
    if not os.getenv("RAG_NO_CACHE"):
        try:
            from ragas.cache import DiskCacheBackend
            cache = DiskCacheBackend()
            print("  [cache] DiskCacheBackend actif — reruns gratuits")
        except Exception:
            pass

    # DeepSeek: OpenAI-compatible API, ~20x cheaper than claude-sonnet-4-6 ($0.14/$0.28 vs $3/$15 per MTok).
    deepseek_client = AsyncOpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        base_url="https://api.deepseek.com/v1",
    )
    llm_kwargs: dict[str, Any] = {
        "provider": "openai",
        "client": deepseek_client,
    }
    if cache is not None:
        llm_kwargs["cache"] = cache
    llm = llm_factory("deepseek-chat", **llm_kwargs)

    openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
    embeddings = embedding_factory(
        provider="openai",
        model="text-embedding-3-large",
        client=openai_client,
    )

    # Reference-free metrics — ascore kwargs differ per metric (inspected)
    # Faithfulness(user_input, response, retrieved_contexts)
    # ContextUtilization(user_input, response, retrieved_contexts)
    # AnswerRelevancy(user_input, response)  — no retrieved_contexts
    # ResponseGroundedness(response, retrieved_contexts)  — no user_input
    scorers: dict[str, Any] = {
        "faithfulness": Faithfulness(llm=llm),
        "context_utilization": ContextUtilization(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
        "response_groundedness": ResponseGroundedness(llm=llm),
    }
    scorer_kwargs: dict[str, dict[str, list[str]]] = {
        "faithfulness":         {"keys": ["user_input", "response", "retrieved_contexts"]},
        "context_utilization":  {"keys": ["user_input", "response", "retrieved_contexts"]},
        "answer_relevancy":     {"keys": ["user_input", "response"]},
        "response_groundedness": {"keys": ["response", "retrieved_contexts"]},
    }

    # Reference-based metrics — only when gold ground_truth is loaded
    if has_ground_truth:
        try:
            from ragas.metrics.collections import (
                ContextPrecision,
                ContextRecall,
                FactualCorrectness,
            )

            scorers["context_precision"] = ContextPrecision(llm=llm)
            scorers["context_recall"] = ContextRecall(llm=llm)
            scorers["factual_correctness"] = FactualCorrectness(llm=llm)
            ref_keys = ["user_input", "retrieved_contexts", "reference"]
            scorer_kwargs["context_precision"]   = {"keys": ref_keys}
            scorer_kwargs["context_recall"]      = {"keys": ref_keys}
            scorer_kwargs["factual_correctness"] = {"keys": ["response", "reference"]}
        except ImportError:
            print("[WARN] ContextPrecision/ContextRecall/FactualCorrectness"
                  " non disponibles dans cette version de Ragas")

    # Filtrage des métriques sélectionnées
    if selected_metrics:
        unknown = [m for m in selected_metrics if m not in scorers]
        if unknown:
            print(f"  [WARN] métriques inconnues ignorées : {unknown}")
        scorers = {k: v for k, v in scorers.items() if k in selected_metrics}
        scorer_kwargs = {k: v for k, v in scorer_kwargs.items() if k in selected_metrics}

    n_metrics = len(scorers)
    n_q = len(answered)
    estimated_cost = n_q * n_metrics * _RAGAS_COST_PER_Q_PER_METRIC
    cache_note = " (1er run — reruns ~gratuits via cache)" if cache else ""
    print(
        f"\n[Ragas] {n_q} échantillons × {n_metrics} métriques"
        f" — coût estimé ~${estimated_cost:.0f}{cache_note}"
    )
    print(f"  Métriques : {', '.join(scorers)}")
    print("  Juge      : claude-sonnet-4-6")

    async def _score_all() -> list[dict[str, Any]]:
        per_q = []
        for r in answered:
            row: dict[str, Any] = {
                "question": r.question[:80],
                "answer_preview": r.answer[:200],
            }
            pool = {
                "user_input": r.question,
                "response": r.answer,
                "retrieved_contexts": r.retrieved_contexts,
                "reference": r.ground_truth if r.ground_truth else "",
            }
            for name, scorer in scorers.items():
                try:
                    keys = scorer_kwargs[name]["keys"]
                    kwargs = {k: pool[k] for k in keys if k in pool}
                    result = await scorer.ascore(**kwargs)
                    row[name] = round(float(result.value), 3)
                except Exception as exc:
                    row[name] = None
                    print(f"  [WARN] {name} sur '{r.question[:50]}': {exc}")
            per_q.append(row)
            answered_names = [k for k in row if k not in ("question", "answer_preview")]
            scores = " | ".join(
                f"{k}={row[k]:.2f}" if row[k] is not None else f"{k}=?" for k in answered_names
            )
            print(f"  {scores}")
        return per_q

    per_question = asyncio.run(_score_all())

    # Aggregate
    out: dict[str, Any] = {}
    for key in scorers:
        vals = [r[key] for r in per_question if r.get(key) is not None]
        out[key] = round(sum(vals) / len(vals), 3) if vals else None
    out["per_question"] = per_question
    return out


# ── Pipeline instrumenté ──────────────────────────────────────────────────────


def _run_eval(
    pipeline: Any, question: str, ground_truth: str = "", experiment_id: str = ""
) -> EvalResult:
    from src.generation import rag_pipeline as rp

    captured_contexts: list[str] = []
    ref_fired: bool = False

    orig_search = pipeline._retriever.search

    def _patched_search(q: str, top_k: int = 10, filters: Any = None) -> list[dict]:
        chunks = orig_search(q, top_k=top_k, filters=filters)
        captured_contexts.extend(c["text"] for c in chunks if c.get("text"))
        return chunks

    orig_fetch_ref = rp._fetch_reference_summaries

    def _patched_fetch_ref(driver: Any, exp_ids: list[str]) -> list[dict]:
        refs = orig_fetch_ref(driver, exp_ids)
        nonlocal ref_fired
        active = [r for r in refs if r["ref_exp_id"] not in exp_ids]
        if active:
            ref_fired = True
            captured_contexts.extend(r["ref_text"] for r in active if r.get("ref_text"))
        return refs

    # Capture augmented chunks — _augment_chunks_from_question bypasses _retriever.search
    # so its results are missing from retrieved_contexts without this patch.
    # Missing augmented contexts → Ragas faithfulness checks the answer against wrong chunks.
    orig_augment = rp._augment_chunks_from_question

    def _patched_augment(*args: Any, **kwargs: Any) -> list[dict]:
        extra = orig_augment(*args, **kwargs)
        captured_contexts.extend(c["text"] for c in extra if c.get("text"))
        return extra

    # Capture Phase 1b aggregate context — formatted string injected directly into the
    # prompt, bypassing all chunk-based capture paths above.
    orig_fetch_agg = rp._fetch_ingredient_aggregate

    def _patched_fetch_agg(driver: Any, tokens: list) -> list[dict]:
        rows = orig_fetch_agg(driver, tokens)
        if rows:
            captured_contexts.append(rp._format_ingredient_aggregate(rows))
        return rows

    pipeline._retriever.search = _patched_search
    rp._fetch_reference_summaries = _patched_fetch_ref
    rp._augment_chunks_from_question = _patched_augment
    rp._fetch_ingredient_aggregate = _patched_fetch_agg

    try:
        t0 = time.monotonic()
        resp = rp.run_query(pipeline, question)
        latency = time.monotonic() - t0
    finally:
        pipeline._retriever.search = orig_search
        rp._fetch_reference_summaries = orig_fetch_ref
        rp._augment_chunks_from_question = orig_augment
        rp._fetch_ingredient_aggregate = orig_fetch_agg

    cited_ids = rp.extract_cited_ids(resp.answer)
    valid_ids = {s.run_id for s in (resp.sources or [])}
    dense_score = rp.get_dense_score(pipeline, question)

    # Post-LLM fallback : gate dense passée mais found_in_corpus=False avec chunks récupérés
    # → le détecteur de non-réponse a déclenché après génération LLM.
    from src.config import SCORE_THRESHOLD
    post_llm_detected = (
        not resp.found_in_corpus
        and dense_score >= SCORE_THRESHOLD
        and bool(captured_contexts)
    )

    return EvalResult(
        question=question,
        answer=resp.answer,
        found_in_corpus=resp.found_in_corpus,
        sources=[{"run_id": s.run_id, "score": s.score} for s in (resp.sources or [])],
        input_tokens=getattr(resp, "input_tokens", 0) or 0,
        output_tokens=getattr(resp, "output_tokens", 0) or 0,
        latency_s=latency,
        ground_truth=ground_truth,
        experiment_id=experiment_id,
        retrieved_contexts=list(captured_contexts),
        ref_traversal_fired=ref_fired,
        cited_ids=cited_ids,
        valid_cited_ids=cited_ids & valid_ids,
        dense_gate_score=dense_score,
        post_llm_detected=post_llm_detected,
    )


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Évaluation RAG")
    parser.add_argument(
        "--ragas", action="store_true", help="Lancer les métriques Ragas (LLM calls)"
    )
    parser.add_argument(
        "--testset", metavar="FILE", help="JSON gold testset (depuis generate_testset.py)"
    )
    parser.add_argument("--save", metavar="PATH", help="Sauvegarder les résultats en JSON")
    parser.add_argument(
        "--questions-absent",
        metavar="FILE",
        help="Fichier JSON de questions absentes custom",
    )
    parser.add_argument(
        "--no-testset",
        action="store_true",
        help="Forcer les questions hardcodées même si data/testset.json existe",
    )
    parser.add_argument(
        "--no-answer-cache",
        action="store_true",
        help="Désactiver le cache des réponses pipeline (force re-run de toutes les questions)",
    )
    parser.add_argument(
        "--metrics",
        metavar="LIST",
        help=(
            "Métriques Ragas à lancer (virgule-séparé, sans espaces). "
            f"Disponibles : {','.join(ALL_RAGAS_METRICS)}. "
            "Défaut : toutes les métriques référence-free (+ référencées si --testset)."
        ),
    )
    args = parser.parse_args()

    # Auto-detect testset if not specified and not explicitly disabled
    if not args.testset and not args.no_testset:
        default_testset = Path("data/testset.json")
        if default_testset.exists():
            args.testset = str(default_testset)
            print(f"[auto] testset détecté : {default_testset}")

    # ── Chargement du testset ou des questions par défaut ─────────────────────
    has_ground_truth = False
    testset_items: list[dict] = []

    present_items: list[dict] = []
    absent_items_from_testset: list[dict] = []

    if args.testset:
        testset_items = json.loads(Path(args.testset).read_text(encoding="utf-8"))
        has_ground_truth = True
        # Items typed "absent" feed the absent pool; everything else (factuelle,
        # synthèse, comparative, cross_experiment) feeds the present pool.
        present_items = [it for it in testset_items if it.get("type") != "absent"]
        absent_items_from_testset = [it for it in testset_items if it.get("type") == "absent"]
        questions_present = [item["question"] for item in present_items]
        print(
            f"Testset chargé : {len(questions_present)} présentes"
            f" + {len(absent_items_from_testset)} absentes avec ground_truth"
        )
    else:
        questions_present = QUESTIONS_PRESENT
        print(f"Questions par défaut : {len(questions_present)} (sans ground_truth)")

    if absent_items_from_testset:
        questions_absent = [it["question"] for it in absent_items_from_testset]
    elif args.questions_absent:
        questions_absent = json.loads(Path(args.questions_absent).read_text(encoding="utf-8"))
    else:
        questions_absent = QUESTIONS_ABSENT

    # ── Answer cache ──────────────────────────────────────────────────────────
    answer_cache: _AnswerCache | None = None
    if not args.no_answer_cache:
        answer_cache = _AnswerCache(Path(".eval_cache/answers.json"))
        print(f"[answer-cache] actif — pipeline_hash={answer_cache.pipeline_hash}")

    # ── Pipeline ──────────────────────────────────────────────────────────────
    from src.generation.rag_pipeline import build_pipeline

    print("Initialisation du pipeline…")
    pipeline = build_pipeline()

    # ── Questions présentes ───────────────────────────────────────────────────
    print(f"\nQuestions présentes ({len(questions_present)})…")
    present_results: list[EvalResult] = []
    for i, q in enumerate(questions_present, 1):
        item = present_items[i - 1] if present_items else None
        gt = item.get("ground_truth", "") if item else ""
        exp_id = (item.get("experiment_id") or "") if item else ""
        cached = answer_cache.get(q) if answer_cache else None
        if cached:
            cached.ground_truth = gt
            cached.experiment_id = exp_id
            present_results.append(cached)
            print(f"  [{i}/{len(questions_present)}] [cache] {q[:70]}…")
        else:
            print(f"  [{i}/{len(questions_present)}] {q[:70]}…")
            result = _run_eval(pipeline, q, gt, exp_id)
            if answer_cache:
                answer_cache.put(q, result)
            present_results.append(result)

    # ── Questions absentes ────────────────────────────────────────────────────
    print(f"\nQuestions absentes ({len(questions_absent)})…")
    absent_results: list[EvalResult] = []
    for i, q in enumerate(questions_absent, 1):
        cached = answer_cache.get(q) if answer_cache else None
        if cached:
            absent_results.append(cached)
            print(f"  [{i}/{len(questions_absent)}] [cache] {q[:70]}…")
        else:
            print(f"  [{i}/{len(questions_absent)}] {q[:70]}…")
            result = _run_eval(pipeline, q)
            if answer_cache:
                answer_cache.put(q, result)
            absent_results.append(result)

    if answer_cache:
        answer_cache.save()
        print(f"\n[answer-cache] {answer_cache.stats()}")

    # ── Métriques custom ──────────────────────────────────────────────────────
    metrics = _compute_custom_metrics(present_results, absent_results)

    print("\n" + "=" * 60)
    print("MÉTRIQUES CUSTOM")
    print("=" * 60)
    print(f"  Corpus présent     : {metrics['n_present']} questions")
    print(f"  Corpus absent      : {metrics['n_absent']} questions")
    print()
    print(f"  absent_fallback_rate      : {metrics['absent_fallback_rate']:.1%}  (cible : 1.0)")
    print(f"  present_fallback_rate     : {metrics['present_fallback_rate']:.1%}  (pre-LLM gate)")
    print(f"  post_llm_fallback_rate    : {metrics['post_llm_fallback_rate']:.1%}  (refus post-génération)")
    print()
    print(f"  citation_coverage     : {metrics['citation_coverage']:.1%}  (cible : 1.0)")
    print(f"  citation_validity     : {metrics['citation_validity']:.1%}  (cible : 1.0)")
    print(f"  mean_sources_cited    : {metrics['mean_sources_cited']:.1f}")
    print()
    print(f"  ref_traversal_rate      : {metrics['ref_traversal_rate']:.1%}  ([:REFERENCES] actif)")
    gate_note = (
        "  ⚠ relancer calibrate_threshold.py" if metrics["mean_dense_gate_score"] > 0.98 else ""
    )
    print(f"  mean_dense_gate_score   : {metrics['mean_dense_gate_score']:.4f}{gate_note}")
    print()
    print(f"  mean_latency_s        : {metrics['mean_latency_s']:.2f}s")
    in_tok = metrics["total_input_tokens"]
    out_tok = metrics["total_output_tokens"]
    print(f"  tokens                : {in_tok} in / {out_tok} out")

    # ── Détail par question ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DÉTAIL PAR QUESTION (présentes)")
    print("=" * 60)
    for r in present_results:
        status = "✓" if r.found_in_corpus and not _is_fallback(r) else "✗ FALLBACK"
        cit = f"{len(r.cited_ids)} sources" if r.found_in_corpus else "—"
        ref = " [REF]" if r.ref_traversal_fired else ""
        gate = f"  gate={r.dense_gate_score:.4f}" if r.dense_gate_score else ""
        print(f"\n  {status}{ref}  {cit}{gate}  {r.latency_s:.1f}s")
        print(f"    Q: {r.question[:80]}")
        if r.ground_truth:
            gt_preview = r.ground_truth[:120].replace("\n", " ")
            print(
                f"    GT: {gt_preview}…" if len(r.ground_truth) > 120 else f"    GT: {gt_preview}"
            )
        if r.found_in_corpus and r.answer and _FALLBACK_SIGNAL not in r.answer:
            ans_preview = r.answer[:150].replace("\n", " ")
            print(f"    A:  {ans_preview}…" if len(r.answer) > 150 else f"    A:  {ans_preview}")

    print()
    print("DÉTAIL PAR QUESTION (absentes)")
    for r in absent_results:
        ok = _is_fallback(r)
        status = "✓ fallback" if ok else "✗ FAUX POSITIF"
        detail = "(dense gate)" if not r.found_in_corpus else "(LLM)"
        print(f"  {status} {detail}  {r.latency_s:.1f}s  —  {r.question[:75]}")

    # ── Ragas ─────────────────────────────────────────────────────────────────
    ragas_metrics: dict[str, Any] = {}
    if args.ragas:
        import os

        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            print("\n[WARN] ANTHROPIC_API_KEY non définie — Ragas ignoré")
        else:
            selected = (
                [m.strip() for m in args.metrics.split(",")]
                if args.metrics
                else None
            )
            ragas_metrics = _compute_ragas_metrics(
                present_results, has_ground_truth, selected
            )
            if ragas_metrics:
                print("\n" + "=" * 60)
                print("MÉTRIQUES RAGAS (LLM-as-judge, deepseek-chat)")
                if has_ground_truth:
                    print("  (avec ground_truth → métriques référencées incluses)")
                print("=" * 60)
                score_map = {
                    "faithfulness": "cible >0.85 — réponse fidèle au contexte",
                    "context_utilization": "cible >0.70 — contexte bien exploité",
                    "answer_relevancy": "cible >0.80 — réponse pertinente à la question",
                    "response_groundedness": "cible >0.85 — claims traçables (anti-hallucination)",
                    "context_precision": "cible >0.75 — chunks pertinents bien classés",
                    "context_recall": "cible >0.70 — ground_truth couverte par le contexte",
                    "factual_correctness": "cible >0.80 — faits corrects vs ground_truth",
                }
                for key, hint in score_map.items():
                    if key in ragas_metrics:
                        print(f"  {key:<26}: {ragas_metrics[key]}  ({hint})")

    # ── Sauvegarde ────────────────────────────────────────────────────────────
    if args.save:
        output = {
            "custom_metrics": metrics,
            "ragas_metrics": {k: v for k, v in ragas_metrics.items() if k != "per_question"},
            "ragas_per_question": ragas_metrics.get("per_question", []),
            "has_ground_truth": has_ground_truth,
            "results": [
                {
                    "question": r.question,
                    "experiment_id": r.experiment_id,
                    "found_in_corpus": r.found_in_corpus,
                    "answer_preview": r.answer[:400] if r.answer else "",
                    "n_sources": len(r.sources),
                    "n_cited": len(r.cited_ids),
                    "n_valid_cited": len(r.valid_cited_ids),
                    "ref_traversal": r.ref_traversal_fired,
                    "post_llm_detected": r.post_llm_detected,
                    "dense_gate_score": r.dense_gate_score,
                    "latency_s": r.latency_s,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "has_ground_truth": bool(r.ground_truth),
                }
                for r in present_results + absent_results
            ],
        }
        save_path = Path(args.save)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nRésultats sauvegardés → {save_path}")

    pipeline._driver.close()


if __name__ == "__main__":
    main()
