"""One-shot calibration script for SCORE_THRESHOLD.

Run once after embed_chunks.py:
    python src/ingest/calibrate_threshold.py

Requires: Neo4j running with chunk embeddings loaded (T3–T5 done).
Updates SCORE_THRESHOLD in src/config.py with empirical measurements.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from neo4j import GraphDatabase
from openai import OpenAI

from src.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, OPENAI_API_KEY
from src.ingest.embed_chunks import embed_text

_PRESENT = [
    "effet de l'huile sur la texture",
    "sourcing fibre extrusion",
    "arôme kefta boulette",
    "NaCl KCl impact recette P02",
    "recette M03 gluten huile extrusion",
    "quel effet a l'huile sur M03",
    "résultats essais strips bœuf grillé Korean BBQ",
    "amélioration jutosité steak",
    "sourcing gluten alternatives",
    "boulette poulet arôme",
]

_ABSENT = [
    "Pisane ES",
    "cystéine en extrusion",
    "ingrédient inventé XYZ123",
    "calcium phosphate dairy",
    "polyester textile synthétique",
    "enzyme transglutaminase",
    "fermentation protéine",
]

_TOP1_CYPHER = """
CALL db.index.vector.queryNodes('chunk_embedding', 1, $query_vector)
YIELD node, score
RETURN score
"""


def _top1_score(session, client: OpenAI, query: str) -> float:
    vec = embed_text(client, query)
    record = session.run(_TOP1_CYPHER, query_vector=vec).single()
    return float(record["score"]) if record else 0.0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = OpenAI(api_key=OPENAI_API_KEY)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        present_scores: list[float] = []
        absent_scores: list[float] = []

        with driver.session() as session:
            print("\n--- Requêtes PRÉSENTES (doivent scorer haut) ---")
            for q in _PRESENT:
                s = _top1_score(session, client, q)
                present_scores.append(s)
                print(f"  {s:.4f}  {q}")

            print("\n--- Requêtes ABSENTES (doivent scorer bas) ---")
            for q in _ABSENT:
                s = _top1_score(session, client, q)
                absent_scores.append(s)
                print(f"  {s:.4f}  {q}")

        min_present = min(present_scores)
        max_present = max(present_scores)
        min_absent = min(absent_scores)
        max_absent = max(absent_scores)
        suggested = round((min_present + max_absent) / 2, 4)

        print(f"\nDistribution présentes : min={min_present:.4f}  max={max_present:.4f}")
        print(f"Distribution absentes  : min={min_absent:.4f}  max={max_absent:.4f}")
        print(f"Seuil suggéré          : {suggested:.4f}  (midpoint du creux)")

        if max_absent >= min_present:
            print("⚠️  Distributions se chevauchent — seuil peu fiable, revoir les requêtes.")

        _update_config(suggested, min_present, max_present, min_absent, max_absent)

    finally:
        driver.close()


def _update_config(
    threshold: float,
    min_present: float,
    max_present: float,
    min_absent: float,
    max_absent: float,
) -> None:
    config_path = Path("src/config.py")
    content = config_path.read_text(encoding="utf-8")
    comment = (
        f"# Calibré empiriquement (calibrate_threshold.py) :\n"
        f"# présentes min={min_present:.4f}/max={max_present:.4f}"
        f" | absentes min={min_absent:.4f}/max={max_absent:.4f}\n"
    )
    content = re.sub(
        r"(?:#[^\n]*\n)+SCORE_THRESHOLD: float = [\d.]+",
        comment + f"SCORE_THRESHOLD: float = {threshold}",
        content,
        flags=re.MULTILINE,
    )
    config_path.write_text(content, encoding="utf-8")
    print(f"config.py mis à jour → SCORE_THRESHOLD = {threshold}")


if __name__ == "__main__":
    main()
