# Rental License History

Public rental-license data in Maryland is usually published only as a **snapshot of what is valid
today**. Baltimore County's GIS service, for example, drops a license the day it expires and keeps no
history, so there is no public way to tell whether a property was licensed on a past date.

This repo pulls the published data every day and keeps every version, so history builds up from the
first pull onward (**Baltimore County: 2026-11-12**). It can't recover anything from before that
date. Older history needs a Public Information Act request to the licensing agency.

## What's here

```
jurisdictions/<slug>/
  snapshots/<layer>.csv   today's published data, sorted; overwritten daily (git keeps every day)
  history/<layer>.csv     one row per record version with first_seen / last_seen
  pulls.csv               one row per layer per pull: time, row count, field list
rlh/                      Python package (standard library only)
tests/
.github/workflows/daily-pull.yml
```

| Jurisdiction | Source | Layers |
|---|---|---|
| `baltimore-county` | `bcgisapps.baltimorecountymd.gov/.../RentalLicense/MapServer` | `licenses` (0), `exemptions` (1), `multifamily` (2) |

To add a jurisdiction, add an entry to `rlh/sources.py`. Non-ArcGIS sources need a fetcher alongside
`rlh/arcgis.py`.

## The daily job

`.github/workflows/daily-pull.yml` runs at 11:17 UTC and can also be started by hand from the
Actions tab. It:

1. runs the tests;
2. pulls every layer (`python -m rlh scrape`);
3. commits `jurisdictions/`;
4. builds `rental_licenses.sqlite` and attaches it to the
   [`data-latest` release](../../releases/tag/data-latest), replacing the previous file.

**Guardrails.** A pull is refused, and nothing is written, if a layer's row count moves by more
than 5% from the previous pull, a layer's field list changes, or a layer comes back empty. The run
then fails, and GitHub emails whoever last edited the workflow's schedule. If the change is real,
re-run the workflow by hand with **force** checked.

**Gaps.** Scheduled runs can be delayed or, rarely, skipped. `pulls.csv` records when each pull
actually happened. A record that disappears is known to have gone *between* its `last_seen` pull and
the next pull (`gone_by`), not on an exact day.

## Using the data

Download the SQLite file from the `data-latest` release:
`gh release download data-latest -R MarylandLegalAid/rental-license-history`. Or build it locally
with `python -m rlh build-sqlite`.

**`license_records`**: one row per record version, across all jurisdictions and layers.

| Column | Meaning |
|---|---|
| `jurisdiction`, `layer`, `record_id` | e.g. `baltimore-county`, `licenses`, `RHR-2024-03598` |
| `version_id` | Hash of the record's full content. A new version starts whenever any published field changes. |
| `house_no`, `street_*`, `unit_type`, `unit`, `city`, `zip`, `parcel_id`, `lon`, `lat` | Location. In Baltimore County, `parcel_id` is `TAXPIN`, the last 10 digits of the SDAT account ID. |
| `issue_date`, `expiration_date`, `status`, `exemption_reason`, `total_units`, `complex_name` | As published |
| `first_seen`, `last_seen` | First and last pull that listed this version (UTC timestamps) |
| `gone_by` | First pull that no longer listed it. NULL while still listed. |
| `currently_listed` | 1 if listed in the latest pull |

**`license_events`**: a view derived from `license_records`.

| `event` | Meaning |
|---|---|
| `baseline` | Listed in the first pull for that layer |
| `listed` | Appeared after the first pull: a new license, a renewal, or a changed record |
| `changed` | This version was replaced by a new version of the same `record_id` |
| `delisted_expired` | Disappeared on or after its expiration date |
| `delisted_early` | Disappeared before its expiration date: possible revocation, withdrawal, or data correction |

`pulls` lists every pull. The raw per-layer tables (e.g. `baltimore_county_licenses`) keep every
source field.

### Was a property licensed on a given date?

There are two kinds of evidence, and they answer different questions:

- **Stated validity:** the county's own `issue_date` ≤ D ≤ `expiration_date` on any version of a
  license for that parcel. This works for dates before the history began, but only for licenses that
  were still listed on or after the first pull.
- **Observed listing:** a version was actually published on the pull nearest D
  (`first_seen` ≤ D ≤ `last_seen`). This is only available from the first pull onward.

```sql
SELECT layer, record_id, status, issue_date, expiration_date, first_seen, last_seen, gone_by,
       (issue_date <= :d AND :d <= expiration_date) AS stated_valid_on_d
FROM license_records
WHERE jurisdiction = 'baltimore-county' AND parcel_id = :taxpin
ORDER BY first_seen;
```

**What "no license found" means.** No row for a parcel doesn't prove the property was unlicensed. It
can also mean an address or parcel mismatch, a unit-level vs building-level license, or an expiry
before the history began.

### Caveats

- **The source data has errors.** Some expiration dates are typos (e.g. year 2222), and one license
  number appears on two rows with slightly different addresses. Records are kept exactly as published.
- **Every version is separate.** Any change to any published field, including a corrected suffix or
  re-geocoded coordinates, creates a new version. Use `record_id` to follow one license across
  versions.
- **The history files are derived.** `python -m rlh rebuild-history` regenerates `history/*.csv` from
  git history. Use it after fixing a bug in the history logic. The committed snapshots are the source
  of truth.

## Local development

Python 3.12+, with no third-party dependencies.

```sh
python -m unittest discover -s tests -t .
python -m rlh scrape --jurisdiction baltimore-county   # writes jurisdictions/; don't commit test pulls
python -m rlh build-sqlite --out build/rental_licenses.sqlite
```
