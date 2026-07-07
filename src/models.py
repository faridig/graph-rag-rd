from pydantic import BaseModel

from src.config import CORPUS_SCOPE


class Source(BaseModel):
    run_id: str
    experiment_id: str
    source_file: str
    score: float
    name: str = ""
    sharepoint_url: str | None = None


class QueryRequest(BaseModel):
    question: str
    top_k: int = 10
    chantier: str | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    found_in_corpus: bool
    corpus_scope: list[str] = CORPUS_SCOPE
    input_tokens: int = 0
    output_tokens: int = 0

    # Signaux de diagnostic (monitoring pertinence RAG — voir src/query_log.py).
    # Optionnels et rétro-compatibles : renseignés par le pipeline, consommés au
    # moment du logging. dense_score = confiance retrieval ; fallback_reason =
    # quelle gate a déclenché le FALLBACK_MESSAGE (None si réponse trouvée).
    dense_score: float | None = None
    fallback_reason: str | None = None
    n_chunks: int | None = None
    query_id: str | None = None
