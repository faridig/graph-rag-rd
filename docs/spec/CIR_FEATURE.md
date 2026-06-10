# Spec : Génération de fiches CIR depuis la base R&D

**Version :** 1.0
**Date :** 2026-06-10
**Statut :** Implémenté — en attente de test end-to-end

---

## 1. Objectif

Générer automatiquement des fiches techniques CIR (Crédit Impôt Recherche) conformes aux
exigences MESRI à partir des données expérimentales internes stockées dans Neo4j.

**Utilisateurs cibles :** Ingénieurs R&D ACCRO (non-techniciens, sans accès terminal)
**Point d'entrée :** Interface Chainlit — bouton "Générer une fiche CIR" ou message contenant "CIR"
**Livrable :** Fiche rédigée en streaming dans l'interface + téléchargement `.docx`

**Contrainte principale :** Conformité MESRI/Frascati — l'auditeur doit pouvoir valider
les 5 critères (nouveauté, créativité, incertitude, systématisme, transférabilité) sur
la base de la fiche seule.

---

## 2. Groupements couverts

| Groupement | Requête Neo4j | Prompt |
|---|---|---|
| Muscles à base de protéines végétales | `cir_grouping = "Muscles..."` | `SYSTEM_PROMPT_CIR_MUSCLES` |
| Produits élaborés à base de muscles végétaux | `cir_grouping = "Produits..."` | `SYSTEM_PROMPT_CIR_PRODUITS` |
| Nouvelles voies de texturation | `chantier = "Installation ligne Emincés..."` | `SYSTEM_PROMPT_CIR_NOUVELLES_VOIES` |

---

## 3. Architecture des modules

### `src/cir.py` — Logique de génération

```
_fetch_rows(driver, groupement)          → list[_RunRow]
_fetch_sharepoint_urls(driver, exp_ids)  → dict[str, str]
_compute_quality(rows)                   → DataQuality
_row_richness(row)                       → int   (sort key: summary > synthesis > objective)
_format_run(row, urls)                   → str
_format_context(rows, urls)              → str   (tronqué à 120 000 chars)
_build_header(groupement, rows)          → str   (CIR_FORMAT.format(...))
_pick_prompt(groupement)                 → str
stream_fiche_cir(driver, client, grp)    → Iterator[str | CirResponse]
export_docx(response, output_path)       → None
_add_hyperlink(para, url, text)          → None  (liens cliquables python-docx)
build_cir_clients()                      → tuple[Driver, anthropic.Anthropic]
```

**Flux de génération :**
1. `_fetch_rows` → requête Neo4j (runs RÉPERTOIRE + liaison [:DETAILS] → Experiment + [:HAS_SUMMARY] → Chunk)
2. `_compute_quality` → DataQuality (warning si < 50 % de runs avec synthèse)
3. `_format_context` → tri par richesse, troncature à 120k chars
4. `stream_fiche_cir` → streaming Anthropic `messages.stream()`, yield str tokens + CirResponse final
5. `export_docx` → python-docx, 4 niveaux heading (H1 titre, H2 sections, H3 sous-sections, H4 rien)

**Garde context overflow :** `_MAX_CONTEXT_CHARS = 120_000` (~30k tokens). Les runs les moins
riches en données sont supprimés en premier ; une note est ajoutée au contexte si troncature.

### `src/generation/prompt_cir.py` — Prompts MESRI

**`_REGLES_MESRI`** (commun aux 3 prompts) :

| Règle | Critère Frascati couvert |
|---|---|
| Valeur numérique obligatoire (⚠ si absent) | Systématisme |
| Citation `[source: run_id]` systématique | Traçabilité |
| État de l'art obligatoire (Section 1a) | Nouveauté — prouve que la solution n'existait pas |
| Incertitude formulée AVANT les travaux | Incertitude |
| Distinction R&D / ingénierie standard | Incertitude (complexité ≠ R&D) |
| Hypothèses formalisées avant chaque axe | Systématisme |
| Essais non concluants obligatoires (Section 3b) | Incertitude — résultat non connu d'avance |
| Règles transférables (Section 4b) | Transférabilité |
| Zéro invention — signaler ⚠ si donnée absente | Exactitude |

