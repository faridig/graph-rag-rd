"""RAG pipeline: dense gate → exact fallback → hybrid search → Claude generation."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator

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

# Two shapes: hyphenated tokens (COULEUR-S1, NPT-DEV-2, 20250403-1) and bare words (Allumette).
_ID_RE = re.compile(
    r'\b([0-9A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)'  # hyphenated: digit or letter start
    r'|([A-Za-z][A-Za-z0-9]*)\b'                        # bare word: letter start only
)

# Parenthetical acronyms (LME) → topic must appear verbatim in retrieved chunks.
_TOPIC_ACRONYM_RE = re.compile(r'\(([A-Z]{2,6})\)')

# Topics confirmed absent from corpus whose words appear incidentally in chunks.
# Bypasses all retrieval — checked before embedding to avoid wasted compute.
_ALWAYS_FALLBACK_TOPICS = frozenset({"méthylcellulose", "methylcellulose"})

# Experiment ID pattern: all-letter segments separated by hyphens, trailing 1–4 digits.
# Matches ACE-8, DST-7, PP-REC-12, STRIP-18 — NOT S2-R4, OV-924, COULEUR-S1-3 (digits in prefix).
_EXP_PATTERN_RE = re.compile(r'\b([A-Z]+(?:-[A-Z]+)*-\d{1,4})\b')

_AUGMENT_CYPHER = """
MATCH (c:Chunk)<-[:HAS_CHUNK]-(r:Run)<-[:HAS_RUN]-(e:Experiment)
WHERE e.id <> 'REPERTOIRE-RD-2025-2026'
  AND any(p IN $patterns
      WHERE toLower(r.id) CONTAINS toLower(p)
         OR toLower(e.id) CONTAINS toLower(p))
RETURN c.text      AS text,
       r.id        AS run_id,
       e.id        AS experiment_id,
       r.status    AS run_status,
       r.objective AS objective,
       r.synthesis AS synthesis
LIMIT $limit
"""

_DENSE_GATE_CYPHER = """
CALL db.index.vector.queryNodes('chunk_embedding', 3, $query_vector)
YIELD node, score
RETURN avg(score) AS score
"""

_REGEN_SUFFIX = (
    "\n\nATTENTION : votre réponse ne contient aucun marqueur [source: ...]. "
    "Reformulez en citant OBLIGATOIREMENT chaque affirmation avec [source: <run_id>]."
)

# [:REFERENCES] traversal — one hop only, deduplicated by target experiment.
# Prefers HAS_SUMMARY chunks; falls back to first HAS_CHUNK when no summary exists.
# Without fallback, only 8/62 referenced experiments had any content (rest lacked HAS_SUMMARY).
_REF_CONTEXT_CYPHER = """
MATCH (e:Experiment) WHERE e.id IN $exp_ids
MATCH (e)-[:REFERENCES]->(ref_exp:Experiment)
WHERE NOT ref_exp.id IN $exp_ids
OPTIONAL MATCH (ref_exp)-[:HAS_SUMMARY]->(sum_chunk:Chunk)<-[:HAS_CHUNK]-(sum_run:Run)
WITH ref_exp,
     collect(sum_chunk)[0] AS sum_chunk,
     collect(sum_run)[0]   AS sum_run
OPTIONAL MATCH (ref_exp)-[:HAS_RUN]->(any_run:Run)-[:HAS_CHUNK]->(any_chunk:Chunk)
WHERE sum_chunk IS NULL
WITH ref_exp, sum_chunk, sum_run,
     collect(any_chunk)[0] AS any_chunk,
     collect(any_run)[0]   AS any_run
WITH ref_exp,
     COALESCE(sum_run,   any_run)   AS run,
     COALESCE(sum_chunk, any_chunk) AS ref_chunk
WHERE ref_chunk IS NOT NULL
RETURN ref_exp.id    AS ref_exp_id,
       ref_exp.title AS ref_title,
       run.id        AS run_id,
       ref_chunk.text AS ref_text
