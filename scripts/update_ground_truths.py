"""Correction semi-automatique des ground_truths du testset via DeepSeek.

Modes :
  --show  : affiche le diff GT actuelle vs réponse système (gratuit, ~5s)
  --update TYPE [TYPE...] : génère les nouvelles GT via DeepSeek + écrit
                            data/testset_candidate.json (jamais testset.json)

Usage :
  PYTHONPATH="." .venv/bin/python scripts/update_ground_truths.py \\
    --show --eval results/eval_custom_ace5fix_2026-06-08.json

  PYTHONPATH="." .venv/bin/python scripts/update_ground_truths.py \\
    --update graph_details graph_ingredient graph_references comparative
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import openai

from src.config import DEEPSEEK_API_KEY, LLM_MODEL
from src.generation.rag_pipeline import build_pipeline, run_query

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

TESTSET_PATH = Path("data/testset.json")
CANDIDATE_PATH = Path("data/testset_candidate.json")

_GT_PROMPT = """\
Tu es expert R&D en analogues de viande végétale.
Question posée au système RAG : {question}
Réponse du système : {answer}

Écris une ground_truth concise (2-4 phrases maximum) qui :
- Liste les FAITS CLÉS (noms d'essais, valeurs mesurées, conclusions principales)
- N'est PAS une copie de la réponse (reformule en 1/3 de la longueur)
- Reste neutre et factuelle
- Omet les détails secondaires et les formules de politesse

Réponds UNIQUEMENT avec la ground_truth, sans introduction ni explication."""

ALL_GRAPH_TYPES = {"graph_ingredient", "graph_details", "graph_references", "comparative"}


def _load_testset() -> list[dict]:
    with open(TESTSET_PATH, encoding="utf-8") as f:
        return json.load(f)


def _show_mode(eval_path: str) -> None:
    testset = _load_testset()
    with open(eval_path, encoding="utf-8") as f:
        eval_data = json.load(f)

    eval_by_q: dict[str, dict] = {r["question"]: r for r in eval_data["results"]}

    print(f"\n{'='*80}")
    print("DIVERGENCES GT vs RÉPONSE SYSTÈME")
    print(f"{'='*80}\n")

    count = 0
    for entry in testset:
        if not (entry["type"].startswith("graph") or entry["type"] == "comparative"):
            continue
        eval_row = eval_by_q.get(entry["question"])
        if not eval_row:
            continue
        answer_preview = eval_row.get("answer_preview", "N/A")
        found = eval_row.get("found_in_corpus", False)

        print(f"[{entry['type']}] {entry['question'][:75]}")
        print(f"  exp_id : {entry.get('experiment_id', '?')}")
        print(f"  found  : {found}")
        print(f"  GT     : {entry['ground_truth'][:150]}")
        print(f"  ANS    : {answer_preview[:150]}")
        print()
        count += 1

    print(f"→ {count} questions graph/comparative affichées.")
    print(f"→ Pour corriger : --update graph_details graph_ingredient ...")


def _update_mode(target_types: list[str]) -> None:
    target_set = set(target_types)
    testset = _load_testset()

    deepseek = openai.OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/v1",
    )

    log.info("Construction du pipeline RAG...")
    pipeline = build_pipeline()

    candidates = [e for e in testset if e["type"] in target_set]
    log.info(f"{len(candidates)} questions à traiter (types: {target_set})")

    updated = 0
    skipped = 0
    for i, entry in enumerate(candidates, 1):
        q = entry["question"]
        log.info(f"[{i}/{len(candidates)}] {q[:65]}...")

        result = run_query(pipeline, q)
        if not result.found_in_corpus:
            log.info("  → found_in_corpus=False, ignoré (GT reste manuelle)")
            skipped += 1
            continue

        full_answer = result.answer
        prompt = _GT_PROMPT.format(question=q, answer=full_answer[:2000])

        response = deepseek.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.1,
        )
        new_gt = response.choices[0].message.content.strip()

        old_gt = entry["ground_truth"]
        log.info(f"  OLD: {old_gt[:100]}")
        log.info(f"  NEW: {new_gt[:100]}")

        # Met à jour l'entrée dans la copie testset (pas l'original)
        for orig in testset:
            if orig["question"] == q:
                orig["ground_truth"] = new_gt
                break
        updated += 1

    # Écrit dans candidate, jamais testset.json
    with open(CANDIDATE_PATH, "w", encoding="utf-8") as f:
        json.dump(testset, f, ensure_ascii=False, indent=2)

    log.info(f"\n✓ {updated} ground_truths mises à jour, {skipped} ignorées.")
    log.info(f"→ Résultat dans : {CANDIDATE_PATH}")
    log.info("→ Vérifier avec : diff data/testset.json data/testset_candidate.json")
    log.info("→ Valider avec  : cp data/testset_candidate.json data/testset.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="Correction semi-auto des ground_truths via DeepSeek")
    ap.add_argument("--show", action="store_true", help="Affiche GT vs réponse système (gratuit)")
    ap.add_argument("--eval", default="results/eval_custom_ace5fix_2026-06-08.json",
                    help="Fichier eval à utiliser pour --show")
    ap.add_argument("--update", nargs="+", metavar="TYPE",
                    choices=list(ALL_GRAPH_TYPES) + ["all"],
                    help="Types de questions à mettre à jour via DeepSeek")
    args = ap.parse_args()

    if not args.show and not args.update:
        ap.print_help()
        sys.exit(1)

    if args.show:
        _show_mode(args.eval)

    if args.update:
        types = list(ALL_GRAPH_TYPES) if "all" in args.update else args.update
        _update_mode(types)


if __name__ == "__main__":
    main()
