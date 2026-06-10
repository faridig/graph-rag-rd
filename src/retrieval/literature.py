"""Fetches relevant literature from Semantic Scholar for CIR section-1 state of the art."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

_log = logging.getLogger(__name__)

_API_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,authors,year,abstract"
_TIMEOUT_S = 10
_MAX_ABSTRACT_CHARS = 350
# Without a key: 1 req/s. With a free key (SEMANTIC_SCHOLAR_API_KEY env var): 10 req/s.
_INTER_QUERY_SLEEP_S = 1.1

# Queries tailored to each CIR groupement — two passes, deduplicated
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


def _search(query: str, limit: int) -> list[dict]:
    params = urllib.parse.urlencode({"query": query, "fields": _FIELDS, "limit": limit})
    headers = {"User-Agent": "accro-graph-rag/1.0"}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(f"{_API_BASE}?{params}", headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        return json.loads(resp.read()).get("data", [])


def _format_paper(p: dict) -> str:
    title = p.get("title") or "Sans titre"
    year = p.get("year") or "?"
    raw_authors = p.get("authors") or []
    names = [a.get("name", "") for a in raw_authors[:3]]
    authors = ", ".join(names) + (" et al." if len(raw_authors) > 3 else "")
    abstract = (p.get("abstract") or "").strip()
    if len(abstract) > _MAX_ABSTRACT_CHARS:
        abstract = abstract[:_MAX_ABSTRACT_CHARS].rsplit(" ", 1)[0] + "…"
    line = f"- {title} ({year})"
    if authors:
        line += f" — {authors}"
    if abstract:
        line += f"\n  {abstract}"
    return line


def fetch_literature(groupement: str, max_papers: int = 6) -> str:
    """Return a formatted literature block to inject into the CIR system prompt.

    Returns an empty string if the groupement is unknown or the API is unavailable.
    """
    queries = _QUERIES.get(groupement, [])
    if not queries:
        return ""

    seen: set[str] = set()
    papers: list[dict] = []
    per_query = max(2, max_papers // len(queries) + 1)

    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(_INTER_QUERY_SLEEP_S)
        try:
            results = _search(query, limit=per_query)
        except Exception as exc:
            _log.warning("Semantic Scholar unavailable (%s) — skipping query: %s", exc, query)
            continue
        for p in results:
            pid = p.get("paperId") or p.get("title", "")
            if pid and pid not in seen and p.get("title"):
                seen.add(pid)
                papers.append(p)
            if len(papers) >= max_papers:
                break

    if not papers:
        return ""

    n = len(papers[:max_papers])
    formatted = "\n".join(_format_paper(p) for p in papers[:max_papers])
    return (
        f"RÉFÉRENCES DE LA LITTÉRATURE SCIENTIFIQUE ({n} articles — Semantic Scholar) :\n"
        "Ces résumés illustrent l'état de l'art général du domaine. "
        "Ne citer aucun titre ni auteur dans la fiche sans en avoir vérifié le contenu exact.\n\n"
        f"{formatted}"
    )
