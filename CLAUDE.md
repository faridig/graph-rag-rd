# ACCRO Graph RAG — R&D Knowledge Base

## Project

Graph RAG system for internal R&D knowledge retrieval (food tech / meat analogues).
Full spec: `docs/spec/SPEC.md` — historique des fixes et évolution : `docs/HISTORY.md`

**Target users:** R&D teams (Extrusion & Applications poles)
**Core constraint:** Zero hallucination — always cite source (experiment_id, run_id), always return fallback when absent from corpus.

---

## État (2026-06-24) — LIVRABLE ✅ + CIR ✅ + Budget tracking ✅

**Corpus : 3072 chunks / 2371 runs / 170 experiments — 100% embedé. SCORE_THRESHOLD = 0.6689.**

| Métrique | Valeur | Cible | Statut |
|---|---|---|---|
| `absent_fallback_rate` | **1.0** | 1.0 | ✅ |
| `present_fallback_rate` | **0.0%** | 0.0 | ✅ |
| `citation_coverage` | **97.3%** | ≥ 98% | ⚠️ |
| `citation_validity` | **97.3%** | ≥ 98% | ⚠️ |
| `context_recall` | **0.692** (74 présentes) | > 0.70 | ✅ |
| `faithfulness` | **0.882** | > 0.85 | ✅ |
| `answer_relevancy` | **0.725** | > 0.72 | ✅ |

**Limite connue :** synthèses multi-sessions KOBE (S1+S2) incomplètes — `panel_ressemblant_score` absent des chunks. Use cases principaux (factuelle, lookup, comparaison) fonctionnent bien.

**Livré session 2026-06-10 :**
- Interface Chainlit complète (liens SharePoint, welcome, CIR)
- Génération fiches CIR MESRI (3 groupements, streaming, export .docx)
- Littérature Semantic Scholar + PubMed intégrée dans `src/retrieval/literature.py`
- CIR : filtre année fiscale (`cir_year`), mode test `CIR_MOCK=1`, fix headings .docx
- CIR : prompt règles 16-18 (ancrage littérature, cohérence S2↔S3, sources hors-scope)

**Livré session 2026-06-24 :**
- LLM RAG migré DeepSeek → Claude (`claude-sonnet-4-6`) — client Anthropic natif
- Suivi budgétaire : `src/usage_tracker.py` — daily/monthly en €, persisté dans `data/usage.json`
- Chainlit : blocage automatique si budget journalier ou mensuel dépassé (`check_budget()`)
- Déploiement sandbox `farid@docker-sandbox:~/accro-rag` (SSH alias `sandbox`) — script `scripts/setup_neo4j_sandbox.sh`

**Livré session 2026-06-25 :**
- `scripts/download_essais.py` : credentials Azure/SharePoint sortis du code → `.env` (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `SHAREPOINT_HOSTNAME`, `SHAREPOINT_SITE_PATH`, `SHAREPOINT_REPERTOIRE_ITEM_ID`) — helper `_require()` pour fail-fast si variable absente

**Axes optionnels si reprise :**
- Contacter Yassine : DST-7 (sans runs) + STRIP-15 (absent SharePoint)
- Re-extraire `panel_ressemblant_score` depuis Excel KOBE → +3-4 questions testset
- Corriger 5 hyperliens cassés dans Répertoire SharePoint (colonne K, cosmétique)
- Compléter effectifs panels sensoriels dans les fiches Excel (FIB-12, ACE-6)

⚠️ **Ne jamais lancer `--ragas` sans accord explicite** — coût ~$7/run.

---

## Carte des modules

