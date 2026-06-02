# ─── Builder stage ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source
COPY . .

# Create data directory for SQLite persistence
RUN mkdir -p /data && touch /data/.gitkeep

# Non-root user for security
RUN useradd -m -u 1000 caretaker && chown -R caretaker:caretaker /app /data
USER caretaker

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/app/config.yaml

# Default: run the FastAPI webhook server
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
