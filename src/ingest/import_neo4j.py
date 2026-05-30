"""Import _knowledge.json files into Neo4j (Experiment/Run/Ingredient/Chantier/Lead nodes)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase

from src.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

logger = logging.getLogger(__name__)

_EXCEL_ARTIFACTS: frozenset[str] = frozenset(
    {"#DIV/0!", "#VALUE!", "#N/A", "#REF!", "#NAME?", "#NULL!", "#NUM!"}
)

_DATA_PATHS: list[Path] = [
    Path("data/repertoire_rd_2025-2026/REPERTOIRE-RD-2025-2026_knowledge.json"),
    Path("data/repertoire_rd_2025-2026/lien_essai/ACE-3/ACE-3_knowledge.json"),
    Path("data/repertoire_rd_2025-2026/lien_essai/ACE-5/ACE-5_knowledge.json"),
    Path("data/repertoire_rd_2025-2026/lien_essai/Essai Allumette-5/Allumette_knowledge.json"),
]


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


def _parse_ace3(path: Path, data: dict) -> dict:
    exp_raw = data["experiment"]
    experiment: dict[str, Any] = {
        "id": exp_raw["id"],
        "title": exp_raw.get("titre", exp_raw.get("title", "")),
        "type": exp_raw.get("type"),
        "objective": exp_raw.get("objectif", exp_raw.get("objective")),
        "date": exp_raw.get("date_production", exp_raw.get("date")),
        "operator": exp_raw.get("operateur", exp_raw.get("operator")),
        "equipment": exp_raw.get("extrudeuse", exp_raw.get("equipment")),
        "domain": exp_raw.get("domaine", exp_raw.get("domain")),
        "source_file": path.name,
    }
    exp_id = experiment["id"]
    runs: list[dict] = []
    for essai in data.get("essais", []):
        local_id = str(essai["essai"])
        raw_ings: list[str] = [
            ing["ingredient"]
            for ing in essai.get("formulation_detaillee", {}).get("ingredients", [])
            if ing.get("ingredient")
        ]
        runs.append(
            {
                "id": f"{exp_id}:Run:{local_id}",
                "name": essai.get("nom", local_id),
                "objective": None,
                "synthesis": essai.get("note_essai"),
                "status": None,
                "date": None,
                "chantier": None,
                "lead": None,
                "pole": None,
                "cir_grouping": None,
                "ingredients_raw": raw_ings,
                "ingredients": [normalize_ingredient_name(i) for i in raw_ings],
            }
        )
    return {"experiment": experiment, "runs": runs}


def _parse_experiment_header(path: Path, exp_raw: dict) -> dict[str, Any]:
    return {
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


def _parse_runs_with_formulation(data: dict, exp_id: str, formulation_key: str) -> list[dict]:
    runs: list[dict] = []
    for raw_run in data.get("runs", []):
        local_id = str(raw_run["id"])
        raw_ings: list[str] = [
            item["component"]
            for item in raw_run.get("inputs", {}).get(formulation_key, [])
            if item.get("component")
        ]
        notes = raw_run.get("notes")
        runs.append(
            {
                "id": f"{exp_id}:Run:{local_id}",
                "name": raw_run.get("name", local_id),
                "objective": None,
                "synthesis": notes if isinstance(notes, str) else None,
                "status": None,
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
    return {"experiment": experiment, "runs": _parse_runs_with_formulation(data, experiment["id"], "formulation")}


def parse_knowledge_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if "essais" in data:
        return _parse_ace3(path, data)
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
    return {"experiment": experiment, "runs": _parse_runs_with_formulation(data, experiment["id"], formulation_key)}


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
                e.domain = $domain, e.source_file = $source_file
            """,
            id=exp["id"],
            title=exp.get("title", ""),
            type=exp.get("type"),
            objective=exp.get("objective"),
            date=exp.get("date"),
            operator=exp.get("operator"),
            equipment=exp.get("equipment"),
            domain=exp.get("domain"),
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
            {"run_id": r["id"], "chantier": r["chantier"]}
            for r in runs
            if r.get("chantier")
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

        lead_pairs = [
            {"run_id": r["id"], "lead": r["lead"]}
            for r in runs
            if r.get("lead")
        ]
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

    return counts


def build_details_relations(driver: Driver) -> int:
    with driver.session() as session:
        result = session.run(
            """
            MATCH (run:Run) WHERE run.id STARTS WITH "REPERTOIRE-RD-2025-2026:Run:"
            WITH run, toUpper(trim(split(run.id, ":Run:")[1])) AS exp_key
            MATCH (exp:Experiment) WHERE toUpper(trim(exp.id)) = exp_key
            MERGE (run)-[:DETAILS]->(exp)
            RETURN count(*) AS cnt
            """
        )
        count: int = result.single()["cnt"]
        if count != 2:
            logger.warning("Expected 2 [:DETAILS] edges, got %d", count)
        missing = session.run(
            """
            MATCH (run:Run) WHERE run.id STARTS WITH "REPERTOIRE-RD-2025-2026:Run:"
            AND NOT (run)-[:DETAILS]->()
            AND run.id IN [
                "REPERTOIRE-RD-2025-2026:Run:ACE-3",
                "REPERTOIRE-RD-2025-2026:Run:ACE-5"
            ]
            RETURN run.id AS run_id
            """
        )
        for record in missing:
            logger.warning("REPERTOIRE run missing [:DETAILS]: %s", record["run_id"])
    return count


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        total: dict[str, int] = {}
        for path in _DATA_PATHS:
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
            det_count = (
                session.run(
                    "MATCH (:Run)-[:DETAILS]->(:Experiment) RETURN count(*) AS cnt"
                ).single()["cnt"]
            )
            logger.info("Validation: %d runs, %d [:DETAILS]", run_count, det_count)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
