"""Fetches relevant literature from Semantic Scholar for CIR section-1 state of the art."""

from __future__ import annotations

import logging
import os

from semanticscholar import SemanticScholar

_log = logging.getLogger(__name__)

_MAX_ABSTRACT_CHARS = 350
_FIELDS_OF_STUDY = ["Agricultural and Food Sciences", "Biology", "Chemistry", "Engineering"]
_YEAR_FILTER = "2010-"        # papers from 2010 onwards
_MIN_CITATIONS = 3            # filter noise, keep cited work
_FIELDS = ["title", "authors", "year", "abstract", "citationCount"]

# Per-groupement queries — two passes, results deduplicated on paperId
_QUERIES: dict[str, list[str]] = {
    "Muscles à base de protéines végétales": [
        "high moisture extrusion plant protein fibrous texture anisotropy",
        "HME plant-based meat analog thermomechanical processing ingredients",
    ],
    "Produits élaborés à base de muscle végétaux": [
        "plant-based meat analog texture sensory formulation water retention",
        "vegetable protein product cohesion Maillard cooking structure",
    ],
    "Nouvelles voies de texturation des protéines végétales": [
        "shear cell technology plant protein texturization fibrous structure",
        "direct shear vegetable protein high temperature processing",
    ],
}


def _build_client() -> SemanticScholar:
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "") or None
    # retry=True (default): retries up to 10× on HTTP 429, 30s apart
    return SemanticScholar(api_key=api_key, timeout=10)


def _format_paper(p: object) -> str:
    title = getattr(p, "title", None) or "Sans titre"
    year = getattr(p, "year", None) or "?"
    raw_authors = getattr(p, "authors", None) or []
    names = [getattr(a, "name", "") for a in raw_authors[:3]]
    authors = ", ".join(names) + (" et al." if len(raw_authors) > 3 else "")
    abstract = (getattr(p, "abstract", None) or "").strip()
    if len(abstract) > _MAX_ABSTRACT_CHARS:
        abstract = abstract[:_MAX_ABSTRACT_CHARS].rsplit(" ", 1)[0] + "…"
    citations = getattr(p, "citationCount", None)
    line = f"- {title} ({year})"
    if authors:
        line += f" — {authors}"
    if citations is not None:
        line += f" [{citations} citations]"
    if abstract:
        line += f"\n  {abstract}"
    return line


def fetch_literature(groupement: str, max_papers: int = 6) -> str:
    """Return a formatted literature block to inject into the CIR system prompt.

    Returns an empty string if the groupement is unknown or the API is unavailable.
    Retries automatically on HTTP 429 (handled by the semanticscholar client).
    """
    queries = _QUERIES.get(groupement, [])
    if not queries:
        return ""

    sch = _build_client()
    seen: set[str] = set()
    papers: list[object] = []
    per_query = max(2, max_papers // len(queries) + 1)

    for query in queries:
        if len(papers) >= max_papers:
            break
        try:
            results = sch.search_paper(
                query,
                fields=_FIELDS,
                fields_of_study=_FIELDS_OF_STUDY,
                year=_YEAR_FILTER,
                min_citation_count=_MIN_CITATIONS,
                limit=per_query,
            )
        except Exception as exc:
            _log.warning("Semantic Scholar unavailable (%s) — skipping query: %s", exc, query)
            continue

        for p in results:
            pid = getattr(p, "paperId", None) or getattr(p, "title", "")
            if pid and pid not in seen and getattr(p, "title", None):
                seen.add(str(pid))
                papers.append(p)
            if len(papers) >= max_papers:
                break

    if not papers:
        return ""

    batch = papers[:max_papers]
    # Sort by citation count descending so the most-cited appear first
    batch.sort(key=lambda p: getattr(p, "citationCount", 0) or 0, reverse=True)
    formatted = "\n".join(_format_paper(p) for p in batch)
    n = len(batch)
    return (
        f"RÉFÉRENCES DE LA LITTÉRATURE SCIENTIFIQUE ({n} articles — Semantic Scholar) :\n"
        "Ces résumés illustrent l'état de l'art général du domaine. "
        "Ne citer aucun titre ni auteur dans la fiche sans en avoir vérifié le contenu exact.\n\n"
        f"{formatted}"
    )
