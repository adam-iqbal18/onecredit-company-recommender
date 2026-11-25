"""Compare company recommendations on the populated database and time the live API."""
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from http.client import HTTPConnection
import json
import math
from pathlib import Path
import random
import statistics
import time

BASELINE = "BAAI/bge-small-en-v1.5"
DESIGN = Path(__file__).resolve().parents[1] / "DESIGN.md"
START = "<!-- evaluation-results-start -->"
END = "<!-- evaluation-results-end -->"

# Manually reviewed sentence openings in this synthetic feed. Unknown templates need review.
MALAY = set("""agensi farmasi hotel kebun kedai kerja-kerja klinik kontraktor ladang mengusahakan
pembangunan pembuatan pengeluaran penghantaran pengilangan pengubahsuaian pengusaha penjualan
penternakan perkhidmatan perniagaan perunding pusat restoran sokongan tadika""".split())
ENGLISH = set("""accounting, architectural bakery boutique civil cloud cold community dental durian freight
general halal home homestay hydroponic it last-mile management manufacture mini mobile oil online operating
poultry precision private production professional recruitment renovation restaurant retail software supply
trading travel trucking tuition wholesale""".split())


@cache
def language(text):
    opening = text.casefold().split()[0]
    if opening in MALAY:
        return "Malay"
    if opening in ENGLISH:
        return "English"
    raise ValueError(f"Review the language of this new description: {text!r}")


