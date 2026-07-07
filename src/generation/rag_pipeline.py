"""RAG pipeline: dense gate → exact fallback → hybrid search → Claude generation."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator

from anthropic import Anthropic
from neo4j import Driver
from openai import OpenAI

from src.config import (
    ABSENT_TOPICS_PATH,
    ANTHROPIC_API_KEY,
    FALLBACK_MESSAGE,
    LLM_MODEL,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    OPENAI_API_KEY,
    RAG_IDS_CACHE_TTL,
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

# Ingredient token extraction: words ≥7 alpha chars to avoid common French function words.
# ≥5 causes false positives: ingredient names like "Beurre De Karité Comme Substitut"
# inject "comme" into the token set, which then matches any French question.
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ]{7,}")

# French common words that appear in some ingredient names but are not product identifiers.
# Without this guard, a question mentioning "formulations" triggers VEILLE-4 (ingredient named
# "Note: Deux Formulations De Marinade Testées En Parallèle") as a false positive.
_INGREDIENT_STOPWORDS = frozenset(
    {
        "formulations",
        "parallèle",
        "testées",
        "testés",
    }
)

# Patterns indiquant que le LLM n'a pas trouvé la donnée dans le contexte récupéré.
# Calibrés sur les 15 réponses AR=0.00 de l'eval v3 (2026-06-07) + DeepSeek (2026-06-08).
_NO_DATA_PATTERNS: tuple[str, ...] = (
    "pas présent dans le contexte",
    "ne figure pas dans le contexte",
    "ne figurent pas dans le contexte",
    "ne contient pas de",
    "Je ne suis pas en mesure de répondre",
    "Je ne peux pas répondre à cette question",
    "Limites de réponse",
    "n'est pas mentionné dans les sources",
    "aucune information relative à",
    "ne permettent pas de répondre à cette question",
    "n'est pas disponible dans le contexte",
    # DeepSeek-specific phrasings (ajoutés 2026-06-08)
    "aucune information n'est disponible",
    "aucune donnée n'est disponible",
    # Réponses négatives directes sans les mots-clés ci-dessus (ajoutés 2026-06-08)
    "aucune donnée sur",
    "aucune information sur",
    "n'est pas présent dans le contexte",
)

# Two shapes: hyphenated tokens (COULEUR-S1, NPT-DEV-2, 20250403-1) and bare words (Allumette).
_ID_RE = re.compile(
    r"\b([0-9A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)"  # hyphenated: digit or letter start
    r"|([A-Za-z][A-Za-z0-9]*)\b"  # bare word: letter start only
)

# Parenthetical acronyms (LME) → topic must appear verbatim in retrieved chunks.
_TOPIC_ACRONYM_RE = re.compile(r"\(([A-Z]{2,6})\)")

# Fallback seed: used when absent_topics.txt is missing or unreadable.
_FALLBACK_TOPICS_SEED = frozenset(
    {
        "méthylcellulose",
        "methylcellulose",
        "fermentation lactique",
    }
)

# Experiment ID pattern: all-letter segments separated by hyphens, trailing 1–4 digits.
# Matches ACE-8, DST-7, PP-REC-12, STRIP-18 — NOT S2-R4, OV-924, COULEUR-S1-3 (digits in prefix).
_EXP_PATTERN_RE = re.compile(r"\b([A-Z]+(?:-[A-Z]+)*-\d{1,4})\b")

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

# Measure-term augmentation: inject section-4 (## 4. Derived & computed values)
# when AI/SME/TPA/anisotropie in question but section-4 absent from hybrid results.
# Long tokens (≥4 chars) matched verbatim; short ones (ai, ph) need word boundaries.
_MEASURE_TERMS = frozenset({"anisotropie", "sme", "tpa"})
_MEASURE_TERMS_SHORT_RE = re.compile(r"\b(ai|ph)\b", re.IGNORECASE)

_MEASURE_SECTION_CYPHER = """
MATCH (c:Chunk)
WHERE c.experiment_id IN $exp_ids
  AND c.type = 'experiment_section'
  AND c.text CONTAINS '## 4'
  AND NOT c.run_id IN $already_run_ids
RETURN c.text          AS text,
       c.run_id        AS run_id,
       c.experiment_id AS experiment_id
LIMIT 1
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

# Phase 3 — Inverse [:REFERENCES] traversal: "qui référence X?" → traverse ←[:REFERENCES].
# Triggered when question contains a "référenc" verb AND a known/empty experiment ID as target.
_INVERSE_REF_CYPHER = """
MATCH (e:Experiment)-[:REFERENCES]->(target:Experiment)
WHERE target.id IN $target_ids
  AND NOT e.id IN $already_exp_ids
OPTIONAL MATCH (e)-[:HAS_SUMMARY]->(sum_chunk:Chunk)<-[:HAS_CHUNK]-(sum_run:Run)
WITH e, collect(sum_chunk)[0] AS sum_chunk, collect(sum_run)[0] AS sum_run
OPTIONAL MATCH (e)-[:HAS_RUN]->(any_run:Run)-[:HAS_CHUNK]->(any_chunk:Chunk)
WHERE sum_chunk IS NULL
WITH e,
     COALESCE(sum_run, any_run)     AS run,
     COALESCE(sum_chunk, any_chunk) AS chunk
WHERE chunk IS NOT NULL
RETURN e.id    AS exp_id,
       e.title AS exp_title,
       run.id  AS run_id,
       chunk.text AS text
LIMIT 6
"""

_INVERSE_REF_RE = re.compile(r"référenc", re.I)

# Phase 2 — Lexical "répertoire" trigger: detect keyword + experiment IDs → direct REPERTOIRE
# lookup + follow [:DETAILS] to full experiment. Deterministic; does not depend on hybrid results.
_REPERTOIRE_DIRECT_CYPHER = """
MATCH (rep_run:Run)<-[:HAS_RUN]-(rep_exp:Experiment {id: 'REPERTOIRE-RD-2025-2026'})
WHERE any(eid IN $exp_ids WHERE toUpper(rep_run.id) CONTAINS eid)
WITH rep_run
OPTIONAL MATCH (rep_run)-[:DETAILS]->(exp:Experiment)
OPTIONAL MATCH (exp)-[:HAS_SUMMARY]->(sum_chunk:Chunk)<-[:HAS_CHUNK]-(sum_run:Run)
WITH rep_run, exp, collect(sum_chunk)[0] AS sum_chunk, collect(sum_run)[0] AS sum_run
OPTIONAL MATCH (exp)-[:HAS_RUN]->(any_run:Run)-[:HAS_CHUNK]->(any_chunk:Chunk)
WHERE sum_chunk IS NULL
WITH rep_run, exp,
     COALESCE(sum_run, any_run)     AS detail_run,
     COALESCE(sum_chunk, any_chunk) AS detail_chunk
RETURN rep_run.id         AS rep_run_id,
       rep_run.objective  AS rep_objective,
       rep_run.synthesis  AS rep_synthesis,
       exp.id             AS exp_id,
       exp.title          AS exp_title,
       detail_run.id      AS run_id,
       detail_chunk.text  AS text
LIMIT 4
"""

