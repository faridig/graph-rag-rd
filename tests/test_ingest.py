"""Unit tests for ingestion helpers — no external services required."""

from __future__ import annotations

import json
from pathlib import Path

from src.ingest.embed_chunks import chunk_documentation, deterministic_chunk_id
from src.ingest.import_neo4j import (
    excel_artifact_to_none,
    normalize_ingredient_name,
    parse_knowledge_json,
)

# ── excel_artifact_to_none ────────────────────────────────────────────────────


def test_excel_artifacts_become_none():
    for artifact in ("#DIV/0!", "#VALUE!", "#N/A", "#REF!", "#NAME?", "#NULL!", "#NUM!"):
        assert excel_artifact_to_none(artifact) is None


def test_excel_artifact_passthrough_normal_values():
    assert excel_artifact_to_none("normal text") == "normal text"
    assert excel_artifact_to_none(42) == 42
    assert excel_artifact_to_none(None) is None
    assert excel_artifact_to_none("") == ""


def test_excel_artifact_whitespace_stripped_before_check():
    assert excel_artifact_to_none("  #DIV/0!  ") is None


# ── normalize_ingredient_name ─────────────────────────────────────────────────


def test_normalize_ingredient_name_trims_and_titles():
    assert normalize_ingredient_name("  pisane es  ") == "Pisane Es"


def test_normalize_ingredient_name_collapses_spaces():
    assert normalize_ingredient_name("gluten  de  ble") == "Gluten De Ble"


def test_normalize_ingredient_name_already_normalized():
    assert normalize_ingredient_name("Nutralys S85F") == "Nutralys S85F"


# ── deterministic_chunk_id ────────────────────────────────────────────────────


def test_chunk_id_is_deterministic():
    id1 = deterministic_chunk_id("ACE-3:Run:1", "ACE-3_documentation.md")
    id2 = deterministic_chunk_id("ACE-3:Run:1", "ACE-3_documentation.md")
    assert id1 == id2
    assert len(id1) == 16


def test_chunk_id_differs_for_different_run():
    id1 = deterministic_chunk_id("ACE-3:Run:1", "ACE-3_documentation.md")
    id2 = deterministic_chunk_id("ACE-3:Run:2", "ACE-3_documentation.md")
    assert id1 != id2


def test_chunk_id_differs_for_different_source():
    id1 = deterministic_chunk_id("ACE-3:Run:1", "ACE-3_documentation.md")
    id2 = deterministic_chunk_id("ACE-3:Run:1", "ACE-5_documentation.md")
    assert id1 != id2


# ── chunk_documentation — REPERTOIRE ─────────────────────────────────────────

_EM = "—"  # em-dash used in section headers


def _make_repertoire_doc(tmp_path: Path) -> Path:
    doc = tmp_path / "REPERTOIRE-RD-2025-2026_documentation.md"
    doc.write_text(
        f"# REPERTOIRE\n\n"
        f"### Run M03 {_EM} Test huile\n"
        f"- factors: chantier=Extrusion, pole=Applications\n"
        f"- date: 2024-01-15\n"
        f"- objective: Tester l'effet de l'huile\n"
        f"- synthesis: Amélioration de la texture\n\n"
        f"### Run EMPTY {_EM} Aucun contenu\n"
        f"- factors: chantier=Extrusion\n\n",
        encoding="utf-8",
    )
    return doc


def test_chunk_documentation_excludes_empty_runs(tmp_path):
    doc = _make_repertoire_doc(tmp_path)
    chunks = chunk_documentation(doc, "REPERTOIRE")
    run_ids = [c["run_id"] for c in chunks]
    assert any("M03" in r for r in run_ids)
    assert not any("EMPTY" in r for r in run_ids)


def test_chunk_payload_has_required_fields(tmp_path):
    doc = _make_repertoire_doc(tmp_path)
    chunks = chunk_documentation(doc, "REPERTOIRE")
    assert chunks
    required = {
        "id",
        "text",
        "source_file",
        "experiment_id",
        "run_id",
        "chantier",
        "date",
        "lead",
        "type",
        "pole",
    }
    assert required <= set(chunks[0].keys())