@cache
def canonical(text):
    words = text.casefold().split()
    # Some feed errors repeat one complete sentence dozens of times.
    for n in range(1, len(words) // 2 + 1):
        if len(words) % n == 0 and words[:n] * (len(words) // n) == words:
            return " ".join(words[:n])
    return " ".join(words)


@dataclass(frozen=True)
class Company:
    registration: str
    text: str
    industry: str | None


def scores(companies, vocabulary, cosine):
    """Macro-average over labelled queries. Keep unlabelled neighbours in the denominator."""
    companies = sorted(companies, key=lambda c: c.registration)
    index = {text: i for i, text in enumerate(vocabulary)}
    groups = defaultdict(list)
    for c in companies:
        groups[c.text].append(c)

    totals = defaultdict(lambda: [0, 0, 0])
    for text, seeds in groups.items():
        distances = cosine[index[text]]
        # Stable sorting retains ascending registration number on equal similarities.
        ranked = sorted(companies, key=lambda c: -distances[index[c.text]])
        pools = {
            "All": ranked,
            "Different text": [c for c in ranked if canonical(c.text) != canonical(text)],
            "Other language": [c for c in ranked if language(c.text) != language(text)],
        }
        for seed in seeds:
            if seed.industry is None:
                continue
            for name, pool in pools.items():
                # At most one of the first 11 candidates can be the query itself.
                top = [c for c in pool[:11] if c.registration != seed.registration][:10]
                if len(top) < 10:
                    raise ValueError(f"Fewer than 10 eligible neighbours for {seed.registration} ({name})")
                counts = totals[language(text), name]
                counts[0] += 1
                counts[1] += sum(c.industry == seed.industry for c in top[:5])
                counts[2] += sum(c.industry == seed.industry for c in top)
    return {key: (n, five / (5 * n), ten / (10 * n)) for key, (n, five, ten) in totals.items()}


def evaluate_model(companies, texts, vectors):
    import numpy as np

    matrix = np.asarray(vectors, dtype=np.float64)
    lengths = np.linalg.norm(matrix, axis=1, keepdims=True)
    if len(matrix) != len(texts) or not np.isfinite(matrix).all() or (lengths == 0).any():
        raise ValueError("Missing, nonfinite or zero embedding")
    normalized = matrix / lengths
    return scores(companies, texts, normalized @ normalized.T)


def database_sample():
    import psycopg
    from recommender.storage import DATABASE_URL

    with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
        rows = conn.execute("""SELECT registration_number, fields->>'description',
                                      fields->>'industry', embedding::text
                               FROM companies ORDER BY registration_number""").fetchall()
    if len(rows) != 2000:
        raise ValueError(f"Expected the complete 2,000-company replay, found {len(rows)} companies")
    companies, vectors = [], {}
    for reg, text, industry, vector in rows:
        if (text is None) != (vector is None):
            raise ValueError(f"Embedding coverage mismatch for {reg}")
        if text is None:
            continue
        if vectors.setdefault(text, vector) != vector:
            raise ValueError(f"Conflicting stored embeddings for the same description at {reg}")
        companies.append(Company(reg, text, industry))
    texts = sorted(vectors)
    return companies, texts, [json.loads(vectors[text]) for text in texts]


def latency(companies):
    # Sample distinct warm-up and measured queries from the same fixed snapshot.
    sample = random.Random(202611).sample(sorted(c.registration for c in companies), 520)
    elapsed = []
    conn = HTTPConnection("127.0.0.1", 8000, timeout=10)
    try:
        for i, reg in enumerate(sample):
            start = time.perf_counter_ns()
            conn.request("GET", f"/companies/{reg}/similar?limit=10")
            response = conn.getresponse()
            body = response.read()
            milliseconds = (time.perf_counter_ns() - start) / 1_000_000
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status} for {reg}: {body[:200]!r}")
            document = json.loads(body)
            if document["registration_number"] != reg or len(document["similar"]) != 10:
                raise ValueError(f"Incomplete response for {reg}")
            if i >= 20:
                elapsed.append(milliseconds)
    finally:
        conn.close()
    elapsed.sort()
    return statistics.median(elapsed), elapsed[math.ceil(0.95 * len(elapsed)) - 1]


def quality_table(results):
    lines = ["| Model | Query language | Candidate pool | Queries | P@5 | P@10 |",
             "|---|---|---|---:|---:|---:|"]
    for model, result in results.items():
        for lang in ("English", "Malay"):
            for pool in ("All", "Different text", "Other language"):
                n, five, ten = result[lang, pool]
                lines.append(f"| {model} | {lang} | {pool} | {n} | {five:.2%} | {ten:.2%} |")
    return "\n".join(lines)


def main():
    from sentence_transformers import SentenceTransformer
    from recommender.storage import MODEL

    print("Reading company embeddings from PostgreSQL.", flush=True)
    companies, texts, stored = database_sample()
    print("Timing 20 warm-ups and 500 sequential HTTP requests.", flush=True)
    p50, p95 = latency(companies)
    print(f"HTTP p50 {p50:.2f} ms, p95 {p95:.2f} ms.", flush=True)
    results = {MODEL: evaluate_model(companies, texts, stored)}
    print(f"Loading {BASELINE} and encoding {len(texts)} distinct descriptions on CPU.", flush=True)
    baseline = SentenceTransformer(BASELINE, device="cpu")
    vectors = baseline.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    results[BASELINE] = evaluate_model(companies, texts, vectors)
    report = (f"Live run on the populated database with {len(companies):,} embedded companies "
              f"and {len(texts)} distinct descriptions.\n\n"
              + quality_table(results)
              + f"\n\nLocal HTTP latency for 500 successful requests was **{p50:.2f} ms p50** "
                f"and **{p95:.2f} ms p95**. The target is p95 below 50 ms.")
    content = DESIGN.read_text()
    if content.count(START) != 1 or content.count(END) != 1:
        raise ValueError("Missing or ambiguous evaluation section in DESIGN.md")
    before, remaining = content.split(START)
    _, after = remaining.split(END)
    draft = DESIGN.with_suffix(".md.tmp")
    draft.write_text(f"{before}{START}\n\n{report}\n\n{END}{after}")
    draft.replace(DESIGN)
    print(report, flush=True)
    print("\nSaved measured results in DESIGN.md.")


if __name__ == "__main__":
    main()