| Fichier | Rôle |
|---------|------|
| `src/config.py` | Constantes et variables d'env (SCORE_THRESHOLD, CORPUS_SCOPE, clés API) |
| `src/models.py` | Dataclasses Pydantic : `QueryRequest`, `QueryResponse`, `Source` |
| `src/api.py` | FastAPI : `POST /query`, `GET /health`, `GET /corpus` — point d'entrée HTTP |
| `src/query.py` | CLI : `python -m src.query "<question>"` — point d'entrée terminal |
| `src/app.py` | Application Gradio (interface web locale) |
| `src/chainlit_app.py` | Interface Chainlit (chat web) — lancer avec `PYTHONPATH="." chainlit run src/chainlit_app.py --port 8001` |
| `src/generation/rag_pipeline.py` | Orchestration RAG : `run_query()`, `build_pipeline()`, `get_dense_score()` |
| `src/generation/prompt_fr.py` | Template de prompt système (français) |
| `src/cir.py` | Génération fiches CIR depuis Neo4j → Claude (3 groupements) — exports : `stream_fiche_cir`, `export_docx`, `get_project_start_year`, `build_cir_clients` |
| `src/generation/prompt_cir.py` | Prompts CIR : `SYSTEM_PROMPT_CIR_MUSCLES/NOUVELLES_VOIES/PRODUITS`, `CIR_FORMAT` — 18 règles MESRI |
| `src/retrieval/literature.py` | `fetch_literature(groupement, max_papers, year_max)` — Semantic Scholar + PubMed, cache 1h |
| `src/retrieval/base.py` | Interface `IRetriever` |
| `src/retrieval/hybrid_retriever.py` | `HybridCypherRetriever` — retrieval dense+sparse via Neo4j |
| `src/retrieval/exact_lookup.py` | Fallback : ingrédient CONTAINS + fulltext Lucene AND |
| `src/retrieval/sharepoint_urls.py` | URLs SharePoint : static fallback + parse download.log + Neo4j lookup |
| `src/ingest/import_neo4j.py` | Import JSON → Neo4j (idempotent via MERGE) — auto-découverte knowledge files |
| `src/ingest/create_indexes.py` | Création index fulltext + vector |
| `src/ingest/embed_chunks.py` | Génère embeddings OpenAI → stocke dans `Chunk.embedding` |
| `src/ingest/calibrate_threshold.py` | Calibration du SCORE_THRESHOLD |
| `scripts/batch_extract.py` | Extraction batch LLM : xlsx/docx/csv → 4 artefacts KG |
| `scripts/build_kg.py` | Triples CSV + documentation MD + validation MD depuis knowledge JSON |
| `src/usage_tracker.py` | Suivi budgétaire API Claude — `record_usage`, `check_budget`, `usage_summary` — persisté dans `data/usage.json` |
| `scripts/download_essais.py` | Téléchargement fichiers liés depuis SharePoint (MSAL device flow) |
| `scripts/setup_neo4j_sandbox.sh` | Setup Neo4j sur sandbox : démarrage, chargement dump, fix NEO4J_URI |

---

## Déploiement sandbox

**Cible :** `farid@docker-sandbox:~/accro-rag` — alias SSH `sandbox` (ProxyJump via `54.36.121.98:54722`)

**Sync du code :**
```bash
rsync -avz --exclude='.git/' --exclude='.venv/' --exclude='__pycache__/' \
  --exclude='.env' --exclude='.playwright-mcp/' --exclude='.claude/' \
  --exclude='data/repertoire_rd_2025-2026/lien_essai/' \
  /home/farid/Documents/projets_accro/r\&d_new/ sandbox:~/accro-rag/
# Envoyer le .env séparément (clés API — à faire manuellement)
scp .env sandbox:~/accro-rag/.env
```

**Premier déploiement :**
```bash
ssh sandbox
cd ~/accro-rag
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./scripts/setup_neo4j_sandbox.sh   # démarre Neo4j, charge le dump, fixe NEO4J_URI
```

**⚠️ Neo4j sur sandbox — problème iptables Docker (Ubuntu) :**
`localhost:7688` est inaccessible depuis l'hôte malgré le port mappé.
Utiliser l'IP interne du container : `bolt://172.30.0.2:7687`
→ Le script `setup_neo4j_sandbox.sh` met à jour `NEO4J_URI` automatiquement.
Si le container est recréé, récupérer la nouvelle IP : `docker inspect accro-rag-neo4j-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'`

**Lancer l'appli :**
```bash
PYTHONPATH=. .venv/bin/chainlit run src/chainlit_app.py --port 8001
```

