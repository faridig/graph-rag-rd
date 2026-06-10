"""Fetches relevant literature from Semantic Scholar and PubMed for CIR section-1 state of the art."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from semanticscholar import SemanticScholar

_log = logging.getLogger(__name__)

_MAX_ABSTRACT_CHARS = 350
_CACHE_TTL_S = 3600  # 1 h — évite de rejouer l'API pour le même groupement
_cache: dict[str, tuple[str, float]] = {}  # groupement -> (result, timestamp)
_FIELDS_OF_STUDY = ["Agricultural and Food Sciences", "Biology", "Chemistry", "Engineering"]
_YEAR_FILTER = "2010-"        # papers from 2010 onwards
_MIN_CITATIONS = 3            # filter noise, keep cited work
_FIELDS = ["title", "authors", "year", "abstract", "citationCount"]

_PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_PUBMED_TOOL = "accro-graph-rag"
_PUBMED_EMAIL = "faridigouti@gmail.com"  # required by NCBI for rate-limit contact

# Per-groupement queries for Semantic Scholar
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


# Per-groupement queries for PubMed (uses [tiab] = title+abstract field tag)
_PUBMED_QUERIES: dict[str, list[str]] = {
    "Muscles à base de protéines végétales": [
        "high moisture extrusion plant protein texture[tiab]",
        "plant-based meat analog fibrous structure[tiab]",
    ],
    "Produits élaborés à base de muscle végétaux": [
        "plant-based meat analog texture formulation sensory[tiab]",
        "vegetable protein product water retention cohesion[tiab]",
    ],
    "Nouvelles voies de texturation des protéines végétales": [
        "shear cell plant protein texturization[tiab]",
        "direct shear technology vegetable protein fibrous[tiab]",
    ],
}


def _pubmed_get(endpoint: str, params: dict) -> bytes:
    params.update({"tool": _PUBMED_TOOL, "email": _PUBMED_EMAIL})
    url = f"{_PUBMED_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read()


def _fetch_pubmed(query: str, limit: int) -> list[dict]:
    """Search PubMed and return a list of dicts with title/authors/year/abstract."""
    # Step 1 — esearch: get PMIDs
    raw = _pubmed_get("esearch.fcgi", {
        "db": "pubmed", "term": query,
        "retmax": limit, "retmode": "json",
        "datetype": "pdat", "mindate": "2010",
    })
    pmids = json.loads(raw).get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []

    time.sleep(0.4)  # NCBI: 3 req/s without API key

    # Step 2 — efetch: get full records with abstracts
    raw_xml = _pubmed_get("efetch.fcgi", {
        "db": "pubmed", "id": ",".join(pmids),
        "retmode": "xml", "rettype": "abstract",
    })
    root = ET.fromstring(raw_xml)
    papers = []
    for article in root.findall(".//PubmedArticle"):
        mc = article.find("MedlineCitation")
        if mc is None:
            continue
        art = mc.find("Article")
        if art is None:
            continue
        title = (art.findtext("ArticleTitle") or "").strip()
        if not title:
            continue
        # Year — try multiple paths
        year = (
            mc.findtext(".//PubDate/Year")
            or mc.findtext(".//PubDate/MedlineDate", "?")[:4]
        )
        # Authors
        authors = [
            " ".join(filter(None, [a.findtext("ForeName"), a.findtext("LastName")]))
            for a in (art.findall("AuthorList/Author") or [])
            if a.findtext("LastName")
        ]
        # Abstract — may be structured (multiple AbstractText elements)
        abstract = " ".join(
            (t.text or "") for t in art.findall("Abstract/AbstractText") if t.text
        ).strip()
        pmid = mc.findtext("PMID") or ""
        papers.append({
            "pmid": pmid, "title": title,
            "authors": authors, "year": year, "abstract": abstract,
        })
    return papers


def _build_client() -> SemanticScholar:
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "") or None
    # retry=False: 30s×10 retries is too slow for a user-facing flow.
    # We handle degradation gracefully by catching exceptions per query.
    return SemanticScholar(api_key=api_key, timeout=10, retry=False)


def _normalize_title(title: str) -> str:
    """Lowercase + strip punctuation for deduplication across sources."""
    return "".join(c for c in title.lower() if c.isalnum() or c == " ").strip()


def _format_paper(p: object | dict) -> str:
    """Format a paper from either Semantic Scholar (object) or PubMed (dict)."""
    if isinstance(p, dict):
        title = p.get("title") or "Sans titre"
        year = p.get("year") or "?"
        raw_authors = p.get("authors") or []
        authors = ", ".join(raw_authors[:3]) + (" et al." if len(raw_authors) > 3 else "")
        abstract = (p.get("abstract") or "").strip()
        citations = None
    else:
        title = getattr(p, "title", None) or "Sans titre"
        year = getattr(p, "year", None) or "?"
        raw_authors = getattr(p, "authors", None) or []
        names = [getattr(a, "name", "") for a in raw_authors[:3]]
        authors = ", ".join(names) + (" et al." if len(raw_authors) > 3 else "")
        abstract = (getattr(p, "abstract", None) or "").strip()
        citations = getattr(p, "citationCount", None)

    if len(abstract) > _MAX_ABSTRACT_CHARS:
        abstract = abstract[:_MAX_ABSTRACT_CHARS].rsplit(" ", 1)[0] + "…"
    line = f"- {title} ({year})"
    if authors:
        line += f" — {authors}"
    if citations is not None:
        line += f" [{citations} citations]"
    if abstract:
        line += f"\n  {abstract}"
    return line


def _collect_s2(groupement: str, max_papers: int) -> list[object]:
    """Fetch papers from Semantic Scholar for the given groupement."""
    queries = _QUERIES.get(groupement, [])
    if not queries:
        return []
    sch = _build_client()
    seen: set[str] = set()
    papers: list[object] = []
    per_query = max(2, max_papers // len(queries) + 1)
    for query in queries:
        if len(papers) >= max_papers:
            break
        try:
            results = list(sch.search_paper(
                query,
                fields=_FIELDS,
                fields_of_study=_FIELDS_OF_STUDY,
                year=_YEAR_FILTER,
                min_citation_count=_MIN_CITATIONS,
                limit=per_query,
            ))
        except Exception as exc:
            _log.warning("Semantic Scholar unavailable (%s) — skipping: %s", exc, query)
            continue
        for p in results:
            pid = getattr(p, "paperId", None) or getattr(p, "title", "")
            if pid and pid not in seen and getattr(p, "title", None):
                seen.add(str(pid))
                papers.append(p)
            if len(papers) >= max_papers:
                break
    return papers


def _collect_pubmed(groupement: str, max_papers: int) -> list[dict]:
    """Fetch papers from PubMed for the given groupement."""
    queries = _PUBMED_QUERIES.get(groupement, [])
    if not queries:
        return []
    seen: set[str] = set()
    papers: list[dict] = []
    per_query = max(2, max_papers // len(queries) + 1)
    for query in queries:
        if len(papers) >= max_papers:
            break
        try:
            results = _fetch_pubmed(query, limit=per_query)
        except Exception as exc:
            _log.warning("PubMed unavailable (%s) — skipping: %s", exc, query)
            continue
        for p in results:
            pid = p.get("pmid") or p.get("title", "")
            if pid and pid not in seen and p.get("title"):
                seen.add(str(pid))
                papers.append(p)
            if len(papers) >= max_papers:
                break
    return papers


def fetch_literature(groupement: str, max_papers: int = 8) -> str:
    """Return a formatted literature block to inject into the CIR system prompt.

    Queries both Semantic Scholar and PubMed, deduplicates by normalized title,
    sorts by citation count. Returns empty string on failure or unknown groupement.
    Results are cached 1h per groupement to protect API quotas.
    """
    if groupement not in _QUERIES and groupement not in _PUBMED_QUERIES:
        return ""

    cached, ts = _cache.get(groupement, ("", 0.0))
    if time.time() - ts < _CACHE_TTL_S:
        _log.debug("literature cache hit for %s", groupement)
        return cached

    per_source = max(4, max_papers // 2)
    s2_papers = _collect_s2(groupement, per_source)
    pubmed_papers = _collect_pubmed(groupement, per_source)

    # Deduplicate across sources by normalized title
    seen_titles: set[str] = set()
    all_papers: list[object | dict] = []
    for p in s2_papers:
        norm = _normalize_title(getattr(p, "title", "") or "")
        if norm and norm not in seen_titles:
            seen_titles.add(norm)
            all_papers.append(p)
    for p in pubmed_papers:
        norm = _normalize_title(p.get("title", "") or "")
        if norm and norm not in seen_titles:
            seen_titles.add(norm)
            all_papers.append(p)

    if not all_papers:
        _cache[groupement] = ("", time.time())
        return ""

    # Sort: S2 papers with citationCount first, then PubMed (no citation count)
    all_papers.sort(
        key=lambda p: getattr(p, "citationCount", None) or 0
        if not isinstance(p, dict) else 0,
        reverse=True,
    )
    batch = all_papers[:max_papers]
    formatted = "\n".join(_format_paper(p) for p in batch)
    n_s2 = sum(1 for p in batch if not isinstance(p, dict))
    n_pm = sum(1 for p in batch if isinstance(p, dict))
    sources = " + ".join(filter(None, [
        f"Semantic Scholar ×{n_s2}" if n_s2 else "",
        f"PubMed ×{n_pm}" if n_pm else "",
    ]))
    result = (
        f"RÉFÉRENCES DE LA LITTÉRATURE SCIENTIFIQUE ({len(batch)} articles — {sources}) :\n"
        "Ces résumés illustrent l'état de l'art général du domaine. "
        "Ne citer aucun titre ni auteur dans la fiche sans en avoir vérifié le contenu exact.\n\n"
        f"{formatted}"
    )
    _cache[groupement] = (result, time.time())
    return result
