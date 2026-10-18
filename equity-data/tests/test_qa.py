"""QA requirements against synthetic temporary fixtures, never sample evidence."""
import tempfile
import unittest

from equity_data.qa import validate
from equity_data.store import Store


class QATests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(self.temporary.name)
        self.config = {"start": "2024-01-02", "end": "2024-01-03", "securities": [{"security_id": "test:stock", "company_id": "test:co", "exchange": "XNYS"}]}

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def put(self, dataset, key, data, source="test", **metadata):
        raw = self.store.put_raw(("SYNTHETIC " + dataset + key + source).encode(), source=source, url="test://fixture")
        base = dict(source=source, raw_id=raw["raw_id"], source_locator="synthetic fixture", company_id="test:co", security_id="test:stock")
        base.update(metadata)
        return self.store.put_record(dataset, key, data, **base)

    def session(self, day, is_session=True):
        self.put("calendar_sessions", "XNYS|" + day, {"exchange": "XNYS", "date": day, "is_session": is_session})

    def bar(self, day, source="test", **overrides):
        values = {"date": day, "open": 100, "high": 110, "low": 90, "close": 105,
                  "volume": 1000, "turnover": 105000, "feed": "sip", "session": "regular"}
        values.update(overrides)
        self.put("daily_bars", day, values, source=source, event_date=day, currency="USD")

    def fact(self, source, value, *, scope="entire_filing_entity", unit="USD", accession="test:filing", available="2024-02-01T00:00:00Z"):
        self.put("financial_facts", "fact", {"taxonomy": "us-gaap", "concept": "Revenues", "value": value,
                 "accession": accession, "accounting_basis": "US-GAAP", "consolidation_scope": scope},
                 source=source, period_start="2023-01-01", period_end="2023-12-31", unit=unit,
                 available_at=available, availability_basis="synthetic_source_timestamp")

    def codes(self, config=None):
        return {finding["code"] for finding in validate(self.store, config)["findings"]}

    def test_missing_open_session_detected_without_filling(self):
        self.session("2024-01-02")
        self.session("2024-01-03")
        self.bar("2024-01-02")
        result = validate(self.store, self.config)
        gap = next(f for f in result["findings"] if f["code"] == "SESSION_BAR_GAPS")
        self.assertEqual(gap["missing_count"], 1)
        self.assertEqual(gap["first_10"], ["2024-01-03"])
        self.assertEqual(len(self.store.query("daily_bars")), 1)

    def test_ohlc_and_volume_anomalies_detected(self):
        self.bar("2024-01-02", low=108, volume=-1)
        codes = self.codes()
        self.assertIn("INVALID_OHLC", codes)
        self.assertIn("NEGATIVE_VOLUME", codes)

    def test_malformed_price_reports_error_without_crashing(self):
        self.bar("2024-01-02", close="not-a-number")
        result = validate(self.store)
        self.assertEqual(result["status"], "failed")
        self.assertIn("INVALID_OHLC", {f["code"] for f in result["findings"]})

    def test_cross_source_same_context_conflict_detected(self):
        self.fact("source-a", 100, accession="a:source-filing-id")
        self.fact("source-b", 101, accession="b:source-filing-id")
        self.assertIn("CROSS_SOURCE_FACT_CONFLICT", self.codes())

    def test_different_scope_or_unit_not_false_conflict(self):
        self.fact("source-a", 100, scope="entire_filing_entity")
        self.fact("source-b", 25, scope="segment:cloud")
        self.fact("source-c", 0.0001, unit="USD millions")
        codes = self.codes()
        self.assertNotIn("CROSS_SOURCE_FACT_CONFLICT", codes)
        self.assertNotIn("FACT_CONTEXT_CONFLICT", codes)

    def test_different_disclosure_vintage_not_cross_source_conflict(self):
        self.fact("source-a", 100, accession="original", available="2024-02-01T00:00:00Z")
        self.fact("source-b", 101, accession="amendment", available="2024-03-01T00:00:00Z")
        self.assertNotIn("CROSS_SOURCE_FACT_CONFLICT", self.codes())

    def test_different_market_feeds_not_silently_compared(self):
        self.bar("2024-01-02", source="source-a", feed="iex", close=104)
        self.bar("2024-01-02", source="source-b", feed="sip", close=105)
        self.assertNotIn("CROSS_SOURCE_BAR_CONFLICT", self.codes())

    def test_weekend_requested_end_is_not_source_lag(self):
        config = dict(self.config, start="2024-01-05", end="2024-01-07")
        self.session("2024-01-05")
        self.session("2024-01-06", False)
        self.session("2024-01-07", False)
        self.bar("2024-01-05")
        codes = self.codes(config)
        self.assertNotIn("BAR_SOURCE_LAG", codes)
        self.assertNotIn("SESSION_BAR_GAPS", codes)


if __name__ == "__main__":
    unittest.main()
