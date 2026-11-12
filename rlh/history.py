"""Snapshot/history bookkeeping.

A *version* is one distinct record content (all fields, hashed) together with
the span of pulls in which it was listed: ``first_seen`` .. ``last_seen``.
``last_seen`` stays blank while the version is still listed, so a daily pull
only rewrites rows that actually changed (keeping git diffs small and readable).
When any field of a record changes, the old version is closed and a new one
starts. Content that disappears and later reappears gets a new version.

Record IDs are not used as keys because the county data is not strictly unique
on them (the same license number can appear on two slightly different rows).
"""

import csv
import hashlib
import io
import json

HISTORY_META = ["row_hash", "first_seen", "last_seen"]


def row_hash(row, fields):
    payload = json.dumps([row.get(f, "") for f in fields], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def update_history(history, snapshot, fields, pulled_at, prev_pulled_at):
    """Fold one pull into the history list (in place) and return it.

    ``prev_pulled_at`` is the timestamp of the previous pull of this layer, or None.
    Open versions (blank ``last_seen``) missing from this pull are closed with
    ``last_seen = prev_pulled_at``: the last pull that still listed them.
    """
    open_by_hash = {h["row_hash"]: h for h in history if not h["last_seen"]}
    seen = set()
    for row in snapshot:
        rh = row_hash(row, fields)
        if rh in seen:
            continue  # exact duplicate row within one pull
        seen.add(rh)
        if rh not in open_by_hash:
            history.append({"row_hash": rh, "first_seen": pulled_at, "last_seen": "",
                            **{f: row.get(f, "") for f in fields}})
    for rh, h in open_by_hash.items():
        if rh not in seen:
            h["last_seen"] = prev_pulled_at
    return history


def sort_rows(rows, key_field):
    return sorted(rows, key=lambda r: (r.get(key_field, ""), json.dumps(r, sort_keys=True)))


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_csv_text(text):
    return list(csv.DictReader(io.StringIO(text)))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
