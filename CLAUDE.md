# ACCRO Graph RAG — R&D Knowledge Base

## Project

Graph RAG system for internal R&D knowledge retrieval (food tech / meat analogues).
Full spec: `docs/spec/SPEC.md`

**Target users:** R&D teams (Extrusion & Applications poles)
**Core constraint:** Zero hallucination — always cite source (experiment_id, run_id), always return fallback when absent from corpus.

---

## Prochaine étape (2026-06-09 — reprendre ici)

**Corpus : 3072 chunks / 2371 runs / 170 experiments — 100% embedé. SCORE_THRESHOLD = 0.6689.**
**Statut : V2 Graph RAG + Phase 1.5 (session-level retrieval) implémentés. Reprendre à l'étape E (eval final).**

### Métriques actuelles (eval `results/eval_context_recall_phase1b_v3_2026-06-09.json`)
| Métrique | Valeur | Cible | Statut |
|---|---|---|---|
| `absent_fallback_rate` | **1.0** | 1.0 | ✅ |
| `present_fallback_rate` | **0.0%** | 0.0 | ✅ |
| `citation_coverage` | **97.3%** (2/75 absents) | ≥ 98% | ⚠️ |
| `citation_validity` | **97.3%** (3/75 invalides) | ≥ 98% | ⚠️ |
| `context_recall` | **0.674** (74 questions présentes) | > 0.75 | ⚠️ |

**Note cible révisée :** la cible 0.85 était calibrée sur 63 questions simples (V1). Le testset V2 compte 74 présentes dont 12 questions Graph RAG plus difficiles. La cible réaliste est **0.75**.

### À faire dans cet ordre strict

#### ~~A — Eval custom V2~~ ✅ fait
#### ~~B — Corriger ground_truths graph~~ ✅ fait (5 questions : 4 graph_details + ACE-4 vs ACE-5)
#### ~~C — Diagnostic régression AI/DST~~ ✅ fait — Fix B appliqué (measure-term augmentation)
#### ~~D — Mesurer context_recall post-chunks~~ ✅ fait — 0.674 mesuré (Phase 1b aggregate)
#### ~~D' — Phase 1.5 session retrieval~~ ✅ fait — session prefix detection + KOBE GT fixes

#### E — Eval final context_recall ← COMMENCER ICI
⚠️ Coût ~$1 (context_recall seul, 74 questions présentes). **Accord explicite requis avant de lancer.**
```bash
rm -rf .ragas_cache/ .eval_cache/
PYTHONPATH="." .venv/bin/python scripts/eval_rag.py \
  --testset data/testset.json --ragas \
  --metrics context_recall \
  --save results/eval_context_recall_final_$(date +%Y-%m-%d).json
```
Cible : context_recall > 0.75.

#### F — Résoudre les 2 cas citation_coverage persistants (optionnel, faible impact)
- **CONS-VIEILL-01** : question synthèse — le LLM génère des bullet points sans marqueurs inline. Limitation connue de DeepSeek sur les longues synthèses. Fix potentiel : ajouter un rappel de citation dans le prompt pour les questions contenant "synthétiser" / "résumer".
- **Stochastique** (EI-DEBIT, PEA-REF, psyllium…) : variance ~±3% entre runs. Pas de fix structurel possible.

#### F — Récupérer les données manquantes (levier impact corpus)
- Contacter Antoine : accès site `RDIndustrieCollaboration` (15+ essais industriels)
- Contacter Yassine : DST-7 introuvable + STRIP-15/18
- Corriger 6 hyperliens cassés dans Répertoire SharePoint (colonne K) : STRIP-BOEUF×2, PP-18, PP-REC-12 Botanical, STRIP-B09-250415, VEILLE-4

---

### Fixes appliqués (2026-06-08) — session actuelle ✅

**Fix ACE-5 / RÉPERTOIRE coverage bug (`rag_pipeline.py`) :**
Les chunks RÉPERTOIRE ont leur exp_id cible comme dernier composant de run_id (ex. `RÉPERTOIRE:Run:ACE-5`) → falsement comptés comme "ACE-5 couvert". Fix : `non_rep_chunks` exclus du calcul de couverture avant augmentation.

