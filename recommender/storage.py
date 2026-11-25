import hashlib
import os

from psycopg.types.json import Jsonb

from .events import merge, parse

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql://postgres:postgres@localhost:{os.getenv('POSTGRES_PORT', '5432')}/companies"
)


def initialize(conn):
    with conn.transaction():
        # Both the API and the consumer can start on an empty database.
        conn.execute("SELECT pg_advisory_xact_lock(590174)")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("""CREATE TABLE IF NOT EXISTS embedding_cache (
            model text, digest text, embedding vector(384) NOT NULL,
            PRIMARY KEY (model, digest))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS companies (
            registration_number text PRIMARY KEY, fields jsonb NOT NULL DEFAULT '{}',
            versions jsonb NOT NULL DEFAULT '{}', embedding vector(384))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS failed_events (
            message_id text PRIMARY KEY, body bytea NOT NULL, reason text NOT NULL)""")


def cached_vector(conn, description, encode):
    digest = hashlib.sha256(description.encode()).hexdigest()
    row = conn.execute("SELECT embedding::text FROM embedding_cache WHERE model=%s AND digest=%s",
                       (MODEL, digest)).fetchone()
    if row:
        return row[0]
    vector = str(encode(description).tolist())
    conn.execute("""INSERT INTO embedding_cache VALUES (%s, %s, %s::vector)
                    ON CONFLICT DO NOTHING""", (MODEL, digest, vector))
    return vector


def ingest(conn, body, message_id, encode):
    with conn.transaction():
        try:
            event = parse(body)
        except (ValueError, UnicodeError) as exc:
            conn.execute("INSERT INTO failed_events VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                         (message_id or hashlib.sha256(body).hexdigest(), body, str(exc)))
            return
        reg = event.registration
        conn.execute("INSERT INTO companies (registration_number) VALUES (%s) ON CONFLICT DO NOTHING",
                     (reg,))
        old, clocks = conn.execute(
            "SELECT fields, versions FROM companies WHERE registration_number=%s FOR UPDATE",
            (reg,)).fetchone()
        fields, versions = merge(old, clocks, event)
        if versions == clocks:
            return
        if fields.get("description") != old.get("description"):
            vector = cached_vector(conn, fields["description"], encode)
            conn.execute("UPDATE companies SET embedding=%s::vector WHERE registration_number=%s",
                         (vector, reg))
        conn.execute("UPDATE companies SET fields=%s, versions=%s WHERE registration_number=%s",
                     (Jsonb(fields), Jsonb(versions), reg))


def recommendations(conn, reg, limit):
    # One snapshot keeps the seed and its neighbours consistent during concurrent updates.
    rows = conn.execute("""
        SELECT seed.embedding IS NOT NULL, neighbour.registration_number,
               neighbour.fields, neighbour.distance
        FROM companies AS seed
        LEFT JOIN LATERAL (
            SELECT c.registration_number, c.fields, c.embedding <=> seed.embedding AS distance
            FROM companies AS c
            WHERE c.registration_number <> seed.registration_number
              AND c.embedding IS NOT NULL AND seed.embedding IS NOT NULL
            ORDER BY distance, c.registration_number LIMIT %s
        ) AS neighbour ON true
        WHERE seed.registration_number = %s
        ORDER BY neighbour.distance, neighbour.registration_number""", (limit, reg)).fetchall()
    if not rows:
        return None
    items = []
    for _, number, fields, distance in rows:
        if number is not None:
            items.append({"registration_number": number, "company_name": fields.get("company_name"),
                          "industry": fields.get("industry"), "state": fields.get("state"),
                          "status": fields["status"], "similarity": max(0.0, min(1.0, 1 - distance))})
    result = {"registration_number": reg, "similar": items}
    if not rows[0][0]:
        result["reason"] = "no_description"
    return result