_REPERTOIRE_RE = re.compile(r"r[ée]pertoire", re.I)

# Phase 1 — [:USES_INGREDIENT] traversal: question tokens → Ingredient → Run → Chunk.
# Limited to 6 candidates (caller takes max 2) to cap token overhead.
_INGREDIENT_CONTEXT_CYPHER = """
MATCH (i:Ingredient)<-[:USES_INGREDIENT]-(r:Run)<-[:HAS_RUN]-(e:Experiment)
WHERE any(token IN $tokens WHERE toLower(i.name) CONTAINS token)
  AND NOT r.id IN $already_run_ids
  AND e.id <> 'REPERTOIRE-RD-2025-2026'
WITH r, e, i ORDER BY r.date DESC LIMIT 6
OPTIONAL MATCH (r)-[:HAS_CHUNK]->(c:Chunk)
WITH r, e, i, collect(c)[0] AS chunk
WHERE chunk IS NOT NULL
RETURN r.id        AS run_id,
       e.id        AS experiment_id,
       i.name      AS ingredient,
       r.objective AS objective,
       r.synthesis AS synthesis,
       chunk.text  AS text
LIMIT 6
"""

# Phase 1b — [:USES_INGREDIENT] aggregate traversal for "quelles expériences ont utilisé X?"
# Returns experiment-level counts instead of individual run chunks.
_INGREDIENT_AGGREGATE_CYPHER = """
MATCH (i:Ingredient)<-[:USES_INGREDIENT]-(r:Run)<-[:HAS_RUN]-(e:Experiment)
WHERE any(token IN $tokens WHERE toLower(i.name) CONTAINS token)
  AND e.id <> 'REPERTOIRE-RD-2025-2026'
WITH e, i, count(r) AS nb_runs, collect(r.id)[0..3] AS sample_run_ids
RETURN e.id        AS experiment_id,
       e.title     AS experiment_title,
       i.name      AS ingredient,
       nb_runs,
       sample_run_ids
ORDER BY nb_runs DESC
"""

# Regex detecting "which experiments used X" intent.
_AGGREGATE_INGREDIENT_RE = re.compile(
    r"quell?es?\s+exp[eé]riences|quels?\s+essais\b|dans\s+quell?es?\s+exp",
    re.IGNORECASE,
)

# Phase 1.5 — Session-level context fetch.
# Detects explicit session prefixes (COULEUR-S1, GOUT-S2, etc.) in the question
# and fetches ALL run chunks from those sessions, bypassing the top_k cap.
# Pattern: uppercase letters + optional alphanumeric groups + "-S" + digits,
# optionally followed by a run-number suffix (-3, -4) that gets stripped to the prefix.
_SESSION_PREFIX_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-S\d+)(?:-\d+)?\b")

_SESSION_CONTEXT_CYPHER = """
MATCH (e:Experiment)-[:HAS_RUN]->(r:Run)-[:HAS_CHUNK]->(c:Chunk)
WHERE any(pfx IN $prefixes
    WHERE r.id CONTAINS (':Run:' + pfx + '-')
       OR r.id ENDS WITH (':Run:' + pfx))
  AND e.id <> 'REPERTOIRE-RD-2025-2026'
RETURN r.id AS run_id, e.id AS experiment_id, c.text AS text,
       c.type AS chunk_type, e.title AS experiment_title
ORDER BY r.id, c.type
LIMIT 30
"""

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


def _is_no_data_response(answer: str) -> bool:
    """True si le LLM signale que la donnée demandée est absente du contexte récupéré.

    Garde : retourne False si des citations valides existent — une réponse
    partielle avec [source: ...] n'est pas un refus pur, même si elle mentionne
    qu'une partie des données est manquante.
    """
    if extract_cited_ids(answer):
        return False
    lower = answer.lower()
    return any(p.lower() in lower for p in _NO_DATA_PATTERNS)