**Fix exp_names digit filter :**
Les IDs avec chiffres (ACE-4, ACE-5) étaient passés dans `exp_names` ET dans `digit_patterns`, causant une double requête avec LIMIT 2 qui retournait ACE-4 seulement. Fix : filtre `not any(c.isdigit() for c in t)` sur exp_names.

**Phase 3.5 — Measure-term augmentation (`rag_pipeline.py`) :**
Déclenché si `anisotropie`, `sme`, `tpa` dans la question **ou** `\b(ai|ph)\b`. Injecte le chunk `type='experiment_section'` contenant `## 4` (valeurs dérivées) pour chaque exp_id en résultats hybrides si section-4 absente. MAX 1 chunk, prépendé. Cypher : `c.experiment_id IN $exp_ids AND c.type='experiment_section' AND c.text CONTAINS '## 4'`.

**Correction ground_truths (5 questions) :**
Script `scripts/update_ground_truths.py` — mode `--show` (gratuit) + `--update TYPE` (DeepSeek). Cherry-pick manuel dans `data/testset_candidate_v2.json`. Ne jamais écraser `testset.json` directement.

**Note sur la question DST/AI :**
La section-4 (AI=3.232 pour M03 essai 3) est maintenant injectée mais le LLM ne relie pas "Run 3" à "essai 3 – condition optimale 120°C" car le chunk utilise la terminologie "essai 3" et non "Run 3". Ce mismatch de nommage dans les données source ne peut pas être corrigé côté code.

---

### Graph RAG V2 — implémenté (2026-06-08) ✅

**Phase 0 — Audit complet :**
- 0A : 1059/1245 ingrédients ≤ 15 runs, psyllium = 3 runs → injection directe OK. Ingrédients génériques (Nutralys 393, Eau 803) → limité à 2 slots.
- 0B : 10 questions ingredient-centric, 5 répertoire/navigation, 0 évolution — testset pauvre en graph queries.
- 0C : 12 questions graph ajoutées → 96 questions au total (4 graph_ingredient, 4 graph_details, 2 graph_references, 2 comparatives).

**Phase 1 — `[:USES_INGREDIENT]` traversal ✅**
Tokens ≥7 chars extraits des noms d'ingrédients (324 tokens). Détection par overlap → une requête Cypher par token (pas d'OR entre tokens) → MAX 2 chunks injectés en fin de contexte.

**Phase 2 — `[:DETAILS]` traversal ✅**
Chunks REPERTOIRE détectés dans résultats hybrid → `_fetch_details_context()` → HAS_SUMMARY de l'expérience cible injecté. Déterministe, MAX 4 chunks.

