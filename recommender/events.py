"""Normalize source events and merge partial records without depending on arrival order."""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

LOCAL = timezone(timedelta(hours=8))
FIELDS = {"companyName": "company_name", "natureOfBusiness": "description",
          "industry": "industry", "state": "state"}


def registration(value: object) -> str:
    match = re.fullmatch(r"(?:SYN-)?([0-9]{1,6})", str(value).strip().upper())
    if not match:
        raise ValueError("invalid registration number")
    return f"SYN-{int(match[1]):06d}"


def timestamp(value: object) -> str:
    if value is None:
        return ""  # Undated patches sort before dated ones.
    if type(value) is int:
        try:
            dt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)
        except OverflowError as exc:
            raise ValueError("invalid epoch timestamp") from exc
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL)
    else:
        raise ValueError("invalid timestamp")
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass
class Event:
    registration: str
    stamp: str
    fields: dict[str, str]


def parse(body: bytes) -> Event:
    event = json.loads(body)
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    reg = registration(event.get("registrationNumber"))
    kind = event.get("type")
    fields = {}
    if kind == "company-upserted":
        data = event.get("data")
        if not isinstance(data, dict):
            raise ValueError("upsert data must be an object")
        fields["status"] = "active"
        for key, field in FIELDS.items():
            raw = data.get(key)
            if isinstance(raw, str) and (value := raw.strip()):
                if field == "company_name" and value.casefold() == "unknown":
                    continue
                fields[field] = value.title() if field == "state" else value
    elif kind == "company-struck-off":
        fields["status"] = "struck_off"
    else:
        raise ValueError("unsupported event type")
    # Fixed-width timestamp followed by body hash gives equal-time patches a stable order.
    instant = timestamp(event.get("updatedAt"))
    digest = hashlib.sha256(body).hexdigest()
    stamp = f"1{instant}|{digest}" if instant else f"0|{digest}"
    return Event(reg, stamp, fields)


def merge(values: dict, versions: dict, event: Event) -> tuple[dict, dict]:
    values, versions = values.copy(), versions.copy()
    for field, value in event.fields.items():
        if event.stamp > versions.get(field, ""):
            versions[field] = event.stamp
            values[field] = value
    return values, versions