**Accès depuis le PC local** (tunnel SSH) :
```bash
ssh -L 8003:localhost:8001 sandbox   # ports 8001/8002 souvent déjà pris localement
# → http://localhost:8003
```

---

## Tech Stack

| Component | Tool | Key detail |
|-----------|------|------------|
| Language | Python 3.11+ | strict typing, ruff |
| Graph DB + Vector DB | Neo4j 2025.x | Cypher 25 — vector store unique |
| Graph RAG | neo4j-graphrag | HybridCypherRetriever |
| Embeddings | OpenAI text-embedding-3-large | **dimensions=1536** (always) |
| LLM (RAG) | claude-sonnet-4-6 | via Anthropic API — `LLM_MODEL` dans config.py |
| LLM (CIR) | claude-sonnet-4-6 | via Anthropic API — `CIR_LLM_MODEL` dans config.py |
| LLM (extraction) | claude-sonnet-4-6 | batch_extract.py — thinking activé, streaming |
| Infra | Docker Compose | Neo4j seul |

---

## Commands

```bash
# Infrastructure
docker compose up -d

# Install
pip install -r requirements.txt

# Extraction batch (depuis racine du projet)
python scripts/batch_extract.py --dry-run                      # voir ce qui serait traité
python scripts/batch_extract.py --file "Nom.xlsx"              # tester un fichier
python scripts/batch_extract.py --file "X.xlsx" --force        # forcer re-extraction

# Ingestion (dans cet ordre, après extraction)
python -m src.ingest.import_neo4j    # JSON knowledge → Neo4j
python -m src.ingest.create_indexes  # fulltext + vector indexes
python -m src.ingest.embed_chunks    # chunks + embeddings

# Query CLI
python -m src.query "Quel effet a l'huile sur M03 ?"

# Interface Chainlit (interface principale utilisateurs)
PYTHONPATH="." chainlit run src/chainlit_app.py --port 8001

# API
uvicorn src.api:app --reload --port 8000

# Eval custom (sans Ragas, ~20 min, gratuit)
PYTHONPATH="." .venv/bin/python scripts/eval_rag.py \
  --testset data/testset.json \
  --save results/eval_custom_$(date +%Y-%m-%d).json

# Eval gold (accord explicite requis — ~$7)
PYTHONPATH="." .venv/bin/python scripts/eval_rag.py \
  --testset data/testset.json --ragas \
  --save results/eval_gold_$(date +%Y-%m-%d).json

# Tests
pytest tests/ -v
pytest tests/ -v --cov=src --cov-report=term-missing

# Lint/format
ruff check src/ tests/
ruff format src/ tests/
```

---

## Environment Variables (`.env`)

Variables obligatoires — project ne démarre pas sans elles :

