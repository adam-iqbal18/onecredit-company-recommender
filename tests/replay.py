"""Publish the assessment feed twice against the running local Compose stack."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

from tests.test_events import project

FEED = Path(__file__).resolve().parents[1] / "data" / "feed.jsonl"
SNAPSHOT = """
SELECT json_build_object(
    'companies', (SELECT json_agg(c ORDER BY registration_number) FROM companies c),
    'embedding_cache', (SELECT json_agg(e ORDER BY model, digest) FROM embedding_cache e),
    'failed_events', (SELECT json_agg(f ORDER BY message_id) FROM failed_events f)
);
"""


def compose_output(service, *command):
    return subprocess.check_output(
        ["docker", "compose", "exec", "-T", service, *command], text=True, timeout=60
    )


def wait_for_consumer():
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        listing = compose_output("rabbitmq", "rabbitmqctl", "--silent", "list_queues",
                                 "name", "consumers", "messages_ready", "messages_unacknowledged")
        for line in listing.splitlines():
            columns = line.split()
            if columns[:1] == ["company-events"]:
                consumers, ready, unacked = map(int, columns[1:])
                if consumers == 0:
                    raise RuntimeError("Start 'just consumer' before running this check.")
                if ready == 0 and unacked == 0:
                    return
                print(f"Waiting for {ready} queued and {unacked} unacknowledged messages.", flush=True)
                break
        else:
            raise RuntimeError("The company-events queue is missing. Start 'just consumer'.")
        time.sleep(3)
    raise TimeoutError("The queue did not drain within 15 minutes. Check the consumer logs.")


def read_snapshot():
    result = compose_output("postgres", "psql", "-XAtq", "-v", "ON_ERROR_STOP=1",
                            "-U", "postgres", "-d", "companies", "-c", SNAPSHOT)
    return json.loads(result)


def verify_rows(snapshot, expected, rejected):
    rows = snapshot["companies"] or []
    actual = {row["registration_number"]: (row["fields"], row["versions"]) for row in rows}
    if actual != expected:
        different = sorted(k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k))
        raise AssertionError(f"Company projection mismatch for {len(different)} IDs, e.g. {different[:5]}")
    for row in rows:
        if bool(row["fields"].get("description")) != (row["embedding"] is not None):
            raise AssertionError(f"Embedding coverage mismatch for {row['registration_number']}")
    failures = snapshot["failed_events"] or []
    expected_ids = {hashlib.sha256(body).hexdigest() for body in rejected}
    if {row["message_id"] for row in failures} != expected_ids:
        raise AssertionError("Persisted failed-event IDs differ from the rejected feed bodies.")


def main():
    lines = FEED.read_bytes().splitlines()
    expected, rejected = project(lines)
    if len(lines) != 2518 or len(expected) != 2000:
        raise RuntimeError("This check targets the assessment feed of 2,518 messages and 2,000 companies.")
    wait_for_consumer()
    previous = None
    for run in (1, 2):
        print(f"Pass {run}: publishing {len(lines):,} messages.", flush=True)
        subprocess.run(["just", "publish"], check=True, timeout=900)
        wait_for_consumer()
        snapshot = read_snapshot()
        verify_rows(snapshot, expected, rejected)
        print(f"Pass {run}: {len(expected):,} companies and {len(rejected)} distinct rejected events verified.",
              flush=True)
        if previous is not None:
            for table in snapshot:
                if snapshot[table] != previous[table]:
                    raise AssertionError(f"Stored rows changed on replay in {table}.")
        previous = snapshot
    print("PASS: company records, embeddings, embedding cache and failed events are identical after replay.")


if __name__ == "__main__":
    main()
