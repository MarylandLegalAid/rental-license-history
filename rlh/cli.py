"""Command line: scrape, rebuild-history, build-sqlite.

    python -m rlh scrape [--jurisdiction baltimore-county] [--force]
    python -m rlh rebuild-history [--jurisdiction baltimore-county]
    python -m rlh build-sqlite --out build/rental_licenses.sqlite
"""

import argparse
import datetime as dt
import pathlib
import sqlite3
import subprocess
import sys

from . import arcgis
from .history import (HISTORY_META, read_csv, read_csv_text, sort_rows,
                      update_history, write_csv)
from .sources import (MAX_ROW_COUNT_CHANGE, MIN_ROWS_FOR_RATIO_CHECK, SOURCES,
                      UNIFIED_COLUMNS)

ROOT = pathlib.Path(__file__).resolve().parent.parent
PULL_FIELDS = ["pulled_at", "layer", "row_count", "fields"]


def jdir(slug):
    return ROOT / "jurisdictions" / slug


def paths(slug, layer):
    d = jdir(slug)
    return d / "snapshots" / f"{layer}.csv", d / "history" / f"{layer}.csv"


def last_pull(pulls, layer):
    rows = [p for p in pulls if p["layer"] == layer]
    return rows[-1] if rows else None


def history_fields(history, fields):
    """Columns for the history file: meta, then every field ever seen (old first)."""
    out = list(HISTORY_META)
    for h in history:
        for k in h:
            if k not in out:
                out.append(k)
    for f in fields:
        if f not in out:
            out.append(f)
    return out


# ---------------------------------------------------------------- scrape

def scrape(slug, force=False):
    src = SOURCES[slug]
    pulled_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    pulls_path = jdir(slug) / "pulls.csv"
    pulls = read_csv(pulls_path)

    fetched, problems = {}, []
    for layer, (layer_id, key) in src["layers"].items():
        fields, rows = arcgis.fetch_layer(src["base_url"], layer_id)
        fetched[layer] = (fields, rows, key)
        prev = last_pull(pulls, layer)
        print(f"{slug}/{layer}: {len(rows)} rows")
        if not rows:
            problems.append(f"{layer}: service returned no rows")
        if prev:
            if prev["fields"] != "|".join(fields):
                problems.append(f"{layer}: field list changed\n  was: {prev['fields']}\n  now: {'|'.join(fields)}")
            n0 = int(prev["row_count"])
            if n0 >= MIN_ROWS_FOR_RATIO_CHECK and abs(len(rows) - n0) / n0 > MAX_ROW_COUNT_CHANGE:
                problems.append(f"{layer}: row count {n0} -> {len(rows)} exceeds {MAX_ROW_COUNT_CHANGE:.0%}")
    if problems and not force:
        print("Refusing to record this pull (re-run with --force if the change is real):", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        sys.exit(1)

    for layer, (fields, rows, key) in fetched.items():
        snap_path, hist_path = paths(slug, layer)
        write_csv(snap_path, sort_rows(rows, key), fields)
        prev = last_pull(pulls, layer)
        history = read_csv(hist_path)
        update_history(history, rows, fields, pulled_at, prev["pulled_at"] if prev else None)
        write_csv(hist_path, sort_rows(history, key), history_fields(history, fields))
        pulls.append({"pulled_at": pulled_at, "layer": layer, "row_count": len(rows),
                      "fields": "|".join(fields)})
    write_csv(pulls_path, pulls, PULL_FIELDS)
    print(f"recorded pull {pulled_at}")


# ---------------------------------------------------------------- rebuild

def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True).stdout


def rebuild_history(slug):
    """Regenerate history/*.csv from the git history of snapshots/ and pulls.csv.

    Use after fixing a bug in the history logic: the committed snapshots are the
    source of truth, the history files are derived from them.
    """
    src = SOURCES[slug]
    rel = pathlib.Path("jurisdictions") / slug
    commits = _git("log", "--reverse", "--format=%H", "--", str(rel / "pulls.csv")).split()
    histories = {layer: [] for layer in src["layers"]}
    last_seen_pull = {layer: None for layer in src["layers"]}
    done = 0
    for c in commits:
        pulls = read_csv_text(_git("show", f"{c}:{rel / 'pulls.csv'}"))
        for p in pulls[done:]:
            layer = p["layer"]
            _, key = src["layers"][layer]
            fields = p["fields"].split("|")
            snap = read_csv_text(_git("show", f"{c}:{rel / 'snapshots' / (layer + '.csv')}"))
            update_history(histories[layer], snap, fields, p["pulled_at"], last_seen_pull[layer])
            last_seen_pull[layer] = p["pulled_at"]
        done = len(pulls)
    for layer, history in histories.items():
        _, key = src["layers"][layer]
        fields = read_csv(jdir(slug) / "pulls.csv")[-1]["fields"].split("|")
        write_csv(paths(slug, layer)[1], sort_rows(history, key), history_fields(history, fields))
    print(f"rebuilt {slug} history from {len(commits)} commits / {done} pulls")


