# Dockerfile — the Python side: API, pipeline scripts, scheduler.
#
# One image serves all three. They share the same code and the same database,
# and the difference is only which command compose runs, so building three
# would mean maintaining three copies of the same dependency set.

FROM python:3.11-slim

# psycopg2 needs libpq; matplotlib and reportlab want the usual font/freetype
# stack for the link graph image in the PDF case report.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libfreetype6 \
        libpng16-16 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # matplotlib has no display in a container and must not try to find one.
    MPLBACKEND=Agg

# Dependencies first so a code change does not reinstall the world.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Non-root. Nothing here needs to write outside /app, and the scheduler's state
# file lives in the working directory.
RUN useradd --create-home --uid 10001 sentinel && chown -R sentinel:sentinel /app
USER sentinel

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
