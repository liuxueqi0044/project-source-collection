"""Entirely synthetic evidence generated in temporary directories."""
import hashlib
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

from equity_data.store import DATASETS, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(self.temporary.name)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def add(self, value, *, published="2024-02-01T21:01:00-05:00", retrieved="2026-01-01T00:00:00Z", available=None):
        raw = self.store.put_raw(("SYNTHETIC:" + str(value)).encode(), source="test", url="test://fixture", retrieved_at=retrieved)
        return self.store.put_record("financial_facts", "co|revenue|2023", {"value": value}, source="test", raw_id=raw["raw_id"], company_id="test:co", published_at=published, publication_precision="second", publication_timezone="America/New_York", available_at=available or published, availability_basis="synthetic_fixture_timestamp", period_end="2023-12-31", unit="USD", currency="USD")

    def test_raw_exact_hash_and_repeat_acquisition(self):
        content = b"\xef\xbb\xbfSYNTHETIC\r\n\x00\xff"
        first = self.store.put_raw(content, source="test", url="test://fixture")
        second = self.store.put_raw(content, source="test", url="test://fixture")
        self.assertEqual(Path(first["path"]).read_bytes(), content)
        self.assertEqual(first["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(first["raw_id"], second["raw_id"])
        self.assertNotEqual(first["retrieval_id"], second["retrieval_id"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM raw_blobs").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM raw_retrievals").fetchone()[0], 2)

    def test_backfill_disclosure_vs_acquisition(self):
        self.add(100)
        self.assertEqual(self.store.query("financial_facts", as_of="2023-12-31"), [])
        self.assertEqual(self.store.query("financial_facts", as_of="2024-02-02T02:00:59Z"), [])
        self.assertEqual(self.store.query("financial_facts", as_of="2024-02-02T02:01:00Z")[0]["data"]["value"], 100)
        self.assertEqual(self.store.query("financial_facts", as_of="2024-02-03", known_at="2024-02-03"), [])
        self.assertEqual(len(self.store.query("financial_facts", as_of="2024-02-03", known_at="2026-01-01")), 1)

    def test_correction_distinct_disclosure_and_lineage(self):
        original = self.add(100)
        corrected = self.add(95, published="2024-03-01T12:00:00Z", retrieved="2026-01-02T00:00:00Z")
        self.assertNotEqual(original, corrected)
        self.assertEqual(self.store.query("financial_facts", as_of="2024-02-20")[0]["data"]["value"], 100)
        rows = self.store.query("financial_facts", as_of="2024-03-01")
        self.assertEqual(rows[0]["data"]["value"], 95)
        self.assertEqual(rows[0]["supersedes"], original)
        self.assertEqual(len(self.store.query("financial_facts", latest=False)), 2)

    def test_ambiguous_revision_fails_closed_until_acquired(self):
        self.add(100)
        self.add(95, retrieved="2026-01-02T00:00:00Z")
        self.assertEqual(self.store.query("financial_facts", as_of="2024-02-20"), [])
        self.assertEqual(self.store.query("financial_facts", as_of="2026-01-01")[0]["data"]["value"], 100)
        self.assertEqual(self.store.query("financial_facts", as_of="2026-01-02")[0]["data"]["value"], 95)
        # What our system actually held before acquiring correction remains recoverable.
        self.assertEqual(self.store.query("financial_facts", as_of="2024-02-20", known_at="2026-01-01")[0]["data"]["value"], 100)

    def test_repeat_records_keep_new_retrieval_evidence(self):
        first = self.add(100)
        repeated = self.add(100, retrieved="2026-01-02T00:00:00Z")
        self.assertEqual(first, repeated)
        self.assertEqual(len(self.store.query("financial_facts", latest=False)), 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM record_evidence").fetchone()[0], 2)

    def test_reversion_a_b_a_is_new_observed_version(self):
        first = self.add(100, retrieved="2026-01-01T00:00:00Z")
        middle = self.add(95, retrieved="2026-01-02T00:00:00Z")
        reverted = self.add(100, retrieved="2026-01-03T00:00:00Z")
        self.assertEqual(len({first, middle, reverted}), 3)
        self.assertEqual(len(self.store.query("financial_facts", latest=False)), 3)
        self.assertEqual(self.store.query("financial_facts", as_of="2026-01-02")[0]["data"]["value"], 95)
        latest = self.store.query("financial_facts", as_of="2026-01-03")[0]
        self.assertEqual(latest["data"]["value"], 100)
        self.assertEqual(latest["supersedes"], middle)
        self.assertEqual(self.add(100, retrieved="2026-01-04T00:00:00Z"), reverted)

    def test_dated_disclosure_archive_replay_is_idempotent(self):
        first = self.add(100, published="2024-02-01T00:00:00Z")
        second = self.add(95, published="2024-03-01T00:00:00Z", retrieved="2026-01-02T00:00:00Z")
        self.assertEqual(self.add(100, published="2024-02-01T00:00:00Z", retrieved="2026-01-03T00:00:00Z"), first)
        self.assertEqual(self.add(95, published="2024-03-01T00:00:00Z", retrieved="2026-01-03T00:00:00Z"), second)
        self.assertEqual(len(self.store.query("financial_facts", latest=False)), 2)
        self.assertEqual(self.store.query("financial_facts", as_of="2024-04-01")[0]["data"]["value"], 95)

    def test_current_copy_reversion_is_not_historical_replay(self):
        ids = []
        for value, available, acquired in ((100, "2024-02-01T00:00:00Z", "2026-01-01T00:00:00Z"),
                                           (95, "2024-03-01T00:00:00Z", "2026-01-02T00:00:00Z"),
                                           (100, "2024-02-01T00:00:00Z", "2026-01-03T00:00:00Z")):
            raw = self.store.put_raw(("SYNTHETIC current copy " + str(value)).encode(), source="test", url="test://copy", retrieved_at=acquired)
            ids.append(self.store.put_record("documents", "mutable", {"value": value}, source="test", raw_id=raw["raw_id"], published_at=available,
                         publication_precision="second", available_at=available, availability_basis="current_copy_first_observed"))
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(self.store.query("documents")[0]["data"]["value"], 100)

    def test_same_precision_reversion_uses_sequence_tiebreaker(self):
        for value in (100, 95, 100):
            self.add(value, retrieved="2026-01-01T00:00:00Z")
        self.assertEqual(len(self.store.query("financial_facts", latest=False)), 3)
        self.assertEqual(self.store.query("financial_facts", as_of="2026-01-01")[0]["data"]["value"], 100)

    def test_concurrent_same_version_appends_once(self):
        raw = self.store.put_raw(b"SYNTHETIC concurrent version", source="test", url="test://concurrent")
        barrier = Barrier(2)
        def write():
            with Store(self.temporary.name) as other:
                barrier.wait(timeout=5)
                return other.put_record("filings", "same_pointer", {"value": 100}, source="test", raw_id=raw["raw_id"])
        with ThreadPoolExecutor(max_workers=2) as executor:
            identifiers = list(executor.map(lambda _: write(), range(2)))
        self.assertEqual(identifiers[0], identifiers[1])
        self.assertEqual(len(self.store.query("filings", latest=False)), 1)

    def test_concurrent_same_blob_is_complete_and_retains_both_acquisitions(self):
        content = b"SYNTHETIC concurrent exact bytes\r\n" * 50000
        barrier = Barrier(2)
        def write():
            with Store(self.temporary.name) as other:
                barrier.wait(timeout=5)
                return other.put_raw(content, source="concurrent", url="test://concurrent-blob")
        with ThreadPoolExecutor(max_workers=2) as executor:
            acquisitions = list(executor.map(lambda _: write(), range(2)))
        self.assertEqual(acquisitions[0]["raw_id"], acquisitions[1]["raw_id"])
        self.assertEqual(Path(acquisitions[0]["path"]).read_bytes(), content)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM raw_retrievals").fetchone()[0], 2)

    def test_unknown_public_time_not_period_end(self):
        raw = self.store.put_raw(b"SYNTHETIC unknown time", source="test", url="test://unknown")
        self.store.put_record("financial_facts", "unknown", {"value": None, "missing_reason": "not disclosed"}, source="test", raw_id=raw["raw_id"], period_end="2020-12-31")
        self.assertEqual(self.store.query("financial_facts", as_of="2030-01-01"), [])
        self.assertIsNone(self.store.query("financial_facts")[0]["available_at"])

    def test_immutable_records_and_raw(self):
        self.add(100)
        for sql in ("UPDATE records SET unit='USDm'", "DELETE FROM records", "DELETE FROM raw_retrievals", "UPDATE raw_blobs SET byte_size=0"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.db.execute(sql)
        self.store.db.rollback()

    def test_sources_are_not_merged(self):
        self.add(100)
        raw = self.store.put_raw(b"SYNTHETIC conflict", source="test2", url="test://other")
        self.store.put_record("financial_facts", "co|revenue|2023", {"value": 1}, source="test2", raw_id=raw["raw_id"], company_id="test:co")
        self.assertEqual(len(self.store.query("financial_facts")), 2)

    def test_checkpoint_and_missing_raw(self):
        self.assertIsNone(self.store.checkpoint("cursor"))
        self.store.checkpoint("cursor", {"offset": 4})
        self.assertEqual(self.store.checkpoint("cursor"), {"offset": 4})
        with self.assertRaises(ValueError):
            self.store.put_record("filings", "bad", {}, source="test", raw_id="absent")

    def test_named_dataset_views_are_read_only_all_versions(self):
        self.add(100)
        self.add(95, published="2024-03-01T12:00:00Z")
        views = {r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        self.assertTrue(set(DATASETS).issubset(views))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM financial_facts").fetchone()[0], 2)
        with self.assertRaises(sqlite3.OperationalError):
            self.store.db.execute("DELETE FROM financial_facts")

    def test_requires_offset_and_availability_basis(self):
        with self.assertRaises(ValueError):
            self.store.put_raw(b"SYNTHETIC", source="test", url="test://x", retrieved_at="2020-01-01")
        raw = self.store.put_raw(b"SYNTHETIC", source="test", url="test://x")
        with self.assertRaises(ValueError):
            self.store.put_record("filings", "x", {}, source="test", raw_id=raw["raw_id"], available_at="2020-01-01T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