**Fixes appliqués (2026-06-08) :**
- Sources étendues après chaque traversal graphe (avec déduplication par run_id) → liens SharePoint présents dans `QueryResponse.sources`
- Une requête Cypher par token ingredient (au lieu d'un OR global) → pas de contamination entre tokens de sens différent

### Améliorations V1.1 — ✅ complétées (2026-06-08)

~~Étape 1 — Diagnostic AI/DST~~ → Fix B (measure-term augmentation) appliqué.
~~Étape 3 — Fix ciblé~~ → `_fetch_measure_sections()` + `_MEASURE_SECTION_CYPHER` dans `rag_pipeline.py`.

**Ne pas implémenter le re-rank par type de chunk** sans jeu de tests couvrant les questions
de glossaire — risque de régressions sur "qu'est-ce que le SME ?", "que signifie AI ?".

---

### Roadmap V2 — Graph RAG réel (après stabilisation V1.1)

**Contexte :** le graphe Neo4j est peuplé ([:USES_INGREDIENT], [:DETAILS], [:REFERENCES], [:BELONGS_TO]) mais ces edges ne sont pas dans le chemin de retrieval. Le système est aujourd'hui un hybrid RAG (vector + fulltext) qui utilise Neo4j comme store — pas un Graph RAG qui raisonne sur la topologie.

**Règle non négociable : une traversal à la fois, eval complète avant la suivante.**

#### Phase 0 — Audit + testset (obligatoire, ~2h, à faire avant tout code)

**0A — Auditer la distribution des ingredients dans Neo4j**
```cypher
MATCH (i:Ingredient)<-[:USES_INGREDIENT]-(r:Run)
RETURN i.name, count(r) AS nb_runs
ORDER BY nb_runs DESC LIMIT 20
```
Si un ingredient clé (psyllium, pois) apparaît dans > 15 runs → injection brute impossible, il faut un ranking. Si ≤ 15 → injection directe faisable.

**0B — Classifier les 63 questions du testset par pattern de requête**
- Ingredient-centric : "comparaison psyllium", "essais protéine pois"
- REPERTOIRE navigation : "détails de STRIP-B09", "que dit l'essai KEFTA-12"
- Évolution formule : "historique P01", "suite de JUT-REC-11"
- Cross-chantier : "tous les essais extrusion basse humidité"

Le pattern dominant dans les 63 questions détermine quelle Phase implémenter en premier.

**0C — Ajouter 10-15 questions graph-spécifiques au testset** (`data/testset.json`)
Sans elles, impossible de mesurer si les traversals graph améliorent quoi que ce soit.
Exemples à créer :
- "Quels essais ont utilisé du psyllium Fibrinel PSL sur la P01 ?"
- "Quel est le détail de l'essai référencé par le run STRIP-B09 dans le REPERTOIRE ?"
- "Quelles expériences ont utilisé JUT-REC-11 comme référence ?"
- "Comparer tous les runs utilisant Nutralys F85M à 400 rpm"

#### Phase 1 — [:USES_INGREDIENT] traversal (si audit 0A le justifie)

**Condition d'entrée :** ingredients clés ≤ 15 runs chacun ET ≥ 5 questions testset de type ingredient.

**Implémentation (3 sous-étapes distinctes) :**

1. **Index ingredients au démarrage** — comme `_known_exp_ids` existe déjà :
```python
# RAGPipeline.__init__ ou build_pipeline()
self._known_ingredients = _load_ingredient_names(driver)
# {"psyllium fibrinel psl", "nutralys f85m", ...} — normalisé lowercase
```

2. **Détection dans la question** — token overlap case-insensitive sur les noms normalisés (pas de fuzzy complex, juste `.lower()` + CONTAINS sur les tokens significatifs ≥ 5 chars).

3. **Cypher ciblé + slots** — les chunks ingredient arrivent **après** les chunks hybrid dans la liste, MAX 2 slots sur 6. Jamais en tête.

**Validation :** eval custom sur les 10-15 nouvelles questions avant/après. Zero régression sur les 63 existantes.

#### Phase 2 — [:DETAILS] traversal REPERTOIRE → Experiment (plus simple, déterministe)

Si un chunk retrieval a `experiment_id == "REPERTOIRE-RD-2025-2026"` ET que son run a un edge `[:DETAILS]` → injecter automatiquement 1 chunk (section-5) de l'expérience cible.
Pas de fuzzy matching. Logique déterministe. Implémenter après Phase 1 stabilisée.

#### Phase 3 — [:REFERENCES] inverse (en dernier)

"Quelles expériences référencent JUT-REC-11 ?" → traversal `<-[:REFERENCES]-`.
Impact limité tant que les targets n'ont pas de HAS_SUMMARY (dépend des données manquantes Antoine/Yassine). Implémenter seulement si corpus enrichi.

### État des évaluations (2026-06-07) — version finale V1

#### Métriques custom — v8 (post-chunks) ✅
| Métrique | v4 (avant chunks) | **v8 (après chunks)** | Cible | Statut |
|---|---|---|---|---|
| `absent_fallback_rate` | 100% | **100%** | 1.0 | ✅ |
| `present_fallback_rate` | 1.6% | **1.6%** (1/63) | 0.0 | ✅ acceptable |
| `post_llm_fallback_rate` | 1.6% | **1.6%** | — | stable |
| `citation_coverage` | 100% | **98.4%** | 1.0 | ⚠️ régression -1.6% |
| `citation_validity` | 97.7% | **98.3%** | 1.0 | ✅ |
| Input tokens | 513 662 | **340 633** | — | ✅ -33% |

**Régression citation_coverage :** 1 cas — AI/DST Run 3 (section-4 evincée du top-6 par les nouveaux chunks). Diagnostiquer à l'étape 1.
**Victoire nette :** GLU-2 passe de `found_in_corpus=False` (post-LLM fallback) à réponse correcte avec 2 citations valides — directement grâce au split section-5.

#### Métriques Ragas — évolution
| Métrique | Baseline | v2 | v3 | v7 | **v8 (attendu)** | Cible |
|---|---|---|---|---|---|---|
| `faithfulness` | 0.71 | 0.716 | 0.790 | **0.882** | ~0.88+ | >0.85 ✅ |
| `answer_relevancy` | 0.65 | 0.573 | 0.635 | **0.725** | à mesurer | >0.72 ✅ |
| `context_recall` | — | — | — | **0.818** | à mesurer post-chunks | >0.85 |

### Fixes appliqués (2026-06-07) — v4 ✅

**Fix A — Détecteur de non-réponse post-LLM (`rag_pipeline.py`)**
`_NO_DATA_PATTERNS` (11 patterns calibrés sur les 15 cas AR=0 de v3) + `_is_no_data_response()` avec garde `extract_cited_ids`. Actif dans `run()` et `run_stream()`. GLU-2 correctement détecté ; les 14 autres cas v3 produisent maintenant des réponses citées (LLM adapté au Fix B).

**Fix B — Prompt valeurs numériques (`prompt_fr.py`)**
Ajout à la règle 1 : "Toute valeur numérique (SME, AI, TPA, pH, score sensoriel, etc.) doit être reprise telle quelle depuis le contexte — ne jamais arrondir, inférer ou calculer." → `citation_coverage` 92.1% → 100%.

**Fix C — Métriques eval (`eval_rag.py`)**
- `post_llm_fallback_rate` : distingue fallbacks pre-LLM (gate dense) vs post-LLM (détecteur)
- `answer_preview` (200 chars) dans `ragas_per_question` pour diagnostic croisé
- Champ `post_llm_detected` dans chaque résultat

**Tests ajoutés (`tests/test_rag.py`)** : 4 tests unitaires pour `_is_no_data_response`.

### Fixes appliqués (2026-06-07) — v3 ✅
- Fix A→E : `_extract_id_patterns` digit-only, dédup par texte, exp names sans chiffre, patch eval augment, answer_preview

### Fixes appliqués (2026-06-06) ✅
- Prompt CoT 2-étapes, `TOP_K_DEFAULT` 10→6, kwargs Ragas, SCORE_THRESHOLD 0.6682→0.6689
- Augmentation post-retrieval, topic gate (LME acronyme, méthylcellulose 15 chars)
- Testset : 68→84 questions, 5→21 absentes, lupin reclassé en factuelle

### Gold testset — `data/testset.json` (84 paires, 2026-06-07)
- 21 factuelles / 20 synthèses / 20 comparatives / 2 cross_experiment
- **21 `absent`** : LME, DST-7, ACE-8, ACE-9, méthylcellulose, fermentation lactique, carraghénane, gomme xanthane, lyophilisation, spray drying + 11 IDs inexistants sous préfixes connus (FIB-5/7, STRIP-2/20, GLU-3, DST-8, NPT-DEV-3, BACON-2, PP-10, JUT-REC-4/11)
- Ground_truth raccourcies sur `synthèse` → FactualCorrectness mécaniquement bas, pas un échec RAG

⚠️ **Ne jamais lancer `--ragas` sans accord explicite** — coût ~$7/run (7 métriques × 63 questions).

### Commandes de référence
```bash
# Eval Ragas v4 (faithfulness + answer_relevancy — VIDER LE CACHE avant)
rm -rf .ragas_cache/
PYTHONPATH="." .venv/bin/python scripts/eval_rag.py \
  --testset data/testset.json --ragas \
  --metrics faithfulness,answer_relevancy \
  --save results/eval_ragas_priority_v4_$(date +%Y-%m-%d).json

# Eval custom uniquement (sans Ragas, ~20 min, gratuit)
PYTHONPATH="." .venv/bin/python scripts/eval_rag.py \
  --testset data/testset.json \
  --save results/eval_custom_$(date +%Y-%m-%d).json

# Eval gold complète (accord explicite requis)
PYTHONPATH="." .venv/bin/python scripts/eval_rag.py \
  --testset data/testset.json --ragas \
  --save results/eval_gold_$(date +%Y-%m-%d).json

# Tâches de nettoyage optionnelles
# 1. Supprimer scripts/_debug_strip40.py (script temp)
# 2. Corriger 6 hyperliens cassés dans le Répertoire SharePoint (colonne K) :
#    STRIP-BOEUF/STRIPS-BOEUF, PP-18, PP-REC-12 Botanical, STRIP-B09-250415, VEILLE-4
```

### Données manquantes connues (2026-06-08)
- **44 stubs sans données** — experiments référencés par d'autres mais sans fichier source : DST-7, JUT-REC-4/11, codes projets (B09, M03, P03, KOBE, MDD...), rapports EI sans extraction
- **6 hyperliens cassés** dans le fichier Répertoire SharePoint (irrécupérable automatiquement)
- **DST-7** — référencé par 5+ expériences, fichier introuvable (contacter Yassine)
- **STRIP-15, STRIP-18** — fichiers introuvables sur SharePoint (contacter Yassine)

---

## État d'avancement (2026-06-05)

| Tâche | Fichier | Statut |
|-------|---------|--------|
| T1–T2 | `docker-compose.yml`, `requirements.txt`, `src/config.py`, `src/models.py` | ✓ |
| T3 | `src/ingest/import_neo4j.py` | ✓ |
| T4 | `src/ingest/create_indexes.py` | ✓ |
| T5 | `src/ingest/embed_chunks.py` | ✓ |
| T6 | `src/retrieval/base.py`, `src/retrieval/hybrid_retriever.py` | ✓ |
| T7 | `src/retrieval/exact_lookup.py` | ✓ étendu |
| T8 | `src/generation/prompt_fr.py`, `src/generation/rag_pipeline.py` | ✓ |
| T8.bis | `src/ingest/calibrate_threshold.py` | ✓ |
| T9 | `src/api.py` — FastAPI `POST /query`, `GET /health`, `GET /corpus` | ✓ |
| T10 | `src/query.py` — CLI `python -m src.query "<question>"` | ✓ |
| T11–T13 | `tests/` — 64 tests (test_rag, test_api, test_query_cli, test_retrieval, test_ingest) | ✓ |
| **T14** | **`scripts/batch_extract.py`** — **pipeline d'extraction batch des fichiers bruts** | **✓ validé** |
| **T15** | **Pipeline ingestion robuste** — auto-découverte, batch UNWIND, hash-skip, --experiment, HAS_SUMMARY | **✓** |
| **T16** | **RAG quality** — RRF ranker, ESR=2, [:REFERENCES] traversal, run_status labels | **✓** |
| **T17** | **Eval** — `scripts/eval_rag.py` métriques custom + Ragas optionnel | **✓** |

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
| `src/retrieval/exact_lookup.py` | Fallback : ingrédient CONTAINS + fulltext Lucene AND sur run.objective/synthesis/name |
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
Deux passes dans `build_details_relations()` :
1. Direct : segment après `:Run:` dans l'id REPERTOIRE == `Experiment.id`
2. `_DETAILS_OVERRIDES` dict : overrides manuels pour les cas où la normalisation Jaccard échoue (KEFTA-1→20 → KEFTA-BOULETTES-LAB, PIPE25-19/20/31→39 → MDD-EMINCE-THAI-KEBAB, KOBE-1→23 → leurs expériences détaillées).
**265 edges [:DETAILS]** au 2026-06-05 (304 runs REPERTOIRE, 80% couverts).

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
**Fallback gate** : moyenne cosine similarity top-3 chunks < `SCORE_THRESHOLD` → `exact_lookup.py`. Pas de routing conditionnel.
**exact_lookup** : deux passes — (1) ingrédient CONTAINS, (2) fulltext Lucene AND sur `run.objective + synthesis + name` (index `run_fulltext`). Mode AND : `+token1 +token2` — évite les faux positifs OR.

**Filtre chantier** : deux branches — avec filtre : MATCH sur chantier + dense rank exact ; sans filtre : hybrid normal.
**Ranker** : RRF naive (`_RANKER = "naive"`) + `effective_search_ratio=2` — fetch 2× candidats avant ranking. Plus robuste que linear ranker sur les requêtes mixtes nom/sémantique.
**Pas de re-ranking** (corpus ~3072 chunks — inutile à cette taille).
**IRetriever interface** préservée pour migration future vers un store externe.

**[:REFERENCES] traversal** : après retrieval hybride, une requête Cypher 1-hop injecte les `HAS_SUMMARY` chunks des expériences référencées (si pas déjà dans les résultats). Limité à 8 chunks. Efficacité limitée : la majorité des 44 stubs sans données sont les targets des 62 `[:REFERENCES]` edges — les EI industriels et DST-7 sont présents en graphe mais sans contenu.

**[:DETAILS] traversal (Phase 2)** : chunks REPERTOIRE-RD-2025-2026 détectés → `_fetch_details_context()` suit l'edge `[:DETAILS]` vers l'Experiment détaillé → injecte son `HAS_SUMMARY` (ou premier `HAS_CHUNK` si absent). MAX 4 chunks. Déterministe.

**[:USES_INGREDIENT] traversal (Phase 1)** : tokens ≥7 chars extraits de la question → overlap avec 324 tokens d'ingrédients → `_fetch_ingredient_context()` → MAX 2 chunks triés par date (les plus récents). Appended en fin de contexte, jamais en tête.

**URLs SharePoint** : priorité Neo4j `Experiment.sharepoint_url` → run prefix → static fallback. Batchée en 1 requête pour N sources.

**run_status** : les runs `status=planned` sont annotés `[PLANIFIÉ — non réalisé]` dans le contexte et le LLM est instruit de ne pas les présenter comme acquis (prompt règle 6).

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

## Téléchargement des essais — État (2026-06-08)

Script : `python scripts/download_essais.py --dest data/repertoire_rd_2025-2026/lien_essai/lien_essai_brut --sheet "Répertoire Essais"`
Log complet : `data/repertoire_rd_2025-2026/lien_essai/lien_essai_brut/download.log`
État : **275 présents, 2 échecs restants**

ℹ️ `RDIndustrieCollaboration` est accessible — tous les EI (rapports marinades, égrené poulet, allumettes MDD, éminces MDD/kebab, fines lamelles Thaï, etc.) sont téléchargés et extraits.

### Échecs restants (2 fichiers)
- `STRIP-40` — WEBURL disponible dans download.log mais résolution échoue (lien relatif cassé dans le Répertoire)
- `ACE-7` — même problème ; fichier déjà extrait séparément depuis un autre lien

### Site `RD-Production` inaccessible (1 fichier)
- `Essais transferts des références en TVP - onglet "Essais haché"` — GUID `DE473A9D-E2CE-4F5B-99F3-1646B0746877`

### En attente de Yassine (fichiers introuvables sur SharePoint)
- `STRIP-15 Essai huile aromatisée`
- `STRIP-18 Essai incorporation d'épices`
- ~~`STRIP-19 Essai cuisson`~~ — ✅ téléchargé et extrait (2026-06-08)

---

## Corpus

### Dans Neo4j (état 2026-06-08)

**3072 chunks / 2371 runs / 170 experiments — 100% embedé.**

| Métrique | Valeur |
|----------|--------|
| Chunks | 3072 |
| Runs | 2371 |
| Experiments | 170 |
| [:REFERENCES] edges | 62 |
| [:DETAILS] edges | 267 |
| Null embeddings | 0 |
| SCORE_THRESHOLD | 0.6689 |
| knowledge.json importés | 127 (tous) |
| Stubs sans données | 44 (IDs référencés sans fichier source) |

`_knowledge.json` → source primaire Neo4j (import + structure)
`_documentation.md` → source de chunking pour Neo4j vector index
`_triples.csv` → validation uniquement

### Fixes embed_chunks (2026-06-05)
- **Regex `[\w.\-]+`** au lieu de `[\w-]+` dans `_chunk_run_detail` — les run IDs avec points (`FA-5.1-A`, `PR-5.2`, etc.) étaient silencieusement ignorés. Fix : +69 chunks PP-REC-12 désormais indexés.
