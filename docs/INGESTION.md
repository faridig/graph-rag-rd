# Pipeline d'ingestion — Guide technique

Ce document explique comment les données R&D d'ACCRO passent des fichiers Excel/DOCX bruts à un graphe interrogeable, et clarifie pourquoi ce système n'est **pas** un Graph RAG au sens classique du terme.

---

## 1. Le point de départ : des données structurées, pas du texte libre

La majorité des systèmes RAG (Retrieval-Augmented Generation) partent d'un corpus de **documents texte** (PDFs, articles, wikis) et les découpent en chunks à embedder.

Ici, la situation est différente. Les données R&D d'ACCRO sont des **fichiers de laboratoire structurés** :

- Des fichiers Excel (`ACE-5.xlsx`) avec des tableaux : facteurs × runs × réponses mesurées (TPA, coupure, anisotropie…)
- Des fichiers Word (`STRIP-10.docx`) avec des comptes-rendus d'essais
- Un Répertoire RD central (`REPERTOIRE-RD-2025-2026.xlsx`) qui recense l'ensemble des runs avec leurs métadonnées

Ce n'est pas du texte libre : chaque ligne d'Excel est un run d'expérience avec des valeurs numériques mesurées, des ingrédients pesés, des conditions procédé. L'information est **dense et typée**.

---

## 2. Vue d'ensemble du pipeline

```
Fichiers bruts (.xlsx / .docx / .csv)
              │
              ▼  ÉTAPE 1
      batch_extract.py
   (LLM Claude + extended thinking)
              │
              ▼
   _knowledge.json  ──────────────────────────────────────┐
              │                                            │
              ▼  ÉTAPE 2                                   │
         build_kg.py                                       │
              │                                            │
       ┌──────┼──────────┐                                 │
       ▼      ▼          ▼                                 │
  _triples  _documentation.md  _validation.md             │
    .csv         │                                         │
                 │                                         │
                 ▼  ÉTAPE 3              ÉTAPE 3           │
          embed_chunks.py        ◄── import_neo4j.py ◄────┘
       (chunks + embeddings)        (nœuds + relations)
                 │                         │
                 └─────────┬───────────────┘
                           ▼
                       Neo4j DB
              ┌────────────────────────┐
              │  Experiment  Run       │
              │  Ingredient  Chantier  │
              │  Chunk (+ embedding)   │
              └────────────────────────┘
```

---

## 3. Étape 1 — Extraction LLM : `batch_extract.py`

### Pourquoi un LLM ?

Un fichier Excel de laboratoire est rarement propre : colonnes fusionnées, formules Excel (`#DIV/0!`), abréviations métier (`EV32`, `P02`, `SME`), données manquantes indiquées par "idem essai 3", notes dispersées dans des cellules de couleur…

Un parseur déterministe ne peut pas gérer cette variabilité. Le LLM (Claude Sonnet 4.6 avec **extended thinking**) lit le dump tabulaire du fichier et produit un **JSON canonique** (`_knowledge.json`).

### Ce que contient le JSON

```json
{
  "experiment": {
    "id": "ACE-5",
    "title": "Effet NaCl sur la texture P02",
    "status": "complete",
    "scale": "pilot"
  },
  "runs": [
    {
      "id": "1",
      "name": "Témoin sans sel",
      "is_control": true,
      "inputs": {
        "formulation": [
          {"component": "Nutralys F85M", "pct_matrix": {"value": 70, "unit": "%"}}
        ]
      },
      "conditions": {
        "screw_speed": {"value": 625, "unit": "rpm"},
        "barrel_temp_z4": {"setpoint": 160, "actual": 158, "unit": "°C"}
      },
      "responses": {
        "cut_T": {"mean": 19470, "sd": 1069, "unit": "g", "replicates": [18900, 19800, 19710]}
      }
    }
  ],
  "references": ["ACE-3", "ACE-4"],
  "observations": {"conclusion": "...", "next_step": "..."}
}
```

**Points critiques extraits :**
- Chaque run est **auto-suffisant** (jamais de "idem run 1" — les valeurs sont copiées)
- Les runs échoués (`bouchage filière`) sont conservés dans `failed_runs`
- Les références croisées (`"suite de ACE-3"`) sont capturées dans `references`
- Setpoints ET valeurs réelles sont conservés séparément