def test_chunk_id_is_16_chars(tmp_path):
    doc = _make_repertoire_doc(tmp_path)
    chunks = chunk_documentation(doc, "REPERTOIRE")
    assert all(len(c["id"]) == 16 for c in chunks)


def test_chunk_experiment_id_is_repertoire(tmp_path):
    doc = _make_repertoire_doc(tmp_path)
    chunks = chunk_documentation(doc, "REPERTOIRE")
    assert all(c["experiment_id"] == "REPERTOIRE-RD-2025-2026" for c in chunks)


def test_chunk_source_type_unknown_uses_generic_path(tmp_path):
    doc = tmp_path / "MY-EXP_documentation.md"
    doc.write_text(
        "# MY-EXP\n\n### Run 1 — Essai 1\n- factors: x=1\n\nTexte.\n",
        encoding="utf-8",
    )
    chunks = chunk_documentation(doc, "MY-EXP")
    assert len(chunks) >= 1
    assert chunks[0]["experiment_id"] == "MY-EXP"


def _make_allumette_doc(tmp_path: Path) -> Path:
    doc = tmp_path / "Allumette_documentation.md"
    doc.write_text(
        f"# Allumette\n\n"
        f"### Run 1 {_EM} Allumette essai 1 - Sel 0.1% matrice  *(control)*\n"
        f"- factors: salt_dose_matrix=0.1\n\n"
        f"Bonne fibration et couleur vive.\n\n"
        f"### Run 2 {_EM} Allumette essai 2 - Sel 1.0% matrice\n"
        f"- factors: salt_dose_matrix=1.0\n\n"
        f"Résultats comparatifs.\n\n"
        f"### Run 3 {_EM} Allumette essai 3 - Sel 3.0% matrice\n"
        f"- factors: salt_dose_matrix=3.0\n\n"
        f"Sel trop élevé — texture dégradée.\n\n",
        encoding="utf-8",
    )
    return doc


def test_chunk_documentation_allumette_experiment_id(tmp_path):
    doc = _make_allumette_doc(tmp_path)
    chunks = chunk_documentation(doc, "Allumette")
    assert chunks
    assert all(c["experiment_id"] == "Allumette" for c in chunks)


def test_chunk_documentation_allumette_run_ids(tmp_path):
    doc = _make_allumette_doc(tmp_path)
    chunks = chunk_documentation(doc, "Allumette")
    run_ids = [c["run_id"] for c in chunks]
    assert "Allumette:Run:1" in run_ids
    assert "Allumette:Run:2" in run_ids
    assert "Allumette:Run:3" in run_ids


def test_chunk_documentation_allumette_run_detail_type(tmp_path):
    doc = _make_allumette_doc(tmp_path)
    chunks = chunk_documentation(doc, "Allumette")
    assert all(c["type"] == "run_detail" for c in chunks)


def test_chunk_documentation_allumette_count(tmp_path):
    doc = _make_allumette_doc(tmp_path)
    chunks = chunk_documentation(doc, "Allumette")
    assert len(chunks) == 3


def _make_ace3_doc(tmp_path: Path) -> Path:
    doc = tmp_path / "ACE-3_documentation.md"
    doc.write_text(
        f"# ACE-3\n\n"
        f"### Run 1 {_EM} Ref P02  *(control)*\n"
        f"Texture correcte. Extrudat homogène.\n\n"
        f"### Run 2 {_EM} P02 + 0,2% NaCl\n"
        f"Résultats mitigés.\n\n",
        encoding="utf-8",
    )
    return doc


def _make_ace5_doc(tmp_path: Path) -> Path:
    doc = tmp_path / "ACE-5_documentation.md"
    doc.write_text(
        f"# ACE-5\n\n"
        f"### Run 1 {_EM} Run initial\n"
        f"Amélioration significative de la texture.\n\n"
        f"### Run 2 {_EM} Run comparatif\n"
        f"Résultats comparatifs.\n\n",
        encoding="utf-8",
    )
    return doc


def test_chunk_documentation_ace3_experiment_id(tmp_path):
    doc = _make_ace3_doc(tmp_path)
    chunks = chunk_documentation(doc, "ACE-3")
    assert chunks
    assert all(c["experiment_id"] == "ACE-3" for c in chunks)


