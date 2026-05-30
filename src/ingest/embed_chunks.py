"""Chunk documentation markdown files and embed into Neo4j :Chunk nodes."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from neo4j import Driver, GraphDatabase
from openai import OpenAI

from src.config import (
    EMBEDDING_DIMS,
    EMBEDDING_MODEL,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    OPENAI_API_KEY,
)

logger = logging.getLogger(__name__)

_DOC_PATHS: list[tuple[Path, str]] = [
    (
        Path("data/repertoire_rd_2025-2026/REPERTOIRE-RD-2025-2026_documentation.md"),
        "REPERTOIRE",
    ),
    (
        Path("data/repertoire_rd_2025-2026/lien_essai/ACE-3/ACE-3_documentation.md"),
        "ACE-3",
    ),
    (
        Path("data/repertoire_rd_2025-2026/lien_essai/ACE-5/ACE-5_documentation.md"),
        "ACE-5",
    ),
    (
        Path("data/repertoire_rd_2025-2026/lien_essai/Essai Allumette-5/Allumette_documentation.md"),
        "Allumette",
    ),
]

_FACTOR_KEYS = ("chantier", "pole", "lead", "status", "cir_grouping")
_FACTOR_ALT = "|".join(_FACTOR_KEYS)


def deterministic_chunk_id(run_id: str, source_file: str) -> str:
    return hashlib.sha256((run_id + source_file).encode()).hexdigest()[:16]


def embed_text(client: OpenAI, text: str) -> list[float]:
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
        dimensions=EMBEDDING_DIMS,
    )
    return response.data[0].embedding


def _parse_factors(factors_str: str) -> dict[str, str | None]:
    result: dict[str, str | None] = dict.fromkeys(_FACTOR_KEYS, None)
    for key in _FACTOR_KEYS:
        m = re.search(rf"{key}=(.+?)(?=,\s*(?:{_FACTOR_ALT})=|$)", factors_str)
        if m:
            result[key] = m.group(1).strip()
    return result


def _extract_date(section: str) -> str | None:
    m = re.search(r"^- date:\s*(\S+)", section, re.MULTILINE)
    return m.group(1) if m else None


def _extract_field(section: str, field: str) -> str | None:
    pattern = rf"^- {re.escape(field)}:\s*(.+?)(?=\n- |\n\n|\n\*\*|$)"
    m = re.search(pattern, section, re.MULTILINE | re.DOTALL)
    if not m:
        return None
    value = m.group(1).strip()
    return value if value else None


def _chunk_run_detail(content: str, source_file: str, experiment_id: str) -> list[dict]:
    parts = re.split(r"### Run (\d+) —[^\n]*\n", content)
    chunks: list[dict] = []
    for i in range(1, len(parts), 2):
        local_id = parts[i].strip()
        section = parts[i + 1] if i + 1 < len(parts) else ""
        run_id = f"{experiment_id}:Run:{local_id}"
        chunks.append(
            {
                "id": deterministic_chunk_id(run_id, source_file),
                "text": f"### Run {local_id}\n{section.strip()}",
                "source_file": source_file,
                "experiment_id": experiment_id,
                "run_id": run_id,
                "chantier": None,
                "date": None,
                "lead": None,
                "type": "run_detail",
                "pole": None,
            }
        )
    return chunks


def chunk_documentation(path: Path, source_type: str) -> list[dict]:
    content = path.read_text(encoding="utf-8")
    source_file = path.name
    chunks: list[dict] = []

    if source_type == "REPERTOIRE":
        experiment_id = "REPERTOIRE-RD-2025-2026"
        parts = re.split(r"### Run ([\w-]+) —[^\n]*\n", content)
        for i in range(1, len(parts), 2):
            local_id = parts[i].strip()
            section = parts[i + 1] if i + 1 < len(parts) else ""
            run_id = f"{experiment_id}:Run:{local_id}"

            factors_m = re.search(r"- factors: (.+)", section)
            factors = _parse_factors(factors_m.group(1)) if factors_m else {}
            objective = _extract_field(section, "objective")
            synthesis = _extract_field(section, "synthesis")

            if not objective and not synthesis:
                continue

            chunks.append(
                {
                    "id": deterministic_chunk_id(run_id, source_file),
                    "text": f"### Run {local_id}\n{section.strip()}",
                    "source_file": source_file,
                    "experiment_id": experiment_id,
                    "run_id": run_id,
                    "chantier": factors.get("chantier"),
                    "date": _extract_date(section),
                    "lead": factors.get("lead"),
                    "type": "run_summary",
                    "pole": factors.get("pole"),
                }
            )

    elif source_type == "ACE-3":
        experiment_id = "ACE-3"
        parts = re.split(r"### Essai (\d+) —[^\n]*\n", content)
        for i in range(1, len(parts), 2):
            local_id = parts[i].strip()
            section = parts[i + 1] if i + 1 < len(parts) else ""
            run_id = f"{experiment_id}:Run:{local_id}"
            chunks.append(
                {
                    "id": deterministic_chunk_id(run_id, source_file),
                    "text": f"### Essai {local_id}\n{section.strip()}",
                    "source_file": source_file,
                    "experiment_id": experiment_id,
                    "run_id": run_id,
                    "chantier": None,
                    "date": None,
                    "lead": None,
                    "type": "run_detail",
                    "pole": None,
                }
            )

    elif source_type in ("ACE-5", "Allumette"):
        chunks.extend(_chunk_run_detail(content, source_file, source_type))

    else:
        raise ValueError(f"Unknown source_type: {source_type!r}")

    return chunks


def upsert_chunk(driver: Driver, chunk: dict) -> None:
    with driver.session() as session:
        session.run(
            """
            MERGE (c:Chunk {id: $id})
            SET c.text = $text,
                c.embedding = $embedding,
                c.source_file = $source_file,
                c.experiment_id = $experiment_id,
                c.run_id = $run_id,
                c.chantier = $chantier,
                c.date = $date,
                c.lead = $lead,
                c.type = $type,
                c.pole = $pole
            WITH c
            MATCH (run:Run {id: $run_id})
            MERGE (run)-[:HAS_CHUNK]->(c)
            """,
            id=chunk["id"],
            text=chunk["text"],
            embedding=chunk["embedding"],
            source_file=chunk["source_file"],
            experiment_id=chunk["experiment_id"],
            run_id=chunk["run_id"],
            chantier=chunk.get("chantier"),
            date=chunk.get("date"),
            lead=chunk.get("lead"),
            type=chunk["type"],
            pole=chunk.get("pole"),
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = OpenAI(api_key=OPENAI_API_KEY)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        total = 0
        for path, source_type in _DOC_PATHS:
            logger.info("Chunking %s (%s)", path.name, source_type)
            chunks = chunk_documentation(path, source_type)
            logger.info("  %d chunks to embed", len(chunks))

            for idx, chunk in enumerate(chunks):
                chunk["embedding"] = embed_text(client, chunk["text"])
                upsert_chunk(driver, chunk)
                if (idx + 1) % 50 == 0:
                    logger.info("  %d/%d done", idx + 1, len(chunks))

            total += len(chunks)

        with driver.session() as session:
            chunk_count = session.run("MATCH (c:Chunk) RETURN count(c) AS cnt").single()["cnt"]
            rel_count = session.run(
                "MATCH (r:Run)-[:HAS_CHUNK]->(c:Chunk) RETURN count(*) AS cnt"
            ).single()["cnt"]
            null_emb = session.run(
                "MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS cnt"
            ).single()["cnt"]
            logger.info(
                "Validation: %d chunks, %d relations, %d null embeddings",
                chunk_count,
                rel_count,
                null_emb,
            )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
