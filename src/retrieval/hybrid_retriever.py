"""Two-branch retriever: hybrid (no filter) or chantier-filtered dense vector."""

from __future__ import annotations

import neo4j
from neo4j import Driver
from neo4j_graphrag.embeddings.base import Embedder
from neo4j_graphrag.retrievers import HybridCypherRetriever
from openai import OpenAI

from src.config import (
    EMBEDDING_DIMS,
    EMBEDDING_MODEL,
    TOP_K_DEFAULT,
)

# Exact Cypher from SPEC §7 — do not modify without updating the spec.
_RETRIEVAL_CYPHER = """
MATCH (node)<-[:HAS_CHUNK]-(run:Run)
OPTIONAL MATCH (run)<-[:HAS_RUN]-(exp:Experiment)
OPTIONAL MATCH (run)-[:USES_INGREDIENT]->(ing:Ingredient)
OPTIONAL MATCH (run)-[:BELONGS_TO]->(chantier:Chantier)
RETURN node.text AS text, run.id AS run_id, run.name AS run_name,
       run.objective AS objective, run.synthesis AS synthesis, run.date AS date,
       exp.id AS experiment_id, collect(DISTINCT ing.name) AS ingredients,
       chantier.name AS chantier, score
ORDER BY score DESC
"""

# Chantier branch: pre-filter by chantier then rank by dense vector score.
_CHANTIER_CYPHER = """
CALL db.index.vector.queryNodes('chunk_embedding', $top_k, $query_vector)
YIELD node, score
WHERE node.chantier = $chantier
MATCH (node)<-[:HAS_CHUNK]-(run:Run)
OPTIONAL MATCH (run)<-[:HAS_RUN]-(exp:Experiment)
OPTIONAL MATCH (run)-[:USES_INGREDIENT]->(ing:Ingredient)
OPTIONAL MATCH (run)-[:BELONGS_TO]->(ch:Chantier)
RETURN node.text AS text, run.id AS run_id, run.name AS run_name,
       run.objective AS objective, run.synthesis AS synthesis, run.date AS date,
       exp.id AS experiment_id, collect(DISTINCT ing.name) AS ingredients,
       ch.name AS chantier, score
ORDER BY score DESC
"""


class _OpenAIEmbedder(Embedder):
    """Thin wrapper that enforces dimensions=1536 on every embed call."""

    def __init__(self, client: OpenAI) -> None:
        super().__init__()
        self._client = client

    def embed_query(self, text: str) -> list[float]:
        response = self._client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
            dimensions=EMBEDDING_DIMS,
        )
        return response.data[0].embedding


def _to_dict(record: neo4j.Record) -> dict:
    return {
        "text": record["text"],
        "run_id": record["run_id"],
        "run_name": record["run_name"],
        "experiment_id": record["experiment_id"],
        "objective": record["objective"],
        "synthesis": record["synthesis"],
        "date": record["date"],
        "ingredients": list(record["ingredients"]),
        "chantier": record["chantier"],
        "score": record["score"],
    }


class HybridNeo4jRetriever:
    """IRetriever-compatible retriever backed by Neo4j vector + fulltext indexes.

    Two branches:
    - No chantier filter → HybridCypherRetriever (vector + fulltext, naive ranker).
    - chantier filter    → dense vector query on the chantier subset only.
    """

    def __init__(self, driver: Driver, openai_client: OpenAI) -> None:
        self._driver = driver
        self._embedder = _OpenAIEmbedder(openai_client)
        self._hybrid = HybridCypherRetriever(
            driver=driver,
            vector_index_name="chunk_embedding",
            fulltext_index_name="chunk_fulltext",
            embedder=self._embedder,
            retrieval_query=_RETRIEVAL_CYPHER,
        )

    def search(
        self, query: str, top_k: int = TOP_K_DEFAULT, filters: dict | None = None
    ) -> list[dict]:
        if filters and filters.get("chantier"):
            return self._search_filtered(query, top_k, filters["chantier"])
        return self._search_hybrid(query, top_k)

    def _search_hybrid(self, query: str, top_k: int) -> list[dict]:
        result = self._hybrid.get_search_results(query_text=query, top_k=top_k)
        return [_to_dict(r) for r in result.records]

    def _search_filtered(self, query: str, top_k: int, chantier: str) -> list[dict]:
        query_vector = self._embedder.embed_query(query)
        with self._driver.session() as session:
            result = session.run(
                _CHANTIER_CYPHER,
                query_vector=query_vector,
                chantier=chantier,
                top_k=top_k,
            )
            return [_to_dict(r) for r in result]
