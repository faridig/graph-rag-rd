# Tests — ACCRO Graph RAG

## Structure des fichiers

| Fichier | Couvre |
|---------|--------|
| `test_rag.py` | `rag_pipeline.py` — run_query, fallback gate, extract_cited_ids |
| `test_api.py` | `api.py` — endpoints FastAPI (POST /query, GET /health, GET /corpus) |
| `test_query_cli.py` | `query.py` — CLI argparse, sortie stdout |
| `test_retrieval.py` | `hybrid_retriever.py`, `exact_lookup.py` |
| `test_ingest.py` | `import_neo4j.py`, `create_indexes.py`, `embed_chunks.py` |

## Pattern de mock — toujours injecter, jamais build_pipeline()

```python
from unittest.mock import MagicMock, patch
from src.generation.rag_pipeline import RAGPipeline

driver_mock = MagicMock()
openai_mock = MagicMock()
anthropic_mock = MagicMock()
pipeline = RAGPipeline(driver_mock, openai_mock, anthropic_mock)

# Contrôler le dense_score retourné par Neo4j
driver_mock.session.return_value.__enter__.return_value.run.return_value.single.return_value = {"score": 0.9}

# Contrôler la réponse LLM
anthropic_mock.messages.create.return_value.content = [MagicMock(text="Réponse [source: RUN-001]")]

# Court-circuiter les embeddings
openai_mock.embeddings.create.return_value.data = [MagicMock(embedding=[0.1] * 1536)]
```

## Test critique anti-hallucination (must pass)

```python
def test_absent_ingredient_returns_fallback(pipeline):
    # Simuler score < SCORE_THRESHOLD → exact_lookup vide
    # Vérifier :
    assert response.found_in_corpus is False
    assert response.answer == FALLBACK_MESSAGE  # exact string match
    assert response.sources == []
    anthropic_mock.messages.create.assert_not_called()  # aucun appel LLM (pre-LLM gate)
```

## Invariant `found_in_corpus=False`

Deux chemins distincts peuvent retourner `found_in_corpus=False` :

| Chemin | Appel LLM | Déclencheur |
|--------|-----------|-------------|
| Pre-LLM | Non | dense_score < SCORE_THRESHOLD + exact_lookup vide, absent_topics, absent_experiment |
| Post-LLM | Oui | `_is_no_data_response()` détecte un refus dans la réponse générée |

`test_no_llm_call_on_fallback` couvre le chemin pre-LLM — c'est intentionnel.
Le chemin post-LLM est couvert par `test_no_data_response_*`.

## Règles

- Ne jamais appeler `build_pipeline()` — injecter les mocks directement
- Tester `found_in_corpus=False` → `answer == FALLBACK_MESSAGE` dans chaque module qui touche au RAG
- Minimum 70% de coverage sur `src/`
- `pytest tests/ -v --cov=src --cov-report=term-missing`