def test_chunk_documentation_ace3_run_ids(tmp_path):
    doc = _make_ace3_doc(tmp_path)
    chunks = chunk_documentation(doc, "ACE-3")
    run_ids = [c["run_id"] for c in chunks]
    assert "ACE-3:Run:1" in run_ids
    assert "ACE-3:Run:2" in run_ids


def test_chunk_documentation_ace5_experiment_id(tmp_path):
    doc = _make_ace5_doc(tmp_path)
    chunks = chunk_documentation(doc, "ACE-5")
    assert chunks
    assert all(c["experiment_id"] == "ACE-5" for c in chunks)


def test_chunk_documentation_ace5_run_ids(tmp_path):
    doc = _make_ace5_doc(tmp_path)
    chunks = chunk_documentation(doc, "ACE-5")
    run_ids = [c["run_id"] for c in chunks]
    assert "ACE-5:Run:1" in run_ids
    assert "ACE-5:Run:2" in run_ids


# ── parse_knowledge_json ──────────────────────────────────────────────────────

_REPERTOIRE_JSON = {
    "experiment": {
        "id": "REPERTOIRE-RD-2025-2026",
        "titre": "Répertoire test",
        "type": "REPERTOIRE",
    },
    "runs": [
        {
            "id": "M03",
            "factor_levels": {
                "chantier": "Extrusion",
                "status": "En cours",
                "lead": "J. Martin",
                "pole": "Applications",
                "cir_grouping": "CIR1",
            },
            "conditions": {"date": "2024-01-15"},
            "responses": {
                "objective": {"value": "Tester l'effet de l'huile"},
                "synthesis": {"value": "Amélioration de la texture"},
            },
        }
    ],
}

_ACE3_JSON = {
    "experiment": {
        "id": "ACE-3",
        "title": "ACE-3 test",
        "type": "ACE",
        "objective": "Tester les formulations",
        "date": "2024-03-01",
        "operator": "Jean",
        "equipment": "Clextral BC45",
        "domain": "Extrusion",
        "scale": "pilot",
        "status": "complete",
    },
    "runs": [
        {
            "id": "1",
            "name": "Ref P02",
            "is_control": True,
            "factor_levels": {"NaCl_pct": 0.0},
            "inputs": {
                "formulation": [
                    {"component": "Nutralys S85F"},
                    {"component": "gluten de ble"},
                ]
            },
            "conditions": {},
            "responses": {},
        }
    ],
}

_ACE5_JSON = {
    "experiment": {"id": "ACE-5", "title": "ACE-5 test", "type": "ACE"},
    "runs": [
        {
            "id": 1,
            "name": "Run 1",
            "notes": "Amélioration significative",
            "inputs": {
                "formulation": [
                    {"component": "Pisane S85"},
                    {"component": "huile de tournesol"},
                ]
            },
        }
    ],
}


