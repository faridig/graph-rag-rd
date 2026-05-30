# Spec : Graph RAG — Capitalisation des savoirs R&D Food Tech

**Version :** 1.2  
**Date :** 2026-05-29  
**Statut :** Validé (Phase 1 → Phase 2)

---

## 1. Objectif

Construire un système de recherche et de synthèse sur la connaissance R&D interne d'ACCRO,
permettant aux équipes de retrouver instantanément ce que l'organisation sait déjà :
ingrédients testés, effets mesurés, résultats d'expériences, avancement de projets.

**Utilisateurs cibles :** Équipes R&D (pôles Extrusion et Applications)

**Questions représentatives (critère de succès fonctionnel) :**
1. "Est-ce que l'ingrédient Pisane ES a déjà été testé ?"
2. "Quel est l'effet de la cystéine en extrusion ?"
3. "Fais-moi une synthèse de toutes les expériences et résultats sur l'utilisation de fibres en extrusion"
4. "Fais-moi un rapport de l'avancement du projet Bacon en 2026"

**Contrainte principale :** Précision avant tout — zéro hallucination, citation source obligatoire,
réponse "absent du corpus" quand l'information n'est pas indexée.

---

## 2. Tech Stack

| Composant | Outil | Version |
|-----------|-------|---------|
| Langage | Python | 3.11+ |
| Graph DB | Neo4j | 2025.x (Cypher 25) |
| Vector DB | Qdrant | latest |
| Graph RAG | neo4j-graphrag | latest |
| Embeddings | OpenAI text-embedding-3-large | **1536 dims** (paramètre `dimensions=1536`) |
| LLM | Anthropic Claude (claude-sonnet-4-6) | via API |
| Infra locale | Docker Compose | — |

---

## 3. API Endpoints

### POST /query — question principale

```python
class QueryRequest(BaseModel):
    question: str
    top_k: int = 10
    chantier: str | None = None     # filtre optionnel (ex: "Kobé", "FiproVex")

class Source(BaseModel):
    run_id: str
    experiment_id: str
    source_file: str
    score: float

class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    found_in_corpus: bool           # False → réponse fallback activée
    corpus_scope: list[str]         # ["REPERTOIRE-RD-2025-2026", "ACE-3", "ACE-5"]
```

### GET /health
```json
{"status": "ok", "neo4j": "connected", "qdrant": "connected"}
```

### GET /corpus
```json
{
  "sources": [
    {"id": "REPERTOIRE-RD-2025-2026", "runs": 316, "type": "registry"},
    {"id": "ACE-3", "runs": 1, "type": "detailed"},
    {"id": "ACE-5", "runs": 11, "type": "detailed"}
  ]
}
```

---

## 4. Commandes

```bash
# Setup
docker compose up -d                    # Lance Neo4j + Qdrant

# Installation
pip install -r requirements.txt

# Ingestion
python src/ingest/01_import_neo4j.py    # Import triples CSV → Neo4j
python src/ingest/02_create_indexes.py  # Crée indexes fulltext + vector
python src/ingest/03_embed_qdrant.py    # Chunks + embeddings → Qdrant

# Query (CLI)
python src/query.py "Quel effet a l'huile sur M03 ?"

# API
uvicorn src.api:app --reload --port 8000

# Tests
pytest tests/ -v
pytest tests/ -v --cov=src --cov-report=term-missing

# Lint
ruff check src/ tests/
ruff format src/ tests/
```

---

## 5. Structure du projet

```
r&d_new/
├── data/                                   # Données source (existant)
│   └── repertoire_rd_2025-2026/
│       ├── REPERTOIRE-RD-2025-2026_triples.csv
│       ├── REPERTOIRE-RD-2025-2026_knowledge.json
│       ├── REPERTOIRE-RD-2025-2026_documentation.md
│       └── lien_essai/
│           ├── ACE-3/                      # Expérience détaillée
│           └── ACE-5/                      # Expérience détaillée
│
├── docs/
│   ├── spec/
│   │   └── SPEC.md                         # Ce fichier
│   └── ideas/
│
├── src/
│   ├── config.py                           # Constantes (MAJUSCULES), chargement .env
│   ├── ingest/
│   │   ├── 01_import_neo4j.py              # LOAD CSV → Neo4j (idempotent via MERGE)
│   │   ├── 02_create_indexes.py            # Indexes Neo4j (IF NOT EXISTS)
│   │   └── 03_embed_qdrant.py              # Chunks → Qdrant (upsert)
│   ├── retrieval/
│   │   ├── hybrid_retriever.py             # HybridCypherRetriever (route principale)
│   │   └── exact_lookup.py                 # Cypher exact fallback si 0 résultat hybrid
│   ├── generation/
│   │   ├── prompt_fr.py                    # Template prompt français
│   │   └── rag_pipeline.py                 # GraphRAG pipeline
│   ├── models.py                           # Pydantic models (QueryRequest, QueryResponse)
│   └── api.py                              # FastAPI entry point
│
├── tests/
│   ├── test_ingest.py
│   ├── test_retrieval.py
│   └── test_rag.py
│
├── docker-compose.yml
├── requirements.txt
├── .env                                    # Existant — à compléter
└── README.md
```

