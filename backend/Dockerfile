# ─────────────────────────────────────────────────────────────────────────────
# KlypSearch backend — FastAPI on Python 3.11
# Built for Render's Docker runtime. Render injects $PORT at runtime.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11.9-slim

# Container-friendly Python behaviour:
#   PYTHONUNBUFFERED        -> logs stream straight to stdout so Render shows them live
#   PYTHONDONTWRITEBYTECODE -> no .pyc clutter baked into the image
#   PIP_NO_CACHE_DIR        -> smaller image, no leftover pip wheel cache
#   HF_HOME                 -> where the embedding model cache lives (see prefetch below)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

# ── Python dependencies ──────────────────────────────────────────────────────
# Copy requirements first so this layer is cached and only rebuilt when the
# dependency list changes, not on every code edit.
COPY requirements.txt .

# Install the CPU-only build of PyTorch BEFORE the rest. sentence-transformers
# pulls in torch, and the default PyPI wheel bundles CUDA (~2.5 GB) which would
# bloat the image and can run the build out of memory on Render. The CPU wheel
# is ~190 MB, and the embedding model in this app runs on CPU anyway.
RUN pip install --upgrade pip \
 && pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

# ── Application code ─────────────────────────────────────────────────────────
COPY . .

# Pre-bake the embedding model (all-MiniLM-L6-v2) into the image so the first
# request isn't blocked on a ~90 MB download, and a restart on Render's
# ephemeral disk doesn't re-download it. Non-fatal: if the build host can't
# reach HuggingFace, the app still starts and falls back to its TF-IDF embedder.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')" \
 || echo "[build] embedding-model prefetch skipped — will download or use TF-IDF fallback at runtime"

# ── Run as a non-root user ───────────────────────────────────────────────────
# The app writes the ChromaDB index under data/ and the model cache under
# .cache/, so hand ownership of /app to an unprivileged user.
RUN useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

# Documented for readers; Render routes to $PORT regardless of EXPOSE.
EXPOSE 8000

# Render sets $PORT (usually 10000); default to 8000 for local `docker run`.
# `exec` makes uvicorn PID 1 so it gets SIGTERM and shuts down cleanly.
# One worker keeps memory bounded — each worker loads its own copy of the model.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]