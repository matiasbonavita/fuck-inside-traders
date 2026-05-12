from __future__ import annotations

from fastapi import FastAPI

from fuck_inside_traders.storage.database import init_db

app = FastAPI(
    title="Fuck Inside Traders",
    description="Dry-run cross-market anomaly radar.",
    version="0.1.0",
)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
