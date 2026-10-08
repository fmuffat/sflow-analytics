# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
# Pango and fonts for the PDF reports (WeasyPrint).
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0       libharfbuzz-subset0 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt

# Test image: docker build --target test ...
FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY . .
CMD ["pytest", "-q"]

FROM base AS runtime
COPY app ./app
# Writable cache for Fontconfig (PDF reports) as the unprivileged user.
ENV XDG_CACHE_HOME=/tmp/.cache
RUN useradd --system --uid 10001 api && mkdir -p /config && chown api /config
# Configuration store (users, sessions, settings, secrets): mount the app-config volume here.
VOLUME ["/config"]
USER api
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]
