"""FastAPI REST interface — POST /query, GET /health, GET /corpus."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException
from neo4j import GraphDatabase
from openai import OpenAI
from pydantic import BaseModel

from src.cir import (
    GROUPEMENTS_VALIDES,
    generate_fiche_cir,
)
from src.config import (
    ANTHROPIC_API_KEY,
    DEEPSEEK_API_KEY,
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
    deepseek_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    _state["pipeline"] = RAGPipeline(driver, openai_client, deepseek_client)
    _state["driver"] = driver
    _state["anthropic"] = anthropic_client
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


class CirRequest(BaseModel):
    groupement: str


class CirSourceOut(BaseModel):
    run_id: str
    experiment_id: str | None
    sharepoint_url: str | None


class DataQualityOut(BaseModel):
    runs_total: int
    runs_with_synthesis: int
    runs_with_detailed_data: int
    completeness_pct: int
    warning: str | None


class CirResponseOut(BaseModel):
    groupement: str
    fiche: str
    data_quality: DataQualityOut
    sources: list[CirSourceOut]
    input_tokens: int
    output_tokens: int


@app.post("/cir", response_model=CirResponseOut)
def cir(request: CirRequest) -> CirResponseOut:
    if request.groupement not in GROUPEMENTS_VALIDES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Groupement inconnu : {request.groupement!r}",
                "groupements_valides": GROUPEMENTS_VALIDES,
            },
        )
    try:
        result = generate_fiche_cir(
            driver=_state["driver"],
            anthropic_client=_state["anthropic"],
            groupement=request.groupement,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Neo4j ou Claude inaccessible : {exc}",
        ) from exc

    return CirResponseOut(
        groupement=result.groupement,
        fiche=result.fiche,
        data_quality=DataQualityOut(
            runs_total=result.data_quality.runs_total,
            runs_with_synthesis=result.data_quality.runs_with_synthesis,
            runs_with_detailed_data=result.data_quality.runs_with_detailed_data,
            completeness_pct=result.data_quality.completeness_pct,
            warning=result.data_quality.warning,
        ),
        sources=[
            CirSourceOut(
                run_id=s.run_id,
                experiment_id=s.experiment_id,
                sharepoint_url=s.sharepoint_url,
            )
            for s in result.sources
        ],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


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