```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<password>
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

Variables optionnelles :
```bash
RAG_IDS_CACHE_TTL=300         # TTL cache IDs Neo4j (0 = reload chaque requête)
CIR_MOCK=1                    # Mode test CIR — bypass LLM + Neo4j, génère une fiche fictive
SEMANTIC_SCHOLAR_API_KEY=...  # Augmente le quota Semantic Scholar
USD_TO_EUR=0.92               # Taux de conversion pour le suivi budgétaire (défaut 0.92)
DAILY_BUDGET_EUR=10.0         # Budget journalier Claude en € (0 = désactivé)
MONTHLY_BUDGET_EUR=100.0      # Budget mensuel Claude en € (0 = désactivé)
```

Variables `scripts/download_essais.py` (téléchargement SharePoint — obligatoires pour ce script) :
```bash
AZURE_CLIENT_ID=...               # ID app Azure AD (device code flow)
AZURE_TENANT_ID=...               # ID tenant Microsoft 365 nxtfood.fr
SHAREPOINT_HOSTNAME=nxtfoodfr.sharepoint.com
SHAREPOINT_SITE_PATH=/sites/RD
SHAREPOINT_REPERTOIRE_ITEM_ID=... # sourcedoc= extrait de l'URL SharePoint du répertoire
```

---

## Code Conventions

- **Typage strict** — annotations sur toutes les fonctions
- **snake_case** partout
- **Constantes en MAJUSCULES** dans `src/config.py`, chargées depuis `.env`
- **ruff** pour lint et format (pas de black, pas de flake8)
- Une fonction = une responsabilité
- Pas de secrets hardcodés — tout dans `.env`

---

## Neo4j Schema

**Nodes:** `Experiment`, `Run`, `Ingredient`, `Chantier`, `Lead`, `Chunk`

**Propriétés Experiment :** `scale`, `sharepoint_url`, `status` (preliminary/ongoing/complete)

**Relations :**
```
(Experiment)-[:HAS_RUN]->(Run)
(Experiment)-[:REFERENCES]->(Experiment)
(Experiment)-[:HAS_SUMMARY]->(Chunk)
(Run)-[:USES_INGREDIENT]->(Ingredient)
(Run)-[:BELONGS_TO]->(Chantier)
(Run)-[:HAS_CHUNK]->(Chunk)
(Run)-[:DETAILS]->(Experiment)   # RÉPERTOIRE Run → Experiment détaillé
```

**[:DETAILS] construction :** deux passes dans `build_details_relations()` — match direct sur segment `:Run:`, puis `_DETAILS_OVERRIDES` pour les cas Jaccard failures. 267 edges au 2026-06-09.

**Vector index:** `chunk_embedding` — 1536 dims, cosine, on `Chunk.embedding`

---

## CIR — Génération fiches MESRI

**Spec complète :** `docs/spec/CIR_FEATURE.md`

**3 groupements :** Muscles HME · Produits élaborés · Nouvelles voies DST

**Règles critiques :**
- Utiliser `CIR_LLM_MODEL` (Anthropic `claude-sonnet-4-6`) pour les fiches CIR — `LLM_MODEL` aussi Claude depuis 2026-06-24
- Contexte tronqué à `_MAX_CONTEXT_CHARS = 120_000` — tri par richesse (summary > synthesis > objective)
- Section 1 OBLIGATOIRE : état de l'art + incertitude + distinction R&D/ingénierie
- Section 3 OBLIGATOIRE : sous-paragraphe "Essais non concluants"
- Section 4 OBLIGATOIRE : "Règles opératoires établies" (transférables)
- ⚠ si donnée absente — ne jamais inventer, ne jamais citer un titre/auteur incertain
- Règle 16 : INTERDIRE "pour la première fois dans les conditions ACCRO" → ancrage littérature obligatoire
- Règle 17 : axes Section 3 doivent correspondre exactement aux axes Section 2
- Règle 18 : sources hors-scope (poisson, autre chantier) exclues

**export_docx — détection des titres :**
Basée sur préfixes markdown : `#### ` → H3, `### ` → H2, `## ` → H1, `# ` → H1.
Ne PAS utiliser `_SECTION_RE` ou `_SUBSECTION_RE` (supprimés) — l'ancien regex matchait les
listes numérotées ("1. Prédiction de l'anisotropie…") et les stylistait incorrectement en H2.

**Filtre année fiscale :**
- `stream_fiche_cir(..., cir_year: int | None)` : filtre les runs par `rep.date STARTS WITH str(cir_year)`
- `get_project_start_year(driver, groupement)` : retourne l'année du premier run (sans filtre — historique complet pour la littérature)
- Chainlit : year picker (2025 recommandé / 2026 / 2024) entre le choix du groupement et la génération

**Mode test :**
- `CIR_MOCK=1` → `stream_fiche_cir` génère une fiche fictive sans appel LLM ni Neo4j

**Cas ACCRO — dossier justificatif (Case 2) :**
ACCRO fait de la R&D interne → déclare via formulaire 2069-A-SD → dossier justificatif pour audit DGFiP/MESRI.
Pas de limite de pages imposée (contrairement au CIROCO agrément, Case 1, non applicable à ACCRO).
Priorité : complétude et précision, pas concision.

**Détection dans Chainlit :**
- `_is_cir_generation_request(text)` : CIR présent ET pas question informative → picker
- "Comment fonctionne le CIR ?" → RAG (pas le générateur)

---

## Littérature scientifique (CIR)

Intégrée directement dans `src/retrieval/literature.py` — pas de MCP nécessaire.

