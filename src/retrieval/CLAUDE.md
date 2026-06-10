# Retrieval — ACCRO Graph RAG

## Architecture

Toutes les requêtes passent par `HybridCypherRetriever` (Neo4j dense+sparse).
Gate fallback : si `mean(top-3 cosine scores) < SCORE_THRESHOLD (0.6689)` → `exact_lookup.py`.

## Fichiers

| Fichier | Rôle |
|---------|------|
| `base.py` | Interface `IRetriever` |
| `hybrid_retriever.py` | `HybridCypherRetriever` + 6 phases de graph traversal |
| `exact_lookup.py` | Fallback : CONTAINS ingrédient → Lucene AND |
| `sharepoint_urls.py` | URLs SharePoint : Neo4j → prefix → static fallback |

## Phases de graph traversal (dans `hybrid_retriever.py`)

| Phase | Déclencheur | Logique |
|-------|-------------|---------|
| 1 | token ≥7 chars qui overlap ingrédients connus | `[:USES_INGREDIENT]` → MAX 2 chunks appended |
| 1b | "quelles exp ont utilisé X ?" | aggregate vue exp × ingrédient × nb_runs |
| 1.5 | préfixe session (COULEUR-S1, GOUT-S2) | `CONTAINS(':Run:' + pfx)` LIMIT 30 |
| 2 | chunks RÉPERTOIRE détectés | `[:DETAILS]` → HAS_SUMMARY cible, MAX 4 |
| 3 | "qui référence X ?" | `[:REFERENCES]` inverse 1-hop, 8 chunks |
| 3.5 | termes mesure : anisotropie/sme/tpa ou `\b(ai|ph)\b` | injecte section-4 valeurs dérivées |

## Pièges Cypher critiques

**`r.id` est composite** : format `EXP:Run:PREFIXE-N`
- ✅ `WHERE r.id CONTAINS ':Run:' + prefix`
- ✅ `WHERE r.id ENDS WITH suffix`
- ❌ `WHERE r.id STARTS WITH prefix` — ne marche pas (l'EXP vient avant)

**exact_lookup** : deux passes séquentielles
1. `WHERE ingredient.name CONTAINS token` (token ≥ 4 chars)
2. Lucene fulltext AND : `+token1 +token2` (tokens ≥ 4 chars, stopwords filtrés)

## SharePoint URLs (priorité décroissante)

1. `Experiment.sharepoint_url` depuis Neo4j (propriété directe)
2. Lookup par préfixe de run_id
3. Static fallback dans `sharepoint_urls.py`

Toujours batcher les lookups — ne jamais faire une requête Neo4j par source.

## Invariants à ne pas casser

- `found_in_corpus=False` → `sources=[]`, `answer=FALLBACK_MESSAGE`, zéro appel LLM
- `run_status=planned` → annoter `[PLANIFIÉ — non réalisé]` dans le contexte injecté
- Score gate pré-LLM : ne jamais appeler le LLM si le corpus ne couvre pas la question