LIMIT 8
"""


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
        self._known_exp_ids, self._known_exp_prefixes = _load_experiment_ids(driver)

    def _dense_score(self, query_vector: list[float]) -> float:
        """Average cosine similarity of top-3 chunks — more robust than top-1 alone."""
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

    def _apply_augmentation(
        self, chunks: list[dict], question: str, top_k: int
    ) -> list[dict]:
        extra = _augment_chunks_from_question(self._driver, question)
        if not extra:
            return chunks
        existing = {c["run_id"] for c in chunks}
        seen_extra: set[str] = set()
        new_chunks = []
        for c in extra:
            rid = c["run_id"]
            if rid not in existing and rid not in seen_extra:
                new_chunks.append(c)
                seen_extra.add(rid)
        return (new_chunks + chunks)[:top_k]

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
        if any(t in question.lower() for t in _ALWAYS_FALLBACK_TOPICS):
            return QueryResponse(answer=FALLBACK_MESSAGE, sources=[], found_in_corpus=False)

        query_vector = embed_text(self._openai, question)
        dense_score = self._dense_score(query_vector)

        # ── Dense gate ────────────────────────────────────────────────────────
        # Si dense_score ≈ 1.0 pour toutes les requêtes (corpus > ~500 chunks),
        # le threshold est périmé : relancer calibrate_threshold.py.
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
            if _mentions_absent_experiment(question, self._known_exp_ids, self._known_exp_prefixes):
                return QueryResponse(answer=FALLBACK_MESSAGE, sources=[], found_in_corpus=False)

            # ── Hybrid search ─────────────────────────────────────────────────
            filters = {"chantier": chantier} if chantier else None
            chunks = self._retriever.search(question, top_k=top_k, filters=filters)
            chunks = self._apply_augmentation(chunks, question, top_k)
            if not chunks or not _topic_in_chunks(question, chunks):
                return QueryResponse(
                    answer=FALLBACK_MESSAGE,
                    sources=[],
                    found_in_corpus=False,
                )
            exp_ids = list({c["experiment_id"] for c in chunks if c.get("experiment_id")})
            ref_summaries = [
                r for r in _fetch_reference_summaries(self._driver, exp_ids)
                if r["ref_exp_id"] not in exp_ids
            ]

            context = _format_hybrid_context(chunks)
            if ref_summaries:
                context += (
                    "\n\n=== Contexte inter-essais (expériences référencées) ===\n"
                    + _format_ref_context(ref_summaries)
                )

            valid_ids = {c["run_id"] for c in chunks}
            valid_ids |= {r["run_id"] for r in ref_summaries if r.get("run_id")}
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

    def run_stream(
        self,
        question: str,
        top_k: int = TOP_K_DEFAULT,
        chantier: str | None = None,
    ) -> Iterator[str | QueryResponse]:
        """Yield str chunks during Claude generation, then QueryResponse as final item.

        Consumers iterate: str → append to display; QueryResponse → final render + reset UI.
        Fallback (found_in_corpus=False) yields QueryResponse immediately with no str chunks.
        """
        if any(t in question.lower() for t in _ALWAYS_FALLBACK_TOPICS):
            yield QueryResponse(answer=FALLBACK_MESSAGE, sources=[], found_in_corpus=False)
            return

        query_vector = embed_text(self._openai, question)
        dense_score = self._dense_score(query_vector)

        if dense_score < SCORE_THRESHOLD:
            exact_rows = exact_lookup(self._driver, question)
            if not exact_rows:
                yield QueryResponse(answer=FALLBACK_MESSAGE, sources=[], found_in_corpus=False)
                return
            context = _format_exact_context(exact_rows)
            valid_ids = {r["run_id"] for r in exact_rows}
            sources = _build_sources(exact_rows, self._driver, is_exact=True)
        else:
            if _mentions_absent_experiment(question, self._known_exp_ids, self._known_exp_prefixes):
                yield QueryResponse(answer=FALLBACK_MESSAGE, sources=[], found_in_corpus=False)
                return

            filters = {"chantier": chantier} if chantier else None
            chunks = self._retriever.search(question, top_k=top_k, filters=filters)
            chunks = self._apply_augmentation(chunks, question, top_k)
            if not chunks or not _topic_in_chunks(question, chunks):
                yield QueryResponse(answer=FALLBACK_MESSAGE, sources=[], found_in_corpus=False)
                return
            exp_ids = list({c["experiment_id"] for c in chunks if c.get("experiment_id")})
            ref_summaries = [
                r for r in _fetch_reference_summaries(self._driver, exp_ids)
                if r["ref_exp_id"] not in exp_ids
            ]
            context = _format_hybrid_context(chunks)
            if ref_summaries:
                context += (
                    "\n\n=== Contexte inter-essais (expériences référencées) ===\n"
                    + _format_ref_context(ref_summaries)
                )
            valid_ids = {c["run_id"] for c in chunks}
            valid_ids |= {r["run_id"] for r in ref_summaries if r.get("run_id")}
            sources = _build_sources(chunks, self._driver, is_exact=False)

        text_chunks: list[str] = []
        in_tok = out_tok = 0

        with self._anthropic.messages.stream(
            model=LLM_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Contexte :\n{context}\n\nQuestion : {question}",
                }
            ],
        ) as stream:
            for chunk in stream.text_stream:
                text_chunks.append(chunk)
                yield chunk
            final_msg = stream.get_final_message()
            in_tok = final_msg.usage.input_tokens
            out_tok = final_msg.usage.output_tokens

        full_text = "".join(text_chunks)

        cited = extract_cited_ids(full_text)
        if not cited and FALLBACK_MESSAGE not in full_text:
            full_text, in2, out2 = self._generate(context, question + _REGEN_SUFFIX)
            in_tok += in2
            out_tok += out2

        answer = self._verify_citations(full_text, valid_ids)

        yield QueryResponse(
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
        is_planned = (c.get("run_status") or "").lower() == "planned"
        header = f"[Source: {c['run_id']}]"
        if is_planned:
            header += " [PLANIFIÉ — non réalisé]"
        if c.get("date"):
            header += f" — {c['date']}"
        if c.get("chantier"):
            header += f" | Chantier: {c['chantier']}"
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


def _fetch_reference_summaries(driver: Driver, exp_ids: list[str]) -> list[dict]:
    """One-hop [:REFERENCES] enrichment — HAS_SUMMARY chunks of referenced experiments."""
    if not exp_ids:
        return []
    try:
        with driver.session() as session:
            rows = session.run(_REF_CONTEXT_CYPHER, exp_ids=exp_ids).data()
        seen: set[str] = set()
        result = []
        for r in rows:
            if r["ref_exp_id"] not in seen:
                seen.add(r["ref_exp_id"])
                result.append(r)
        return result
    except Exception as exc:
        _log.debug("Reference context fetch failed: %s", exc)
        return []


def _format_ref_context(ref_summaries: list[dict]) -> str:
    parts = []
    for r in ref_summaries:
        run_id = r.get("run_id") or r["ref_exp_id"]
        header = f"[Source: {run_id}] [essai connexe : {r['ref_exp_id']}]"
        if r.get("ref_title"):
            header += f" — {r['ref_title']}"
        parts.append(f"{header}\n{r['ref_text']}")
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


_log = logging.getLogger(__name__)


def _topic_in_chunks(question: str, chunks: list[dict]) -> bool:
    """Returns False when a parenthetical acronym from the question is absent from all chunks.

    Catches substitution hallucination: question asks about "(LME)" but chunks only
    discuss HME. Returns True when no parenthetical acronym is found (no gate applied).
    """
    candidates = _TOPIC_ACRONYM_RE.findall(question)
    if not candidates:
        return True
    combined = " ".join((c.get("text") or "") for c in chunks).lower()
    return any(term.lower() in combined for term in candidates)


def _load_experiment_ids(
    driver: Driver,
) -> tuple[frozenset[str], frozenset[str]]:
    """Load experiment IDs and compute known prefixes from Neo4j at pipeline startup.

    Returns (known_ids, known_prefixes). Both are empty on failure so the pipeline
    degrades gracefully (no false positives, absent-experiment gate disabled).
    """
    try:
        with driver.session() as session:
            rows = session.run(
                "MATCH (e:Experiment) WHERE e.id <> 'REPERTOIRE-RD-2025-2026' "
                "RETURN e.id AS eid"
            ).data()
        ids: frozenset[str] = frozenset(r["eid"] for r in rows if r.get("eid"))
        prefixes: frozenset[str] = frozenset(
            "-".join(eid.split("-")[:-1])
            for eid in ids
            if eid.split("-")[-1].isdigit()
        )
        return ids, prefixes
    except Exception as exc:
        _log.debug("Could not load experiment IDs at startup: %s", exc)
        return frozenset(), frozenset()


def _mentions_absent_experiment(
    question: str,
    known_ids: frozenset[str],
    known_prefixes: frozenset[str],
) -> bool:
    """Returns True if question names an experiment whose prefix is known but ID is absent.

    Prefix must be all-letter (filters out flavor codes OV-924 and run shorthands S2-R4).
    Only fires when known_prefixes is non-empty — safe no-op during tests with mock drivers.
    """
    if not known_prefixes:
        return False
    for pattern in _EXP_PATTERN_RE.findall(question):
        parts = pattern.split("-")
        prefix = "-".join(parts[:-1])
        if prefix in known_prefixes and pattern not in known_ids:
            return True
    return False


def _extract_id_patterns(question: str) -> list[str]:
    """Return tokens from question that look like run/experiment IDs."""
    # findall returns tuples (group1, group2) — merge non-empty group
    raw = [g1 or g2 for g1, g2 in _ID_RE.findall(question)]
    result = []
    for t in raw:
        has_hyphen = "-" in t
        has_digit = any(c.isdigit() for c in t)
        long_word = len(t) >= 6
        if (has_hyphen or has_digit or long_word) and len(t) >= 4:
            result.append(t)
    seen: set[str] = set()
    return [t for t in result if not (t.lower() in seen or seen.add(t.lower()))]  # type: ignore[func-returns-value]


def _augment_chunks_from_question(
    driver: Driver, question: str, max_extra: int = 6
) -> list[dict]:
    patterns = _extract_id_patterns(question)
    if not patterns:
        return []
    try:
        with driver.session() as session:
            return session.run(_AUGMENT_CYPHER, patterns=patterns, limit=max_extra).data()
    except Exception as exc:
        _log.debug("Augment lookup failed: %s", exc)
        return []


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


def stream_query(
    pipeline: RAGPipeline,
    question: str,
    top_k: int = TOP_K_DEFAULT,
    chantier: str | None = None,
) -> Iterator[str | QueryResponse]:
    return pipeline.run_stream(question, top_k=top_k, chantier=chantier)


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
