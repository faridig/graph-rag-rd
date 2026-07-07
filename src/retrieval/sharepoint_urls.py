"""SharePoint URL resolution — static fallback + dynamic lookup from download.log and Neo4j."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

_log = logging.getLogger(__name__)

_SP_BASE = "https://nxtfoodfr.sharepoint.com"
_BASE = f"{_SP_BASE}/sites/RD/Documents%20partages"

_ESSAIS_MDD_BASE = (
    f"{_SP_BASE}/:x:/r/sites/RD/_layouts/15/Doc.aspx"
    "?sourcedoc=%7BA114D55E-1F7F-5AAC-79FE-6826168B5315%7D"
    "&file=Essais%20MDD.xlsx&action=default&mobileredirect=true"
)

# Experiment-level URLs with sheet anchors for MDD onglet experiments
_MDD_EXPERIMENT_URLS: dict[str, str] = {
    "MDD-STEAKS-BURGER": f"{_ESSAIS_MDD_BASE}#'steaks burger'!A1",
    "ESSAIS-MDD-STEAKS-CARREFOUR": f"{_ESSAIS_MDD_BASE}#'Steaks Carrefour'!A1",
    "ESSAIS-MDD-ALLUMETTES": f"{_ESSAIS_MDD_BASE}#'Allumettes'!A1",
    "ESSAIS-MDD-KEFTA": f"{_ESSAIS_MDD_BASE}#'kefta'!A1",
    "ESSAIS-MDD-EMINCÉS-TEX-MEX": f"{_ESSAIS_MDD_BASE}#'émincés tex mex'!A1",
    "MDD-ESSAIS": _ESSAIS_MDD_BASE,
}

# Static fallback for the 5 experiments that existed before dynamic URL tracking
_STATIC_EXPERIMENT_URLS: dict[str, str] = {
    "REPERTOIRE-RD-2025-2026": (
        f"{_SP_BASE}/:x:/r/sites/RD/_layouts/15/Doc.aspx"
        "?sourcedoc=%7B3E7753D0-1871-4786-9EF4-040E3B08AF38%7D"
        "&file=Chantier%20%26%20R%C3%A9pertoire%20Essais%20R%26D%202025.xlsx"
        "&action=default&mobileredirect=true"
    ),
    "ACE-3": (
        f"{_BASE}/2.%20Extrusion/3.%20Projets/2025"
        "/10.%20Aromatisation%20des%20bandes/Effet%20du%20sel%20en%20extrusion"
        "/ACE-3-Impact%20NaCl%20et%20KCl%20sur%20P02.xlsx?web=1"
    ),
    "ACE-5": (
        f"{_BASE}/2.%20Extrusion/3.%20Projets/2025"
        "/09.%20Int%C3%A9gration%20d%27huile%20en%20extrusion"
        "/ACE-5%20Impact%20huile%20sur%20M03"
        "/ACE-5-Impact%20huile%20sur%20M03.xlsx?web=1"
    ),
    "Allumette": (
        f"{_BASE}/2.%20Extrusion/3.%20Projets/2025"
        "/13.%20Maitrise%20absorption%20des%20marinades"
        "/Essai%20Allumette-5.xlsx?web=1"
    ),
    "ESC-QUICK": (
        f"{_BASE}/3.%20Post-extrusion/3.%20Projet%20produits%20pan%C3%A9s"
        "/Farmchix/Escalopes/Escalope%20pan%C3%A9e%20Quick.xlsx?web=1"
    ),
    "emince_mdd": (
        f"{_SP_BASE}/:x:/r/sites/RD/_layouts/15/Doc.aspx"
        "?sourcedoc=%7BDA0BD7AA-8C76-4A20-B298-B765367EE2C9%7D"
        "&file=Eminc%C3%A9s%20tha%C3%AF%20et%20kebab%20MDD.xlsx&action=default&mobileredirect=true"
    ),
}

_RUN_PREFIX_URLS: dict[str, str] = {
    "BOULETTEIT": f"{_ESSAIS_MDD_BASE}#'boulettes italiennes'!A1",
    "PIPE25": f"{_ESSAIS_MDD_BASE}#'kefta'!A1",
    "ALLUMETTE": f"{_ESSAIS_MDD_BASE}#'Allumettes'!A1",
}

_PREFIX_RE = re.compile(r"^([A-Za-z]+)")

# (.*?) with lookahead on ' url= correctly handles apostrophes in labels
# e.g. "Rapport d'EI Croquetas N°1.docx" is captured in full, not truncated at d'
_LOG_ENTRY_RE = re.compile(r"label='(.*?)'\s+url=(\S+)")

# Absolute path relative to this source file:
#   sharepoint_urls.py → parents[0]=retrieval/ → parents[1]=src/ → parents[2]=project root
_DOWNLOAD_LOG = (
    Path(__file__).resolve().parents[2]
    / "data/repertoire_rd_2025-2026/lien_essai/lien_essai_brut/download.log"
)


def _normalize_url(url: str) -> str | None:
    """Convert relative or malformed SharePoint URLs to absolute ones.

    Handles:
    - Already absolute: https://nxtfoodfr.sharepoint.com/...
    - Relative with :x:/s/ or :x:/r/ sharing path after ../ chain
    - Relative file paths (../../path) → unresolvable → None
    - action=editnew → replaced with action=default
    - Double-encoded chars (%25uXXXX → %uXXXX, %25XX → %XX)
    """
    # Already absolute
    if url.startswith("https://"):
        absolute = url
    else:
        # Strip leading ../ chain, then check for SharePoint sharing pattern
        stripped = re.sub(r"^(\.\./)+", "", url)
        if re.match(r":[a-z]:/[sr]/", stripped):
            absolute = f"{_SP_BASE}/{stripped}"
        else:
            # Relative file path — cannot resolve without SharePoint tree context
            return None

    # Fix action=editnew (would create a new document instead of opening the existing one)
    absolute = absolute.replace("action=editnew", "action=default")

    # Fix double-encoded percent sequences: %25XX → %XX
    absolute = re.sub(r"%25([0-9A-Fa-f]{2})", r"%\1", absolute)

    # Fix JavaScript-style unicode escapes %uXXXX or %25uXXXX → proper UTF-8 percent-encoding
    def _encode_unicode_escape(m: re.Match) -> str:
        char = chr(int(m.group(1), 16))
        return "".join(f"%{b:02X}" for b in char.encode("utf-8"))

    absolute = re.sub(r"%25u([0-9A-Fa-f]{4})", lambda m: _encode_unicode_escape(m), absolute)
    absolute = re.sub(r"%u([0-9A-Fa-f]{4})", lambda m: _encode_unicode_escape(m), absolute)

    return absolute


_WEBURL_RE = re.compile(r"WEBURL: label='(.*?)'\s+url=(\S+)")


@lru_cache(maxsize=1)
def _load_log_urls() -> dict[str, str]:
    """Parse download.log → {normalized_label: sharepoint_url}.

    WEBURL entries (recorded on successful download, include real webUrl from Graph)
    take priority over LIEN MORT entries (hyperlinks from RÉPERTOIRE, may be stale).

    Cached after first call. Labels are lowercased and stripped of extension
    for fuzzy matching.
    """
    if not _DOWNLOAD_LOG.exists():
        _log.warning(
            "download.log not found at %s — SharePoint URLs will use static fallback only",
            _DOWNLOAD_LOG,
        )
        return {}

    text = _DOWNLOAD_LOG.read_text(encoding="utf-8", errors="replace")
    mapping: dict[str, str] = {}

    # Pass 1: LIEN MORT entries (lower priority — may have bad action= or wrong GUIDs)
    for label, raw_url in _LOG_ENTRY_RE.findall(text):
        url = _normalize_url(raw_url)
        if not url:
            continue
        key = re.sub(r"\.(xlsx|xlsm|docx|csv)$", "", label, flags=re.IGNORECASE).lower().strip()
        mapping[key] = url
        mapping[label.lower().strip()] = url

    # Pass 2: WEBURL entries (higher priority — real webUrl from Graph API)
    for label, web_url in _WEBURL_RE.findall(text):
        url = _normalize_url(web_url) or web_url  # webUrl is already absolute
        key = re.sub(r"\.(xlsx|xlsm|docx|csv)$", "", label, flags=re.IGNORECASE).lower().strip()
        mapping[key] = url
        mapping[label.lower().strip()] = url

    return mapping


def get_url_for_file(filename: str) -> str | None:
    """Look up SharePoint URL by source filename (from download.log)."""
    log_map = _load_log_urls()
    # Try exact match first (lowercase)
    key = filename.lower().strip()
    if key in log_map:
        return log_map[key]
    # Try without extension
    key_no_ext = re.sub(r"\.(xlsx|xlsm|docx|csv)$", "", key, flags=re.IGNORECASE).strip()
    return log_map.get(key_no_ext)


# ---------------------------------------------------------------------------
# Public API (used by rag_pipeline.py)


def get_sharepoint_url(experiment_id: str) -> str | None:
    """Return SharePoint URL for an experiment_id (static fallback only).

    The primary lookup is via Neo4j (Experiment.sharepoint_url property).
    This function is the last-resort fallback for legacy experiments.
    """
    return _STATIC_EXPERIMENT_URLS.get(experiment_id) or _MDD_EXPERIMENT_URLS.get(experiment_id)


def get_sharepoint_url_for_run(run_id: str) -> str | None:
    """Return onglet-level SharePoint URL for a run (static fallback only)."""
    local = run_id.split(":Run:", 1)[-1] if ":Run:" in run_id else run_id
    m = _PREFIX_RE.match(local)
    if not m:
        return None
    return _RUN_PREFIX_URLS.get(m.group(1).upper())
