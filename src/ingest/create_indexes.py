"""Create Neo4j constraints and indexes (idempotent, IF NOT EXISTS)."""

from __future__ import annotations

import logging

from neo4j import Driver, GraphDatabase

from src.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

logger = logging.getLogger(__name__)

_DDL: list[tuple[str, str]] = [
    (
        "constraint:exp_id",
        "CREATE CONSTRAINT exp_id IF NOT EXISTS FOR (e:Experiment) REQUIRE e.id IS UNIQUE",
    ),
    (
        "constraint:run_id",
        "CREATE CONSTRAINT run_id IF NOT EXISTS FOR (r:Run) REQUIRE r.id IS UNIQUE",
    ),
    (
        "constraint:ingredient_name",
        "CREATE CONSTRAINT ingredient_name IF NOT EXISTS"
        " FOR (i:Ingredient) REQUIRE i.name IS UNIQUE",
    ),
    (
        "index:chunk_fulltext",
        "CREATE FULLTEXT INDEX chunk_fulltext IF NOT EXISTS"
        " FOR (c:Chunk) ON EACH [c.text]",
    ),
    (
        "index:run_fulltext",
        "CREATE FULLTEXT INDEX run_fulltext IF NOT EXISTS"
        " FOR (r:Run) ON EACH [r.objective, r.synthesis, r.name]",
    ),
    (
        "index:chunk_embedding",
        "CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS"
        " FOR (c:Chunk) ON (c.embedding)"
        " OPTIONS { indexConfig: {"
        " `vector.dimensions`: 1536,"
        " `vector.similarity_function`: 'cosine' } }",
    ),
]


def create_indexes(driver: Driver) -> int:
    with driver.session() as session:
        for name, cypher in _DDL:
            session.run(cypher)
            logger.info("Applied %s", name)
    return len(_DDL)


_INDEX_NAMES: list[str] = [
    "exp_id",
    "run_id",
    "ingredient_name",
    "chunk_fulltext",
    "run_fulltext",
    "chunk_embedding",
]


def verify_indexes(driver: Driver) -> list[dict]:
    with driver.session() as session:
        result = session.run(
            "SHOW INDEXES YIELD name, type, state"
            " WHERE name IN $names RETURN name, type, state",
            names=_INDEX_NAMES,
        )
        return [dict(r) for r in result]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        count = create_indexes(driver)
        logger.info("Applied %d DDL statements", count)
        indexes = verify_indexes(driver)
        for idx in indexes:
            state = idx.get("state", "?")
            if state != "ONLINE":
                logger.warning("Index %s is %s (not ONLINE)", idx.get("name"), state)
            else:
                logger.info("  %s [%s] %s", idx.get("name"), idx.get("type"), state)
        logger.info("Total reported: %d / 6 expected", len(indexes))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
