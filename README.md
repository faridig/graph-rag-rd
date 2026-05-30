# Graph RAG — Capitalisation des savoirs R&D

Système de recherche et de synthèse sur la connaissance R&D interne d'ACCRO (food tech / analogues de viande). Permet aux équipes R&D de retrouver instantanément ce que l'organisation sait déjà : ingrédients testés, effets mesurés, résultats d'expériences.

**Contrainte principale :** zéro hallucination — toute réponse cite sa source (experiment_id + run_id), ou retourne explicitement "absent du corpus".

## Questions représentatives

> *"Est-ce que l'ingrédient Pisane ES a déjà été testé ?"*
> *"Quel est l'effet de la cystéine en extrusion ?"*
> *"Fais-moi une synthèse de toutes les expériences sur les fibres en extrusion."*
> *"Rapport d'avancement du projet Bacon en 2026."*

## Architecture

```
JSON knowledge base
      ↓
Neo4j (graph)            Chunks → OpenAI embeddings
  Experiment              ↓
  Run ──────────────→ Vector index (1536 dims, cosine)
  Ingredient              ↓
  Chantier         HybridCypherRetriever
                    (dense + sparse, RRF)
                          ↓
                   Exact lookup fallback
                   (Cypher CONTAINS si score < threshold)
                          ↓
                   Claude (réponse sourcée)
                          ↓
                   FastAPI / Gradio UI
```

## Stack

| Composant | Technologie |
|-----------|-------------|
| Graph DB | Neo4j 2025.x (Cypher 25) |
| Graph RAG | neo4j-graphrag (HybridCypherRetriever) |
| Embeddings | OpenAI text-embedding-3-large (1536 dims) |
| LLM | Claude (claude-sonnet-4-6) |
| UI | Gradio |
| API | FastAPI |
| Infra | Docker Compose |
| Tests | pytest (55 tests, >70% coverage) |

## Lancement

```bash
# Infrastructure
docker compose up -d

# Installation
pip install -r requirements.txt

# Ingestion (dans l'ordre)
python src/ingest/import_neo4j.py    # JSON → Neo4j (idempotent via MERGE)
python src/ingest/create_indexes.py  # Index fulltext + vector
python src/ingest/embed_chunks.py    # Embeddings → Neo4j vector index

# CLI
python src/query.py "Quel effet a l'huile sur M03 ?"

# API
uvicorn src.api:app --reload --port 8000

# Interface Gradio
python src/app.py

# Tests
pytest tests/ -v --cov=src
```

## Structure

```
src/
├── api.py              # FastAPI : POST /query, GET /health, GET /corpus
├── app.py              # Interface Gradio
├── query.py            # CLI
├── config.py           # Variables d'env (SCORE_THRESHOLD, etc.)
├── models.py           # Dataclasses : QueryRequest, QueryResponse, Source
├── generation/
│   ├── rag_pipeline.py # Orchestration RAG + appel Claude
│   └── prompt_fr.py    # Prompt système (français)
├── retrieval/
│   ├── hybrid_retriever.py  # HybridCypherRetriever (dense + sparse)
│   └── exact_lookup.py      # Fallback Cypher CONTAINS
└── ingest/
    ├── import_neo4j.py      # Import JSON → Neo4j
    ├── create_indexes.py    # Création index fulltext + vector
    ├── embed_chunks.py      # Génération embeddings
    └── calibrate_threshold.py
```

## Variables d'environnement

```bash
cp .env.example .env
# NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
# OPENAI_API_KEY
# ANTHROPIC_API_KEY
# SCORE_THRESHOLD (défaut : 0.5)
```
