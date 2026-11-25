# Company recommendation service

The consumer reads the registry's durable RabbitMQ queue, normalizes one event and
commits its effects in Postgres before acknowledging it. A database error causes
requeue. Invalid input goes to `failed_events` in a committed transaction. Each
distinct rejected body from the supplied producer has one row, even after replay.

An upsert is a patch. Each field carries its own source timestamp so that an older
event can supply a description omitted by a newer name change. Null, blank and
absent fields preserve existing values. The row lock serializes concurrent edits to
one registration number. The body hash breaks equal-time ties consistently.
Undated fields sort before dated ones. Naive timestamps use Malaysian local time.
A struck-off event changes the status and preserves other fields. Cards expose that
status. A company without a usable description stays in the database and its
similarity endpoint returns an empty list with a reason.

The consumer uses sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
on the CPU. The model processes both English and Malay text. Postgres caches
vectors by model name and the SHA-256 of the trimmed description. The API ranks
eligible companies by exact pgvector cosine distance with registration number as
the tie-breaker. At 2,000 companies the exact scan met the local
latency target reported below. The API requires no model download.

## Verification

The offline tests exercised all 2,518 source messages. They produced 2,000
company records and 22 distinct rejected bodies across 27 deliveries. The
complete projected records, field versions and rejected bodies matched after
a second replay and a shuffled replay. These checks run in memory.

The live three-event smoke test passed on the developer's Mac, as confirmed by
the terminal output from `just smoke`. For seed `SYN-000297`, the API returned
`SYN-000315` first (cosine similarity 1.0, identical Malay business description)
and `SYN-001777` second (0.0155882). This exercised the supplied publisher,
RabbitMQ, CPU embedding, Postgres with pgvector, and the HTTP endpoint.

The developer confirmed a passing live `just verify-replay` run on the same Mac.
After each of two publishes of all 2,518 messages, RabbitMQ drained and Postgres
contained the expected 2,000 company records and 22 distinct rejected events.
The second publish left every stored company field, field version, embedding,
cache entry and failed-event row identical to the first snapshot.

The eight event and evaluation tests passed on Python 3.14.10. Seven additional
service checks exercised numeric and padded IDs, missing descriptions, card
fields, unknown IDs, invalid limits, HTTP 503, and the consumer's choice of ack
or requeue. These checks used controlled database outcomes. Docker Compose
validated the service definitions, and `just setup` installed the locked producer
dependencies.
Service dependencies are currently supplied through uv run options in the Justfile.

## Recommendation quality and latency

Precision@5 and precision@10 measure how often a recommended company shares the
query's industry. Industry is a proxy for relevance. Each company with an embedding
and a known industry counts once as a query. Candidates with unknown industry
remain eligible and count as misses. Scores use exact cosine ranking with company
registration number as the tie-breaker. Both models use the same queries.

The language labels come from a manual review of the synthetic description
templates. The “Different text” pool excludes candidates matching the query after
case folding, whitespace cleanup and collapsing exactly repeated phrases. It
still includes near-duplicates such as descriptions differing only by city.
“Other language” searches only candidates in the opposite language. Both filters
are applied before selecting neighbours. These are evaluation restrictions.

The local HTTP benchmark uses one connection, concurrency one, a fixed random
sample, 20 warm-ups and 500 distinct measured queries. It times the request through
receipt of the complete response. Each response must contain ten recommendations.
This measures warm latency on this dataset at low load.

<!-- evaluation-results-start -->

Live run on the populated database with 1,961 embedded companies and 175 distinct descriptions.

| Model | Query language | Candidate pool | Queries | P@5 | P@10 |
|---|---|---|---:|---:|---:|
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | English | All | 1280 | 93.00% | 94.58% |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | English | Different text | 1280 | 78.14% | 77.69% |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | English | Other language | 1280 | 84.53% | 81.69% |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | Malay | All | 583 | 95.13% | 94.73% |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | Malay | Different text | 583 | 86.42% | 89.57% |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | Malay | Other language | 583 | 90.91% | 92.52% |
| BAAI/bge-small-en-v1.5 | English | All | 1280 | 92.62% | 94.40% |
| BAAI/bge-small-en-v1.5 | English | Different text | 1280 | 56.62% | 56.05% |
| BAAI/bge-small-en-v1.5 | English | Other language | 1280 | 30.52% | 28.75% |
| BAAI/bge-small-en-v1.5 | Malay | All | 583 | 93.48% | 92.06% |
| BAAI/bge-small-en-v1.5 | Malay | Different text | 583 | 27.58% | 27.17% |
| BAAI/bge-small-en-v1.5 | Malay | Other language | 583 | 27.38% | 25.71% |

Local HTTP latency for 500 successful requests was **3.22 ms p50** and **4.28 ms p95**. The target is p95 below 50 ms.

<!-- evaluation-results-end -->

We kept multilingual MiniLM for the portal. With identical descriptions excluded,
it returned same-industry neighbours more often than the English baseline for both
English and Malay queries. The largest gap appeared when a Malay query could
retrieve only English descriptions. Across the 583 Malay queries, roughly nine
of every ten top-five slots matched the query industry with MiniLM, compared
with fewer than three in ten with the baseline. This matters when two companies
describe the same work in different languages.

## At ten times the volume

Ten times this snapshot is about 20,000 companies. I would first replay a larger
feed with a realistic mix of repeated and new descriptions. Queue age, cache hits,
new descriptions encoded per second, and API p95 under concurrent traffic would
show whether ingestion or search needs attention.

If CPU inference falls behind, I would batch distinct uncached descriptions
before the write transactions, keeping acknowledgements after commit. If exact
search misses the latency target at the larger size, I would benchmark a pgvector
HNSW cosine index against the exact results and compare recall@10, write cost and
p95. None of the current measurements cover that workload.

## What is left

Of the 2,000 companies, 39 have no usable business description. Their records
remain queryable by registration number through the similarity endpoint. They
cannot appear as vector neighbours until a description arrives. An industry-based
fallback would need separate relevance checks and a way to identify its results.
Struck-off companies remain eligible and carry their status on the card.

A real database or broker restart during ingestion still needs an isolated
recovery drill. The live replay covers duplicate delivery, and the service tests
exercise simulated failure responses. Service dependencies resolved by the
Justfile still need a full transitive lock for reproducible installations.

## AI assistance

Codex assisted with implementation, tests, the model comparison and documentation.
The live smoke, replay and evaluation commands were run on the developer's Mac.