**Source autorisée pour l'état de l'art :** connaissance du domaine scientifique du LLM
(entraînement) + contexte scientifique fourni dans le prompt système. Aucune citation
de titre/auteur incertain.

**Structure obligatoire du FORMAT :**
```
1. VERROU SCIENTIFIQUE
   1a. État des connaissances au début des travaux
   1b. Incertitude scientifique / technique identifiée
   1c. Pourquoi ces travaux ne relèvent pas de l'ingénierie standard
2. DÉMARCHE EXPÉRIMENTALE
   (Pour chaque axe : Hypothèse → Protocole → Paramètres testés)
3. RÉSULTATS
   3a. Résultats significatifs (valeurs chiffrées + sources)
   3b. Essais non concluants ou partiels
4. NOUVELLES CONNAISSANCES ACQUISES
   4a. Apports scientifiques par axe
   4b. Règles opératoires établies (transférables)
```

### `src/chainlit_app.py` — Intégration Chainlit

**Détection CIR :**
- `_CIR_RE` : présence de `\bcir\b`
- `_CIR_QUESTION_RE` : filtre les questions informatives (comment, pourquoi, qu'est-ce que…)
- `_is_cir_generation_request(text)` : True si CIR présent ET pas question informative
- → Les questions "qu'est-ce que le CIR ?" partent dans le RAG, pas dans le générateur

**Flux UX :**
1. Message "CIR" ou clic starter → `_show_cir_groupement_picker()` (3 boutons `cl.Action`)
2. Clic groupement → `on_cir_groupement` → `_run_cir_generation(groupement)`
3. `cl.Step` "Chargement des données" pendant la requête Neo4j
4. Stream LLM → `msg.stream_token(token)` sur le message principal
5. `export_docx` → temp file → `cl.File` download
6. `@cl.on_chat_end` → suppression des temp files

**Threading pattern (identique au RAG) :**
```python
# Thread producteur → asyncio.Queue → coroutine consommateur
def _produce():
    for item in stream_fiche_cir(...):
        run_coroutine_threadsafe(queue.put(item), loop).result()
    run_coroutine_threadsafe(queue.put(None), loop).result()

thread.start()
while True:
    item = await queue.get()
    if item is None: break
    if isinstance(item, str): await msg.stream_token(item)
    else: final_response = item
await asyncio.to_thread(thread.join)
```

---

## 4. Schéma Neo4j utilisé

```cypher
MATCH (rep:Run)
WHERE rep.cir_grouping = $cir_grouping
  AND rep.id STARTS WITH "REPERTOIRE"
OPTIONAL MATCH (rep)-[:DETAILS]->(exp:Experiment)
OPTIONAL MATCH (exp)-[:HAS_SUMMARY]->(summary:Chunk)
RETURN rep.id, rep.chantier, rep.objective, rep.synthesis,
       rep.lead, rep.date, rep.status,
       exp.id, exp.title, summary.text
```

**Propriétés Run utilisées :** `cir_grouping`, `chantier`, `objective`, `synthesis`,
`lead`, `date`, `status`
**Note :** "Nouvelles voies" utilise `chantier = "Installation ligne Emincés..."` car
les runs DST sont classés `cir_grouping = "Muscles"` dans le Répertoire.

---

## 5. Config LLM

| Usage | Modèle | Fichier |
|---|---|---|
| RAG (questions R&D) | `deepseek-chat` | `src/config.py` `LLM_MODEL` |
| CIR (génération fiches) | `claude-sonnet-4-6` | `src/config.py` `CIR_LLM_MODEL` |

`max_tokens = 16 000` pour la génération CIR (fiches longues).

---

## 6. Améliorations Chainlit (hors CIR)

| Problème | Cause | Fix |
|---|---|---|
| Liens SharePoint non cliquables | `&` dans URLs corrompt le `href` | `html.escape(url, quote=True)` |
| Interface bloquée (stop toujours actif) | `thread.join()` bloquait l'event loop | `await asyncio.to_thread(thread.join)` |
| Pas de contexte au démarrage | — | Welcome message + `chainlit.md` |

---

## 7. MCP Littérature scientifique

**Fichier :** `.mcp.json` (racine projet)

| Serveur | Commande | Base(s) | Usage CIR |
|---|---|---|---|
| `semantic-scholar` | `npx -y @xbghc/semanticscholar-mcp` | 225M papiers toutes disciplines | État de l'art HME, DST, protéines végétales |
| `academic` | `python academic_server.py` | PubMed, arXiv, bioRxiv, medRxiv, Semantic Scholar | Food science, nutrition, agroalimentaire |

**Installation Academic-MCP :**
```bash
cd ~/.claude/tools/Academic-MCP-Server
python3 -m venv venv
venv/bin/pip install requests bs4 mcp feedparser beautifulsoup4 PyPDF2
# scihub intentionnellement omis (légalement gris, non nécessaire)
```

**Usage prévu :** Avant de générer une fiche CIR, interroger les deux bases pour
trouver 3-5 références réelles sur les procédés/ingrédients du groupement et les
injecter dans la section état de l'art.

---

## 8. Commandes

```bash
# Lancer l'interface Chainlit
PYTHONPATH="." chainlit run src/chainlit_app.py --port 8001

# Tests
pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format src/ tests/
```

---

## 9. Critères de succès

| Critère | Condition vérifiable |
|---|---|
| Génération CIR fonctionne | Fiche produite pour les 3 groupements sans erreur |
| Conformité MESRI section 1 | La fiche contient état de l'art + incertitude + distinction R&D/ingénierie |
| Conformité MESRI section 3 | Sous-paragraphe "Essais non concluants" présent |
| Conformité MESRI section 4 | Paragraphe "Règles opératoires établies" présent |
| Valeurs numériques | Au moins 1 valeur chiffrée avec source par résultat significatif |
| Export docx | Fichier téléchargeable, hiérarchie H1/H2/H3 correcte, URLs cliquables |
| Détection CIR | "CIR" → picker ; "qu'est-ce que le CIR" → RAG |
| Pas de fuite mémoire | Temp files supprimés à la fin de session |

---

## 10. Limites connues et axes d'amélioration

| Limite | Impact | Solution envisagée |
|---|---|---|
| État de l'art basé sur entraînement LLM | Citations non vérifiées si nommées | Utiliser MCP Semantic Scholar pour injecter vraies références |
| Runs sans synthèse | Sections 3-4 partielles | Contacter Yassine (DST-7, STRIP-15) |
| `panel_ressemblant_score` KOBE absent | Fiche Produits incomplète sur critères sensoriels | Re-extraire depuis Excel KOBE |
| Pas de régénération partielle section par section | Tout ou rien | Non prioritaire — régénérer la fiche entière suffit |

---

## 11. Boundaries

**Always do :**
- `MERGE` (jamais `CREATE`) pour les imports Neo4j
- `dimensions=1536` à chaque appel OpenAI embeddings
- `CIR_LLM_MODEL` (Anthropic) pour la génération CIR — jamais `LLM_MODEL` (DeepSeek)
- Règle MESRI 9 : jamais inventer une donnée, toujours signaler `⚠`

**Ask first :**
- Ajouter un nouveau groupement CIR
- Changer le modèle de génération CIR
- Modifier la structure en 4 sections du FORMAT

**Never do :**
- Hardcoder des clés API
- Lancer `--ragas` sans accord explicite (~$7/run)
- Citer un titre/auteur/DOI incertain dans l'état de l'art

---

## 12. Open questions

- [ ] Les MCP Semantic Scholar + Academic sont configurés mais pas encore testés en session
      → À valider : redémarrer Claude Code et tester une recherche sur "HME plant protein"
- [ ] Intégration des références MCP dans le prompt CIR
      → Architecture à définir : pré-requête au MCP avant génération, injection dans user_content
- [ ] Qualité de la fiche sur "Nouvelles voies" (peu de runs DST disponibles)
      → Vérifier avec `_compute_quality` : si < 3 runs → message d'avertissement
