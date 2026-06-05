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
from src.retrieval.sharepoint_urls import get_sharepoint_url, get_sharepoint_url_for_run

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

    def _generate(self, context: str, question: str) -> tuple[str, int, int]:
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
        return (
            response.content[0].text,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

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
            sources = _build_sources(exact_rows, self._driver, is_exact=True)
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
            sources = _build_sources(chunks, self._driver, is_exact=False)

        # ── Generation ────────────────────────────────────────────────────────
        answer, in_tok, out_tok = self._generate(context, question)

        # ── Citation verification ─────────────────────────────────────────────
        cited = extract_cited_ids(answer)
        if not cited and FALLBACK_MESSAGE not in answer:
            answer, in2, out2 = self._generate(context, question + _REGEN_SUFFIX)
            in_tok += in2
            out_tok += out2
        answer = self._verify_citations(answer, valid_ids)

        return QueryResponse(
            answer=answer,
            sources=sources,
            found_in_corpus=True,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _format_hybrid_context(chunks: list[dict]) -> str:
    parts = []
    for c in chunks:
        header = f"[Source: {c['run_id']}]"
        if c.get("date"):
            header += f" — {c['date']}"
        lines = [header]
        if c.get("objective"):
            lines.append(f"Objectif: {c['objective']}")
        if c.get("synthesis"):
            lines.append(f"Synthèse: {c['synthesis']}")
        if c.get("ingredients"):
            lines.append(f"Ingrédients: {', '.join(c['ingredients'])}")
        lines.append(c["text"])
        parts.append("\n".join(lines))
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


import logging as _logging  # noqa: E402 — kept near usage

_log = _logging.getLogger(__name__)


def _fetch_urls_from_neo4j(driver: Driver, exp_ids: list[str]) -> dict[str, str]:
    """Single batched query: exp_id → sharepoint_url for all requested IDs."""
    if not exp_ids:
        return {}
    try:
        with driver.session() as session:
            records = session.run(
                "MATCH (e:Experiment) WHERE e.id IN $ids AND e.sharepoint_url IS NOT NULL "
                "RETURN e.id AS id, e.sharepoint_url AS url",
                ids=exp_ids,
            ).data()
            return {r["id"]: r["url"] for r in records if r["url"]}
    except Exception as exc:
        _log.debug("Neo4j URL lookup failed: %s", exc)
        return {}


def _build_sources(items: list[dict], driver: Driver, is_exact: bool) -> list[Source]:
    """Build Source list with a single batched Neo4j URL lookup."""
    exp_ids = list({(item.get("experiment_id") or "") for item in items})
    neo4j_urls = _fetch_urls_from_neo4j(driver, [e for e in exp_ids if e])

    sources = []
    for item in items:
        exp_id = item.get("experiment_id") or ""
        run_id = item["run_id"]
        # URL priority: Neo4j → run prefix deep link → static fallback
        url = (
            neo4j_urls.get(exp_id)
            or get_sharepoint_url_for_run(run_id)
            or get_sharepoint_url(exp_id)
        )
        sources.append(Source(
            run_id=run_id,
            experiment_id=exp_id,
            source_file=f"{exp_id}_documentation.md" if exp_id else "",
            score=0.0 if is_exact else float(item.get("score") or 0.0),
            name=item.get("run_name") or "",
            sharepoint_url=url,
        ))
    return sources


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
