import pathlib
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from rlh import cli
from rlh.history import update_history, read_csv

F = ["ID", "ADDR", "EXPIRATION_DATE"]


def row(i, addr="1 MAIN ST", exp="2030-01-01"):
    return {"ID": i, "ADDR": addr, "EXPIRATION_DATE": exp}


class UpdateHistoryTest(unittest.TestCase):
    def test_versions(self):
        h = []
        update_history(h, [row("A"), row("B"), row("B")], F, "t1", None)
        self.assertEqual(len(h), 2, "exact duplicate rows collapse")
        update_history(h, [row("A"), row("B", addr="2 MAIN ST")], F, "t2", "t1")
        a, b_old, b_new = sorted(h, key=lambda x: (x["ID"], x["first_seen"]))
        self.assertEqual((a["first_seen"], a["last_seen"]), ("t1", ""), "open: last_seen blank")
        self.assertEqual((b_old["first_seen"], b_old["last_seen"]), ("t1", "t1"))
        self.assertEqual((b_new["first_seen"], b_new["last_seen"]), ("t2", ""))
        # A disappears at t3 and comes back at t4 with identical content: new version.
        update_history(h, [row("B", addr="2 MAIN ST")], F, "t3", "t2")
        update_history(h, [row("A"), row("B", addr="2 MAIN ST")], F, "t4", "t3")
        a_versions = [x for x in h if x["ID"] == "A"]
        self.assertEqual([(x["first_seen"], x["last_seen"]) for x in a_versions],
                         [("t1", "t2"), ("t4", "")])


class EndToEndTest(unittest.TestCase):
    """Scrape fake pulls into a temp git repo, then check SQLite events and rebuild."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(self.root), "config", k, v], check=True)
        self.patches = [
            mock.patch.object(cli, "ROOT", self.root),
            mock.patch.dict(cli.SOURCES, {"test": {
                "kind": "arcgis", "base_url": "x",
                "layers": {"licenses": (0, "ID")},
                "columns": {"street_name": "ADDR", "expiration_date": "EXPIRATION_DATE"},
            }}, clear=True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def pull(self, rows, when):
        clock = mock.patch.object(cli.dt, "datetime", wraps=cli.dt.datetime)
        with mock.patch.object(cli.arcgis, "fetch_layer", return_value=(F, rows)), clock as c:
            c.now.return_value = cli.dt.datetime.fromisoformat(when + "+00:00")
            cli.scrape("test")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", when], check=True)

    def test_events_and_rebuild(self):
        base = [row(f"R{i}") for i in range(200)]
        self.pull(base + [row("EXP", exp="2026-01-01"), row("EARLY"), row("CHG")], "2026-01-01T11:00:00")
        self.pull(base + [row("CHG", addr="9 ELM ST"), row("NEW")], "2026-01-02T11:00:00")

        db_path = self.root / "out.sqlite"
        cli.build_sqlite(db_path)
        db = sqlite3.connect(db_path)
        ev = dict(db.execute("SELECT record_id, event FROM license_events "
                             "WHERE event <> 'baseline' ORDER BY 1, 2").fetchall())
        # 'CHG' has both a 'changed' (old version) and a 'listed' (new version) row; check the former.
        changed = db.execute("SELECT count(*) FROM license_events WHERE record_id='CHG' AND event='changed'").fetchone()[0]
        self.assertEqual(changed, 1)
        self.assertEqual(ev["EXP"], "delisted_expired")
        self.assertEqual(ev["EARLY"], "delisted_early")
        self.assertEqual(ev["NEW"], "listed")
        current = db.execute("SELECT count(*) FROM license_records WHERE currently_listed=1").fetchone()[0]
        self.assertEqual(current, 202)

        hist = self.root / "jurisdictions/test/history/licenses.csv"
        before = hist.read_text()
        hist.unlink()
        cli.rebuild_history("test")
        self.assertEqual(hist.read_text(), before)

    def test_guardrail_refuses_big_drop(self):
        self.pull([row(f"R{i}") for i in range(200)], "2026-01-01T11:00:00")
        with mock.patch.object(cli.arcgis, "fetch_layer", return_value=(F, [row("R1")])):
            with self.assertRaises(SystemExit):
                cli.scrape("test")
        self.assertEqual(len(read_csv(self.root / "jurisdictions/test/pulls.csv")), 1)


if __name__ == "__main__":
    unittest.main()
