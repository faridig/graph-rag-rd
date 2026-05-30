"""RAG pipeline: dense gate → exact fallback → hybrid search → Claude generation."""

from __future__ import annotations

import re

import anthropic
from neo4j import Driver
from openai import OpenAI

from src.config import (
    ANTHROPIC_API_KEY,
    FALLBACK_MESSAGE,
    LLM_MODEL,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    OPENAI_API_KEY,
    SCORE_THRESHOLD,
    TOP_K_DEFAULT,
)
from src.generation.prompt_fr import SYSTEM_PROMPT
from src.ingest.embed_chunks import embed_text
from src.models import QueryResponse, Source
from src.retrieval.exact_lookup import exact_lookup
from src.retrieval.hybrid_retriever import HybridNeo4jRetriever

_CITATION_RE = re.compile(r"\[source:\s*([^\]]+)\]", re.IGNORECASE)

_DENSE_GATE_CYPHER = """
CALL db.index.vector.queryNodes('chunk_embedding', 1, $query_vector)
YIELD node, score
RETURN score
"""

_REGEN_SUFFIX = (
    "\n\nATTENTION : votre réponse ne contient aucun marqueur [source: ...]. "
    "Reformulez en citant OBLIGATOIREMENT chaque affirmation avec [source: <run_id>]."
)


def extract_cited_ids(text: str) -> set[str]:
    return {m.group(1).strip() for m in _CITATION_RE.finditer(text)}


class RAGPipeline:
    def __init__(
        self,
        driver: Driver,
        openai_client: OpenAI,
        anthropic_client: anthropic.Anthropic,
    ) -> None:
        self._driver = driver
        self._openai = openai_client
        self._anthropic = anthropic_client
        self._retriever = HybridNeo4jRetriever(driver, openai_client)

    def _dense_score(self, query_vector: list[float]) -> float:
        with self._driver.session() as session:
            record = session.run(
                _DENSE_GATE_CYPHER, query_vector=query_vector
            ).single()
            return float(record["score"]) if record else 0.0

    def _generate(self, context: str, question: str) -> str:
        response = self._anthropic.messages.create(
            model=LLM_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Contexte :\n{context}\n\nQuestion : {question}",
                }
            ],
        )
        return response.content[0].text

    def _verify_citations(self, answer: str, valid_ids: set[str]) -> str:
        """Strip [source: id] markers whose id is not in valid_ids."""

        def _keep_or_drop(m: re.Match) -> str:
            return m.group(0) if m.group(1).strip() in valid_ids else ""

        return _CITATION_RE.sub(_keep_or_drop, answer).strip()

    def run(
        self,
        question: str,
        top_k: int = TOP_K_DEFAULT,
        chantier: str | None = None,
    ) -> QueryResponse:
        query_vector = embed_text(self._openai, question)
        dense_score = self._dense_score(query_vector)

        # ── Dense gate ────────────────────────────────────────────────────────
        if dense_score < SCORE_THRESHOLD:
            exact_rows = exact_lookup(self._driver, question)
            if not exact_rows:
                return QueryResponse(
                    answer=FALLBACK_MESSAGE,
                    sources=[],
                    found_in_corpus=False,
                )
            # Exact match found: use as context
            context = _format_exact_context(exact_rows)
            valid_ids = {r["run_id"] for r in exact_rows}
            sources = [_source_from_exact(r) for r in exact_rows]
        else:
            # ── Hybrid search ─────────────────────────────────────────────────
            filters = {"chantier": chantier} if chantier else None
            chunks = self._retriever.search(question, top_k=top_k, filters=filters)
            if not chunks:
                return QueryResponse(
                    answer=FALLBACK_MESSAGE,
                    sources=[],
                    found_in_corpus=False,
                )
            context = _format_hybrid_context(chunks)
            valid_ids = {c["run_id"] for c in chunks}
            sources = [_source_from_chunk(c) for c in chunks]

        # ── Generation ────────────────────────────────────────────────────────
        answer = self._generate(context, question)

        # ── Citation verification ─────────────────────────────────────────────
        cited = extract_cited_ids(answer)
        if not cited:
            # Regenerate once with explicit citation instruction
            answer = self._generate(context, question + _REGEN_SUFFIX)
        answer = self._verify_citations(answer, valid_ids)

        return QueryResponse(
            answer=answer,
            sources=sources,
            found_in_corpus=True,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _format_hybrid_context(chunks: list[dict]) -> str:
    parts = [f"[Source: {c['run_id']}]\n{c['text']}" for c in chunks]
    return "\n---\n".join(parts)


def _format_exact_context(rows: list[dict]) -> str:
    parts = []
    for r in rows:
        lines = [f"[Source: {r['run_id']}]"]
        if r.get("objective"):
            lines.append(f"Objectif: {r['objective']}")
        if r.get("synthesis"):
            lines.append(f"Synthèse: {r['synthesis']}")
        if r.get("ingredient_match"):
            lines.append(f"Ingrédient: {r['ingredient_match']}")
        parts.append("\n".join(lines))
    return "\n---\n".join(parts)


def _source_from_chunk(chunk: dict) -> Source:
    exp_id = chunk.get("experiment_id") or ""
    return Source(
        run_id=chunk["run_id"],
        experiment_id=exp_id,
        source_file=f"{exp_id}_documentation.md" if exp_id else "",
        score=float(chunk.get("score") or 0.0),
        name=chunk.get("run_name") or "",
    )


def _source_from_exact(row: dict) -> Source:
    exp_id = row.get("experiment_id") or ""
    return Source(
        run_id=row["run_id"],
        experiment_id=exp_id,
        source_file=f"{exp_id}_documentation.md" if exp_id else "",
        score=0.0,
        name=row.get("run_name") or "",
    )


# ── Public API ────────────────────────────────────────────────────────────────


def run_query(
    pipeline: RAGPipeline,
    question: str,
    top_k: int = TOP_K_DEFAULT,
    chantier: str | None = None,
) -> QueryResponse:
    return pipeline.run(question, top_k=top_k, chantier=chantier)


def get_dense_score(pipeline: RAGPipeline, query: str) -> float:
    vec = embed_text(pipeline._openai, query)
    return pipeline._dense_score(vec)


def build_pipeline() -> RAGPipeline:
    """Convenience factory using env-configured credentials."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return RAGPipeline(driver, openai_client, anthropic_client)
