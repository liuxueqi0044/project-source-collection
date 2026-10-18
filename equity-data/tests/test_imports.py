"""Authorized-import guards; all evidence is synthetic and temporary."""
import json
import tempfile
import unittest
from pathlib import Path

from equity_data.public_docs import import_materials
from equity_data.store import Store


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "store")
        (self.root / "evidence.txt").write_text("SYNTHETIC TEST evidence, value 10", encoding="utf-8")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def entry(self, **overrides):
        entry = dict(dataset="guidance", record_key="test:co|revenue|2025", source="synthetic", source_url="test://evidence",
                     source_locator="paragraph 1", evidence_path="evidence.txt", authorization_note="synthetic test only",
                     rights="authorized", classification="public_or_authorized_nonconfidential", knowledge_type="company_statement",
                     data={"metric": "revenue", "low": 10, "high": 12}, company_id="test:co")
        entry.update(overrides)
        return entry

    def write(self, *entries):
        path = self.root / "import.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
        return path

    def test_idempotence_and_unknown_availability(self):
        path = self.write(self.entry())
        first, second = import_materials(self.store, path), import_materials(self.store, path)
        self.assertEqual(first["record_ids"], second["record_ids"])
        self.assertEqual(len(self.store.query("guidance", latest=False)), 1)
        self.assertEqual(self.store.query("guidance", as_of="2030-01-01"), [])

    def test_reject_confidential_without_partial_inserts(self):
        for overrides in ({"rights": "unknown"}, {"classification": "leaked"}, {"knowledge_type": "unclassified"}):
            with self.assertRaises(ValueError):
                import_materials(self.store, self.write(self.entry(), self.entry(record_key="bad", **overrides)))
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM raw_blobs").fetchone()[0], 0)

    def test_survey_requires_scope_and_method(self):
        with self.assertRaises(ValueError):
            import_materials(self.store, self.write(self.entry(knowledge_type="survey_estimate")))
        result = import_materials(self.store, self.write(self.entry(knowledge_type="survey_estimate", data={"sample_scope": "synthetic 2 stores", "methodology": "synthetic interview", "value": 10})))
        self.assertEqual(result["count"], 1)

    def test_inference_requires_inputs(self):
        with self.assertRaises(ValueError):
            import_materials(self.store, self.write(self.entry(knowledge_type="system_inference")))

    def test_available_time_requires_evidence(self):
        with self.assertRaises(ValueError):
            import_materials(self.store, self.write(self.entry(available_at="2025-01-01T12:00:00Z")))

    def test_invalid_timestamp_batch_has_no_durable_inserts(self):
        bad = self.entry(record_key="bad", available_at="2025-01-01", availability_basis="claimed_source_release", availability_evidence="SYNTHETIC invalid day only")
        with self.assertRaises(ValueError):
            import_materials(self.store, self.write(self.entry(), bad))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM raw_blobs").fetchone()[0], 0)
        self.assertEqual(self.store.query("guidance", latest=False), [])

    def test_invalid_published_clock_is_not_silently_saved(self):
        bad = self.entry(published_at="2025-01-01T25:61:00", publication_precision="second", publication_timezone="America/New_York")
        with self.assertRaises(ValueError):
            import_materials(self.store, self.write(bad))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM raw_blobs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