| Source | API | Clé |
|--------|-----|-----|
| Semantic Scholar | `semanticscholar` Python client | `SEMANTIC_SCHOLAR_API_KEY` dans `.env` |
| PubMed | E-utilities HTTP (stdlib) | aucune |

Cache 1h par `(groupement, year_max)`. Dégradation gracieuse si une source est indisponible.

`year_max` : si fourni, exclut les articles publiés à partir de cette année (état de l'art = connaissances disponibles AU DÉMARRAGE des travaux). Obtenu via `get_project_start_year()` avant d'appeler `fetch_literature()`.

---

## Critical Rules (Always Do)

- `MERGE` (never `CREATE`) pour les imports Neo4j — idempotence
- `IN TRANSACTIONS OF 500 ROWS` pour les imports CSV
- `IF NOT EXISTS` sur tous les index/contraintes
- `dimensions=1536` à chaque appel OpenAI embeddings
- Valider l'import avec une requête count post-ingestion
- Chaque réponse API doit inclure `sources` (experiment_id + run_id)
- Retourner `FALLBACK_MESSAGE` quand `found_in_corpus=False`
- Lancer `embed_chunks` après chaque import — nouveaux runs non cherchables sans embedding

## Boundaries (Ask First)

- Ajouter des dépendances Python majeures
- Modifier le schéma Neo4j (nouveaux labels ou relations)
- Changer le modèle d'embedding (force re-indexation complète)
- Exposer l'API sur un port public

## Never Do

- Hardcoder des clés API — toujours `.env`
- Retourner une réponse sans vérifier qu'elle vient du contexte
- Supprimer des nœuds Neo4j sans backup
- Envoyer des données internes à des services externes non approuvés

---

## Retrieval Strategy

**Toujours hybride** — toutes les requêtes passent par `HybridCypherRetriever` (Neo4j).
**Fallback gate** : moyenne cosine similarity top-3 chunks < `SCORE_THRESHOLD` → `exact_lookup.py`.
**exact_lookup** : deux passes — (1) ingrédient CONTAINS, (2) fulltext Lucene AND (`+token1 +token2`).
**Ranker** : RRF naive + `effective_search_ratio=2`. Pas de re-ranking (3072 chunks — inutile).

**Graph traversals implémentées :**
- `[:USES_INGREDIENT]` (Phase 1) : tokens ≥7 chars → overlap 324 tokens ingrédients → MAX 2 chunks, appended
- `[:USES_INGREDIENT]` aggregate (Phase 1b) : "quelles exp ont utilisé X ?" → vue exp × ingrédient × nb_runs
- Session-level (Phase 1.5) : préfixes COULEUR-S1, GOUT-S2 → `CONTAINS(':Run:' + pfx)`, LIMIT 30. **Note Cypher :** `r.id` est composite (`EXP:Run:PREFIXE-N`) — utiliser CONTAINS/ENDS WITH, pas STARTS WITH.
- `[:DETAILS]` (Phase 2) : chunks RÉPERTOIRE → `_fetch_details_context()` → HAS_SUMMARY cible, MAX 4
- `[:REFERENCES]` inverse (Phase 3) : "qui référence X ?" → 1-hop, limité à 8 chunks
- Measure-term (Phase 3.5) : `anisotropie/sme/tpa` ou `\b(ai|ph)\b` → injecte section-4 (valeurs dérivées)

**URLs SharePoint** : priorité Neo4j `Experiment.sharepoint_url` → run prefix → static fallback. Batchée.
**run_status** : runs `status=planned` → annotés `[PLANIFIÉ — non réalisé]` dans le contexte.

---

## API publique (`src/generation/rag_pipeline.py`)

```python
run_query(pipeline, question, top_k=10, chantier=None) -> QueryResponse
get_dense_score(pipeline, query) -> float
extract_cited_ids(text) -> set[str]
build_pipeline() -> RAGPipeline  # factory via variables d'env
```

**Invariant critique :** `found_in_corpus=False` → `answer == FALLBACK_MESSAGE`, `sources == []`, aucun appel LLM.

---

## Chainlit — Pièges connus

**Lancement :** toujours avec `PYTHONPATH="." chainlit run src/chainlit_app.py --port 8001`

**Deadlock asyncio + thread :** `thread.join()` dans une coroutine async bloque l'event loop.
L'interface se fige (bouton stop toujours actif, aucun token streamé).
→ Toujours utiliser `await asyncio.to_thread(thread.join)`.

**Threading pattern pour streaming (RAG et CIR) :**
```python
def _produce():
    for item in stream_fn(...):
        asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
    asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

thread = threading.Thread(target=_produce, daemon=True)
thread.start()
while True:
    item = await queue.get()
    if item is None: break
    if isinstance(item, str): await msg.stream_token(item)
    else: final_response = item
await asyncio.to_thread(thread.join)  # ← jamais thread.join() direct
```

**Liens SharePoint non cliquables :** les URLs contiennent des `&` qui corrompent le `href`.
Toujours utiliser `html.escape(url, quote=True)` avant injection dans un tag `<a>` :
```python
import html
safe_url = html.escape(s.sharepoint_url, quote=True)  # & → &amp;
f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">...'
```
Prérequis : `unsafe_allow_html = true` dans `.chainlit/config.toml`.

**Fichiers temporaires (.docx CIR) :** enregistrer les chemins dans `cl.user_session.set("tmp_files", [...])` et nettoyer dans `@cl.on_chat_end` via `os.unlink`.

**Téléchargement .docx :** utiliser `cl.File(display="side")`, jamais `display="inline"`.
Chainlit ne peut pas prévisualiser les .docx en inline — affiche silencieusement rien.

**CSS — sélecteurs DOM Chainlit 2.x :** pas de `data-role="assistant"` ni `.message-content` dans le DOM réel. Inspecter le DOM réel pour cibler les bons sélecteurs.

**Bulle vide sur fallback (réponse sans tokens streamés) :** un fallback (`found_in_corpus=False`, p.ex. déclenché par `absent_topics` avant génération) yield un `QueryResponse` **sans streamer aucun token**. Le handler `on_message` calcule `answer` mais ne l'écrit pas dans la bulle → **bulle vide**, ressenti comme « rien ne se passe » / appli figée. Correctif : après la boucle de streaming, si rien n'a été accumulé, écrire le texte explicitement :
```python
if not accumulated and answer:
    await msg.stream_token(answer)
```
Piège de diagnostic : le symptôme imite un blocage websocket/navigateur, mais les logs serveur ne montrent **aucun appel embeddings/LLM** (le fallback court-circuite avant tout appel réseau) et le process reste à 0 % CPU. Reproduire côté serveur (navigateur neuf / Playwright) avant de suspecter le cache ou une extension.

---

## Chargement sélectif par tâche

> Pour chaque type de tâche, lire ces fichiers AVANT de coder. Ne pas charger tout le projet.

| Tâche | Fichiers à lire |
|-------|----------------|
| **RAG / retrieval** | `src/retrieval/hybrid_retriever.py`, `src/generation/rag_pipeline.py`, `src/config.py` |
| **CIR génération** | `src/cir.py`, `src/generation/prompt_cir.py`, `docs/spec/CIR_FEATURE.md` |
| **Interface Chainlit** | `src/chainlit_app.py` (section Chainlit — Pièges connus ci-dessus) |
| **Ingest / import** | `src/ingest/import_neo4j.py`, `src/ingest/embed_chunks.py`, `src/config.py` |
| **Littérature** | `src/retrieval/literature.py` |
| **Eval / metrics** | `scripts/eval_rag.py`, `data/testset.json` |
| **Tests** | Fichier de test concerné + `src/` correspondant |

---

## Tests

Framework: `pytest` + `pytest-cov` — couverture minimale 70% sur `src/`

**Pattern de mock :** injecter `RAGPipeline(driver_mock, openai_mock, anthropic_mock)` directement.
- `driver.session().run().single()` → contrôle `dense_score`
- `anthropic_mock.messages.create()` → contrôle réponse LLM
- `openai_mock.embeddings.create()` → court-circuite réseau

**Test critique :** ingrédient absent → `FALLBACK_MESSAGE` exact, `sources=[]`, `found_in_corpus=False`.