### Résilience de l'extraction

Si la réponse LLM est tronquée (128K tokens output cap), le script détecte l'échec et effectue un appel de **continuation** qui demande uniquement les runs manquants, puis fusionne les deux résultats.

Si des formulations incomplètes sont détectées (run avec `pct_matrix: null`), un **second appel de réparation** ciblé remplit les valeurs manquantes depuis le fichier source.

---

## 4. Étape 2 — Génération d'artefacts : `build_kg.py`

Le script `build_kg.py` transforme le JSON canonique en trois fichiers :

| Fichier | Contenu | Usage |
|---------|---------|-------|
| `_triples.csv` | `sujet, prédicat, objet, unité` | Traçabilité, analyse externe |
| `_documentation.md` | Synthèse lisible : objectif, formulations, résultats, observations | **Source des chunks** pour les embeddings |
| `_validation.md` | Contrôles de cohérence : réplicats vs moyenne, unités manquantes | Validation humaine |

La `_documentation.md` est le fichier clé : c'est elle qui sera découpée en chunks et embedée. Elle est générée **deterministiquement** depuis le JSON — pas par un LLM.

---

## 5. Étape 3 — Import Neo4j : `import_neo4j.py`

Le JSON canonique est importé dans Neo4j avec des `MERGE` (idempotents). Le schéma de graphe est **métier**, pas générique :

```
(Experiment)-[:HAS_RUN]──────────────────►(Run)
(Experiment)-[:REFERENCES]───────────────►(Experiment)
(Experiment)-[:HAS_SUMMARY]─────────────►(Chunk)
(Run)-[:HAS_CHUNK]───────────────────────►(Chunk)
(Run)-[:USES_INGREDIENT]─────────────────►(Ingredient)
(Run)-[:BELONGS_TO]──────────────────────►(Chantier)
(Run)-[:DETAILS]─────────────────────────►(Experiment)  ← depuis le Répertoire
```

**La relation `[:DETAILS]`** est construite en deux passes :
1. Match automatique sur les IDs (préfixe REPERTOIRE → Experiment.id)
2. Overrides manuels pour les cas où le match automatique échoue (KOBE, PIPE25, KEFTA…)

---

## 6. Étape 4 — Embeddings : `embed_chunks.py`

La `_documentation.md` est découpée en **chunks sémantiques** par type :

| Type de chunk | Contenu |
|---------------|---------|
| `run_detail` | Un run complet : formulation, conditions, mesures |
| `run_summary` | Résumé d'une ligne du Répertoire (objectif + synthèse) |
| `experiment_intro` | En-tête de l'expérience : objectif global, cibles, références |
| `experiment_summary` | Section d'observations et conclusions |
| `experiment_section` | Section thématique (valeurs dérivées, glossaire…) |

Chaque chunk est embedé via **OpenAI `text-embedding-3-large` (1536 dimensions)** et stocké comme propriété du nœud `Chunk` dans Neo4j. L'embedding est calculé uniquement si le texte a changé (hash SHA-256).

---

## 7. Ce que ce n'est PAS : Graph RAG

Le terme "Graph RAG" est trompeur dans ce contexte. Voici la distinction :

### Graph RAG classique (ex: Microsoft GraphRAG)

```
Texte non structuré (PDFs, articles)
        │
        ▼  LLM
  Extraction d'entités et relations  ←── inférence NLP
        │
        ▼
  Graphe générique (entités ↔ entités)
        │
        ▼
  Traversée du graphe = mécanisme de retrieval
  (communautés Louvain, hops d'entités, résumés hiérarchiques)
```

Le graph **est** le mécanisme de retrieval : on répond aux questions en parcourant le graphe.

### Ce système — Hybrid RAG avec augmentation par graphe

