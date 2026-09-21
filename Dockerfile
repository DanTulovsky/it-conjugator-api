FROM python:3.12-slim

WORKDIR /app

# for healthcheck curl (tiny and handy)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
# Databases are built locally (`task db`) and are not stored in git.
COPY data/verbs.db data/dictionary.db ./data/
# OpenAPI contract (regenerate with `task swagger`); startup checks it is in sync.
COPY swagger.json .

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
