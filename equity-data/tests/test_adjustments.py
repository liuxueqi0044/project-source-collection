"""Arithmetic checks use synthetic fixtures only, never delivered market data."""
import copy
import tempfile
import unittest

from equity_data.adjustments import compute_adjusted_bars, materialize_adjustments
from equity_data.store import Store


def bar(day, close, volume=10):
    return {"session_date": day, "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": volume, "currency": "USD", "amount": None}


class AdjustmentTests(unittest.TestCase):
    def test_split_backward_raw_immutable_and_volume_inverse(self):
        bars = [bar("2024-01-02", 100), bar("2024-01-03", 50)]
        untouched = copy.deepcopy(bars)
        actions = [{"action_type": "split", "ex_date": "2024-01-03", "split_ratio": 2,
                    "available_at": "2024-01-01T00:00:00Z"}]
        adjusted = compute_adjusted_bars(bars, actions, as_of="2024-01-03")
        self.assertEqual(adjusted[0]["close"], 50)
        self.assertEqual(adjusted[0]["volume"], 20)
        self.assertEqual(adjusted[1]["close"], 50)
        self.assertEqual(bars, untouched)

    def test_cash_dividend_backward(self):
        bars = [bar("2024-01-02", 100), bar("2024-01-03", 99)]
        actions = [{"action_type": "cash_dividend", "ex_date": "2024-01-03", "cash_amount": 1,
                    "currency": "USD", "previous_session_date": "2024-01-02",
                    "available_at": "2024-01-01T00:00:00Z"}]
        adjusted = compute_adjusted_bars(bars, actions, method="total_return", as_of="2024-01-03")
        self.assertAlmostEqual(adjusted[0]["close"], 99)
        self.assertEqual(adjusted[0]["volume"], 10)
        self.assertEqual(compute_adjusted_bars(bars, actions, as_of="2024-01-03")[0]["close"], 100)

    def test_future_action_does_not_leak(self):
        bars = [bar("2024-01-02", 100), bar("2024-01-03", 100)]
        action = {"action_type": "split", "ex_date": "2024-01-03", "split_ratio": 2,
                  "available_at": "2024-02-01T00:00:00Z"}
        self.assertEqual(compute_adjusted_bars(bars, [action], as_of="2024-01-03")[0]["close"], 100)

    def test_refuse_unsupported_actions_and_unknown_availability(self):
        bars = [bar("2024-01-02", 100), bar("2024-01-03", 100)]
        for action in [{"action_type": "spinoff", "ex_date": "2024-01-03", "available_at": "2024-01-01T00:00:00Z"},
                       {"action_type": "split", "ex_date": "2024-01-03", "split_ratio": 2}]:
            with self.assertRaises(ValueError):
                compute_adjusted_bars(bars, [action], as_of="2024-01-03")

    def test_refuse_missing_currency_or_stale_previous_close(self):
        bars = [bar("2024-01-02", 100), bar("2024-01-04", 99)]
        base = {"action_type": "cash_dividend", "ex_date": "2024-01-04", "cash_amount": 1,
                "available_at": "2024-01-01T00:00:00Z"}
        for extra in [{}, {"currency": "USD"}, {"currency": "USD", "previous_session_date": "2024-01-03"}]:
            with self.assertRaises(ValueError):
                compute_adjusted_bars(bars, [dict(base, **extra)], method="total_return", as_of="2024-01-04")

    def test_same_date_split_dividend_requires_share_basis(self):
        bars = [bar("2024-01-02", 100), bar("2024-01-03", 49)]
        actions = [{"action_type": "split", "ex_date": "2024-01-03", "split_ratio": 2, "available_at": "2024-01-01T00:00:00Z"},
                   {"action_type": "cash_dividend", "ex_date": "2024-01-03", "cash_amount": 1, "currency": "USD", "previous_session_date": "2024-01-02", "available_at": "2024-01-01T00:00:00Z"}]
        with self.assertRaises(ValueError):
            compute_adjusted_bars(bars, actions, method="total_return", as_of="2024-01-03")
        actions[1]["dividend_share_basis"] = "post_split"
        result = compute_adjusted_bars(bars, actions, method="total_return", as_of="2024-01-03")
        self.assertAlmostEqual(result[0]["close"], 49)

    def test_duplicate_bars_and_actions_refused(self):
        with self.assertRaises(ValueError):
            compute_adjusted_bars([bar("2024-01-02", 100)] * 2, [])
        action = {"action_type": "split", "ex_date": "2024-01-03", "split_ratio": 2}
        with self.assertRaises(ValueError):
            compute_adjusted_bars([bar("2024-01-02", 100), bar("2024-01-03", 50)], [action, action])

    def test_same_economic_action_from_two_sources_refused(self):
        bars = [bar("2024-01-02", 100), bar("2024-01-03", 10)]
        action = {"action_type": "split", "ex_date": "2024-01-03", "split_ratio": 10}
        actions = [{"source": "issuer", "record_id": "test:issuer", "available_at": "2024-01-01T00:00:00Z", "data": action},
                   {"source": "vendor", "record_id": "test:vendor", "available_at": "2024-01-01T00:00:00Z", "data": action}]
        with self.assertRaisesRegex(ValueError, "Multiple sources"):
            compute_adjusted_bars(bars, actions, as_of="2024-01-03")

    def test_payment_date_cannot_substitute_for_ex_date(self):
        bars = [bar("2024-01-02", 100), bar("2024-01-03", 99)]
        action = {"event_date": "2024-01-03", "available_at": "2024-01-01T00:00:00Z",
                  "data": {"action_type": "cash_dividend", "ex_date": None, "payment_date": "2024-01-03",
                           "cash_amount": 1, "currency": "USD", "previous_session_date": "2024-01-02"}}
        with self.assertRaises(ValueError):
            compute_adjusted_bars(bars, [action], method="total_return", as_of="2024-01-03")

    def test_materialized_prices_preserve_originals_identity_and_lineage(self):
        with tempfile.TemporaryDirectory() as directory, Store(directory) as store:
            raw = store.put_raw(b"SYNTHETIC bars and split", source="test", url="test://fixture", retrieved_at="2024-01-04T00:00:00Z")
            for day, close in (("2024-01-02", 100), ("2024-01-03", 50)):
                store.put_record("daily_bars", "test:stock|" + day, bar(day, close), source="test", raw_id=raw["raw_id"],
                                 company_id="test:co", security_id="test:stock", currency="USD", event_date=day,
                                 available_at=day + "T22:00:00Z", availability_basis="synthetic_bar_end")
            store.put_record("corporate_actions", "test:split", {"action_type": "split", "ex_date": "2024-01-03", "split_ratio": 2},
                             source="test", raw_id=raw["raw_id"], company_id="test:co", security_id="test:stock",
                             available_at="2024-01-01T00:00:00Z", availability_basis="synthetic_announcement")
            before = store.query("daily_bars")
            result = materialize_adjustments(store, "test:stock", as_of="2024-01-04")
            self.assertEqual(result["records"], 2)
            self.assertEqual(store.query("daily_bars"), before)
            adjusted = store.query("adjusted_bars")
            self.assertEqual(adjusted[0]["company_id"], "test:co")
            self.assertEqual(adjusted[0]["data"]["close"], 50)
            self.assertEqual(adjusted[0]["data"]["raw_record_id"], before[0]["record_id"])
            self.assertEqual(len(adjusted[0]["data"]["corporate_action_record_ids"]), 1)
            self.assertEqual(adjusted[0]["availability_basis"], "system_calculation_time")
            repeated = materialize_adjustments(store, "test:stock", as_of="2024-01-04")
            self.assertEqual(repeated["raw_id"], result["raw_id"])
            self.assertEqual(store.query("adjusted_bars", latest=False), adjusted)
            self.assertEqual(store.db.execute("SELECT count(*) FROM records WHERE dataset='adjusted_bars'").fetchone()[0], 2)
            vendor_raw = store.put_raw(b"SYNTHETIC duplicate vendor split", source="vendor", url="test://vendor")
            store.put_record("corporate_actions", "vendor:split", {"action_type": "split", "ex_date": "2024-01-03", "split_ratio": 2},
                             source="vendor", raw_id=vendor_raw["raw_id"], security_id="test:stock",
                             available_at="2024-01-01T00:00:00Z", availability_basis="synthetic_announcement")
            with self.assertRaisesRegex(ValueError, "Multiple sources"):
                materialize_adjustments(store, "test:stock", as_of="2024-01-04")
            selected = materialize_adjustments(store, "test:stock", as_of="2024-01-04", action_source="test")
            self.assertEqual(selected["records"], 2)
            selected_rows = [r for r in store.query("adjusted_bars") if r["data"].get("action_source") == "test"]
            self.assertEqual(selected_rows[0]["data"]["close"], 50)

    def test_no_raw_bars_creates_no_derived_evidence(self):
        with tempfile.TemporaryDirectory() as directory, Store(directory) as store:
            result = materialize_adjustments(store, "test:missing", as_of="2024-01-04")
            self.assertEqual(result["status"], "no_data")
            self.assertEqual(result["records"], 0)
            self.assertIsNone(result["raw_id"])
            self.assertEqual(store.db.execute("SELECT count(*) FROM raw_blobs").fetchone()[0], 0)
            self.assertEqual(store.query("adjusted_bars", latest=False), [])


if __name__ == "__main__":
    unittest.main()
