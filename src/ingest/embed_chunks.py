"""Chunk documentation markdown files and embed into Neo4j :Chunk nodes."""

from __future__ import annotations

import argparse
import hashlib
import json
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

_LIEN_ESSAI_DIR = Path("data/repertoire_rd_2025-2026/lien_essai")
_REPERTOIRE_DOC = Path("data/repertoire_rd_2025-2026/REPERTOIRE-RD-2025-2026_documentation.md")


def _discover_doc_paths() -> list[tuple[Path, str]]:
    """Return (doc_path, experiment_id) for REPERTOIRE + all auto-discovered lien_essai docs.

    experiment_id is derived from filename: ``FOO_documentation.md`` → ``FOO``.
    Special routing keys REPERTOIRE and ACE-3 are preserved by their fixed IDs.
    Every other experiment uses the generic _chunk_run_detail() path.
    """
    paths: list[tuple[Path, str]] = [(_REPERTOIRE_DOC, "REPERTOIRE")]
    for doc_path in sorted(_LIEN_ESSAI_DIR.glob("**/*_documentation.md")):
        exp_id = _exp_id_for_doc(doc_path)
        paths.append((doc_path, exp_id))
    return paths


def _exp_id_for_doc(doc_path: Path) -> str:
    """Return the authoritative experiment ID for a documentation file.

    Reads the sibling *_knowledge.json when available — the JSON is the
    source of truth for the ID, not the filename.  Falls back to the
    filename stem if the JSON is absent or malformed.
    """
    knowledge_path = doc_path.with_name(
        doc_path.stem.replace("_documentation", "_knowledge") + ".json"
    )
    if knowledge_path.exists():
        try:
            return json.loads(knowledge_path.read_text(encoding="utf-8"))["experiment"]["id"]
        except (json.JSONDecodeError, KeyError):
            logger.warning(
                "Could not read exp_id from %s — falling back to filename", knowledge_path.name
            )
    else:
        logger.warning("No knowledge JSON found beside %s — falling back to filename", doc_path.name)
    return doc_path.stem.replace("_documentation", "")

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
    parts = re.split(r"### Run ([\w-]+) —[^\n]*\n", content)
    chunks: list[dict] = []
    first_local_id: str | None = None

    for i in range(1, len(parts), 2):
        local_id = parts[i].strip()
        if first_local_id is None:
            first_local_id = local_id
        section = parts[i + 1] if i + 1 < len(parts) else ""

        # For the last run, strip post-run sections (## 4, ## 5, …) so its
        # embedding reflects only the run data, not the experiment summary.
        is_last = (i + 2 >= len(parts))
        if is_last:
            # Everything from the first level-2 heading (## N.) is summary material.
            m = re.search(r"\n## \d+\.", section)
            if m:
                section = section[: m.start()]

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

    # Summary chunk: extract sections 4+ (variations table + observations/conclusions).
    # Linked to the first run so the retrieval Cypher can reach it.
    last_section = parts[-1] if len(parts) > 1 else ""
    m_summary = re.search(r"\n(## \d+\..*)", last_section, re.DOTALL)
    if m_summary and first_local_id is not None:
        summary_text = m_summary.group(1).strip()
        run_id_summary = f"{experiment_id}:Run:{first_local_id}"
        chunks.append(
            {
                "id": deterministic_chunk_id(f"{experiment_id}:summary", source_file),
                "text": f"# {experiment_id} — synthèse et conclusions\n\n{summary_text}",
                "source_file": source_file,
                "experiment_id": experiment_id,
                "run_id": run_id_summary,
                "chantier": None,
                "date": None,
                "lead": None,
                "type": "experiment_summary",
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

    else:
        # Generic path: all batch_extract.py outputs use ### Run N — title format.
        chunks.extend(_chunk_run_detail(content, source_file, source_type))

    return chunks


def clean_experiment_chunks(driver: Driver, exp_id: str) -> int:
    """Delete all Chunk nodes for the given experiment. Returns the count deleted.

    Call this before re-embedding an updated experiment to prevent orphan chunks
    when documentation structure changes (e.g. Phase 2 results added).
    """
    with driver.session() as session:
        count = session.run(
            """
            MATCH (:Experiment {id: $exp_id})-[:HAS_RUN]->(:Run)-[:HAS_CHUNK]->(c:Chunk)
            RETURN count(c) AS cnt
            """,
            exp_id=exp_id,
        ).single()["cnt"]
        if count > 0:
            session.run(
                """
                MATCH (:Experiment {id: $exp_id})-[:HAS_RUN]->(:Run)-[:HAS_CHUNK]->(c:Chunk)
                DETACH DELETE c
                """,
                exp_id=exp_id,
            )
    return count


_UPSERT_BATCH_SIZE = 100


def _fetch_existing_hashes(driver: Driver, chunk_ids: list[str]) -> dict[str, str]:
    """Return {chunk_id: text_hash} for chunks that already have a hash stored."""
    if not chunk_ids:
        return {}
    with driver.session() as session:
        result = session.run(
            """
            UNWIND $ids AS id
            MATCH (c:Chunk {id: id})
            WHERE c.text_hash IS NOT NULL
            RETURN c.id AS id, c.text_hash AS hash
            """,
            ids=chunk_ids,
        )
        return {r["id"]: r["hash"] for r in result}


def upsert_chunks_batch(driver: Driver, chunks: list[dict]) -> None:
    """Upsert chunks in batches of 100 using UNWIND for efficiency."""
    for i in range(0, len(chunks), _UPSERT_BATCH_SIZE):
        batch = [
            {
                "id": c["id"],
                "text": c["text"],
                "text_hash": c["text_hash"],
                "embedding": c["embedding"],
                "source_file": c["source_file"],
                "experiment_id": c["experiment_id"],
                "run_id": c["run_id"],
                "chantier": c.get("chantier"),
                "date": c.get("date"),
                "lead": c.get("lead"),
                "type": c["type"],
                "pole": c.get("pole"),
            }
            for c in chunks[i : i + _UPSERT_BATCH_SIZE]
        ]
        with driver.session() as session:
            session.run(
                """
                UNWIND $rows AS c
                MERGE (chunk:Chunk {id: c.id})
                SET chunk.text = c.text,
                    chunk.text_hash = c.text_hash,
                    chunk.embedding = c.embedding,
                    chunk.source_file = c.source_file,
                    chunk.experiment_id = c.experiment_id,
                    chunk.run_id = c.run_id,
                    chunk.chantier = c.chantier,
                    chunk.date = c.date,
                    chunk.lead = c.lead,
                    chunk.type = c.type,
                    chunk.pole = c.pole
                WITH chunk, c
                MATCH (run:Run {id: c.run_id})
                MERGE (run)-[:HAS_CHUNK]->(chunk)
                """,
                rows=batch,
            )
            summary_rows = [c for c in batch if c["type"] == "experiment_summary"]
            if summary_rows:
                session.run(
                    """
                    UNWIND $rows AS c
                    MATCH (chunk:Chunk {id: c.id})
                    MATCH (exp:Experiment {id: c.experiment_id})
                    MERGE (exp)-[:HAS_SUMMARY]->(chunk)
                    """,
                    rows=summary_rows,
                )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Embed documentation chunks into Neo4j.")
    parser.add_argument(
        "--experiment",
        metavar="EXP_ID",
        help="Re-embed a single experiment (deletes its old chunks first).",
    )
    args = parser.parse_args()

    client = OpenAI(api_key=OPENAI_API_KEY)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        all_paths = _discover_doc_paths()

        if args.experiment:
            paths = [(p, t) for p, t in all_paths if t == args.experiment]
            if not paths:
                logger.error(
                    "No documentation found for experiment '%s'. "
                    "Check that *_documentation.md exists under lien_essai/.",
                    args.experiment,
                )
                return
            deleted = clean_experiment_chunks(driver, args.experiment)
            logger.info("Cleaned %d old chunks for %s", deleted, args.experiment)
        else:
            paths = all_paths

        total = 0
        skipped_total = 0
        for path, source_type in paths:
            logger.info("Chunking %s (%s)", path.name, source_type)
            chunks = chunk_documentation(path, source_type)
            if not chunks:
                logger.warning("  0 chunks produced — check doc format")
                continue

            for chunk in chunks:
                chunk["text_hash"] = hashlib.sha256(chunk["text"].encode()).hexdigest()

            existing_hashes = _fetch_existing_hashes(driver, [c["id"] for c in chunks])
            to_embed = [c for c in chunks if existing_hashes.get(c["id"]) != c["text_hash"]]
            skipped = len(chunks) - len(to_embed)

            logger.info("  %d chunks: %d to embed, %d unchanged (skipped)", len(chunks), len(to_embed), skipped)

            for idx, chunk in enumerate(to_embed):
                chunk["embedding"] = embed_text(client, chunk["text"])
                if (idx + 1) % 50 == 0:
                    logger.info("  %d/%d embedded", idx + 1, len(to_embed))

            if to_embed:
                upsert_chunks_batch(driver, to_embed)

            total += len(to_embed)
            skipped_total += skipped

        logger.info("Done — %d embedded, %d skipped (unchanged)", total, skipped_total)
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
