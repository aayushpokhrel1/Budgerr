from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import require_api_key
from app.config import settings
from app.routers import bet_import, bets, budgeting, plaid, playstat_proxy, rewards

# docs_url/redoc_url/openapi_url=None disables FastAPI's built-in docs routes,
# which bypass the app-level auth dependency. They are re-added below as normal
# routes so `Depends(require_api_key)` covers them.
app = FastAPI(
    title="Budgerr",
    dependencies=[Depends(require_api_key)],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    # allow_headers=["*"] already mirrors back any requested preflight header
    # (incl. the browser's X-API-Key) since allow_credentials is not set to
    # True here; no explicit "X-API-Key" entry needed.
    allow_headers=["*"],
)
app.include_router(plaid.router)
app.include_router(bets.router)
app.include_router(budgeting.router)
app.include_router(rewards.router)
app.include_router(bet_import.router)
app.include_router(playstat_proxy.router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent.parent / "static"), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/openapi.json", include_in_schema=False)
def openapi_json() -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
def swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Budgerr docs")


@app.get("/redoc", include_in_schema=False)
def redoc() -> HTMLResponse:
    return get_redoc_html(openapi_url="/openapi.json", title="Budgerr docs")
