set shell := ["bash", "-euo", "pipefail", "-c"]

default: setup

# Install the producer's dependencies.
setup:
    uv sync --locked

# Start Postgres (with pgvector) and RabbitMQ, and wait until both are healthy.
up:
    docker compose up -d --wait

# Stop everything and delete the volumes.
down:
    docker compose down -v

# Replay the registry feed onto the company-events queue.
publish:
    uv run python -m producer.publish

# Ingest company events using a local multilingual model.
consumer:
    uv run --locked --with 'psycopg[binary,pool]>=3.2,<4' --with sentence-transformers python -m recommender.consumer

# Serve recommendations on the local machine.
api:
    uv run --locked --with 'psycopg[binary,pool]>=3.2,<4' --with fastapi --with uvicorn python -m uvicorn recommender.api:app --host 127.0.0.1 --port 8000

test:
    uv run --locked python -m unittest discover -s tests

# Requires the consumer, API and an isolated local stack.
smoke:
    uv run --locked python -m tests.smoke

# Verify the persisted full-feed replay on the local assessment stack.
verify-replay:
    uv run --locked python -m tests.replay

# Compare two embedding models and benchmark the running API on the fully replayed feed.
eval:
    uv run --locked --with 'psycopg[binary,pool]>=3.2,<4' --with sentence-transformers python -m scripts.evaluate

# Exercise API and consumer failure paths without a running stack.
test-service:
    uv run --locked --with 'psycopg[binary,pool]>=3.2,<4' --with fastapi --with httpx2 python -m unittest tests.service_checks -v
