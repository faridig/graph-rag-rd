# ACCRO Graph RAG — R&D Knowledge Base

## Project

Graph RAG system for internal R&D knowledge retrieval (food tech / meat analogues).
Full spec: `docs/spec/SPEC.md`

**Target users:** R&D teams (Extrusion & Applications poles)
**Core constraint:** Zero hallucination — always cite source (experiment_id, run_id), always return fallback when absent from corpus.

---

## État d'avancement (2026-05-29)

| Tâche | Fichier | Statut |
|-------|---------|--------|
| T1–T2 | `docker-compose.yml`, `requirements.txt`, `src/config.py`, `src/models.py` | ✓ |
| T3 | `src/ingest/import_neo4j.py` | ✓ |
| T4 | `src/ingest/create_indexes.py` | ✓ |
| T5 | `src/ingest/embed_chunks.py` | ✓ |
| T6 | `src/retrieval/base.py`, `src/retrieval/hybrid_retriever.py` | ✓ |
| T7 | `src/retrieval/exact_lookup.py` | ✓ |
| T8 | `src/generation/prompt_fr.py`, `src/generation/rag_pipeline.py` | ✓ |
| T8.bis | `src/ingest/calibrate_threshold.py` | ✓ |
| T9 | `src/api.py` — FastAPI `POST /query`, `GET /health`, `GET /corpus` | ✓ |
| T10 | `src/query.py` — CLI `python src/query.py "<question>"` | ✓ |
| T11–T13 | `tests/` — 55 tests (test_rag, test_api, test_query_cli, test_retrieval, test_ingest) | ✓ |

---

## Carte des modules

| Fichier | Rôle |
|---------|------|
| `src/config.py` | Constantes et variables d'env (SCORE_THRESHOLD, CORPUS_SCOPE, clés API) |
| `src/models.py` | Dataclasses Pydantic : `QueryRequest`, `QueryResponse`, `Source` |
| `src/api.py` | FastAPI : `POST /query`, `GET /health`, `GET /corpus` — point d'entrée HTTP |
| `src/query.py` | CLI : `python src/query.py "<question>"` — point d'entrée terminal |
| `src/generation/rag_pipeline.py` | Orchestration RAG : `run_query()`, `build_pipeline()`, `get_dense_score()` |
| `src/generation/prompt_fr.py` | Template de prompt système (français) |
| `src/retrieval/base.py` | Interface `IRetriever` |
| `src/retrieval/hybrid_retriever.py` | `HybridCypherRetriever` — retrieval dense+sparse via Neo4j |
| `src/retrieval/exact_lookup.py` | Fallback Cypher `CONTAINS` quand score < SCORE_THRESHOLD |
| `src/ingest/import_neo4j.py` | Import JSON → Neo4j (idempotent via MERGE) |
| `src/ingest/create_indexes.py` | Création index fulltext + vector |
| `src/ingest/embed_chunks.py` | Génère embeddings OpenAI → stocke dans `Chunk.embedding` |
| `src/ingest/calibrate_threshold.py` | Calibration du SCORE_THRESHOLD sur données réelles |

---

## Tech Stack

| Component | Tool | Key detail |
|-----------|------|------------|
| Language | Python 3.11+ | strict typing, ruff |
| Graph DB + Vector DB | Neo4j 2025.x | Cypher 25 — vector store unique (MVP) |
| Graph RAG | neo4j-graphrag | HybridCypherRetriever |
| Embeddings | OpenAI text-embedding-3-large | **dimensions=1536** (always) |
| LLM | claude-sonnet-4-6 | via Anthropic API |
| Infra | Docker Compose | Neo4j seul (Qdrant absent du MVP) |

---

## Commands

```bash
# Infrastructure
docker compose up -d

# Install
pip install -r requirements.txt

# Ingestion (in order)
python src/ingest/import_neo4j.py    # JSON knowledge → Neo4j (idempotent)
python src/ingest/create_indexes.py  # fulltext + vector indexes
python src/ingest/embed_chunks.py    # chunks + embeddings → Neo4j vector index

# Query CLI
python src/query.py "Quel effet a l'huile sur M03 ?"

# API
uvicorn src.api:app --reload --port 8000

# Tests
pytest tests/ -v
pytest tests/ -v --cov=src --cov-report=term-missing

# Lint/format
ruff check src/ tests/
ruff format src/ tests/
```

---

## Code Conventions

- **Typage strict** — annotations sur toutes les fonctions
- **snake_case** partout
- **Constantes en MAJUSCULES** dans `src/config.py`, chargées depuis `.env`
- **ruff** pour lint et format (pas de black, pas de flake8)
- Pas de commentaires évidents — noms de fonctions explicites
- Une fonction = une responsabilité
- Pas de secrets hardcodés — tout dans `.env`

