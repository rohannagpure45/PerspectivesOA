#!/usr/bin/env python3
"""Convert a Chrome-saved HAR into a deterministic fixtures/ tree.

Usage::

    python scripts/parse_har.py /Users/rohan/Downloads/secure.simplepractice.com.har \
        [--out fixtures]

We persist response bodies for the four endpoint families we care about:

* ``/frontend/treatable-clients/{hashedId}?filter[findByHashedId]=true&...``
  -> ``fixtures/treatable-client.json``
* ``/frontend/clients/{numericId}?include=...``
  -> ``fixtures/client.json``
* ``/frontend/overview-items?filter[clientId]={numericId}&include=...``
  -> ``fixtures/overview-items.json``
* ``/frontend/appointments/{appointmentId}?include=...``
  -> ``fixtures/appointments/{appointmentId}.json``

If the HAR does not contain one of the per-appointment payloads (the user only
opened two of three appointments before exporting the HAR), we synthesise a
minimal stub from the timeline so extraction still succeeds end-to-end. The
stub includes the appointment + its progress / psychotherapy notes that are
already present in the timeline ``included`` array.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "fixtures"


def _entries(har: dict[str, Any]) -> Iterable[dict[str, Any]]:
    return har.get("log", {}).get("entries", []) or []


def _path_of(url: str) -> str:
    return unquote(urlparse(url).path)


def _body(entry: dict[str, Any]) -> str | None:
    txt = entry.get("response", {}).get("content", {}).get("text")
    return txt if isinstance(txt, str) and txt.strip() else None


def _is_get_200(entry: dict[str, Any]) -> bool:
    return entry.get("request", {}).get("method") == "GET" and entry.get("response", {}).get("status") == 200


def _last_match(entries: list[dict[str, Any]], predicate) -> dict[str, Any] | None:
    chosen: dict[str, Any] | None = None
    for e in entries:
        if _is_get_200(e) and predicate(_path_of(e["request"]["url"])):
            chosen = e
    return chosen


def _write_json(out_path: Path, body: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parsed = json.loads(body)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)
    print(f"  wrote {out_path.relative_to(REPO_ROOT)} ({out_path.stat().st_size} bytes)")


def _write_treatable_client(entries: list[dict[str, Any]], out: Path) -> str | None:
    e = _last_match(entries, lambda p: "/frontend/treatable-clients/" in p)
    if not e:
        return None
    body = _body(e)
    if not body:
        return None
    _write_json(out / "treatable-client.json", body)
    parsed = json.loads(body)
    primary = parsed.get("data", {})
    if isinstance(primary, dict):
        return primary.get("id")
    return None


def _write_client(entries: list[dict[str, Any]], numeric_id: str, out: Path) -> None:
    e = _last_match(
        entries,
        lambda p: p == f"/frontend/clients/{numeric_id}" or p.startswith(f"/frontend/clients/{numeric_id}?"),
    )
    if not e:
        # try without numeric narrowing
        e = _last_match(
            entries,
            lambda p: (
                p.startswith("/frontend/clients/") and p.endswith(numeric_id) is False and "include=" in p
            ),
        )
    if not e:
        print("WARNING: no clients/{id} response in HAR", file=sys.stderr)
        return
    body = _body(e)
    if body:
        _write_json(out / "client.json", body)


def _write_overview(entries: list[dict[str, Any]], out: Path) -> dict[str, Any] | None:
    e = _last_match(entries, lambda p: p == "/frontend/overview-items")
    if not e:
        print("WARNING: no overview-items response in HAR", file=sys.stderr)
        return None
    body = _body(e)
    if not body:
        return None
    _write_json(out / "overview-items.json", body)
    return json.loads(body)


def _write_appointments(
    entries: list[dict[str, Any]],
    overview: dict[str, Any] | None,
    out: Path,
) -> None:
    apt_dir = out / "appointments"
    apt_dir.mkdir(parents=True, exist_ok=True)

    appointment_ids: list[str] = []
    if overview:
        for d in overview.get("data", []) or []:
            if d.get("type") == "appointments" and d.get("id"):
                appointment_ids.append(str(d["id"]))

    seen: set[str] = set()
    # 1) Use real captured payloads where available.
    for e in entries:
        if not _is_get_200(e):
            continue
        path = _path_of(e["request"]["url"])
        if not path.startswith("/frontend/appointments/"):
            continue
        tail = path.removeprefix("/frontend/appointments/")
        # only the canonical "appointments/{id}" with include=, not collection routes
        if "/" in tail or not tail.isdigit():
            continue
        body = _body(e)
        if not body:
            continue
        _write_json(apt_dir / f"{tail}.json", body)
        seen.add(tail)

    # 2) Synthesize missing appointments from the overview timeline + included notes.
    if not overview:
        return
    included = overview.get("included", []) or []
    overview_data = overview.get("data", []) or []

    # index notes by id
    notes_by_id: dict[str, dict[str, Any]] = {r["id"]: r for r in included if r.get("type") == "notes"}
    apts_by_id: dict[str, dict[str, Any]] = {
        r["id"]: r for r in overview_data if r.get("type") == "appointments"
    }

    for apt_id in appointment_ids:
        if apt_id in seen:
            continue
        apt_resource = apts_by_id.get(apt_id)
        if not apt_resource:
            continue
        # find a progress note whose `notable` rel points at this appointment
        attached_note: dict[str, Any] | None = None
        psych_note: dict[str, Any] | None = None
        for note in notes_by_id.values():
            notable = note.get("relationships", {}).get("notable", {}).get("data") or {}
            if notable.get("type") == "appointments" and str(notable.get("id")) == apt_id:
                this_type = note.get("attributes", {}).get("thisType")
                if this_type == "Progress":
                    attached_note = note
                elif this_type == "Psychotherapy":
                    psych_note = note
        synthesized_included: list[dict[str, Any]] = []
        relationships = dict(apt_resource.get("relationships") or {})
        if attached_note:
            synthesized_included.append(attached_note)
            relationships["progressNote"] = {"data": {"type": "notes", "id": attached_note["id"]}}
        if psych_note:
            synthesized_included.append(psych_note)
            relationships["psychotherapyNote"] = {"data": {"type": "notes", "id": psych_note["id"]}}
        synthesized_data = {
            "type": "appointments",
            "id": apt_id,
            "attributes": dict(apt_resource.get("attributes") or {}),
            "relationships": relationships,
            "_synthesized_from": "overview-items",
        }
        body = json.dumps(
            {"data": synthesized_data, "included": synthesized_included},
            indent=2,
            ensure_ascii=False,
        )
        _write_json(apt_dir / f"{apt_id}.json", body)


def main() -> int:
    p = argparse.ArgumentParser(description="Convert SimplePractice HAR -> fixtures/")
    p.add_argument("har", type=Path, help="Path to the HAR file")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output fixtures dir")
    args = p.parse_args()

    if not args.har.exists():
        print(f"HAR not found: {args.har}", file=sys.stderr)
        return 2

    print(f"reading HAR: {args.har}")
    with args.har.open("r", encoding="utf-8") as f:
        har = json.load(f)

    entries = list(_entries(har))
    print(f"  {len(entries)} entries")

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    numeric_id = _write_treatable_client(entries, out)
    if not numeric_id:
        print("WARNING: could not resolve numeric client id from HAR", file=sys.stderr)
        numeric_id = "106612410"  # fallback for the demo dataset
    print(f"  numeric client id: {numeric_id}")

    _write_client(entries, numeric_id, out)
    overview = _write_overview(entries, out)
    _write_appointments(entries, overview, out)

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
