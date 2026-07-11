from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import bets, budgeting, plaid, rewards

app = FastAPI(title="Budgerr")
app.include_router(plaid.router)
app.include_router(bets.router)
app.include_router(budgeting.router)
app.include_router(rewards.router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent.parent / "static"), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
