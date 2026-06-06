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
    # Formulations longues (style utilisateur réel)
    "Quel est l'effet de l'huile sur la dureté et l'anisotropie dans l'essai M03 ?",
    "NaCl et KCl ont-ils le même effet sur la texturation de P02 ?",
    "quel impact du sel sur les bandes texturées Allumette HME",
    "formulations escalope panée Quick coût matière première",
    "quelles formulations ont atteint l'objectif coût inférieur à 2 euros par kg Quick",
    "impact teneur sel absorption eau extrusion HME",
    "SME pression filière huile M03 extrusion",
    "anisotropie coupe transversale longitudinale P02",
    # Lupin : présent dans PP-REC-12 (lupine LP90 WOA, fix regex embed_chunks)
    "Y a-t-il des données sur l'utilisation de protéines de lupin en extrusion haute humidité ?",
]

_ABSENT = [
    # In-domain (food tech, même vocabulaire) mais genuinement absents du corpus.
    # Ces requêtes sont plus discriminantes que les off-domain : elles stressent le gate
    # sur des concepts proches des expériences sans en être (grep _knowledge/_doc = 0).
    "Quels essais d'extrusion HME ont été conduits avec la protéine de chanvre ?",
    "Quel est l'impact du glucomannane de konjac sur la texture des strips végétaux ?",
    "Résultats d'analyse microbiologique sur les émincés MDD après 21 jours en rayon ?",
    "Quels essais de congélation-décongélation ont été réalisés sur les boulettes kefta ?",
    "Quel effet a la teneur en zinc sur la structuration des protéines texturées HME ?",
    # LME et méthylcellulose : passaient le dense gate, hallucinations par substitution.
    # Couverts par le topic gate (_topic_in_chunks) dans rag_pipeline.py.
    "Quels résultats ont été obtenus lors des essais d'extrusion basse humidité (LME) sur M03 ?",
    "Y a-t-il des données sur l'incorporation de méthylcellulose dans les formulations extrudées ?",
    # Off-domain : garde-fou (doivent scorer très bas)
    "ingrédient inventé XYZ123",
    "polyester textile synthétique",
    "lyophilisation protéine végétale",
]

# Utilise avg(top-3) — IDENTIQUE à _DENSE_GATE_CYPHER dans rag_pipeline.py.
# Ne pas changer l'un sans changer l'autre, sinon le seuil calibré devient invalide.
_GATE_CYPHER = """
CALL db.index.vector.queryNodes('chunk_embedding', 3, $query_vector)
YIELD node, score
RETURN avg(score) AS score
"""


def _top1_score(session, client: OpenAI, query: str) -> float:
    vec = embed_text(client, query)
    record = session.run(_GATE_CYPHER, query_vector=vec).single()
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
        # When distributions overlap, midpoint can fall above min_present (false negatives).
        # Use min_present - 0.01 to guarantee all present queries pass the gate.
        midpoint = round((min_present + max_absent) / 2, 4)
        if max_absent >= min_present:
            suggested = round(min_present - 0.01, 4)
        else:
            suggested = midpoint

        print(f"\nDistribution présentes : min={min_present:.4f}  max={max_present:.4f}")
        print(f"Distribution absentes  : min={min_absent:.4f}  max={max_absent:.4f}")
        print(f"Midpoint du creux      : {midpoint:.4f}")
        print(f"Seuil suggéré          : {suggested:.4f}")

        if max_absent >= min_present:
            print(
                "⚠️  Distributions se chevauchent — seuil fixé à min_present - 0.01"
                " pour garantir zéro faux négatif."
                " Ajouter des chunks summary améliorerait la séparation."
            )

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
