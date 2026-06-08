"""Diagnostic retrieval — classement RRF complet + position automatique du chunk cible.

Usage :
  PYTHONPATH="." .venv/bin/python scripts/debug_retrieval.py \
    "valeur indice anisotropie AI Run 3 M03 DST essai 3 Soja Tmat 120"
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")
from src.generation.rag_pipeline import build_pipeline

def _is_target(c: dict) -> bool:
    """True only for experiment_section chunks containing ## 4 (Derived & computed values).
    run_detail chunks may also contain 'anisotropie' in their text but don't have the values.
    """
    text = (c.get("text") or "").lower()
    exp = (c.get("experiment_id") or "").upper()
    return "DST" in exp and "## 4" in text and "synthèse et conclusions" in text


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspecte le classement RRF brut du retriever")
    ap.add_argument("question", help="Question à tester")
    ap.add_argument("--top-k", type=int, default=30, help="Nombre de chunks à récupérer (défaut 30)")
    args = ap.parse_args()

    print("Construction du pipeline…")
    pipeline = build_pipeline()
    chunks = pipeline._retriever.search(args.question, top_k=args.top_k)

    print(f"\n{'#':>3}  {'score':>6}  {'exp_id':<24}  {'run_id':<38}  text[:65]")
    print("-" * 118)

    target_pos: int | None = None
    for i, c in enumerate(chunks, 1):
        is_tgt = _is_target(c)
        if is_tgt and target_pos is None:
            target_pos = i
        marker = "  ◄◄◄ CIBLE" if is_tgt else ""
        text = (c.get("text") or "")[:65].replace("\n", " ")
        print(
            f"{i:>3}  {c.get('score', 0.0):>6.4f}  "
            f"{c.get('experiment_id', '?'):<24}  "
            f"{c.get('run_id', '?'):<38}  "
            f"{text}{marker}"
        )

    print()
    if target_pos is not None:
        print(f">>> CHUNK CIBLE : position {target_pos}/{len(chunks)}")
        if target_pos <= 10:
            print(">>> FIX RECOMMANDÉ : TOP_K_DEFAULT 6→8 dans src/config.py")
        else:
            print(">>> FIX RECOMMANDÉ : augmentation ciblée termes de mesure dans rag_pipeline.py")
    else:
        print(f">>> CHUNK CIBLE NON TROUVÉ dans les {len(chunks)} résultats")
        print(">>> Vérifier l'étape 0 : le chunk existe-t-il dans Neo4j avec embedding non-null ?")


if __name__ == "__main__":
    main()