```
Données structurées (Excel/DOCX de labo)
        │
        ▼  LLM (extraction, pas inférence)
  JSON canonique → graphe métier typé
  (Experiment, Run, Ingredient — objets R&D, pas entités NLP)
        │
        ▼
  Retrieval principal : dense (cosine) + sparse (Lucene fulltext)
  combinés par RRF (Reciprocal Rank Fusion)
        │
        ▼
  Augmentation par traversée ciblée du graphe (6 phases)
  ← le graphe ENRICHIT le contexte, il n'est pas le retrieval
        │
        ▼
  Claude génère la réponse avec contexte augmenté
```

**Les différences fondamentales :**

| | Graph RAG classique | Ce système |
|--|---------------------|------------|
| Source | Texte non structuré | Données structurées (Excel/DOCX) |
| Construction du graphe | Inférence NLP automatique | Extraction guidée + schéma métier fixe |
| Types de nœuds | Entités génériques | `Experiment`, `Run`, `Ingredient` (domaine R&D) |
| Mécanisme de retrieval | Traversée du graphe | Dense + sparse (vector + fulltext) |
| Rôle du graphe au query time | Principal | Augmentation (6 traversées ciblées) |
| Hallucination | Risque via inférence d'entités | Zéro : chaque réponse cite un run_id réel |

Le meilleur terme pour ce système : **Hybrid RAG with graph-augmented context**.

---

## 8. Traversées de graphe au query time

Quand une question arrive, le retrieval principal (dense + sparse) est complété par six phases d'augmentation :

| Phase | Déclencheur | Traversée | Effet |
|-------|-------------|-----------|-------|
| 1 | Token ≥7 chars overlap ingrédients connus | `[:USES_INGREDIENT]` | Ajoute jusqu'à 2 chunks sur cet ingrédient |
| 1b | "quelles exp ont utilisé X ?" | agrégat exp × ingrédient × nb_runs | Vue synthétique |
| 1.5 | Préfixe de session (COULEUR-S1…) | `CONTAINS(':Run:' + pfx)` | Retrouve les runs d'une session |
| 2 | Chunk du Répertoire détecté | `[:DETAILS]` → HAS_SUMMARY | Ajoute le détail de l'expérience cible |
| 3 | "qui référence X ?" | `[:REFERENCES]` inverse | Remonte les expériences liées |
| 3.5 | Termes mesure (anisotropie, SME, TPA) | section valeurs dérivées | Injecte les valeurs calculées |

Ces traversées ne remplacent pas le retrieval — elles l'enrichissent.

---

## 9. Gate anti-hallucination

Avant tout appel LLM, la moyenne des scores cosine des 3 meilleurs chunks est calculée. Si elle est inférieure au seuil (`SCORE_THRESHOLD = 0.6689`, calibré empiriquement), le système renvoie directement le message de fallback — **sans appeler Claude**.

```
Question → embedding → cosine search → mean(top-3 scores)
                                               │
                          ┌────────────────────┤
                          │                    │
                  < 0.6689 │          ≥ 0.6689 │
                          ▼                    ▼
               exact_lookup.py          contexte → Claude
            (Cypher CONTAINS + Lucene)
                          │
                   si 0 résultats
                          ▼
                  FALLBACK_MESSAGE
              "absent du corpus R&D"
```

---

## 10. Commandes

```bash
# 1. Extraire les fichiers Excel/DOCX bruts → JSON (coût ~$0.10-0.30 par fichier)
python scripts/batch_extract.py --dry-run         # voir ce qui sera traité
python scripts/batch_extract.py --file ACE-5.xlsx # extraire un fichier
python scripts/batch_extract.py                   # traiter tous les fichiers restants

# 2. Importer le JSON dans Neo4j (idempotent)
python -m src.ingest.import_neo4j

# 3. Créer/mettre à jour les index (une seule fois, ou après un changement de schéma)
python -m src.ingest.create_indexes

# 4. Embedder les chunks (calcul uniquement si le texte a changé)
python -m src.ingest.embed_chunks

# Re-embedder une seule expérience après mise à jour
python -m src.ingest.embed_chunks --experiment ACE-5
```

> **Important :** L'ordre 2 → 3 → 4 est obligatoire. Les chunks ne sont pas cherchables sans embedding. L'étape 3 (`create_indexes`) n'est nécessaire qu'au premier déploiement ou si le schéma change.
