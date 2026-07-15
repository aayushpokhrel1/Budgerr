from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import bet_import, bets, budgeting, plaid, rewards

app = FastAPI(title="Budgerr")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(plaid.router)
app.include_router(bets.router)
app.include_router(budgeting.router)
app.include_router(rewards.router)
app.include_router(bet_import.router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent.parent / "static"), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