class RAGPipeline:
    def __init__(
        self,
        driver: Driver,
        openai_client: OpenAI,
        anthropic_client: Anthropic,
    ) -> None:
        self._driver = driver
        self._openai = openai_client
        self._llm = anthropic_client
        self._retriever = HybridNeo4jRetriever(driver, openai_client)
        self._known_exp_ids, self._known_exp_prefixes, self._empty_exp_ids = _load_experiment_ids(
            driver
        )
        self._absent_topics = _load_absent_topics()
        self._ingredient_tokens = _load_ingredient_tokens(driver)
        self._ids_loaded_at: float = time.monotonic()

    def _maybe_reload_ids(self) -> None:
        """Reload experiment ID sets and absent topics if TTL has expired."""
        if RAG_IDS_CACHE_TTL <= 0:
            return
        if time.monotonic() - self._ids_loaded_at < RAG_IDS_CACHE_TTL:
            return
        self._known_exp_ids, self._known_exp_prefixes, self._empty_exp_ids = _load_experiment_ids(
            self._driver
        )
        self._absent_topics = _load_absent_topics()
        self._ingredient_tokens = _load_ingredient_tokens(self._driver)
        self._ids_loaded_at = time.monotonic()
        _log.debug("Reloaded experiment ID sets and absent topics (TTL=%ds)", RAG_IDS_CACHE_TTL)

    def _dense_score(self, query_vector: list[float]) -> float:
        """Average cosine similarity of top-3 chunks — more robust than top-1 alone."""
        with self._driver.session() as session:
            record = session.run(_DENSE_GATE_CYPHER, query_vector=query_vector).single()
            return float(record["score"]) if record else 0.0

    def _generate(
        self,
        context: str,
        question: str,
        history: list[dict] | None = None,
    ) -> tuple[str, int, int]:
        history_messages = _format_history_messages(history) if history else []
        response = self._llm.messages.create(
            model=LLM_MODEL,
            max_tokens=128000,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[
                *history_messages,
                {"role": "user", "content": f"Contexte :\n{context}\n\nQuestion : {question}"},
            ],
        )
        return (
            response.content[0].text,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

    def _apply_augmentation(self, chunks: list[dict], question: str, top_k: int) -> list[dict]:
        # Experiment name patterns: tokens matching known experiment IDs that
        # contain no digits (e.g. "Allumette"). The digit-based rule in
        # _extract_id_patterns ignores them, but they may be the only anchor
        # for questions like "...de l'essai Allumette".
        # IDs with digits (ACE-4, PP-16) are already handled by digit_patterns/uncovered
        # and must NOT be passed again via exp_names — that would mix covered and
        # uncovered experiments in a single augmentation Cypher call.
        tokens = {g1 or g2 for g1, g2 in _ID_RE.findall(question)}
        exp_names = [
            t for t in tokens if t in self._known_exp_ids and not any(c.isdigit() for c in t)
        ]

        # Skip augmentation for digit-based patterns already satisfied by hybrid.
        # Generic run labels like "S1-R4" appear across many experiments; if hybrid
        # already retrieved the correct experiment's chunk, CONTAINS augmentation
        # would inject same-label chunks from other experiments and evict the hit.
        # Guard uses EXACT suffix match (last component after ':Run:') so that
        # session-level patterns like "COULEUR-S1" do NOT match "COULEUR-S1-1" and
        # still trigger augmentation to retrieve all runs in the session.
        # RÉPERTOIRE chunks are excluded from coverage: their run_id last component
        # is the referenced experiment ID (e.g. "ACE-5"), which would falsely mark
        # that experiment as already covered when it is not.
        non_rep_chunks = [
            c for c in chunks if c.get("experiment_id", "") != "REPERTOIRE-RD-2025-2026"
        ]
        hybrid_run_suffixes = {
            c.get("run_id", "").lower().rsplit(":", 1)[-1] for c in non_rep_chunks
        }
        hybrid_exp_ids_lower = {c.get("experiment_id", "").lower() for c in non_rep_chunks}
        digit_patterns = [p for p in _extract_id_patterns(question) if len(p) >= 4]
        uncovered = [
            p
            for p in digit_patterns
            if p.lower() not in hybrid_run_suffixes
            and not any(p.lower() in eid for eid in hybrid_exp_ids_lower)
        ]
        if not uncovered and not exp_names:
            return chunks

        # Cap augmentation slots so that uncovered patterns don't evict hybrid
        # results when top_k is the total budget. For comparative questions with
        # n uncovered experiments, allocate at most 2 slots per experiment.
        n_aug = max(2, 2 * len(uncovered)) if uncovered else 6
        extra = _augment_chunks_from_question(
            self._driver,
            question,
            extra_patterns=exp_names or None,
            patterns_override=uncovered if digit_patterns else None,
            max_extra=n_aug,
        )
        if not extra:
            return chunks
        # Deduplicate by text prefix — not by run_id, because multiple chunks
        # can share a run_id (e.g. summary chunk + detail chunk for run:1) and
        # deduplication by run_id would silently drop the detail chunk if the
        # summary was already retrieved by the hybrid search.
        existing = {c.get("text", "")[:100] for c in chunks}
        seen_extra: set[str] = set()
        new_chunks = []
        for c in extra:
            key = c.get("text", "")[:100]
            if key not in existing and key not in seen_extra:
                new_chunks.append(c)
                seen_extra.add(key)
        return (new_chunks + chunks)[:top_k]

    def _verify_citations(self, answer: str, valid_ids: set[str]) -> str:
        """Strip [source: id] markers whose id is not in valid_ids.

        Also accepts the local part after ':Run:' — the LLM sometimes cites
        with the shorthand from the question (e.g. 'S2-R4') rather than the
        full run_id ('CONSERVATEUR-VIEILLISSEMENT:Run:S2-R4').
        """
        short_forms = {vid.split(":Run:")[-1] for vid in valid_ids if ":Run:" in vid}
        accepted = valid_ids | short_forms

        def _keep_or_drop(m: re.Match) -> str:
            return m.group(0) if m.group(1).strip() in accepted else ""

        return _CITATION_RE.sub(_keep_or_drop, answer).strip()

    def _fallback(self, reason: str, dense_score: float | None = None) -> QueryResponse:
        """FALLBACK_MESSAGE annoté de la gate déclenchée (monitoring — query_log)."""
        return QueryResponse(
            answer=FALLBACK_MESSAGE,
            sources=[],
            found_in_corpus=False,
            fallback_reason=reason,
            dense_score=dense_score,
        )

    def run(
        self,
        question: str,
        top_k: int = TOP_K_DEFAULT,
        chantier: str | None = None,
        history: list[dict] | None = None,
    ) -> QueryResponse:
        self._maybe_reload_ids()
        if any(t in question.lower() for t in self._absent_topics):
            return self._fallback("absent_topic")

        query_vector = embed_text(self._openai, question)
        dense_score = self._dense_score(query_vector)

        # ── Dense gate ────────────────────────────────────────────────────────
        # Si dense_score ≈ 1.0 pour toutes les requêtes (corpus > ~500 chunks),
        # le threshold est périmé : relancer calibrate_threshold.py.
        if dense_score < SCORE_THRESHOLD:
            exact_rows = exact_lookup(self._driver, question)
            if not exact_rows:
                return self._fallback("dense_gate_no_exact", dense_score)
            # Exact match found: use as context
            context = _format_exact_context(exact_rows)
            valid_ids = {r["run_id"] for r in exact_rows}
            sources = _build_sources(exact_rows, self._driver, is_exact=True)
        else:
            # Phase 3 — Inverse [:REFERENCES]: "qui référence X?" bypasses absent-exp guard
            # because the target (e.g. JUT-REC-11) may be a stub with no data, yet other
            # experiments DO reference it and can be answered via graph traversal.
            if _INVERSE_REF_RE.search(question):
                target_ids = _detect_inverse_ref_targets(
                    question, self._known_exp_ids, self._empty_exp_ids
                )
                if target_ids:
                    inv_ctx = _fetch_inverse_references(self._driver, target_ids, [])
                    if inv_ctx:
                        inv_context = _format_inverse_ref_context(inv_ctx, target_ids)
                        inv_valid_ids = {r["run_id"] for r in inv_ctx if r.get("run_id")}
                        inv_sources = _build_sources(inv_ctx, self._driver, is_exact=True)
                        answer, in_tok, out_tok = self._generate(inv_context, question, history)
                        cited = extract_cited_ids(answer)
                        if not cited and FALLBACK_MESSAGE not in answer:
                            answer, in2, out2 = self._generate(
                                inv_context, question + _REGEN_SUFFIX
                            )
                            in_tok += in2
                            out_tok += out2
                        answer = self._verify_citations(answer, inv_valid_ids)
                        if _is_no_data_response(answer):
                            return self._fallback("llm_declined", dense_score)
                        return QueryResponse(
                            answer=answer,
                            sources=inv_sources,
                            found_in_corpus=True,
                            input_tokens=in_tok,
                            output_tokens=out_tok,
                            dense_score=dense_score,
                            n_chunks=len(inv_sources),
                        )

            if _mentions_absent_experiment(
                question,
                self._known_exp_ids,
                self._known_exp_prefixes,
                self._empty_exp_ids,
            ):
                return self._fallback("absent_experiment", dense_score)

            # ── Hybrid search ─────────────────────────────────────────────────
            filters = {"chantier": chantier} if chantier else None
            chunks = self._retriever.search(question, top_k=top_k, filters=filters)
            chunks = self._apply_augmentation(chunks, question, top_k)
            if not chunks or not _topic_in_chunks(question, chunks):
                return self._fallback("no_chunks_or_topic_mismatch", dense_score)
            # Measure-term augmentation: prepend section-4 chunk (MAX 1) when
            # AI/SME/TPA/anisotropie in question and no section-4 in hybrid results.
            _ql = question.lower()
            if any(t in _ql for t in _MEASURE_TERMS) or _MEASURE_TERMS_SHORT_RE.search(_ql):
                _meas_exp_ids = list(
                    {
                        c["experiment_id"]
                        for c in chunks
                        if c.get("experiment_id")
                        and c["experiment_id"] != "REPERTOIRE-RD-2025-2026"
                    }
                )
                if _meas_exp_ids and not any("## 4" in (c.get("text") or "") for c in chunks):
                    _meas = _fetch_measure_sections(
                        self._driver,
                        _meas_exp_ids,
                        [c["run_id"] for c in chunks if c.get("run_id")],
                    )
                    if _meas:
                        chunks = _meas[:1] + chunks

            exp_ids = list({c["experiment_id"] for c in chunks if c.get("experiment_id")})
            ref_summaries = [
                r
                for r in _fetch_reference_summaries(self._driver, exp_ids)
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

            # Phase 2 — Lexical "répertoire" trigger (deterministic, replaces passive approach)
            if _REPERTOIRE_RE.search(question):
                exp_ids_in_q = [
                    p for p in _EXP_PATTERN_RE.findall(question) if p in self._known_exp_ids
                ]
                if exp_ids_in_q:
                    rep_ctx = _fetch_repertoire_direct(self._driver, exp_ids_in_q)
                    if rep_ctx:
                        context += (
                            "\n\n=== Description Répertoire + détails essai ===\n"
                            + _format_repertoire_context(rep_ctx)
                        )
                        valid_ids |= {r["rep_run_id"] for r in rep_ctx if r.get("rep_run_id")}
                        valid_ids |= {r["run_id"] for r in rep_ctx if r.get("run_id")}
                        existing_run_ids = {s.run_id for s in sources}
                        seen_new: set[str] = set()
                        new_items: list[dict] = []
                        for r in rep_ctx:
                            if r.get("run_id") and r["run_id"] not in existing_run_ids | seen_new:
                                new_items.append(
                                    {"run_id": r["run_id"], "experiment_id": r.get("exp_id", "")}
                                )
                                seen_new.add(r["run_id"])
                            if (
                                r.get("rep_run_id")
                                and r["rep_run_id"] not in existing_run_ids | seen_new
                            ):
                                new_items.append(
                                    {
                                        "run_id": r["rep_run_id"],
                                        "experiment_id": "REPERTOIRE-RD-2025-2026",
                                    }
                                )
                                seen_new.add(r["rep_run_id"])
                        if new_items:
                            sources += _build_sources(new_items, self._driver, is_exact=True)

            # Phase 1 — [:USES_INGREDIENT] traversal
            ing_tokens = _detect_ingredient_tokens(question, self._ingredient_tokens)
            if ing_tokens:
                # Phase 1b — aggregate mode for "quelles expériences ont utilisé X?" queries
                if _is_aggregate_ingredient_query(question):
                    agg_rows = _fetch_ingredient_aggregate(self._driver, ing_tokens)
                    if agg_rows:
                        context += (
                            "\n\n=== Expériences par ingrédient (vue agrégée) ===\n"
                            + _format_ingredient_aggregate(agg_rows)
                        )
                        existing_run_ids = {s.run_id for s in sources}
                        agg_source_items = [
                            {"run_id": r["sample_run_ids"][0], "experiment_id": r["experiment_id"]}
                            for r in agg_rows
                            if r["sample_run_ids"]
                            and r["sample_run_ids"][0] not in existing_run_ids
                        ]
                        if agg_source_items:
                            sources += _build_sources(agg_source_items, self._driver, is_exact=True)

                # Phase 1a — per-run chunks (MAX 2 slots, appended last)
                ing_chunks = _fetch_ingredient_context(self._driver, ing_tokens, list(valid_ids))[
                    :2
                ]
                if ing_chunks:
                    context += (
                        "\n\n=== Essais utilisant les ingrédients mentionnés ===\n"
                        + _format_ingredient_context(ing_chunks)
                    )
                    valid_ids |= {c["run_id"] for c in ing_chunks}
                    existing_run_ids = {s.run_id for s in sources}
                    sources += _build_sources(
                        [c for c in ing_chunks if c["run_id"] not in existing_run_ids],
                        self._driver,
                        is_exact=True,
                    )

            # Phase 1.5 — Session-level context (KOBE-style multi-run session questions).
            # Fires when the question explicitly names a session prefix like COULEUR-S1.
            # Fetches ALL run chunks in those sessions, bypassing the top_k cap.
            session_pfxs = _detect_session_prefixes(question)
            if session_pfxs:
                sess_chunks = _fetch_session_context(self._driver, session_pfxs)
                already = {c.get("text", "")[:80] for c in chunks}
                fresh = [c for c in sess_chunks if c.get("text", "")[:80] not in already]
                if fresh:
                    context += (
                        "\n\n=== Données de session (tous les runs de la session) ===\n"
                        + _format_session_context(fresh)
                    )
                    valid_ids |= {c["run_id"] for c in fresh if c.get("run_id")}
                    existing_run_ids = {s.run_id for s in sources}
                    sources += _build_sources(
                        [
                            c
                            for c in fresh
                            if c.get("run_id") and c["run_id"] not in existing_run_ids
                        ],
                        self._driver,
                        is_exact=True,
                    )

        # ── Generation ────────────────────────────────────────────────────────
        answer, in_tok, out_tok = self._generate(context, question, history)

        # ── Citation verification ─────────────────────────────────────────────
        cited = extract_cited_ids(answer)
        if not cited and FALLBACK_MESSAGE not in answer:
            answer, in2, out2 = self._generate(context, question + _REGEN_SUFFIX)
            in_tok += in2
            out_tok += out2
        answer = self._verify_citations(answer, valid_ids)

        if _is_no_data_response(answer):
            return self._fallback("llm_declined", dense_score)

        return QueryResponse(
            answer=answer,
            sources=sources,
            found_in_corpus=True,
            input_tokens=in_tok,
            output_tokens=out_tok,
            dense_score=dense_score,
            n_chunks=len(sources),
        )

    def run_stream(
        self,
        question: str,
        top_k: int = TOP_K_DEFAULT,
        chantier: str | None = None,
        history: list[dict] | None = None,
    ) -> Iterator[str | QueryResponse]:
        """Yield str chunks during Claude generation, then QueryResponse as final item.

        Consumers iterate: str → append to display; QueryResponse → final render + reset UI.
        Fallback (found_in_corpus=False) yields QueryResponse immediately with no str chunks.
        """
        self._maybe_reload_ids()
        if any(t in question.lower() for t in self._absent_topics):
            yield self._fallback("absent_topic")
            return

        query_vector = embed_text(self._openai, question)
        dense_score = self._dense_score(query_vector)

        if dense_score < SCORE_THRESHOLD:
            exact_rows = exact_lookup(self._driver, question)
            if not exact_rows:
                yield self._fallback("dense_gate_no_exact", dense_score)
                return
            context = _format_exact_context(exact_rows)
            valid_ids = {r["run_id"] for r in exact_rows}
            sources = _build_sources(exact_rows, self._driver, is_exact=True)
        else:
            # Phase 3 — Inverse [:REFERENCES]: bypass absent-exp guard for "qui référence X?"
            if _INVERSE_REF_RE.search(question):
                target_ids = _detect_inverse_ref_targets(
                    question, self._known_exp_ids, self._empty_exp_ids
                )
                if target_ids:
                    inv_ctx = _fetch_inverse_references(self._driver, target_ids, [])
                    if inv_ctx:
                        inv_context = _format_inverse_ref_context(inv_ctx, target_ids)
                        inv_valid_ids = {r["run_id"] for r in inv_ctx if r.get("run_id")}
                        inv_sources = _build_sources(inv_ctx, self._driver, is_exact=True)
                        answer, in_tok, out_tok = self._generate(inv_context, question, history)
                        cited = extract_cited_ids(answer)
                        if not cited and FALLBACK_MESSAGE not in answer:
                            answer, in2, out2 = self._generate(
                                inv_context, question + _REGEN_SUFFIX
                            )
                            in_tok += in2
                            out_tok += out2
                        answer = self._verify_citations(answer, inv_valid_ids)
                        if _is_no_data_response(answer):
                            yield self._fallback("llm_declined", dense_score)
                            return
                        yield QueryResponse(
                            answer=answer,
                            sources=inv_sources,
                            found_in_corpus=True,
                            input_tokens=in_tok,
                            output_tokens=out_tok,
                            dense_score=dense_score,
                            n_chunks=len(inv_sources),
                        )
                        return

            if _mentions_absent_experiment(
                question,
                self._known_exp_ids,
                self._known_exp_prefixes,
                self._empty_exp_ids,
            ):
                yield self._fallback("absent_experiment", dense_score)
                return

            filters = {"chantier": chantier} if chantier else None
            chunks = self._retriever.search(question, top_k=top_k, filters=filters)
            chunks = self._apply_augmentation(chunks, question, top_k)
            if not chunks or not _topic_in_chunks(question, chunks):
                yield self._fallback("no_chunks_or_topic_mismatch", dense_score)
                return
            # Measure-term augmentation (same logic as run())
            _ql = question.lower()
            if any(t in _ql for t in _MEASURE_TERMS) or _MEASURE_TERMS_SHORT_RE.search(_ql):
                _meas_exp_ids = list(
                    {
                        c["experiment_id"]
                        for c in chunks
                        if c.get("experiment_id")
                        and c["experiment_id"] != "REPERTOIRE-RD-2025-2026"
                    }
                )
                if _meas_exp_ids and not any("## 4" in (c.get("text") or "") for c in chunks):
                    _meas = _fetch_measure_sections(
                        self._driver,
                        _meas_exp_ids,
                        [c["run_id"] for c in chunks if c.get("run_id")],
                    )
                    if _meas:
                        chunks = _meas[:1] + chunks

            exp_ids = list({c["experiment_id"] for c in chunks if c.get("experiment_id")})
            ref_summaries = [
                r
                for r in _fetch_reference_summaries(self._driver, exp_ids)
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

            # Phase 2 — Lexical "répertoire" trigger (deterministic)
            if _REPERTOIRE_RE.search(question):
                exp_ids_in_q = [
                    p for p in _EXP_PATTERN_RE.findall(question) if p in self._known_exp_ids
                ]
                if exp_ids_in_q:
                    rep_ctx = _fetch_repertoire_direct(self._driver, exp_ids_in_q)
                    if rep_ctx:
                        context += (
                            "\n\n=== Description Répertoire + détails essai ===\n"
                            + _format_repertoire_context(rep_ctx)
                        )
                        valid_ids |= {r["rep_run_id"] for r in rep_ctx if r.get("rep_run_id")}
                        valid_ids |= {r["run_id"] for r in rep_ctx if r.get("run_id")}
                        existing_run_ids = {s.run_id for s in sources}
                        seen_new_s: set[str] = set()
                        new_items_s: list[dict] = []
                        for r in rep_ctx:
                            if r.get("run_id") and r["run_id"] not in existing_run_ids | seen_new_s:
                                new_items_s.append(
                                    {"run_id": r["run_id"], "experiment_id": r.get("exp_id", "")}
                                )
                                seen_new_s.add(r["run_id"])
                            if (
                                r.get("rep_run_id")
                                and r["rep_run_id"] not in existing_run_ids | seen_new_s
                            ):
                                new_items_s.append(
                                    {
                                        "run_id": r["rep_run_id"],
                                        "experiment_id": "REPERTOIRE-RD-2025-2026",
                                    }
                                )
                                seen_new_s.add(r["rep_run_id"])
                        if new_items_s:
                            sources += _build_sources(new_items_s, self._driver, is_exact=True)

            # Phase 1 — [:USES_INGREDIENT] traversal
            ing_tokens = _detect_ingredient_tokens(question, self._ingredient_tokens)
            if ing_tokens:
                # Phase 1b — aggregate mode for "quelles expériences ont utilisé X?" queries
                if _is_aggregate_ingredient_query(question):
                    agg_rows = _fetch_ingredient_aggregate(self._driver, ing_tokens)
                    if agg_rows:
                        context += (
                            "\n\n=== Expériences par ingrédient (vue agrégée) ===\n"
                            + _format_ingredient_aggregate(agg_rows)
                        )
                        existing_run_ids = {s.run_id for s in sources}
                        agg_source_items = [
                            {"run_id": r["sample_run_ids"][0], "experiment_id": r["experiment_id"]}
                            for r in agg_rows
                            if r["sample_run_ids"]
                            and r["sample_run_ids"][0] not in existing_run_ids
                        ]
                        if agg_source_items:
                            sources += _build_sources(agg_source_items, self._driver, is_exact=True)

                # Phase 1a — per-run chunks (MAX 2 slots, appended last)
                ing_chunks = _fetch_ingredient_context(self._driver, ing_tokens, list(valid_ids))[
                    :2
                ]
                if ing_chunks:
                    context += (
                        "\n\n=== Essais utilisant les ingrédients mentionnés ===\n"
                        + _format_ingredient_context(ing_chunks)
                    )
                    valid_ids |= {c["run_id"] for c in ing_chunks}
                    existing_run_ids = {s.run_id for s in sources}
                    sources += _build_sources(
                        [c for c in ing_chunks if c["run_id"] not in existing_run_ids],
                        self._driver,
                        is_exact=True,
                    )

            # Phase 1.5 — Session-level context (mirror of run())
            session_pfxs = _detect_session_prefixes(question)
            if session_pfxs:
                sess_chunks = _fetch_session_context(self._driver, session_pfxs)
                already = {c.get("text", "")[:80] for c in chunks}
                fresh = [c for c in sess_chunks if c.get("text", "")[:80] not in already]
                if fresh:
                    context += (
                        "\n\n=== Données de session (tous les runs de la session) ===\n"
                        + _format_session_context(fresh)
                    )
                    valid_ids |= {c["run_id"] for c in fresh if c.get("run_id")}
                    existing_run_ids = {s.run_id for s in sources}
                    sources += _build_sources(
                        [
                            c
                            for c in fresh
                            if c.get("run_id") and c["run_id"] not in existing_run_ids
                        ],
                        self._driver,
                        is_exact=True,
                    )

        text_chunks: list[str] = []
        in_tok = out_tok = 0

        history_messages = _format_history_messages(history) if history else []
        with self._llm.messages.stream(
            model=LLM_MODEL,
            max_tokens=128000,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[
                *history_messages,
                {"role": "user", "content": f"Contexte :\n{context}\n\nQuestion : {question}"},
            ],
        ) as stream:
            for delta in stream.text_stream:
                text_chunks.append(delta)
                yield delta
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

        if _is_no_data_response(answer):
            yield self._fallback("llm_declined", dense_score)
            return

        yield QueryResponse(
            answer=answer,
            sources=sources,
            found_in_corpus=True,
            input_tokens=in_tok,
            output_tokens=out_tok,
            dense_score=dense_score,
            n_chunks=len(sources),
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


def _fetch_repertoire_direct(driver: Driver, exp_ids: list[str]) -> list[dict]:
    """Phase 2 — direct REPERTOIRE lookup for experiments mentioned in question.

    Triggered lexically when the question contains 'répertoire' AND a known experiment ID.
    Fetches the REPERTOIRE run description + follows [:DETAILS] to full experiment chunk.
    """
    if not exp_ids:
        return []
    try:
        with driver.session() as s:
            return s.run(_REPERTOIRE_DIRECT_CYPHER, exp_ids=exp_ids).data()
    except Exception as exc:
        _log.debug("Repertoire direct fetch failed: %s", exc)
        return []


def _format_repertoire_context(rep_ctx: list[dict]) -> str:
    parts = []
    for r in rep_ctx:
        header = f"[Source: {r['rep_run_id']}] [Répertoire → {r.get('exp_id', '')}]"
        if r.get("exp_title"):
            header += f" — {r['exp_title']}"
        lines = [header]
        if r.get("rep_objective"):
            lines.append(f"Objectif Répertoire: {r['rep_objective']}")
        if r.get("rep_synthesis"):
            lines.append(f"Synthèse Répertoire: {r['rep_synthesis']}")
        if r.get("text"):
            lines.append(r["text"])
        parts.append("\n".join(lines))
    return "\n---\n".join(parts)


def _format_ref_context(ref_summaries: list[dict]) -> str:
    parts = []
    for r in ref_summaries:
        run_id = r.get("run_id") or r["ref_exp_id"]
        header = f"[Source: {run_id}] [essai connexe : {r['ref_exp_id']}]"
        if r.get("ref_title"):
            header += f" — {r['ref_title']}"
        parts.append(f"{header}\n{r['ref_text']}")
    return "\n---\n".join(parts)


def _detect_inverse_ref_targets(
    question: str,
    known_ids: frozenset[str],
    empty_ids: frozenset[str],
) -> list[str]:
    """Extract experiment IDs that are targets of an inverse reference query.

    Only returns IDs that are stubs (empty_ids) or completely unknown — i.e. the
    question asks 'who references X' where X has no data of its own.
    Known experiments with data are not targets: the question would be answered
    by hybrid retrieval directly.
    """
    targets = []
    for pattern in _EXP_PATTERN_RE.findall(question):
        if pattern in empty_ids or pattern not in known_ids:
            targets.append(pattern)
    return targets


def _fetch_inverse_references(
    driver: Driver, target_ids: list[str], already_exp_ids: list[str]
) -> list[dict]:
    """Phase 3 — traverse ←[:REFERENCES] to find experiments that reference target_ids."""
    if not target_ids:
        return []
    try:
        with driver.session() as s:
            return s.run(
                _INVERSE_REF_CYPHER,
                target_ids=target_ids,
                already_exp_ids=already_exp_ids,
            ).data()
    except Exception as exc:
        _log.debug("Inverse reference fetch failed: %s", exc)
        return []


def _format_inverse_ref_context(inv_ctx: list[dict], target_ids: list[str]) -> str:
    target_str = ", ".join(target_ids)
    parts = []
    for r in inv_ctx:
        header = f"[Source: {r['run_id']}] [Expérience {r['exp_id']} RÉFÉRENCE → {target_str}]"
        if r.get("exp_title"):
            header += f" — {r['exp_title']}"
        parts.append(f"{header}\n{r['text']}")
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


def _load_absent_topics(path: str = ABSENT_TOPICS_PATH) -> frozenset[str]:
    """Load absent-topic strings from a plain-text file (one per line, # = comment).

    Falls back to _FALLBACK_TOPICS_SEED when the file is missing or unreadable,
    so the pipeline stays operational without the data file.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            topics = frozenset(
                line.strip().lower() for line in fh if line.strip() and not line.startswith("#")
            )
        _log.debug("Loaded %d absent topics from %s", len(topics), path)
        return topics or _FALLBACK_TOPICS_SEED
    except OSError:
        _log.debug("absent_topics.txt not found at %s — using seed", path)
        return _FALLBACK_TOPICS_SEED


def _load_experiment_ids(
    driver: Driver,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Load experiment IDs from Neo4j at pipeline startup.

    Returns (known_ids, known_prefixes, empty_ids).
    - known_ids: all experiment IDs
    - known_prefixes: letter-only prefixes (e.g. "DST", "ACE") for unknown-ID detection
    - empty_ids: experiments that exist as nodes but have no runs or summary chunk
      (referenced by others but file unavailable — treat as absent)
    All sets are empty on failure so the pipeline degrades gracefully.
    """
    try:
        with driver.session() as session:
            rows = session.run(
                "MATCH (e:Experiment) WHERE e.id <> 'REPERTOIRE-RD-2025-2026' RETURN e.id AS eid"
            ).data()
            empty_rows = session.run(
                "MATCH (e:Experiment) "
                "WHERE e.id <> 'REPERTOIRE-RD-2025-2026' "
                "  AND NOT (e)-[:HAS_RUN]->() "
                "  AND NOT (e)-[:HAS_SUMMARY]->() "
                "RETURN e.id AS eid"
            ).data()
        ids: frozenset[str] = frozenset(r["eid"] for r in rows if r.get("eid"))
        prefixes: frozenset[str] = frozenset(
            "-".join(eid.split("-")[:-1]) for eid in ids if eid.split("-")[-1].isdigit()
        )
        empty_ids: frozenset[str] = frozenset(r["eid"] for r in empty_rows if r.get("eid"))
        return ids, prefixes, empty_ids
    except Exception as exc:
        _log.debug("Could not load experiment IDs at startup: %s", exc)
        return frozenset(), frozenset(), frozenset()


def _mentions_absent_experiment(
    question: str,
    known_ids: frozenset[str],
    known_prefixes: frozenset[str],
    empty_ids: frozenset[str],
) -> bool:
    """Returns True if question names an experiment with no data in the corpus.

    Two cases:
    - Prefix known but ID absent (e.g. ACE-8 where ACE-1..7 exist): pure fabrication.
    - ID exists as node but has no runs or chunks (e.g. DST-7): referenced stub, no data.

    Prefix must be all-letter — filters out flavor codes (OV-924) and run shorthands (S2-R4).
    No-op when both sets are empty (tests with mock drivers).
    """
    if not known_prefixes and not empty_ids:
        return False
    for pattern in _EXP_PATTERN_RE.findall(question):
        if pattern in empty_ids:
            return True
        parts = pattern.split("-")
        prefix = "-".join(parts[:-1])
        if prefix in known_prefixes and pattern not in known_ids:
            return True
    return False


def _extract_id_patterns(question: str) -> list[str]:
    """Return tokens from question that look like run/experiment IDs.

    Rule: token must contain at least one digit (all real run/experiment IDs
    in the corpus have a digit). This excludes French compound words like
    'observe-t-on', ingredient names like 'Flavoset', and common verbs like
    'comparant' that the old long-word heuristic incorrectly included.
    """
    raw = [g1 or g2 for g1, g2 in _ID_RE.findall(question)]
    result = []
    for t in raw:
        has_digit = any(c.isdigit() for c in t)
        if has_digit and len(t) >= 3:
            result.append(t)
    seen: set[str] = set()
    return [t for t in result if not (t.lower() in seen or seen.add(t.lower()))]  # type: ignore[func-returns-value]


def _augment_chunks_from_question(
    driver: Driver,
    question: str,
    max_extra: int = 6,
    extra_patterns: list[str] | None = None,
    patterns_override: list[str] | None = None,
) -> list[dict]:
    # patterns_override replaces the internal _extract_id_patterns call so that
    # _apply_augmentation can restrict the search to uncovered patterns only.
    patterns = (
        list(patterns_override) if patterns_override is not None else _extract_id_patterns(question)
    )
    if extra_patterns:
        seen = {p.lower() for p in patterns}
        for p in extra_patterns:
            if p.lower() not in seen:
                patterns.append(p)
                seen.add(p.lower())
    # Short patterns (< 4 chars) over-match via CONTAINS in the augmentation Cypher
    # (e.g. "M03" matches "EI-DEBIT-M03002", "MDD" matches AROME-GIVAUDAN-OPT runs).
    patterns = [p for p in patterns if len(p) >= 4]
    if not patterns:
        return []
    try:
        with driver.session() as session:
            return session.run(_AUGMENT_CYPHER, patterns=patterns, limit=max_extra).data()
    except Exception as exc:
        _log.debug("Augment lookup failed: %s", exc)
        return []


def _load_ingredient_tokens(driver: Driver) -> frozenset[str]:
    """Extract ≥5-char alpha tokens from all ingredient names for question matching."""
    try:
        with driver.session() as s:
            rows = s.run("MATCH (i:Ingredient) RETURN toLower(i.name) AS name").data()
        tokens: set[str] = set()
        for r in rows:
            for tok in _TOKEN_RE.findall(r["name"]):
                tokens.add(tok)
        return frozenset(tokens)
    except Exception as exc:
        _log.debug("Could not load ingredient tokens: %s", exc)
        return frozenset()


def _detect_ingredient_tokens(question: str, ingredient_tokens: frozenset[str]) -> list[str]:
    """Return ingredient tokens that appear in the question (case-insensitive)."""
    q_tokens = set(_TOKEN_RE.findall(question.lower()))
    return [t for t in q_tokens & ingredient_tokens if t not in _INGREDIENT_STOPWORDS]


def _fetch_measure_sections(
    driver: Driver, exp_ids: list[str], already_run_ids: list[str]
) -> list[dict]:
    """Fetch section-4 (## 4. Derived & computed values) for the given experiments.

    Called when a measure term (AI, SME, TPA, anisotropie) is in the question but no
    section-4 chunk appears in the hybrid results. Returns at most 1 chunk.
    """
    if not exp_ids:
        return []
    try:
        with driver.session() as s:
            rows = s.run(
                _MEASURE_SECTION_CYPHER,
                exp_ids=exp_ids,
                already_run_ids=already_run_ids,
            ).data()
    except Exception as exc:
        _log.debug("Measure section fetch failed: %s", exc)
        return []
    return [
        {
            "text": r["text"],
            "run_id": r["run_id"],
            "experiment_id": r["experiment_id"],
            "score": 0.0,
            "run_status": None,
            "objective": None,
            "synthesis": None,
            "date": None,
            "ingredients": [],
            "chantier": None,
        }
        for r in rows
    ]


def _fetch_ingredient_context(
    driver: Driver, tokens: list[str], already_run_ids: list[str]
) -> list[dict]:
    """Phase 1 — one query per token, 1 run per token, globally deduplicated.

    Querying tokens independently avoids the OR across unrelated ingredients:
    a question mentioning both 'plantfer' and 'nuggets' (product context) would
    otherwise inject runs matching *either* term — potentially unrelated to the
    user's intent.
    """
    if not tokens:
        return []
    results: list[dict] = []
    seen: set[str] = set(already_run_ids)
    try:
        with driver.session() as s:
            for token in tokens:
                rows = s.run(
                    _INGREDIENT_CONTEXT_CYPHER,
                    tokens=[token],
                    already_run_ids=list(seen),
                ).data()
                for row in rows:
                    if row["run_id"] not in seen:
                        results.append(row)
                        seen.add(row["run_id"])
                        break  # 1 run per token
    except Exception as exc:
        _log.debug("Ingredient context fetch failed: %s", exc)
    return results


def _format_ingredient_context(ing_chunks: list[dict]) -> str:
    parts = []
    for c in ing_chunks:
        header = f"[Source: {c['run_id']}] [Ingrédient : {c['ingredient']}]"
        lines = [header]
        if c.get("objective"):
            lines.append(f"Objectif: {c['objective']}")
        if c.get("synthesis"):
            lines.append(f"Synthèse: {c['synthesis']}")
        if c.get("text"):
            lines.append(c["text"])
        parts.append("\n".join(lines))
    return "\n---\n".join(parts)


def _is_aggregate_ingredient_query(question: str) -> bool:
    """True when the question asks which experiments used an ingredient (aggregate intent)."""
    return bool(_AGGREGATE_INGREDIENT_RE.search(question))


def _is_valid_ingredient_name(name: str) -> bool:
    """Reject data-entry artifacts masquerading as ingredient names.

    Some knowledge.json files stored notes or compound labels as ingredient names:
    '/' separates two alternatives, 'Note:' prefixes a free-text remark.
    These produce noise in the aggregate and should be excluded.
    """
    return "/" not in name and not name.lower().startswith("note")


def _fetch_ingredient_aggregate(driver: Driver, tokens: list[str]) -> list[dict]:
    """Phase 1b — per-token aggregate query: experiment × ingredient × run count.

    Each token is queried independently so unrelated ingredient names in the same
    question don't cross-contaminate results.
    """
    if not tokens:
        return []
    seen_exp: set[tuple[str, str]] = set()
    results: list[dict] = []
    try:
        with driver.session() as s:
            for token in tokens:
                rows = s.run(
                    _INGREDIENT_AGGREGATE_CYPHER,
                    tokens=[token],
                ).data()
                for row in rows:
                    if not _is_valid_ingredient_name(row["ingredient"]):
                        continue
                    key = (row["experiment_id"], row["ingredient"])
                    if key not in seen_exp:
                        results.append(row)
                        seen_exp.add(key)
    except Exception as exc:
        _log.debug("Ingredient aggregate fetch failed: %s", exc)
    return results


def _format_ingredient_aggregate(rows: list[dict]) -> str:
    """Format aggregate rows as a structured ingredient-usage table."""
    by_ingredient: dict[str, list[dict]] = {}
    for row in rows:
        by_ingredient.setdefault(row["ingredient"], []).append(row)

    parts = []
    for ingredient, exps in by_ingredient.items():
        lines = [f"=== Expériences utilisant « {ingredient} » ==="]
        for exp in exps:
            sample = ", ".join(exp["sample_run_ids"]) if exp["sample_run_ids"] else "—"
            title = f" — {exp['experiment_title']}" if exp.get("experiment_title") else ""
            lines.append(
                f"- {exp['experiment_id']}{title} ({exp['nb_runs']} runs)"
                f" — Exemple de runs : {sample}"
            )
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _detect_session_prefixes(question: str) -> list[str]:
    """Extract deduplicated session prefixes (e.g. COULEUR-S1) from the question."""
    return list({m.group(1).upper() for m in _SESSION_PREFIX_RE.finditer(question)})


def _fetch_session_context(driver: Driver, prefixes: list[str]) -> list[dict]:
    """Phase 1.5 — fetch all run chunks belonging to the detected session prefixes."""
    if not prefixes:
        return []
    try:
        with driver.session() as s:
            return s.run(_SESSION_CONTEXT_CYPHER, prefixes=prefixes).data()
    except Exception as exc:
        _log.debug("Session context fetch failed: %s", exc)
        return []


def _format_session_context(chunks: list[dict]) -> str:
    """Format session chunks grouped by run_id, sorted for readability."""
    by_run: dict[str, list[str]] = {}
    for c in chunks:
        by_run.setdefault(c["run_id"], []).append(c["text"])
    lines = []
    for run_id, texts in sorted(by_run.items()):
        lines.append(f"--- Run {run_id} ---\n" + "\n".join(texts))
    return "\n\n".join(lines)


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
        sources.append(
            Source(
                run_id=run_id,
                experiment_id=exp_id,
                source_file=f"{exp_id}_documentation.md" if exp_id else "",
                score=0.0 if is_exact else float(item.get("score") or 0.0),
                name=item.get("run_name") or "",
                sharepoint_url=url,
            )
        )
    return sources


# ── Public API ────────────────────────────────────────────────────────────────


def _format_history_messages(history: list[dict]) -> list[dict]:
    """Convert last 3 Gradio chat exchanges to LLM messages, stripping UI footers."""
    recent = history[-6:] if len(history) > 6 else history
    result = []
    for msg in recent:
        role = msg.get("role", "")
        raw = msg.get("content") or ""
        content = (
            raw
            if isinstance(raw, str)
            else " ".join(p if isinstance(p, str) else (p.get("text") or "") for p in raw)
        )
        content = content.strip()
        if role == "assistant":
            idx = content.find("\n\n---\n**Sources**")
            content = content[:idx].strip() if idx != -1 else content
        if role in ("user", "assistant") and content:
            result.append({"role": role, "content": content})
    return result


def stream_query(
    pipeline: RAGPipeline,
    question: str,
    top_k: int = TOP_K_DEFAULT,
    chantier: str | None = None,
    history: list[dict] | None = None,
) -> Iterator[str | QueryResponse]:
    return pipeline.run_stream(question, top_k=top_k, chantier=chantier, history=history)


def run_query(
    pipeline: RAGPipeline,
    question: str,
    top_k: int = TOP_K_DEFAULT,
    chantier: str | None = None,
    history: list[dict] | None = None,
) -> QueryResponse:
    return pipeline.run(question, top_k=top_k, chantier=chantier, history=history)


def get_dense_score(pipeline: RAGPipeline, query: str) -> float:
    vec = embed_text(pipeline._openai, query)
    return pipeline._dense_score(vec)


def build_pipeline() -> RAGPipeline:
    """Convenience factory using env-configured credentials."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return RAGPipeline(driver, openai_client, anthropic_client)
