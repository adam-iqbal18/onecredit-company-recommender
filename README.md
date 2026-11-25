# Similar companies API

A RabbitMQ consumer reads changes to the company registry and stores the latest
company fields and business-description embeddings in PostgreSQL. A FastAPI
endpoint uses pgvector to return the nearest companies, including the fields
needed for each portal card. The feed contains 2,518 events for 2,000 synthetic
Malaysian companies. This implementation uses assessment version `mle-v2026.11`.

## Run locally

Install Docker with Compose, uv and just. Start the Docker engine. The first run
downloads Python 3.14, service images, Python packages and the multilingual model.
The model runs on the CPU. Open three terminals at the root of this checkout.

1. In terminal one, start the services and consumer.

   ```sh
   just setup
   just up
   just consumer
   ```

   Wait for `Listening on company-events` and leave this process running.

2. In terminal two, run `just api`. Leave it running once Uvicorn reports
   `Application startup complete`.

3. In terminal three, run `just verify-replay`. It publishes the full feed twice
   and waits for all deliveries to be acknowledged. A passing run verifies 2,000
   company records and checks that replay leaves all persisted rows identical.
   For a single publish, use `just publish`; the consumer continues processing
   after the publisher returns.

Query the API from terminal three:

```sh
curl -fsS 'http://127.0.0.1:8000/companies/SYN-000297/similar?limit=5'
```

Each item in `similar` includes registration number, company name, industry,
state, status and similarity. Results exclude the query company. `limit` defaults
to 10 and accepts 1–50. Compact numeric IDs and lowercase or padded IDs are
normalized. A known company without a description returns HTTP 200 with an empty
list and `reason: no_description`. An unknown company returns 404, invalid limits
return 422, and a database outage during a request returns 503.

To pause the stack, stop the consumer and API with Ctrl+C in their terminals and
run `docker compose stop`. The template's `just down` deletes the database and
queue volumes.

## Checks and measurements

| Command | What it checks | When to run |
|---|---|---|
| `just test` | Field merging, full-feed replay in memory, language labels and ranking metrics | Any time after setup |
| `just test-service` | API routes and error responses, acknowledgement order, requeue on a simulated database error | Any time after setup |
| `just smoke` | Three real feed events through RabbitMQ, the consumer, pgvector and the HTTP endpoint | On an empty local stack, before publishing the feed |
| `just verify-replay` | Every company after two full publishes and equality of all three application tables | With the local stack and consumer running |
| `just eval` | English and multilingual model precision, plus 500 timed local HTTP requests | After full replay, with the API running |

The evaluation downloads the English baseline on first use and writes its results
into [DESIGN.md](DESIGN.md). That document also covers data assumptions, the 10×
scaling plan and known limitations.

## Local configuration

The API listens on `127.0.0.1:8000`. The Compose defaults are PostgreSQL on 5432,
RabbitMQ on 5672 and its management UI on 15672. Set `POSTGRES_PORT`,
`RABBITMQ_PORT` or `RABBITMQ_UI_PORT` before startup to change the host ports.
Use the same values in each terminal. The application also accepts `DATABASE_URL`
and `RABBITMQ_URL`. The replay checker inspects the local Compose containers.
