"""FastAPI REST interface — POST /query, GET /health, GET /corpus."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import anthropic
from fastapi import FastAPI
from neo4j import GraphDatabase
from openai import OpenAI

from src.config import (
    ANTHROPIC_API_KEY,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    OPENAI_API_KEY,
)
from src.generation.rag_pipeline import RAGPipeline, run_query
from src.models import QueryRequest, QueryResponse

_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    _state["pipeline"] = RAGPipeline(driver, openai_client, anthropic_client)
    _state["driver"] = driver
    yield
    driver.close()


app = FastAPI(title="ACCRO Graph RAG", lifespan=lifespan)


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    return run_query(
        _state["pipeline"],
        request.question,
        top_k=request.top_k,
        chantier=request.chantier,
    )


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with _state["driver"].session() as session:
            session.run("RETURN 1").single()
        neo4j_status = "connected"
    except Exception:
        neo4j_status = "unreachable"
    return {"status": "ok", "neo4j": neo4j_status}


@app.get("/corpus")
def corpus() -> dict[str, Any]:
    _CYPHER = """
    MATCH (e:Experiment)-[:HAS_RUN]->(r:Run)
    RETURN e.id AS id, count(r) AS run_count
    ORDER BY e.id
    """
    with _state["driver"].session() as session:
        records = session.run(_CYPHER)
        sources = [{"id": r["id"], "run_count": int(r["run_count"])} for r in records]
    return {"sources": sources}
