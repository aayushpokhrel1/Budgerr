# Multi-arch (linux/arm64 for Pi 5, linux/amd64 for the laptop).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build context is backend/, so this copies app/, static/, pyproject.toml, etc.
COPY . /app

# Editable install: a plain wheel install puts `app` in site-packages WITHOUT
# static/ alongside it, breaking app.main's __file__-relative StaticFiles mount.
# psycopg[binary] ships wheels, so no libpq build toolchain is needed.
RUN pip install -e .

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

# /health is auth-exempt (Task 1); no key needed. curl is absent from -slim,
# so probe with stdlib urllib.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