# ---------------------------------------------------------------- sqlite

EVENTS_VIEW = """
CREATE VIEW license_events AS
WITH first_pull AS (
  SELECT jurisdiction, layer, min(pulled_at) AS t FROM pulls GROUP BY 1, 2
)
SELECT r.jurisdiction, r.layer, r.record_id, r.version_id,
       r.first_seen AS event_at,
       CASE WHEN r.first_seen = fp.t THEN 'baseline' ELSE 'listed' END AS event,
       r.issue_date, r.expiration_date, r.parcel_id
FROM license_records r JOIN first_pull fp USING (jurisdiction, layer)
UNION ALL
SELECT r.jurisdiction, r.layer, r.record_id, r.version_id,
       r.gone_by AS event_at,
       CASE
         WHEN EXISTS (SELECT 1 FROM license_records n
                      WHERE n.jurisdiction = r.jurisdiction AND n.layer = r.layer
                        AND n.record_id = r.record_id AND n.first_seen = r.gone_by)
           THEN 'changed'
         WHEN r.expiration_date <> '' AND r.expiration_date <= substr(r.gone_by, 1, 10)
           THEN 'delisted_expired'
         ELSE 'delisted_early'
       END AS event,
       r.issue_date, r.expiration_date, r.parcel_id
FROM license_records r
WHERE r.gone_by IS NOT NULL;
"""


def build_sqlite(out):
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("CREATE TABLE pulls (jurisdiction TEXT, layer TEXT, pulled_at TEXT, row_count INTEGER)")
    db.execute(f"""CREATE TABLE license_records (
        jurisdiction TEXT, layer TEXT, record_id TEXT, version_id TEXT,
        {", ".join(c + " TEXT" for c in UNIFIED_COLUMNS)},
        first_seen TEXT, last_seen TEXT, gone_by TEXT, currently_listed INTEGER)""")
    for slug, src in SOURCES.items():
        pulls = read_csv(jdir(slug) / "pulls.csv")
        db.executemany("INSERT INTO pulls VALUES (?,?,?,?)",
                       [(slug, p["layer"], p["pulled_at"], int(p["row_count"])) for p in pulls])
        for layer, (_, key) in src["layers"].items():
            times = [p["pulled_at"] for p in pulls if p["layer"] == layer]
            nxt = dict(zip(times, times[1:]))
            latest = times[-1] if times else None
            history = read_csv(paths(slug, layer)[1])
            if not history:
                continue
            raw = f"{slug}_{layer}".replace("-", "_")
            cols = list(history[0])
            db.execute(f'CREATE TABLE "{raw}" ({", ".join(f"[{c}] TEXT" for c in cols)}, gone_by TEXT)')
            # Open versions have a blank last_seen in the CSV; in SQLite they get the
            # latest pull time, and gone_by (first pull that no longer listed them) stays NULL.
            def seen(h):
                return h["last_seen"] or latest

            def gone(h):
                return nxt.get(h["last_seen"]) if h["last_seen"] else None

            db.executemany(f'INSERT INTO "{raw}" VALUES ({",".join("?" * (len(cols) + 1))})',
                           [[h[c] if c != "last_seen" else seen(h) for c in cols] + [gone(h)]
                            for h in history])
            db.executemany(
                f"INSERT INTO license_records VALUES ({','.join('?' * (len(UNIFIED_COLUMNS) + 8))})",
                [[slug, layer, h.get(key, ""), h["row_hash"],
                  *[h.get(src["columns"].get(c, ""), "") for c in UNIFIED_COLUMNS],
                  h["first_seen"], seen(h), gone(h), int(not h["last_seen"])] for h in history])
    db.executescript("""
        CREATE INDEX ix_records_parcel ON license_records (jurisdiction, parcel_id);
        CREATE INDEX ix_records_address ON license_records (jurisdiction, house_no, street_name);
        CREATE INDEX ix_records_id ON license_records (jurisdiction, record_id);
    """ + EVENTS_VIEW)
    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("built_at", dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("source_repo", "MarylandLegalAid/rental-license-history"),
    ])
    db.commit()
    n = db.execute("SELECT count(*) FROM license_records").fetchone()[0]
    db.execute("VACUUM")
    db.close()
    print(f"wrote {out} ({n} record versions)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rlh")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrape")
    s.add_argument("--jurisdiction", choices=SOURCES, action="append")
    s.add_argument("--force", action="store_true", help="record the pull even if guardrails trip")
    r = sub.add_parser("rebuild-history")
    r.add_argument("--jurisdiction", choices=SOURCES, action="append")
    b = sub.add_parser("build-sqlite")
    b.add_argument("--out", default="build/rental_licenses.sqlite")
    a = ap.parse_args(argv)
    if a.cmd == "scrape":
        for slug in a.jurisdiction or SOURCES:
            scrape(slug, a.force)
    elif a.cmd == "rebuild-history":
        for slug in a.jurisdiction or SOURCES:
            rebuild_history(slug)
    else:
        build_sqlite(a.out)
