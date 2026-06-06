"""Exact fallback: ingredient CONTAINS + fulltext on run objective/synthesis/name."""

from __future__ import annotations

import re

from neo4j import Driver

_LUCENE_SPECIAL = re.compile(r'[+\-!(){}[\]^"~*?:\\/]|&&|\|\|')

_INGREDIENT_CYPHER = """
MATCH (i:Ingredient) WHERE toLower(i.name) CONTAINS toLower($name)
MATCH (run:Run)-[:USES_INGREDIENT]->(i)
OPTIONAL MATCH (run)<-[:HAS_RUN]-(exp:Experiment)
OPTIONAL MATCH (run)-[:BELONGS_TO]->(chantier:Chantier)
RETURN DISTINCT run.id AS run_id, run.name AS run_name,
       exp.id AS experiment_id,
       run.objective AS objective, run.synthesis AS synthesis,
       run.date AS date, chantier.name AS chantier,
       i.name AS ingredient_match
LIMIT 20
"""

_FULLTEXT_CYPHER = """
CALL db.index.fulltext.queryNodes('run_fulltext', $query) YIELD node AS run, score
WHERE score > 1.0  // BM25: seuil empirique pour filtrer les matchs partiels faibles
WITH run, score
OPTIONAL MATCH (run)<-[:HAS_RUN]-(exp:Experiment)
OPTIONAL MATCH (run)-[:BELONGS_TO]->(chantier:Chantier)
RETURN run.id AS run_id, run.name AS run_name,
       exp.id AS experiment_id,
       run.objective AS objective, run.synthesis AS synthesis,
       run.date AS date, chantier.name AS chantier,
       null AS ingredient_match
ORDER BY score DESC
LIMIT 20
"""


def _sanitize(query: str) -> str:
    """Escape Lucene specials and build an AND query (all terms required)."""
    clean = _LUCENE_SPECIAL.sub(" ", query).strip()
    # Prefix each token with + so Lucene treats them as required (AND mode).
    tokens = [t for t in clean.split() if len(t) > 2]
    return " ".join(f"+{t}" for t in tokens) if tokens else clean


def exact_lookup(driver: Driver, name: str) -> list[dict]:
    """Ingredient CONTAINS search + fulltext fallback on run objective/synthesis/name."""
    seen: set[str] = set()
    results: list[dict] = []

    with driver.session() as session:
        # 1. Ingredient name match
        for r in session.run(_INGREDIENT_CYPHER, name=name):
            d = dict(r)
            if d["run_id"] not in seen:
                seen.add(d["run_id"])
                results.append(d)

        # 2. Fulltext on run fields
        safe_query = _sanitize(name)
        if safe_query:
            for r in session.run(_FULLTEXT_CYPHER, parameters={"query": safe_query}):
                d = dict(r)
                if d["run_id"] not in seen:
                    seen.add(d["run_id"])
                    results.append(d)

    return results
