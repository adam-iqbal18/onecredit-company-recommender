import json
import random
import unittest
from pathlib import Path

from recommender.events import merge, parse, registration, timestamp


def project(lines):
    result, rejected = {}, set()
    for body in lines:
        try:
            event = parse(body)
        except ValueError:
            rejected.add(body)
            continue
        old, versions = result.get(event.registration, ({}, {}))
        result[event.registration] = merge(old, versions, event)
    return result, rejected


def upsert(date, data):
    return json.dumps({"type": "company-upserted", "registrationNumber": 119,
                       "updatedAt": date, "data": data}).encode()


class EventsTest(unittest.TestCase):
    def test_partial_updates_in_either_order(self):
        older = upsert("2026-01-01", {"companyName": "Old", "natureOfBusiness": "Coffee"})
        newer = upsert("2026-02-01", {"companyName": "New", "natureOfBusiness": None})
        for pair in [(older, newer), (newer, older)]:
            result, _ = project(pair)
            fields, _ = result["SYN-000119"]
            self.assertEqual((fields["company_name"], fields["description"]), ("New", "Coffee"))

    def test_ties_and_undated_patches(self):
        a = upsert("2026-01-01", {"companyName": "A"})
        b = upsert("2026-01-01", {"companyName": "B"})
        c = upsert(None, {"companyName": "C", "state": "selangor"})
        projected = project([c, b, a])
        self.assertEqual(projected, project([a, b, c, a]))
        fields, _ = projected[0]["SYN-000119"]
        self.assertIn(fields["company_name"], ("A", "B"))
        self.assertEqual(fields["state"], "Selangor")

    def test_identifiers_and_timestamps(self):
        for alias in (" syn-000119 ", 119, "119"):
            self.assertEqual(registration(alias), "SYN-000119")
        for instant in ("1970-01-01T08:00:00+08:00", "1970-01-01T08:00:00", 0):
            self.assertEqual(timestamp(instant), timestamp("1970-01-01T00:00:00Z"))

    def test_struck_off_does_not_erase_description(self):
        active = upsert("2026-01-01", {"natureOfBusiness": "Coffee"})
        struck = b'{"type":"company-struck-off","registrationNumber":119,"updatedAt":"2026-02-01"}'
        fields, _ = project([struck, active])[0]["SYN-000119"]
        self.assertEqual(fields, {"description": "Coffee", "status": "struck_off"})

    def test_malformed_data(self):
        for body in (b'{', b'null', b'{}', upsert(None, "junk")):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse(body)

    def test_complete_feed_replay(self):
        path = Path(__file__).resolve().parents[1] / "data" / "feed.jsonl"
        lines = path.read_bytes().splitlines()
        first = project(lines)
        shuffled = lines.copy()
        random.Random(119).shuffle(shuffled)
        self.assertEqual(len(first[0]), 2000)
        self.assertEqual(project(lines + lines), first)
        self.assertEqual(project(shuffled), first)
