"""CLI: python src/query.py "<question>" [--chantier <name>] [--top-k N]"""

from __future__ import annotations

import argparse
import sys

from src.generation.rag_pipeline import build_pipeline, run_query


def main() -> None:
    parser = argparse.ArgumentParser(description="Interroger la base R&D ACCRO.")
    parser.add_argument("question", help="Question en langage naturel")
    parser.add_argument("--chantier", default=None, help="Filtrer par chantier")
    parser.add_argument("--top-k", type=int, default=10, dest="top_k")
    args = parser.parse_args()

    pipeline = build_pipeline()
    response = run_query(pipeline, args.question, top_k=args.top_k, chantier=args.chantier)

    print(response.answer)
    if response.found_in_corpus and response.sources:
        print("\nSources :")
        for s in response.sources:
            label = s.run_id
            if s.name and s.name != s.run_id.split(":")[-1]:
                label += f" — {s.name}"
            print(f"  - {label} (score: {s.score:.3f})")
    pipeline._driver.close()


if __name__ == "__main__":
    sys.exit(main())
