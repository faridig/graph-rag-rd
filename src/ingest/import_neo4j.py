"""Import _knowledge.json files into Neo4j (Experiment/Run/Ingredient/Chantier/Lead nodes)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase

from src.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from src.retrieval.sharepoint_urls import get_sharepoint_url, get_url_for_file

logger = logging.getLogger(__name__)

_EXCEL_ARTIFACTS: frozenset[str] = frozenset(
    {"#DIV/0!", "#VALUE!", "#N/A", "#REF!", "#NAME?", "#NULL!", "#NUM!"}
)

_LIEN_ESSAI_DIR = Path("data/repertoire_rd_2025-2026/lien_essai")

_FIXED_PATHS: list[Path] = [
    Path("data/repertoire_rd_2025-2026/REPERTOIRE-RD-2025-2026_knowledge.json"),
]


def _discover_knowledge_paths() -> list[Path]:
    """Return all *_knowledge.json files under lien_essai/, sorted for stable ordering."""
    return sorted(_LIEN_ESSAI_DIR.glob("**/*_knowledge.json"))


def _get_data_paths() -> list[Path]:
    return _FIXED_PATHS + _discover_knowledge_paths()


def excel_artifact_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip() in _EXCEL_ARTIFACTS:
        return None
    return value


def normalize_ingredient_name(raw: str) -> str:
    return " ".join(raw.strip().split()).title()


def _nested_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return excel_artifact_to_none(value.get("value"))
    return excel_artifact_to_none(str(value))


def _parse_repertoire(path: Path, data: dict) -> dict:
    exp_raw = data["experiment"]
    experiment: dict[str, Any] = {
        "id": exp_raw["id"],
        "title": exp_raw.get("title", exp_raw.get("titre", "")),
        "type": exp_raw.get("type"),
        "objective": exp_raw.get("objective", exp_raw.get("objectif")),
        "date": exp_raw.get("date"),
        "operator": exp_raw.get("operator", exp_raw.get("operateur")),
        "equipment": exp_raw.get("equipment", exp_raw.get("extrudeuse")),
        "domain": exp_raw.get("domain", exp_raw.get("domaine")),
        "source_file": path.name,
    }
    exp_id = experiment["id"]
    runs: list[dict] = []
    for raw_run in data.get("runs", []):
        fl = raw_run.get("factor_levels", {})
        cond = raw_run.get("conditions", {})
        resp = raw_run.get("responses", {})
        local_id = str(raw_run["id"])
        runs.append(
            {
                "id": f"{exp_id}:Run:{local_id}",
                "name": raw_run.get("name", local_id),
                "objective": _nested_str(resp.get("objective")),
                "synthesis": _nested_str(resp.get("synthesis")),
                "status": fl.get("status"),
                "date": _nested_str(cond.get("date")),
                "chantier": fl.get("chantier"),
                "lead": fl.get("lead"),
                "pole": fl.get("pole"),
                "cir_grouping": fl.get("cir_grouping"),
                "ingredients_raw": [],
                "ingredients": [],
            }
        )
    return {"experiment": experiment, "runs": runs}


def _parse_experiment_header(path: Path, exp_raw: dict) -> dict[str, Any]:
    exp_id = exp_raw["id"]
    # URL priority: explicit in JSON → download.log → static fallback
    sharepoint_url = (
        exp_raw.get("sharepoint_url")
        or get_url_for_file(exp_raw.get("source_file", ""))
        or get_sharepoint_url(exp_id)
    )
    return {
        "id": exp_id,
        "title": exp_raw.get("title", exp_raw.get("titre", "")),
        "type": exp_raw.get("type"),
        "objective": exp_raw.get("objective", exp_raw.get("objectif")),
        "date": exp_raw.get("date"),
        "operator": exp_raw.get("operator", exp_raw.get("operateur")),
        "equipment": exp_raw.get("equipment", exp_raw.get("extrudeuse")),
        "domain": exp_raw.get("domain", exp_raw.get("domaine")),
        "scale": exp_raw.get("scale"),
        "status": exp_raw.get("status"),
        "sharepoint_url": sharepoint_url,
        "source_file": path.name,
    }


def _find_formulation_list(inputs: dict, preferred_key: str) -> list[dict]:
    """Return the first non-empty list from inputs, preferring preferred_key."""
    if isinstance(inputs.get(preferred_key), list):
        return inputs[preferred_key]
    for v in inputs.values():
        if isinstance(v, list) and v:
            return v
    return []


def _parse_runs_with_formulation(data: dict, exp_id: str, formulation_key: str) -> list[dict]:
    runs: list[dict] = []
    for raw_run in data.get("runs", []):
        local_id = str(raw_run["id"])
        comp_list = _find_formulation_list(raw_run.get("inputs", {}), formulation_key)
        raw_ings: list[str] = [item["component"] for item in comp_list if item.get("component")]
        notes = raw_run.get("notes")
        runs.append(
            {
                "id": f"{exp_id}:Run:{local_id}",
                "name": raw_run.get("name", local_id),
                "objective": None,
                "synthesis": notes if isinstance(notes, str) else None,
                "status": raw_run.get("status"),
                "date": None,
                "chantier": None,
                "lead": None,
                "pole": None,
                "cir_grouping": None,
                "ingredients_raw": raw_ings,
                "ingredients": [normalize_ingredient_name(i) for i in raw_ings],
            }
        )
    return runs


def _parse_ace5(path: Path, data: dict) -> dict:
    experiment = _parse_experiment_header(path, data["experiment"])
    return {
        "experiment": experiment,
        "runs": _parse_runs_with_formulation(data, experiment["id"], "formulation"),
    }


def parse_knowledge_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    exp_id = data.get("experiment", {}).get("id", "")
    if exp_id.upper().startswith("REPERTOIRE"):
        return _parse_repertoire(path, data)
    experiment = _parse_experiment_header(path, data["experiment"])
    runs_raw = data.get("runs", [])
    formulation_key = (
        "formulation_HME"
        if runs_raw and "formulation_HME" in runs_raw[0].get("inputs", {})
        else "formulation"
    )
    return {
        "experiment": experiment,
        "runs": _parse_runs_with_formulation(data, experiment["id"], formulation_key),
        "references": data.get("references", []),
    }


def import_source(driver: Driver, data: dict) -> dict:
    exp = data["experiment"]
    runs = data["runs"]
    counts: dict[str, int] = {
        "experiments": 0,
        "runs": 0,
        "chantiers": 0,
        "leads": 0,
        "ingredients": 0,
    }

    with driver.session() as session:
        session.run(
            """
            MERGE (e:Experiment {id: $id})
            SET e.title = $title, e.type = $type, e.objective = $objective,
                e.date = $date, e.operator = $operator, e.equipment = $equipment,
                e.domain = $domain, e.scale = $scale, e.status = $status,
                e.sharepoint_url = $sharepoint_url, e.source_file = $source_file
            """,
            id=exp["id"],
            title=exp.get("title", ""),
            scale=exp.get("scale"),
            status=exp.get("status"),
            type=exp.get("type"),
            objective=exp.get("objective"),
            date=exp.get("date"),
            operator=exp.get("operator"),
            equipment=exp.get("equipment"),
            domain=exp.get("domain"),
            sharepoint_url=exp.get("sharepoint_url"),
            source_file=exp.get("source_file", ""),
        )
        counts["experiments"] = 1

        run_rows = [
            {
                "id": r["id"],
                "name": r["name"],
                "objective": r.get("objective"),
                "synthesis": r.get("synthesis"),
                "status": r.get("status"),
                "date": r.get("date"),
                "chantier": r.get("chantier"),
                "lead": r.get("lead"),
                "pole": r.get("pole"),
                "cir_grouping": r.get("cir_grouping"),
                "exp_id": exp["id"],
            }
            for r in runs
        ]
        for i in range(0, len(run_rows), 500):
            result = session.run(
                """
                UNWIND $rows AS r
                MERGE (run:Run {id: r.id})
                SET run.name = r.name, run.objective = r.objective,
                    run.synthesis = r.synthesis, run.status = r.status,
                    run.date = r.date, run.chantier = r.chantier,
                    run.lead = r.lead, run.pole = r.pole,
                    run.cir_grouping = r.cir_grouping
                WITH run, r
                MATCH (exp:Experiment {id: r.exp_id})
                MERGE (exp)-[:HAS_RUN]->(run)
                RETURN count(run) AS cnt
                """,
                rows=run_rows[i : i + 500],
            )
            counts["runs"] += result.single()["cnt"]

        chantier_pairs = [
            {"run_id": r["id"], "chantier": r["chantier"]} for r in runs if r.get("chantier")
        ]
        for i in range(0, len(chantier_pairs), 500):
            result = session.run(
                """
                UNWIND $rows AS row
                MATCH (run:Run {id: row.run_id})
                MERGE (ch:Chantier {name: row.chantier})
                MERGE (run)-[:BELONGS_TO]->(ch)
                RETURN count(DISTINCT ch) AS cnt
                """,
                rows=chantier_pairs[i : i + 500],
            )
            counts["chantiers"] += result.single()["cnt"]

        lead_pairs = [{"run_id": r["id"], "lead": r["lead"]} for r in runs if r.get("lead")]
        for i in range(0, len(lead_pairs), 500):
            result = session.run(
                """
                UNWIND $rows AS row
                MATCH (run:Run {id: row.run_id})
                MERGE (lead:Lead {name: row.lead})
                MERGE (run)-[:LED_BY]->(lead)
                RETURN count(DISTINCT lead) AS cnt
                """,
                rows=lead_pairs[i : i + 500],
            )
            counts["leads"] += result.single()["cnt"]

        ing_pairs = [
            {
                "run_id": r["id"],
                "name": r["ingredients"][idx],
                "name_raw": r["ingredients_raw"][idx],
            }
            for r in runs
            for idx in range(len(r.get("ingredients", [])))
        ]
        for i in range(0, len(ing_pairs), 500):
            result = session.run(
                """
                UNWIND $rows AS row
                MATCH (run:Run {id: row.run_id})
                MERGE (ing:Ingredient {name: row.name})
                ON CREATE SET ing.name_raw = row.name_raw
                MERGE (run)-[:USES_INGREDIENT]->(ing)
                RETURN count(DISTINCT ing) AS cnt
                """,
                rows=ing_pairs[i : i + 500],
            )
            counts["ingredients"] += result.single()["cnt"]

        # Cross-experiment [:REFERENCES] edges (new field from batch extraction)
        def _ref_id(r: Any) -> str | None:
            if isinstance(r, dict):
                return r.get("id")
            return r if isinstance(r, str) else None

        ref_pairs = [
            {"from_id": exp["id"], "to_id": rid}
            for r in data.get("references", [])
            if (rid := _ref_id(r))
        ]
        if ref_pairs:
            session.run(
                """
                UNWIND $rows AS row
                MERGE (src:Experiment {id: row.from_id})
                MERGE (tgt:Experiment {id: row.to_id})
                MERGE (src)-[:REFERENCES]->(tgt)
                """,
                rows=ref_pairs,
            )
            counts["references"] = len(ref_pairs)

    return counts


_REPERTOIRE_PREFIX = "REPERTOIRE-RD-2025-2026:Run:"

# REPERTOIRE runs whose local ID doesn't match an Experiment.id via simple suffix comparison.
# Maps local_run_id → list of target experiment IDs.
_DETAILS_OVERRIDES: dict[str, list[str]] = {
    # KEFTA-BOULETTES-LAB
    **{f"KEFTA-{i}": ["KEFTA-BOULETTES-LAB"] for i in range(1, 21)},
    # MDD-EMINCE-THAI-KEBAB  (PIPE25 rows 19, 20, 31–39)
    **{
        f"PIPE25-{i}": ["MDD-EMINCE-THAI-KEBAB"]
        for i in [19, 20, 31, 32, 33, 34, 35, 36, 37, 38, 39]
    },
    # KOBE-1→23
    "KOBE-1": ["KOBE-AROMES-GIVAUDAN"],
    **{
        f"KOBE-{i}": ["ESSAIS-TVP-HACHE", "TVP-HACHE"]
        for i in [2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
    },
    "KOBE-9": ["MORTEAU-ESSAIS-LABO"],
    "KOBE-23": ["KOBE-2026-AMELIORATIONS"],
}


def build_details_relations(driver: Driver) -> int:
    total = 0
    with driver.session() as session:
        # 1. Direct ID match: REPERTOIRE run suffix == Experiment.id
        result = session.run(
            """
            MATCH (run:Run) WHERE run.id STARTS WITH "REPERTOIRE-RD-2025-2026:Run:"
            WITH run, toUpper(trim(split(run.id, ":Run:")[1])) AS exp_key
            MATCH (exp:Experiment) WHERE toUpper(trim(exp.id)) = exp_key
            MERGE (run)-[:DETAILS]->(exp)
            RETURN count(*) AS cnt
            """
        )
        total += result.single()["cnt"]

        # 2. Manual overrides (Jaccard normalization failures)
        pairs = [
            {"run_id": _REPERTOIRE_PREFIX + local_id, "exp_id": exp_id}
            for local_id, exp_ids in _DETAILS_OVERRIDES.items()
            for exp_id in exp_ids
        ]
        if pairs:
            session.run(
                """
                UNWIND $pairs AS p
                MATCH (run:Run {id: p.run_id})
                MATCH (exp:Experiment {id: p.exp_id})
                MERGE (run)-[:DETAILS]->(exp)
                """,
                pairs=pairs,
            )

    # Re-count total after both passes
    with driver.session() as session:
        total = session.run(
            "MATCH (:Run)-[:DETAILS]->(:Experiment) RETURN count(*) AS cnt"
        ).single()["cnt"]

    logger.info("[:DETAILS] edges total: %d", total)
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        data_paths = _get_data_paths()
        logger.info("Importing %d knowledge files", len(data_paths))
        total: dict[str, int] = {}
        for path in data_paths:
            logger.info("Importing %s", path.name)
            data = parse_knowledge_json(path)
            counts = import_source(driver, data)
            logger.info("  %s", counts)
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v

        details = build_details_relations(driver)
        total["details_relations"] = details
        logger.info("Import complete: %s", total)

        with driver.session() as session:
            run_count = session.run("MATCH (r:Run) RETURN count(r) AS cnt").single()["cnt"]
            det_count = session.run(
                "MATCH (:Run)-[:DETAILS]->(:Experiment) RETURN count(*) AS cnt"
            ).single()["cnt"]
            logger.info("Validation: %d runs, %d [:DETAILS]", run_count, det_count)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