---

## 6. Schéma de données Neo4j

### Nœuds

| Label | Propriétés clés | Source |
|-------|-----------------|--------|
| `Experiment` | `id`, `title`, `date`, `equipment`, `domain`, `objective` | `_triples.csv` |
| `Run` | `id`, `name`, `date`, `objective`, `synthesis`, `status`, `is_control` | `_triples.csv` |
| `Ingredient` | `name` | extrait des `Input` |
| `Chantier` | `name`, `cir_grouping` | REPERTOIRE |
| `Lead` | `name` | REPERTOIRE |
| `Chunk` | `text`, `embedding`, `source_file` | généré à l'ingestion |

### Relations

```
(Experiment)-[:HAS_RUN]->(Run)
(Run)-[:USES_INGREDIENT]->(Ingredient)
(Run)-[:BELONGS_TO]->(Chantier)
(Run)-[:LED_BY]->(Lead)
(Run)-[:HAS_CHUNK]->(Chunk)
(Run)-[:DETAILS]->(Experiment)   # lien REPERTOIRE Run → Experiment détaillé
```

### Relation [:DETAILS] — construction programmatique

Le lien entre un run du REPERTOIRE et son expérience détaillée (ACE-3, ACE-5…) n'est
**pas un prédicat explicite dans les CSV** — il se déduit en deux étapes après le chargement
des 3 sources :

1. **Signal dans le CSV REPERTOIRE** : le run porte un `note` "Lien vers fiche détaillée:
   ACE-3-Impact NaCl et KCl sur P02" et son `id` contient déjà le code de l'expérience
   (`REPERTOIRE-RD-2025-2026:Run:ACE-3`).

2. **Matching par id** : extraire le segment après `:Run:` et vérifier qu'un nœud
   `Experiment` avec cet id existe dans le graph.

```cypher
-- À exécuter après LOAD CSV des 3 sources
MATCH (run:Run)
WHERE run.id STARTS WITH "REPERTOIRE-RD-2025-2026:Run:"
WITH run, split(run.id, ":Run:")[1] AS exp_id
MATCH (exp:Experiment {id: exp_id})
WHERE exp_id <> "REPERTOIRE-RD-2025-2026"
MERGE (run)-[:DETAILS]->(exp)
```

Ce lien est **critique** pour la Q4 ("rapport Kobé 2026") : il permet de remonter depuis
les runs résumés du REPERTOIRE vers les données complètes des expériences ACE-3/ACE-5.

### Indexes obligatoires

```cypher
CREATE CONSTRAINT exp_id IF NOT EXISTS
  FOR (e:Experiment) REQUIRE e.id IS UNIQUE;

CREATE CONSTRAINT run_id IF NOT EXISTS
  FOR (r:Run) REQUIRE r.id IS UNIQUE;

CREATE CONSTRAINT ingredient_name IF NOT EXISTS
  FOR (i:Ingredient) REQUIRE i.name IS UNIQUE;

CREATE FULLTEXT INDEX run_fulltext IF NOT EXISTS
  FOR (r:Run) ON EACH [r.objective, r.synthesis, r.name];

CREATE FULLTEXT INDEX chunk_fulltext IF NOT EXISTS
  FOR (c:Chunk) ON EACH [c.text];

CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
  FOR (c:Chunk) ON (c.embedding)
  OPTIONS { indexConfig: {
    `vector.dimensions`: 1536,
    `vector.similarity_function`: 'cosine'
  }};
```

---

## 7. Architecture de retrieval

### Stratégie de routing — Always Hybrid

**Pas de routing conditionnel.** Toutes les questions passent par `HybridCypherRetriever`.
Si résultat = 0, fallback sur `exact_lookup.py` (Cypher `CONTAINS toLower()`).
Le graph traversal intégré dans le Cypher de retrieval gère les 4 types de questions.

### HybridCypherRetriever — route principale

