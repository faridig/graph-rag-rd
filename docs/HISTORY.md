# ACCRO Graph RAG — Historique des décisions et fixes

> Archivé depuis CLAUDE.md le 2026-06-09. Référence pour comprendre l'évolution du projet.

---

## Étapes V2 — complétées (2026-06-09)

- A — Eval custom V2 ✅
- B — Corriger ground_truths graph ✅ (5 questions : 4 graph_details + ACE-4 vs ACE-5)
- C — Diagnostic régression AI/DST ✅ (measure-term augmentation)
- D — Mesurer context_recall post-chunks ✅ — 0.674
- D' — Phase 1.5 session retrieval + GT fixes ✅ — 0.692 final
- E — Eval final ✅ — 0.692 livré

---

## Fixes appliqués (2026-06-08)

**Fix ACE-5 / RÉPERTOIRE coverage bug (`rag_pipeline.py`) :**
Les chunks RÉPERTOIRE ont leur exp_id cible comme dernier composant de run_id (ex. `RÉPERTOIRE:Run:ACE-5`) → falsement comptés comme "ACE-5 couvert". Fix : `non_rep_chunks` exclus du calcul de couverture avant augmentation.

**Fix exp_names digit filter :**
Les IDs avec chiffres (ACE-4, ACE-5) étaient passés dans `exp_names` ET dans `digit_patterns`, causant une double requête avec LIMIT 2 qui retournait ACE-4 seulement. Fix : filtre `not any(c.isdigit() for c in t)` sur exp_names.

**Phase 3.5 — Measure-term augmentation (`rag_pipeline.py`) :**
Déclenché si `anisotropie`, `sme`, `tpa` dans la question ou `\b(ai|ph)\b`. Injecte le chunk `type='experiment_section'` contenant `## 4` pour chaque exp_id si section-4 absente. MAX 1 chunk, prépendé.

**Correction ground_truths (5 questions) :**
Script `scripts/update_ground_truths.py` — mode `--show` (gratuit) + `--update TYPE` (DeepSeek). Cherry-pick manuel dans `data/testset_candidate_v2.json`. Ne jamais écraser `testset.json` directement.

**Note DST/AI :** La section-4 (AI=3.232 pour M03 essai 3) est injectée mais le LLM ne relie pas "Run 3" à "essai 3 – condition optimale 120°C" — terminologie source incompatible, non corrigeable côté code.

---

## Graph RAG V2 — implémentation (2026-06-08)

**Phase 0 — Audit :**
- 0A : 1059/1245 ingrédients ≤ 15 runs, psyllium = 3 runs → injection directe OK. Ingrédients génériques (Nutralys 393, Eau 803) → limité à 2 slots.
- 0B : 10 questions ingredient-centric, 5 répertoire/navigation, 0 évolution — testset pauvre en graph queries.
- 0C : 12 questions graph ajoutées → 96 questions au total.

**Phase 1 — `[:USES_INGREDIENT]` traversal :**
Tokens ≥7 chars extraits (324 tokens). Détection par overlap → une requête Cypher par token → MAX 2 chunks en fin de contexte.

**Phase 2 — `[:DETAILS]` traversal :**
Chunks RÉPERTOIRE → `_fetch_details_context()` → HAS_SUMMARY injecté. Déterministe, MAX 4 chunks.

---

## Roadmap V2 (planification initiale — implémentée)

**Règle non négociable : une traversal à la fois, eval complète avant la suivante.**

### Phase 0 — Audit + testset
```cypher
MATCH (i:Ingredient)<-[:USES_INGREDIENT]-(r:Run)
RETURN i.name, count(r) AS nb_runs
ORDER BY nb_runs DESC LIMIT 20
```

### Phase 1 — [:USES_INGREDIENT]
Condition : ingredients clés ≤ 15 runs ET ≥ 5 questions testset de type ingredient.
Index `_known_ingredients` au démarrage, token overlap ≥5 chars, MAX 2 slots.

### Phase 2 — [:DETAILS] RÉPERTOIRE → Experiment
Déterministe. Si chunk a `experiment_id == "RÉPERTOIRE-RD-2025-2026"` ET edge `[:DETAILS]` → injecter section-5 cible.

### Phase 3 — [:REFERENCES] inverse
"Quelles expériences référencent JUT-REC-11 ?" → `<-[:REFERENCES]-`. Impact limité (44 stubs sans données).

---

## État des évaluations V1 (2026-06-07)

### Métriques custom v8 (post-chunks)
| Métrique | v4 (avant chunks) | v8 (après chunks) | Cible |
|---|---|---|---|
| `absent_fallback_rate` | 100% | 100% | 1.0 ✅ |
| `present_fallback_rate` | 1.6% | 1.6% | 0.0 ✅ acceptable |
| `citation_coverage` | 100% | 98.4% | 1.0 ⚠️ |
| `citation_validity` | 97.7% | 98.3% | 1.0 ✅ |
| Input tokens | 513 662 | 340 633 | — ✅ -33% |

