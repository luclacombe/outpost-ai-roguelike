FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Pre-cache the sentence-transformers model used by the personality eval
# metric (~80MB) so the first /eval call doesn't pay the download. Kept in
# its own layer (between deps and app code) so app edits don't bust the
# cache. Keep the model name in sync with EMBEDDING_MODEL_NAME in
# app/eval/personality.py.
ENV SENTENCE_TRANSFORMERS_HOME=/root/.cache/sentence-transformers
RUN uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini ./

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
