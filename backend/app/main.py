from fastapi import FastAPI

from app.routers import bets, budgeting, plaid, rewards

app = FastAPI(title="Budgerr")
app.include_router(plaid.router)
app.include_router(bets.router)
app.include_router(budgeting.router)
app.include_router(rewards.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