### Métriques Ragas — évolution
| Métrique | Baseline | v7 | Cible |
|---|---|---|---|
| `faithfulness` | 0.71 | 0.882 | >0.85 ✅ |
| `answer_relevancy` | 0.65 | 0.725 | >0.72 ✅ |
| `context_recall` | — | 0.818 (V1) → 0.692 (V2 96q) | >0.70 ✅ |

---

## Fixes appliqués (2026-06-07) — v4

**Fix A — Détecteur de non-réponse post-LLM :**
`_NO_DATA_PATTERNS` (11 patterns) + `_is_no_data_response()`. Actif dans `run()` et `run_stream()`.

**Fix B — Prompt valeurs numériques :** "Toute valeur numérique (SME, AI, TPA, pH...) doit être reprise telle quelle depuis le contexte." → `citation_coverage` 92.1% → 100%.

**Fix C — Métriques eval :** `post_llm_fallback_rate`, `answer_preview` (200 chars), `post_llm_detected`.

---

## Fixes appliqués (2026-06-06)

- Prompt CoT 2-étapes, `TOP_K_DEFAULT` 10→6, SCORE_THRESHOLD 0.6682→0.6689
- Augmentation post-retrieval, topic gate (LME acronyme, méthylcellulose 15 chars)
- Testset : 68→84 questions, 5→21 absentes

---

## Gold testset — `data/testset.json` (96 paires, 2026-06-09)

- 21 absentes / 74 présentes + 1 sans ground_truth
- 12 questions Graph RAG (graph_ingredient, graph_details, graph_references, comparatives)
- Ground_truth raccourcies sur `synthèse` → FactualCorrectness mécaniquement bas, pas un échec RAG

Absentes : LME, DST-7, ACE-8/9, méthylcellulose, fermentation lactique, carraghénane, gomme xanthane, lyophilisation, spray drying + IDs inexistants (FIB-5/7, STRIP-2/20, GLU-3, DST-8, NPT-DEV-3, BACON-2, PP-10, JUT-REC-4/11).

---

## Pipeline d'extraction batch — état final

**Extraction complète : 127 knowledge.json, 3072 chunks, 100% embedé.**

### Triage des fichiers
| Niveau | Fichiers | Traitement |
|--------|----------|------------|
| L1 (69) | Formulations, VEILLE, PP, labo, DOCX | Batch auto |
| L2 (18) | MDD onglet-*, DST, GLU, nuggets | Batch + relire `_validation.md` |
| L3 (22) | ACE-1/2/4/6, FIB-*, STRIP-*, FIPROVEX-2, PP-REC-* | `--force-complex` |

### Artefacts par fichier
```
lien_essai/{nom_fichier}/
├── {id}_knowledge.json     ← source de vérité
├── {id}_triples.csv        ← relations graphe
├── {id}_documentation.md   ← texte indexé RAG
└── {id}_validation.md      ← cohérence données
```

### Robustesse batch_extract.py
- Streaming obligatoire (max_tokens=128_000), thinking activé (budget=10_000)
- Retry 5xx + erreurs réseau httpx
- Prompt caching system prompt (cache_control ephemeral 5m)
- MAX_INVENTORY_CHARS=800_000

### Fixes (2026-06-04/05)
- Détection auto fichiers tronqués → `process_file_continuation` automatique
- `sharepoint_urls.py` : `_normalize_url()` corrige `action=editnew` + `%25uXXXX`
- `embed_chunks` regex `[\w.\-]+` (was `[\w-]+`) → +69 chunks PP-REC-12 (IDs avec points)

### URLs SharePoint — état (2026-06-05)
- 117/123 experiments avec URL valide (95%)
- 7 liens corrigés (null) : STRIP-19, STRIP-BOEUF×2, PP-18, BATONNET-POISSON, MARINADES-CHAUDES×2
- 53 URLs récupérées via WEBURLs Graph API
- 5 hyperliens cassés dans Répertoire SharePoint (correction manuelle, cosmétique)

---

## Téléchargement des essais — état (2026-06-08)

**275 présents, 2 échecs restants.**

- `STRIP-40` — lien relatif cassé dans le Répertoire
- `ACE-7` — extrait séparément via un autre lien
- Site `RD-Production` inaccessible : `Essais transferts TVP - onglet "Essais haché"` (GUID DE473A9D)
- En attente Yassine : STRIP-15

---

## État d'avancement T1–T17 (2026-06-05)

Toutes les tâches ✓ — voir git log pour les détails de chaque implémentation.

| Range | Périmètre |
|-------|-----------|
| T1–T8 | Infrastructure, modèles, ingest, retrieval, génération |
| T9–T10 | API FastAPI + CLI |
| T11–T13 | Tests (64 tests) |
| T14 | batch_extract.py |
| T15 | Pipeline ingestion robuste (hash-skip, HAS_SUMMARY) |
| T16 | RAG quality (RRF, ESR=2, [:REFERENCES], run_status) |
| T17 | Eval (eval_rag.py custom + Ragas optionnel) |
