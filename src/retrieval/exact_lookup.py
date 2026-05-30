"""Exact fallback: ingredient name CONTAINS search via Cypher (SPEC §7)."""

from __future__ import annotations

from neo4j import Driver


def exact_lookup(driver: Driver, name: str) -> list[dict]:
    """Return runs that use an ingredient whose name contains `name` (case-insensitive)."""
    with driver.session() as session:
        result = session.run(
            """
            MATCH (i:Ingredient) WHERE toLower(i.name) CONTAINS toLower($name)
            MATCH (run:Run)-[:USES_INGREDIENT]->(i)
            OPTIONAL MATCH (run)<-[:HAS_RUN]-(exp:Experiment)
            OPTIONAL MATCH (run)-[:BELONGS_TO]->(chantier:Chantier)
            RETURN DISTINCT run.id AS run_id, run.name AS run_name,
                   exp.id AS experiment_id,
                   run.objective AS objective, run.synthesis AS synthesis,
                   run.date AS date, chantier.name AS chantier,
                   i.name AS ingredient_match
            """,
            name=name,
        )
        return [dict(r) for r in result]