```python
retrieval_query = """
MATCH (node)<-[:HAS_CHUNK]-(run:Run)
OPTIONAL MATCH (run)<-[:HAS_RUN]-(exp:Experiment)
OPTIONAL MATCH (run)-[:USES_INGREDIENT]->(ing:Ingredient)
OPTIONAL MATCH (run)-[:BELONGS_TO]->(chantier:Chantier)
RETURN
    node.text AS text,
    run.id AS run_id,
    run.objective AS objective,
    run.synthesis AS synthesis,
    run.date AS date,
    exp.id AS experiment_id,
    collect(DISTINCT ing.name) AS ingredients,
    chantier.name AS chantier,
    score
ORDER BY score DESC
"""
```

### Qdrant — collection hybride avec RRF

```python
# Dense : OpenAI text-embedding-3-large, 1536 dims
# Sparse : BM25 natif Qdrant (IDF modifier) — pas de fastembed requis
# Fusion : RRF (Reciprocal Rank Fusion)

client.create_collection(
    collection_name="rd_foodtech",
    vectors_config={
        "text-dense": VectorParams(size=1536, distance=Distance.COSINE)
    },
    sparse_vectors_config={
        "bm25": SparseVectorParams(modifier=Modifier.IDF)  # natif Qdrant, sans modèle externe
    },
)

# Requête hybride
client.query_points(
    collection_name="rd_foodtech",
    prefetch=[
        models.Prefetch(query=sparse_vec, using="bm25", limit=20),
        models.Prefetch(query=dense_vec, using="text-dense", limit=20),
    ],
    query=models.FusionQuery(fusion=models.Fusion.RRF),  # fusion RRF
    limit=10,
)
```

### Payload Qdrant (par chunk)

```python
payload = {
    "experiment_id": str,       # "ACE-5" | "REPERTOIRE-RD-2025-2026"
    "run_id": str,              # "ACE-5:Run:1"
    "chantier": str,            # pour filtre Q4 type
    "date": str,                # format ISO "2025-07-18"
    "lead": str,
    "ingredients": list[str],
    "source_file": str,
    "type": str,                # "run_detail" | "run_summary"
    "pole": str,                # "Extrusion" | "Applications"
}
```

### Stratégie de chunking

| Source | Granularité | Taille estimée | Type payload |
|--------|-------------|----------------|--------------|
| REPERTOIRE runs | 1 chunk / run (objectif + synthèse) | ~200 tokens | `run_summary` |
| ACE-3/ACE-5 runs | 1 chunk / essai (conditions + résultats) | ~800 tokens | `run_detail` |
| Source de contenu | `_documentation.md` uniquement | — | — |

Le `_knowledge.json` alimente le **graph Neo4j** (structure), pas Qdrant (vecteur).

### exact_lookup.py — fallback Q1

```cypher
-- Fallback si HybridCypherRetriever retourne 0 résultat
MATCH (i:Ingredient)
WHERE toLower(i.name) CONTAINS toLower($name)
OPTIONAL MATCH (i)<-[:USES_INGREDIENT]-(run:Run)<-[:HAS_RUN]-(exp:Experiment)
RETURN i.name, count(run) AS nb_essais,
       collect({run: run.id, exp: exp.id, date: run.date,
                objective: run.objective, synthesis: run.synthesis}) AS essais
```

---

## 8. Prompt français (template)

```
Tu es un assistant R&D spécialisé en analogues de viande à base de protéines végétales
(extrusion HME, texturation, formulation).

Contexte extrait de la base de connaissances interne :
{context}

Question : {query_text}

Règles strictes :
1. Réponds UNIQUEMENT depuis le contexte fourni. Aucune information externe.
2. Cite toujours la source (experiment_id, run_id) pour chaque affirmation.
3. Si l'information est absente, réponds EXACTEMENT :
   "Information absente du corpus actuel.
    Sources indexées : REPERTOIRE-RD-2025-2026 (316 runs), ACE-3, ACE-5.
    Les autres fiches d'essai ne sont pas encore intégrées."
4. Pour les synthèses : structure Résumé → Résultats clés → Limites connues.
5. Réponds en français.

Réponse :
```

---

## 9. Code Style

```python
# Convention : snake_case, typage strict, pas de commentaires évidents
# Une fonction = une responsabilité

from neo4j import GraphDatabase
from neo4j_graphrag.retrievers import HybridCypherRetriever

def build_retriever(driver, embedder) -> HybridCypherRetriever:
    return HybridCypherRetriever(
        driver=driver,
        vector_index_name="chunk_embedding",
        fulltext_index_name="chunk_fulltext",
        retrieval_query=RETRIEVAL_CYPHER,
        embedder=embedder,
    )

def search(query: str, top_k: int = 10) -> dict:
    response = rag.search(
        query_text=query,
        retriever_config={"top_k": top_k},
        return_context=True,
        response_fallback=FALLBACK_MESSAGE,
    )
    return {"answer": response.answer, "sources": response.retriever_result}
```

