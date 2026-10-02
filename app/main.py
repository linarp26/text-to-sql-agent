from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()  # make provider API keys from .env visible to the SDKs

from .agent import SQLAgent  # noqa: E402
from .llm import get_provider  # noqa: E402
from .models import AgentResponse, QueryRequest  # noqa: E402
from .schema import get_schema  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"

EXAMPLES = [
    "What were total sales by month in 2025?",
    "Which 5 products generated the most revenue overall?",
    "What is the average order value by sales channel?",
    "Which top-level category has the highest average review rating?",
    "How many customers placed more than 5 orders?",
    "What is the average delivery time in days for each carrier?",
    "Show month-over-month revenue growth for 2025.",
    "What percentage of orders were returned in each country?",
]

app = FastAPI(title="Text-to-SQL Analytics Agent", version="1.0.0")


@lru_cache
def get_agent() -> SQLAgent:
    return SQLAgent(get_provider(), get_schema())


@app.get("/health")
def health() -> dict:
    try:
        get_schema()
        return {"status": "ok", "database": "ok"}
    except Exception as e:
        return {"status": "degraded", "database": str(e)}


@app.get("/api/schema")
def api_schema() -> dict:
    return get_schema().to_dict()


@app.get("/api/examples")
def api_examples() -> dict:
    return {"examples": EXAMPLES}


@app.post("/api/query", response_model=AgentResponse)
async def api_query(req: QueryRequest) -> AgentResponse:
    try:
        agent = get_agent()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Agent unavailable: {e}") from e
    return await run_in_threadpool(agent.ask, req.question.strip())


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
