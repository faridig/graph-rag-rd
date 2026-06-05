# ACCRO Graph RAG — R&D Knowledge Base

## Project

Graph RAG system for internal R&D knowledge retrieval (food tech / meat analogues).
Full spec: `docs/spec/SPEC.md`

**Target users:** R&D teams (Extrusion & Applications poles)
**Core constraint:** Zero hallucination — always cite source (experiment_id, run_id), always return fallback when absent from corpus.

---

## Prochaine étape (2026-06-05 — reprendre ici)

**Corpus ingéré : 2398 chunks / 2356 runs / 167 experiments — 100% embedé. SCORE_THRESHOLD recalibré à 0.6698.**

```bash
# 1. STRIP-18 (L3 complexe, données instrumentales — déféré)
docker compose up -d
python scripts/batch_extract.py --force-complex --file "STRIP-18 Essai incorporation d'épices.xlsx"
python -m src.ingest.import_neo4j && python -m src.ingest.embed_chunks

# 2. KEFTA-LAB (traitement découpé — voir section ci-dessous)

# 3. (Optionnel) Corriger les 6 hyperliens cassés dans le Répertoire SharePoint
#    Ouvrir le fichier Répertoire → colonne K → corriger manuellement :
#    STRIP-BOEUF/STRIPS-BOEUF, PP-18, PP-REC-12 Botanical, STRIP-B09-250415, VEILLE-4
#    Puis relancer : python scripts/download_essais.py --dest ... --sheet "Répertoire Essais"
```

### Problème Kefta (traitement séparé — pas encore résolu)
`KEFTA-LAB` : 71+ runs, trop dense pour 128K tokens même en continuation.
`_llm_raw_response.txt` (324K) présent dans `lien_essai/Essais labo boulettes kefta/`.
Traiter manuellement ou en plusieurs appels découpés.

### Données manquantes connues (2026-06-05)
- **268 runs REPERTOIRE sans `[:DETAILS]`** — experiments non encore extraits (knowledge.json absent)
- **45 experiments sans `HAS_SUMMARY`** — chunk synthèse manquant
- **6 hyperliens cassés** dans le fichier Répertoire SharePoint (irrécupérable automatiquement)

---

## État d'avancement (2026-06-03)

| Tâche | Fichier | Statut |
|-------|---------|--------|
| T1–T2 | `docker-compose.yml`, `requirements.txt`, `src/config.py`, `src/models.py` | ✓ |
| T3 | `src/ingest/import_neo4j.py` | ✓ modifié |
| T4 | `src/ingest/create_indexes.py` | ✓ |
| T5 | `src/ingest/embed_chunks.py` | ✓ |
| T6 | `src/retrieval/base.py`, `src/retrieval/hybrid_retriever.py` | ✓ |
| T7 | `src/retrieval/exact_lookup.py` | ✓ |
| T8 | `src/generation/prompt_fr.py`, `src/generation/rag_pipeline.py` | ✓ modifié |
| T8.bis | `src/ingest/calibrate_threshold.py` | ✓ |
| T9 | `src/api.py` — FastAPI `POST /query`, `GET /health`, `GET /corpus` | ✓ |
| T10 | `src/query.py` — CLI `python -m src.query "<question>"` | ✓ |
| T11–T13 | `tests/` — 64 tests (test_rag, test_api, test_query_cli, test_retrieval, test_ingest) | ✓ |
| **T14** | **`scripts/batch_extract.py`** — **pipeline d'extraction batch des fichiers bruts** | **✓ validé** |
| **T15** | **Pipeline ingestion robuste** — auto-découverte, batch UNWIND, hash-skip, --experiment, HAS_SUMMARY | **✓** |

---

## Carte des modules