---

## Neo4j Schema (summary)

**Nodes:** `Experiment`, `Run`, `Ingredient`, `Chantier`, `Lead`, `Chunk`

**Key relations:**
```
(Experiment)-[:HAS_RUN]->(Run)
(Run)-[:USES_INGREDIENT]->(Ingredient)
(Run)-[:BELONGS_TO]->(Chantier)
(Run)-[:HAS_CHUNK]->(Chunk)
(Run)-[:DETAILS]->(Experiment)   # REPERTOIRE Run → Experiment détaillé (ACE-3, ACE-5…)
```

**[:DETAILS] — construction programmatique (après LOAD CSV des 3 sources) :**
Extraire le segment après `:Run:` dans l'id du run REPERTOIRE → matcher sur `Experiment.id`.
Confirmé par le champ `note` "Lien vers fiche détaillée: ACE-3-…" dans le CSV REPERTOIRE.
Ce lien est obligatoire pour naviguer depuis les résumés REPERTOIRE vers les données ACE complètes.

**Vector index:** `chunk_embedding` — 1536 dims, cosine, on `Chunk.embedding`

---

## Critical Rules (Always Do)

- `MERGE` (never `CREATE`) for Neo4j imports — idempotence
- `IN TRANSACTIONS OF 500 ROWS` for CSV imports
- `IF NOT EXISTS` on all index/constraint creation
- `dimensions=1536` passed to every OpenAI embeddings call
- Validate import with post-ingestion count query
- Every API response must include `sources` (experiment_id + run_id)
- Return fallback message when `found_in_corpus=False`

## Boundaries (Ask First)

- Adding new major Python dependencies
- Modifying Neo4j schema (new labels or relations)
- Changing embedding model (forces full re-indexation)
- Exposing API on a public port

## Never Do

- Hardcode API keys — always use `.env`
- Return a response without verifying it comes from context
- Delete Neo4j nodes without backup
- Send internal data to unapproved external services

---

## Retrieval Strategy

**Toujours hybride** — toutes les requêtes passent par `HybridCypherRetriever` (Neo4j).
**Fallback gate** : si `dense_score` absolu du top-1 < `SCORE_THRESHOLD` → `exact_lookup.py` (Cypher `CONTAINS toLower()`). Pas de routing conditionnel.

**Filtre chantier** : deux branches — avec filtre : MATCH sur chantier + dense rank exact ; sans filtre : hybrid normal.
**Pas de re-ranking** (corpus ~320 chunks — inutile à cette taille).
**IRetriever interface** préservée pour migration future vers un store externe.

---

## API publique implémentée (`src/generation/rag_pipeline.py`)

```python
# Fonctions module-level (utilisées par API + CLI)
run_query(pipeline: RAGPipeline, question: str, top_k: int = 10, chantier: str | None = None) -> QueryResponse
get_dense_score(pipeline: RAGPipeline, query: str) -> float
extract_cited_ids(text: str) -> set[str]
build_pipeline() -> RAGPipeline  # factory via variables d'env

# QueryResponse (src/models.py)
# .answer: str — texte avec [source: run_id] ou FALLBACK_MESSAGE
# .sources: list[Source] — [] si found_in_corpus=False
# .found_in_corpus: bool
# .corpus_scope: list[str]  # CORPUS_SCOPE constant
```

**Invariant critique :** si `found_in_corpus=False` → `answer == FALLBACK_MESSAGE` exactement, `sources == []`, aucun appel LLM.

---

## Tests

Framework: `pytest` + `pytest-cov`
Minimum coverage: 70% on `src/`

**Pattern de mock (T11–T13) :** injecter `RAGPipeline(driver_mock, openai_mock, anthropic_mock)` directement — ne pas appeler `build_pipeline()` en test.
- `driver.session().run().single()` → mock pour contrôler `dense_score`
- `anthropic_mock.messages.create()` → mock pour contrôler la réponse LLM
- `openai_mock.embeddings.create()` → mock pour court-circuiter les appels réseau

Critical test: absent ingredient must return `FALLBACK_MESSAGE` exactly, `sources=[]`, `found_in_corpus=False` — never hallucination.

---

## Corpus (MVP scope)

| Source | Runs | Detail level |
|--------|------|-------------|
| REPERTOIRE-RD-2025-2026 | 316 | Summary (objective, synthesis, status) |
| ACE-3 | 1 experiment | Full detail |
| ACE-5 | 1 experiment | Full detail |

`_knowledge.json` → source primaire Neo4j (import + structure)
`_documentation.md` → source de chunking pour Neo4j vector index
`_triples.csv` → validation uniquement