def _write_json(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_parse_knowledge_json_repertoire_experiment_id(tmp_path):
    path = _write_json(tmp_path, "REPERTOIRE_knowledge.json", _REPERTOIRE_JSON)
    result = parse_knowledge_json(path)
    assert result["experiment"]["id"] == "REPERTOIRE-RD-2025-2026"


def test_parse_knowledge_json_repertoire_run_id(tmp_path):
    path = _write_json(tmp_path, "REPERTOIRE_knowledge.json", _REPERTOIRE_JSON)
    result = parse_knowledge_json(path)
    assert result["runs"][0]["id"] == "REPERTOIRE-RD-2025-2026:Run:M03"


def test_parse_knowledge_json_repertoire_nested_str(tmp_path):
    path = _write_json(tmp_path, "REPERTOIRE_knowledge.json", _REPERTOIRE_JSON)
    result = parse_knowledge_json(path)
    assert result["runs"][0]["objective"] == "Tester l'effet de l'huile"
    assert result["runs"][0]["synthesis"] == "Amélioration de la texture"


def test_parse_knowledge_json_ace3_dispatched_correctly(tmp_path):
    path = _write_json(tmp_path, "ACE-3_knowledge.json", _ACE3_JSON)
    result = parse_knowledge_json(path)
    assert result["experiment"]["id"] == "ACE-3"


def test_parse_knowledge_json_ace3_run_id_and_ingredients(tmp_path):
    path = _write_json(tmp_path, "ACE-3_knowledge.json", _ACE3_JSON)
    result = parse_knowledge_json(path)
    run = result["runs"][0]
    assert run["id"] == "ACE-3:Run:1"
    assert "Nutralys S85F" in run["ingredients"]


def test_parse_knowledge_json_ace3_status_and_scale(tmp_path):
    path = _write_json(tmp_path, "ACE-3_knowledge.json", _ACE3_JSON)
    result = parse_knowledge_json(path)
    assert result["experiment"]["status"] == "complete"
    assert result["experiment"]["scale"] == "pilot"


def test_parse_knowledge_json_ace5_dispatched_correctly(tmp_path):
    path = _write_json(tmp_path, "ACE-5_knowledge.json", _ACE5_JSON)
    result = parse_knowledge_json(path)
    assert result["experiment"]["id"] == "ACE-5"


def test_parse_knowledge_json_ace5_run_ingredients(tmp_path):
    path = _write_json(tmp_path, "ACE-5_knowledge.json", _ACE5_JSON)
    result = parse_knowledge_json(path)
    run = result["runs"][0]
    assert run["id"] == "ACE-5:Run:1"
    assert "Pisane S85" in run["ingredients"]


# ── parse_knowledge_json — Allumette ─────────────────────────────────────────

_ALLUMETTE_JSON = {
    "experiment": {
        "id": "Allumette",
        "title": "Essai Allumette - impact de la teneur en sel",
        "type": "Essai R&D - HME",
        "objective": "Evaluer l'impact du sel",
        "equipment": "Extrudeur bivis HME",
        "domain": "analogue de viande vegetal / texturation HME",
    },
    "runs": [
        {
            "id": "1",
            "name": "Allumette essai 1 - Sel 0.1% matrice",
            "is_control": True,
            "factor_levels": {"salt_dose_matrix": 0.1},
            "inputs": {
                "formulation_HME": [
                    {"component": "Nutralys F853M"},
                    {"component": "Vital Viten Wheat Gluten"},
                    {"component": "Sel"},
                ]
            },
        },
        {
            "id": "2",
            "name": "Allumette essai 2 - Sel 1.0% matrice",
            "inputs": {
                "formulation_HME": [
                    {"component": "Nutralys F853M"},
                    {"component": "Sel"},
                ]
            },
        },
    ],
}


def test_parse_knowledge_json_allumette_dispatched_correctly(tmp_path):
    path = _write_json(tmp_path, "Allumette_knowledge.json", _ALLUMETTE_JSON)
    result = parse_knowledge_json(path)
    assert result["experiment"]["id"] == "Allumette"


def test_parse_knowledge_json_allumette_run_ids(tmp_path):
    path = _write_json(tmp_path, "Allumette_knowledge.json", _ALLUMETTE_JSON)
    result = parse_knowledge_json(path)
    run_ids = [r["id"] for r in result["runs"]]
    assert "Allumette:Run:1" in run_ids
    assert "Allumette:Run:2" in run_ids


def test_parse_knowledge_json_allumette_ingredients_from_formulation_hme(tmp_path):
    path = _write_json(tmp_path, "Allumette_knowledge.json", _ALLUMETTE_JSON)
    result = parse_knowledge_json(path)
    run = result["runs"][0]
    assert "Nutralys F853M" in run["ingredients"]
    assert "Vital Viten Wheat Gluten" in run["ingredients"]
    assert "Sel" in run["ingredients"]


def test_parse_knowledge_json_allumette_not_dispatched_as_ace5(tmp_path):
    path = _write_json(tmp_path, "Allumette_knowledge.json", _ALLUMETTE_JSON)
    result = parse_knowledge_json(path)
    # ACE-5 reads formulation[], Allumette reads formulation_HME[] — must not be empty
    assert len(result["runs"][0]["ingredients"]) > 0