| Fichier | Rôle |
|---------|------|
| `src/config.py` | Constantes et variables d'env (SCORE_THRESHOLD, CORPUS_SCOPE, clés API) |
| `src/models.py` | Dataclasses Pydantic : `QueryRequest`, `QueryResponse`, `Source` |
| `src/api.py` | FastAPI : `POST /query`, `GET /health`, `GET /corpus` — point d'entrée HTTP |
| `src/query.py` | CLI : `python -m src.query "<question>"` — point d'entrée terminal |
| `src/app.py` | Application Gradio (interface web locale) |
| `src/generation/rag_pipeline.py` | Orchestration RAG : `run_query()`, `build_pipeline()`, `get_dense_score()` |
| `src/generation/prompt_fr.py` | Template de prompt système (français) |
| `src/retrieval/base.py` | Interface `IRetriever` |
| `src/retrieval/hybrid_retriever.py` | `HybridCypherRetriever` — retrieval dense+sparse via Neo4j |
| `src/retrieval/exact_lookup.py` | Fallback Cypher `CONTAINS` quand score < SCORE_THRESHOLD |
| `src/retrieval/sharepoint_urls.py` | URLs SharePoint : static fallback + parse download.log + Neo4j lookup |
| `src/ingest/import_neo4j.py` | Import JSON → Neo4j (idempotent via MERGE) — auto-découverte knowledge files |
| `src/ingest/create_indexes.py` | Création index fulltext + vector |
| `src/ingest/embed_chunks.py` | Génère embeddings OpenAI → stocke dans `Chunk.embedding` |
| `src/ingest/calibrate_threshold.py` | Calibration du SCORE_THRESHOLD sur données réelles |
| `scripts/batch_extract.py` | Extraction batch LLM (Opus 4.8) : xlsx/docx/csv → 4 artefacts KG |
| `scripts/inventory.py` | Dump openpyxl/docx (copié du skill rnd-experiment-extractor) |
| `scripts/build_kg.py` | Génère triples CSV + documentation MD + validation MD depuis knowledge JSON |
| `scripts/download_essais.py` | Téléchargement des fichiers liés depuis SharePoint (MSAL device flow) |

---

## Tech Stack

| Component | Tool | Key detail |
|-----------|------|------------|
| Language | Python 3.11+ | strict typing, ruff |
| Graph DB + Vector DB | Neo4j 2025.x | Cypher 25 — vector store unique (MVP) |
| Graph RAG | neo4j-graphrag | HybridCypherRetriever |
| Embeddings | OpenAI text-embedding-3-large | **dimensions=1536** (always) |
| LLM (RAG) | claude-sonnet-4-6 | via Anthropic API |
| LLM (extraction) | claude-sonnet-4-6 | batch_extract.py — $3/$15 input/output, 128K output, thinking activé |
| Infra | Docker Compose | Neo4j seul (Qdrant absent du MVP) |

---

## Commands

