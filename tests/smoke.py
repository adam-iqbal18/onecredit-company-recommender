"""Live producer -> queue -> consumer -> pgvector -> HTTP check on an isolated stack."""
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from producer.publish import publish

SEED = "SYN-000297"
MATCH = "SYN-000315"
OTHER = "SYN-001777"
BASE = "http://127.0.0.1:8000"


def similar(reg):
    try:
        with urlopen(f"{BASE}/companies/{reg}/similar?limit=5", timeout=5) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def main():
    ids = {SEED, MATCH, OTHER}
    samples = {}
    path = Path(__file__).resolve().parents[1] / "data" / "feed.jsonl"
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("registrationNumber") in ids:
            samples.setdefault(event["registrationNumber"], line)
        if len(samples) == len(ids):
            break
    if samples.keys() != ids:
        raise RuntimeError("Smoke fixtures are missing from the assessment feed.")
    if any(similar(reg) is not None for reg in ids):
        raise RuntimeError("The sample companies already exist. Use an empty, isolated smoke stack.")
    port = os.getenv("RABBITMQ_PORT", "5672")
    url = os.getenv("RABBITMQ_URL", f"amqp://guest:guest@localhost:{port}/")
    asyncio.run(publish(list(samples.values()), url))
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        result = similar(SEED)
        if result and len(result["similar"]) >= 2:
            first, second, *others = result["similar"]
            expected = json.loads(samples[MATCH])["data"]
            if (first["registration_number"] != MATCH or second["registration_number"] != OTHER
                    or others or first["company_name"] != expected["companyName"]
                    or first["state"] != "Sarawak" or first["industry"] != expected["industry"]
                    or first["similarity"] < 0.999 or first["similarity"] <= second["similarity"]):
                raise AssertionError(result)
            print(json.dumps(result, indent=2))
            return
        time.sleep(0.5)
    raise TimeoutError("Expected both neighbours within 180 seconds. Check the consumer and API logs.")


if __name__ == "__main__":
    main()