**Règles :**
- Typage Python strict (`str`, `list[str]`, etc.)
- `ruff` pour lint et format
- Pas de commentaires évidents — noms explicites
- Constantes en MAJUSCULES dans `src/config.py`
- Pas de secrets dans le code — tout dans `.env`

---

## 10. Stratégie de tests

**Framework :** `pytest` + `pytest-cov`

**Niveaux :**

| Niveau | Quoi tester | Localisation |
|--------|-------------|--------------|
| Unit | Chunking, payload construction, prompt formatting | `tests/test_ingest.py` |
| Integration | Import Neo4j, indexes, Qdrant upsert | `tests/test_ingest.py` (requires docker) |
| E2E | Les 4 questions réelles → vérifier sources citées | `tests/test_rag.py` |

**Couverture minimale :** 70% sur `src/`

**Test critique :**
```python
def test_absent_ingredient_returns_fallback():
    """Pisane ES absent du corpus → réponse fallback, pas d'hallucination."""
    response = search("Est-ce que Pisane ES a déjà été testé ?")
    assert "absent" in response["answer"].lower() or \
           "non trouvé" in response["answer"].lower()
    assert "hallucin" not in response["answer"].lower()
```

---

## 11. Limites (Boundaries)

**Always do :**
- Citer la source (experiment_id, run_id) dans chaque réponse
- Retourner le fallback si l'info est absente du corpus
- Utiliser `IN TRANSACTIONS OF 500 ROWS` pour tout import CSV
- Utiliser `MERGE` (jamais `CREATE`) pour l'import Neo4j — **idempotence garantie**
- Utiliser `upsert=True` pour Qdrant — **idempotence garantie**
- Créer les indexes Neo4j avec `IF NOT EXISTS` avant d'importer les données
- Valider l'import avec une query de vérification post-ingestion
- Passer `dimensions=1536` à l'API OpenAI embeddings

**Ask first :**
- Ajouter de nouvelles dépendances Python majeures
- Modifier le schéma Neo4j (ajout/suppression de labels ou relations)
- Changer le modèle d'embedding (impact sur les dimensions et re-indexation)
- Exposer l'API sur un port public

**Never do :**
- Hardcoder des clés API dans le code
- Retourner une réponse sans vérification que l'info vient du contexte
- Supprimer des nœuds Neo4j sans backup
- Envoyer des données internes à des services externes non approuvés

---

## 12. Critères de succès

- [ ] `python src/ingest/01_import_neo4j.py` s'exécute sans erreur, 316+ nœuds Run créés
- [ ] `python src/ingest/02_create_indexes.py` crée 6 indexes sans erreur
- [ ] `python src/ingest/03_embed_qdrant.py` indexe tous les chunks avec payload complet
- [ ] Q1 : "Pisane ES testé ?" → réponse "absent" ou liste d'essais, jamais hallucination
- [ ] Q2 : "Effet cystéine extrusion ?" → réponse avec source citée ou "absent"
- [ ] Q3 : "Synthèse fibres extrusion ?" → liste tous runs FIB avec synthèses (≥ FIB-1 à FIB-11)
- [ ] Q4 : "Rapport Kobé 2026 ?" → rapport structuré depuis les runs KOBE-*
- [ ] `pytest tests/ -v` → tous les tests passent
- [ ] Temps de réponse < 10s pour une question standard

---

## 13. Décisions actées

| Décision | Choix | Raison |
|----------|-------|--------|
| Interface | **API REST (FastAPI)** dès le MVP | Intégration future facilitée |
| LLM | **claude-sonnet-4-6** | Suffisant, meilleur rapport qualité/coût |
| Re-ranking | **Absent du MVP** | Corpus trop petit (~100 chunks) — le HybridCypherRetriever suffit. À ajouter (Cohere Rerank 3.5) quand le corpus dépasse 500 chunks |
| Corpus MVP | REPERTOIRE + ACE-3 + ACE-5 | Les 11 fichiers bruts restent en stand-by |

---

## 14. Corpus indexé (scope MVP)

| Source | Runs | Niveau de détail |
|--------|------|-----------------|
| REPERTOIRE-RD-2025-2026 | 316 | Résumé (objectif, synthèse, statut) |
| ACE-3 | 1 expérience | Complet (formulations, paramètres, mesures) |
| ACE-5 | 1 expérience | Complet (formulations, paramètres, mesures) |

**Important :** Toute réponse doit mentionner ce scope si l'information est absente.