```bash
# Infrastructure
docker compose up -d

# Install
pip install -r requirements.txt

# Extraction batch des fichiers bruts (à lancer depuis la racine du projet)
python scripts/batch_extract.py --dry-run                      # voir ce qui serait traité
python scripts/batch_extract.py                               # lancer (L1+L2)
python scripts/batch_extract.py --file "Nom.xlsx"             # tester un seul fichier
python scripts/batch_extract.py --force-complex --level 3     # batch L3 (Sonnet 4.6 gère)
python scripts/batch_extract.py --file "X.xlsx" --force       # forcer re-extraction

# Ingestion (in order, après extraction)
python -m src.ingest.import_neo4j    # JSON knowledge → Neo4j (idempotent, auto-découverte)
python -m src.ingest.create_indexes  # fulltext + vector indexes
python -m src.ingest.embed_chunks    # chunks + embeddings → Neo4j vector index

# Query CLI
python -m src.query "Quel effet a l'huile sur M03 ?"

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

**Propriétés Experiment :** `scale`, `sharepoint_url`, `status` (preliminary/ongoing/complete)

**Key relations:**
```
(Experiment)-[:HAS_RUN]->(Run)
(Experiment)-[:REFERENCES]->(Experiment)   # liens inter-expériences extraits des fichiers
(Experiment)-[:HAS_SUMMARY]->(Chunk)       # chunk synthèse expérience (en plus de HAS_CHUNK)
(Run)-[:USES_INGREDIENT]->(Ingredient)
(Run)-[:BELONGS_TO]->(Chantier)
(Run)-[:HAS_CHUNK]->(Chunk)
(Run)-[:DETAILS]->(Experiment)   # REPERTOIRE Run → Experiment détaillé
```

**[:DETAILS] — construction programmatique :**
Extraire le segment après `:Run:` dans l'id du run REPERTOIRE → matcher sur `Experiment.id`.
Nombre d'edges croît avec le corpus (20 au 2026-06-03). Construction automatique à chaque import_neo4j.

**Vector index:** `chunk_embedding` — 1536 dims, cosine, on `Chunk.embedding`

---

## Pipeline d'extraction batch (T14)

### Triage des fichiers
| Niveau | Fichiers | Traitement |
|--------|----------|------------|
| L1 (69) | Formulations, VEILLE, PP, labo, DOCX rapports | Batch auto |
| L2 (18) | Essais MDD onglet-*, DST, GLU, nuggets | Batch + relire `_validation.md` |
| L3 (22) | ACE-1/2/4/6, FIB-*, STRIP-*, FIPROVEX-2, PP-REC-* | Batch avec `--force-complex` (Sonnet 4.6 gère) |

### Artefacts produits par fichier
```
lien_essai/{nom_fichier}/
├── {id}_knowledge.json     ← source de vérité (runs, formulations, mesures)
├── {id}_triples.csv        ← relations pour le graphe (1000+ rows typiquement)
├── {id}_documentation.md   ← texte indexé dans le RAG
└── {id}_validation.md      ← cohérence replicate/mean, unités manquantes
```

### Schéma knowledge JSON enrichi
Champs R&D ajoutés par rapport au skill original :
- `experiment.scale` (lab/pilot/industrial), `experiment.batch_size`, `experiment.sharepoint_url`
- `targets{}` — critères d'acceptance (anisotropie > 1.2, etc.)
- `references[]` — liens inter-expériences
- `failed_runs[]` — runs échoués avec reason + conditions
- `observations.sensory` — séparé de `observations.process`
- `derived[].computed` — SME, rendement, valeurs nutritionnelles
- `not_measured` — objets `{analysis, reason}` au lieu de strings
- `inputs[].supplier`, `inputs[].lot_number` — traçabilité ingrédients

### Robustesse
- Détection + réparation automatique des formulations incomplètes ("idem ESSAI X") via appel Sonnet 4.6 ciblé
- Streaming obligatoire (max_tokens=128_000), thinking activé (budget=10_000)
- Retry sur 5xx + erreurs réseau httpx (RemoteProtocolError, ReadError, ConnectError)
- Prompt caching system prompt (cache_control ephemeral 5m)
- URLs SharePoint injectées depuis `download.log` (81 URLs disponibles)
- MAX_INVENTORY_CHARS=800_000 (contexte 1M Sonnet 4.6)
- `--force` bypasse les 3 checks skip set ; `--force-complex` uniquement le filtre L3

### Tarifs Anthropic vérifiés (2026-06-03)
`claude-sonnet-4-6` : **$3 input / $15 output / $3.75 cache_write_5m / $0.30 cache_read** (per MTok)
Coût batch L3 (15 fichiers) : $9.51. Coût batch L1+L2 (session 2026-06-04, ~50 fichiers) : ~$13–20 estimé.

### Fixes appliqués (2026-06-04)
- `batch_extract.py` : détection auto fichiers tronqués (`_llm_raw_response.txt` sans `_knowledge.json`) → `process_file_continuation` automatique
- `build_kg.py` : `replicates` peut être int au lieu de list → guard `isinstance` aux deux endroits
- `sharepoint_urls.py` : `_normalize_url()` corrige `action=editnew` + double-encodage `%25uXXXX` ; `_load_log_urls()` lit `WEBURL:` en priorité ; `_MDD_EXPERIMENT_URLS` avec ancres onglet
- `download_essais.py` : log `WEBURL: label='...' url=...` pour chaque téléchargement réussi

### Fixes appliqués (2026-06-05)
- `sharepoint_urls.py` : encodage `emince_mdd` corrigé (`%25u00e9` → `%C3%A9`) + `file=` mis à jour
- 7 knowledge.json : `sharepoint_url` → `null` (liens pointant vers mauvais fichier)
- 7 knowledge.json : paramètre `file=` mis à jour (fichier renommé sur SharePoint, GUID correct)
- `download_essais.py` : WEBURL loggé pour TOUS les fichiers résolus (pas seulement nouveaux téléchargements)
- 53 knowledge.json supplémentaires : `sharepoint_url` peuplée via WEBURLs Graph API

### URLs SharePoint — état (2026-06-05)
| Type | Nb | Description | Statut |
|------|----|-------------|--------|
| Liens vers mauvais fichier | 7 | STRIP-19, STRIP-BOEUF×2, PP-18, BATONNET-POISSON, MARINADES-CHAUDES×2 | ✅ corrigé (null) |
| Double-encodage `%25u` | 1 | emince_mdd static fallback | ✅ corrigé |
| `file=` périmé (fichier renommé) | 7 | MDD-EMINCE-THAI-KEBAB, GLU-1, GLU-2, etc. | ✅ corrigé |
| URLs manquantes récupérées | 53 | Via WEBURLs Graph API (download_essais.py re-run) | ✅ |
| Répertoire a mauvais hyperliens | 6 | STRIP-BOEUF×2, PP-18, PP-REC-12 Botanical, STRIP-B09-250415, VEILLE-4 | ❌ irrécupérable auto — corriger dans Répertoire |
| **Total avec URL valide** | **117/123** | **95%** | |

---

## Critical Rules (Always Do)

- `MERGE` (never `CREATE`) for Neo4j imports — idempotence
- `IN TRANSACTIONS OF 500 ROWS` for CSV imports
- `IF NOT EXISTS` on all index/constraint creation
- `dimensions=1536` passed to every OpenAI embeddings call
- Validate import with post-ingestion count query
- Every API response must include `sources` (experiment_id + run_id)
- Return fallback message when `found_in_corpus=False`
- Lancer `embed_chunks` après chaque import — les nouveaux runs ne sont pas cherchables sans Chunk+embedding

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
**Pas de re-ranking** (corpus ~382 chunks — inutile à cette taille).
**IRetriever interface** préservée pour migration future vers un store externe.

**URLs SharePoint** : `_resolve_sharepoint_url()` dans `rag_pipeline.py` — priorité : Neo4j `Experiment.sharepoint_url` → run prefix → static fallback. Batchée en 1 requête pour N sources.

**Idée — Enrichissement via navigation du graphe (à explorer)** : le Cypher actuel s'arrête à `Chunk → Run → Experiment`. Or 55 liens `[:REFERENCES]` existent entre expériences (ex. STRIPS-BOEUF → STRIP-3, DST-5 → DST-4). Idée : quand on trouve un Experiment, remonter automatiquement ses expériences référencées et injecter leurs chunks `[:HAS_SUMMARY]` dans le contexte Claude — pour des comparaisons inter-essais automatiques. Questions à trancher : toujours ou seulement si score élevé ? 1 ou 2 niveaux ? Risque : trop de contexte. Fichiers concernés : `_RETRIEVAL_CYPHER` dans `hybrid_retriever.py`, `_format_hybrid_context` dans `rag_pipeline.py`.

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
# Source.sharepoint_url: str | None  # lien cliquable vers le fichier SharePoint d'origine
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

## Téléchargement des essais — Échecs connus (2026-06-03)

Script : `python scripts/download_essais.py --dest data/repertoire_rd_2025-2026/lien_essai/lien_essai_brut --sheet "Répertoire Essais"`
Log complet : `data/repertoire_rd_2025-2026/lien_essai/lien_essai_brut/download.log`
État : 234 présents, **41 échecs restants**

### En attente des autorisations Antoine — site `RDIndustrieCollaboration` inaccessible
Le token n'a accès qu'à `sites/RD`. Ces fichiers sont sur `sites/RDIndustrieCollaboration`.
- `Essai industriel R&D RB 17.02.2025` / `18.02.2025`
- `Rapport EI marinades à chaud 14.03.25`
- `Rapport d'essai industriel 250325/250326/250327/250403 - substitution gluten`
- `Rapport EI égrené poulet 01.09.25` / `21.08.25` / `22.09.25`
- `Rapport EI boulette tomate 24.07.25`
- `Essai fines lamelles Thaï`
- `Rapport EI aug debit éminces natures.docx` / `haché burger.docx`
- `Rapport EI eminces mdd 550 kgh.docx` / `façon kebab 550 kgh.docx`
- `VALIDE - Rapport EI allumettes MDD 01.08.25.docx`
- `STRIP-40`, `ACE-7`

### Site `RD-Production` inaccessible (1 fichier)
- `Essais transferts des références en TVP - onglet "Essais haché"` — GUID `DE473A9D-E2CE-4F5B-99F3-1646B0746877`

### En attente de Yassine (fichiers introuvables sur SharePoint)
- `STRIP-15 Essai huile aromatisée`
- `STRIP-18 Essai incorporation d'épices`
- `STRIP-19 Essai cuisson`

---

## Corpus

### Dans Neo4j (état 2026-06-05 — ingestion complète)

**2398 chunks / 2356 runs / 167 experiments — 100% embedé. 62 edges `[:REFERENCES]`.**

| Lot | Runs | Chunks | Statut |
|-----|------|--------|--------|
| REPERTOIRE + tous lien_essai (127 knowledge.json) | 2356 | 2398 | ✓ ingéré + embedé |
| STRIP-18 | — | — | ❌ pas encore extrait |
| KEFTA-LAB | — | — | ❌ trop dense — traitement séparé |

`_knowledge.json` → source primaire Neo4j (import + structure)
`_documentation.md` → source de chunking pour Neo4j vector index
`_triples.csv` → validation uniquement
