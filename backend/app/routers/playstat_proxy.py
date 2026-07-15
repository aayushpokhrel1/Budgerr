import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings

router = APIRouter(prefix="/playstat", tags=["playstat"])


def _headers() -> dict[str, str]:
    # Same rule as playstat_client._headers(): playstat rejects requests
    # without X-API-Key when its AUTH_ENABLED is on.
    if settings.playstat_api_key:
        return {"X-API-Key": settings.playstat_api_key}
    return {}


@router.get("/{path:path}")
async def proxy(path: str, request: Request) -> Response:
    url = f"{settings.playstat_base_url}/{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params=dict(request.query_params),
                headers=_headers(),
            )
    except httpx.RequestError:
        return JSONResponse(status_code=502, content={"detail": "playstat upstream unavailable"})

    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        except ValueError:
            pass
    return Response(status_code=resp.status_code, content=resp.content, media_type=content_type or None)
