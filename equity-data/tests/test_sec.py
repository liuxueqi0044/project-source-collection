"""Entirely synthetic SEC fixtures; no live network or observed sample claims."""
import hashlib
import json
import tempfile
import unittest

from equity_data.sec import (_columns_to_rows, _document_url, _header_metadata, _selected_filings,
                             _time_evidence, collect_sec, financial_snapshot)
from equity_data.store import Store


class RecordingStore:
    def __init__(self):
        self.records = []
        self.issues = []
        self.checkpoints = {}

    def put_record(self, dataset, record_key, data, **kwargs):
        entry = {"dataset": dataset, "record_key": record_key, "data": data, **kwargs}
        self.records.append(entry)
        return str(len(self.records))

    def issue(self, code, message, **kwargs):
        self.issues.append({"code": code, "message": message, **kwargs})

    def checkpoint(self, key, value=None):
        if value is not None:
            self.checkpoints[key] = value
        return self.checkpoints.get(key)


class FixtureClient:
    def __init__(self, fixtures, store=None):
        self.fixtures = fixtures
        self.calls = []
        self.store = store

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.fixtures[url]
        if isinstance(value, Exception):
            raise value
        content = value if isinstance(value, bytes) else json.dumps(value).encode()
        if self.store:
            return content, self.store.put_raw(content, source="SEC EDGAR", url=url, retrieved_at="2026-10-09T01:00:00Z")
        digest = hashlib.sha256(content).hexdigest()
        return content, {"raw_id": digest, "sha256": digest, "url": url,
                         "source": "SEC EDGAR", "retrieved_at": "2026-10-09T01:00:00Z"}


def columns(rows):
    keys = set().union(*(r.keys() for r in rows)) if rows else set()
    return {key: [r.get(key) for r in rows] for key in keys}


class SecTests(unittest.TestCase):
    def test_columnar_archives_are_parsed(self):
        self.assertEqual(_columns_to_rows({"accessionNumber": ["a", "b"], "form": ["10-K"]}),
                         [{"accessionNumber": "a", "form": "10-K"}, {"accessionNumber": "b", "form": None}])

    def test_after_hours_header_and_dst(self):
        metadata = _header_metadata(b"&lt;ACCEPTANCE-DATETIME&gt;20240801163028\n&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;FILENAME&gt;release.htm\n")
        evidence = _time_evidence({"filingDate": "2024-08-01", "acceptanceDateTime": "2024-08-01T16:30:28.000Z"}, metadata)
        self.assertEqual(evidence["published_at"], "2024-08-01T20:30:28Z")
        self.assertEqual(evidence["available_at"], "2024-08-02T04:00:00Z")
        self.assertEqual(metadata["documents"][0]["filename"], "release.htm")
        winter = _time_evidence({"filingDate": "2024-01-15"}, {"accepted_local_clock": "20240115160100"})
        self.assertEqual(winter["published_at"], "2024-01-15T21:01:00Z")

    def test_unverified_api_clock_not_used_for_intraday(self):
        evidence = _time_evidence({"filingDate": "2024-08-01", "acceptanceDateTime": "2024-08-01T16:30:28.000Z", "reportDate": "2024-06-30"})
        self.assertEqual(evidence["published_at"], "2024-08-01")
        self.assertEqual(evidence["publication_precision"], "day")
        self.assertEqual(evidence["available_at"], "2024-08-02T04:00:00Z")
        self.assertIsNone(_time_evidence({"reportDate": "2024-06-30"})["available_at"])
        self.assertIsNone(_time_evidence({"form": "CORRESP", "filingDate": "2024-08-01"})["available_at"])

    def test_adjusted_filing_day_not_earlier_than_header_acceptance(self):
        evidence = _time_evidence({"filingDate": "2024-08-01"}, {"accepted_local_clock": "20240802163028"})
        self.assertEqual(evidence["available_at"], "2024-08-03T04:00:00Z")

    def test_sample_includes_amendment_and_delisting(self):
        rows = [{"accessionNumber": str(i), "form": form, "filingDate": "2024-10-01"}
                for i, form in enumerate(["10-K", "10-Q", "8-K", "10-K/A", "25-NSE"])]
        selected = _selected_filings(rows, 2)
        self.assertEqual({r["form"] for r in selected}, {"10-K/A", "25-NSE"})

    def test_required_accessions_and_confined_xsl_paths(self):
        rows = [{"accessionNumber": "a", "form": "8-K", "filingDate": "2024-05-22"},
                {"accessionNumber": "b", "form": "10-K", "filingDate": "2024-11-22"}]
        self.assertEqual(_selected_filings(rows, 1, ["a"])[0]["accessionNumber"], "a")
        self.assertEqual(_document_url("https://www.sec.gov/Archives/base/", "xslF25X02/primary_doc.xml"),
                         "https://www.sec.gov/Archives/base/xslF25X02/primary_doc.xml")
        for path in ("../escape.htm", "/escape.htm", "https://other.test/a", "a/../escape.htm", "a\\b"):
            with self.assertRaises(ValueError):
                _document_url("https://www.sec.gov/Archives/base/", path)

    def _collect(self, max_docs=2, primary_failure=False, persistent_store=None, reverse_fact_order=False, required_accessions=None):
        cik = "0000000123"
        a, b = "0000000123-24-000001", "0000000123-24-000002"
        original = {"accessionNumber": a, "form": "10-Q", "filingDate": "2024-08-01", "reportDate": "2024-06-30", "primaryDocument": "quarter.htm"}
        amendment = {"accessionNumber": b, "form": "10-Q/A", "filingDate": "2024-09-01", "reportDate": "2024-06-30", "primaryDocument": "amended.htm"}
        archive_name = "CIK0000000123-submissions-001.json"
        submissions = {"cik": 123, "name": "Synthetic Test Company", "tickers": ["TEST"], "exchanges": ["Nasdaq"],
                       "formerNames": [{"name": "Synthetic Old Name", "from": "2010-01-01", "to": "2020-01-01"}],
                       "filings": {"recent": columns([amendment]), "files": [
                           {"name": archive_name, "filingFrom": "2024-01-01", "filingTo": "2024-08-01"},
                           {"name": "CIK0000000123-submissions-002.json", "filingFrom": "2000-01-01", "filingTo": "2009-01-01"}]}}
        common = {"start": "2024-01-01", "end": "2024-06-30", "fy": 2024, "fp": "Q2"}
        facts = {"facts": {"us-gaap": {
            "Revenues": {"label": "Revenues", "units": {"USD": [
                {**common, "val": 1000000, "accn": a, "filed": "2024-08-01", "form": "10-Q"},
                {**common, "val": 900000, "accn": b, "filed": "2024-09-01", "form": "10-Q/A"},
                {**common, "start": "2024-04-01", "val": 400000, "accn": a, "filed": "2024-08-01", "form": "10-Q"}]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [{"end": "2024-06-30", "val": 1.25, "accn": a, "filed": "2024-08-01", "form": "10-Q"}]}},
            "InventoryNet": {"units": {"USD": [{"end": "2024-06-30", "val": 25000, "accn": a, "filed": "2024-08-01", "form": "10-Q"}]}}
        }, "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [{"end": "2024-07-01", "val": 100, "accn": a, "filed": "2024-08-01", "form": "10-Q"}]}}}}}
        if reverse_fact_order:
            facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"].reverse()
        fixtures = {
            f"https://data.sec.gov/submissions/CIK{cik}.json": submissions,
            "https://data.sec.gov/submissions/" + archive_name: columns([original]),
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json": facts,
        }
        for accession, filename, accepted in [(a, "quarter.htm", "20240801163028"), (b, "amended.htm", "20240901170000")]:
            base = f"https://www.sec.gov/Archives/edgar/data/123/{accession.replace('-', '')}/"
            fixtures[base + accession + "-index-headers.html"] = (
                f"<ACCEPTANCE-DATETIME>{accepted}\n<DOCUMENT>\n<TYPE>10-Q\n<FILENAME>{filename}\n</DOCUMENT>\n"
                "<DOCUMENT>\n<TYPE>EX-99.1\n<FILENAME>release.htm\n<DESCRIPTION>Test release\n</DOCUMENT>\n").encode()
            fixtures[base + filename] = RuntimeError("synthetic primary failure") if primary_failure else b"<html>synthetic report only</html>"
            fixtures[base + "release.htm"] = b"<html>synthetic management statement only</html>"
        config = {"start": "2025-01-01", "end": "2025-12-31", "sec": {"user_agent": "UnitTest test@example.test", "max_documents_per_company": max_docs},
                  "securities": [{"cik": cik, "company_id": "sec:" + cik, "security_id": "local:test:common", "symbol": "TEST", "currency": "USD",
                                  "start": "2024-01-01", "end": "2024-12-31", "security_type": "common_stock"}]}
        if required_accessions is not None:
            config["securities"][0]["required_accessions"] = required_accessions
        store = persistent_store if persistent_store is not None else RecordingStore()
        client = FixtureClient(fixtures, persistent_store)
        result = collect_sec(store, client, config)
        return result, store, client

    def test_archived_pages_amendments_units_and_periods(self):
        result, store, client = self._collect()
        self.assertEqual(result["financial_facts"], 6)
        self.assertEqual(result["filings"], 2)
        self.assertEqual(result["documents"], 6)
        self.assertEqual(result["companies"][0]["archive_pages"], 1)
        self.assertFalse(any("submissions-002" in url for url, _ in client.calls))
        facts = [r for r in store.records if r["dataset"] == "financial_facts"]
        revenues = [r for r in facts if r["data"]["concept"] == "Revenues"]
        self.assertEqual({r["data"]["value"] for r in revenues}, {1000000, 900000, 400000})
        self.assertEqual({r["data"]["duration_days"] for r in revenues}, {182, 91})
        self.assertTrue(all(r["data"]["scale"] == 1 for r in facts))
        self.assertTrue(all(r.get("security_id") is None for r in facts))
        eps = next(r for r in facts if r["unit"] == "USD/shares")
        self.assertEqual(eps["data"]["value"], 1.25)
        self.assertIn("USD~1shares", eps["source_locator"])
        self.assertEqual(eps["currency"], "USD")
        amended = next(r for r in revenues if r["data"]["is_amendment"])
        self.assertIsNone(amended["data"]["amends_accession"])
        self.assertEqual(amended["available_at"], "2024-09-02T04:00:00Z")

    def test_identity_history_has_no_invented_publication(self):
        _, store, _ = self._collect()
        identities = [r for r in store.records if r["dataset"] in ("company_profiles", "securities", "identity_history")]
        self.assertTrue(all(r.get("available_at") is None and r.get("published_at") is None for r in identities))
        self.assertEqual(len(identities), 3)

    def test_document_limit_is_explicit_not_silent_complete(self):
        result, store, _ = self._collect(max_docs=0)
        self.assertEqual(result["documents"], 0)
        self.assertEqual(result["status"], "partial")
        self.assertIn("SEC_DOCUMENT_SAMPLE_LIMIT", {i["code"] for i in store.issues})

    def test_primary_failure_cannot_be_hidden_by_successful_exhibit(self):
        result, store, _ = self._collect(primary_failure=True)
        filings = [r for r in store.records if r["dataset"] == "filings"]
        self.assertTrue(all(not r["data"]["original_downloaded"] for r in filings))
        self.assertEqual(result["status"], "partial")

    def test_no_email_no_network(self):
        client = FixtureClient({})
        result = collect_sec(RecordingStore(), client, {"sec": {}})
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(client.calls)

    def test_real_store_repeat_and_array_reordering_are_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-sec-test-") as temp:
            with Store(temp) as store:
                first, _, _ = self._collect(persistent_store=store)
                count = store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                second, _, _ = self._collect(persistent_store=store)
                self.assertEqual(first["financial_facts"], second["financial_facts"])
                self.assertEqual(count, store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0])
                self._collect(persistent_store=store, reverse_fact_order=True)
                self.assertEqual(count, store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0])
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM quality_issues WHERE code='AMBIGUOUS_REVISION_TIME'").fetchone()[0], 0)

    def test_changed_download_selection_retains_existing_header_and_fact_evidence(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-sec-test-") as temp:
            with Store(temp) as store:
                self._collect(persistent_store=store)
                before = {r["record_id"] for r in store.query("financial_facts", latest=False)}
                all_before = store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                reduced, _, _ = self._collect(max_docs=1, persistent_store=store, required_accessions=["0000000123-24-000001"])
                self.assertEqual(reduced["companies"][0]["cached_accessions_retained"], 2)
                self.assertEqual(before, {r["record_id"] for r in store.query("financial_facts", latest=False)})
                self.assertEqual(all_before, store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0])
                no_download, _, client = self._collect(max_docs=0, persistent_store=store)
                self.assertEqual(no_download["documents"], 6)
                self.assertFalse(any("/Archives/" in url for url, _ in client.calls))
                self.assertEqual(all_before, store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0])
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM quality_issues WHERE code='AMBIGUOUS_REVISION_TIME'").fetchone()[0], 0)

    def test_new_header_does_not_revise_same_numeric_fact(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-sec-test-") as temp:
            with Store(temp) as store:
                self._collect(max_docs=0, persistent_store=store)
                before = {r["record_id"] for r in store.query("financial_facts", latest=False)}
                self._collect(max_docs=1, persistent_store=store, required_accessions=["0000000123-24-000001"])
                self.assertEqual(before, {r["record_id"] for r in store.query("financial_facts", latest=False)})
                self._collect(max_docs=1, persistent_store=store, required_accessions=["0000000123-24-000002"])
                self.assertEqual(before, {r["record_id"] for r in store.query("financial_facts", latest=False)})
                historical = financial_snapshot(store, "sec:0000000123", "2024-08-10T00:00:00Z")
                self.assertTrue(historical["records"])

    def test_snapshot_does_not_leak_future_amendment_or_mix_ytd_with_quarter(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-sec-test-") as temp:
            with Store(temp) as store:
                self._collect(persistent_store=store)
                before = financial_snapshot(store, "sec:0000000123", "2024-08-10T00:00:00Z")
                after = financial_snapshot(store, "sec:0000000123", "2024-09-10T00:00:00Z")
                historical = [r for r in before["records"] if r["data"]["concept"] == "Revenues"]
                revised = [r for r in after["records"] if r["data"]["concept"] == "Revenues"]
                self.assertEqual({r["data"]["value"] for r in historical}, {1000000, 400000})
                self.assertEqual({r["data"]["value"] for r in revised}, {900000, 400000})
                ytd = next(r for r in revised if r["data"]["start"] == "2024-01-01")
                self.assertEqual(ytd["data"]["accession"], "0000000123-24-000002")
                self.assertEqual(ytd["snapshot_selection"]["earlier_eligible_disclosures"], 1)
                actual_system_then = financial_snapshot(store, "sec:0000000123", "2024-09-10T00:00:00Z", known_at="2024-09-10T00:00:00Z")
                self.assertEqual(actual_system_then["records"], [])

    def test_snapshot_same_day_conflicting_disclosures_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-sec-test-") as temp:
            with Store(temp) as store:
                raw = store.put_raw(b"synthetic evidence only", source="SEC EDGAR", url="https://example.test/synthetic", retrieved_at="2026-10-09T00:00:00Z")
                for accession, value in [("0000000123-24-000001", 100), ("0000000123-24-000002", 90)]:
                    store.put_record("financial_facts", accession, {"taxonomy": "us-gaap", "concept": "Revenues", "unit": "USD",
                                     "value": value, "start": "2024-01-01", "end": "2024-03-31", "accession": accession},
                                     raw_id=raw["raw_id"], source="SEC EDGAR", company_id="sec:0000000123",
                                     available_at="2024-05-02T04:00:00Z", availability_basis="synthetic_test_day_boundary")
                result = financial_snapshot(store, "sec:0000000123", "2024-06-01T00:00:00Z")
                self.assertEqual(result["records"], [])
                self.assertEqual(len(result["conflicts"]), 1)
                self.assertEqual(len(result["conflicts"][0]["accessions"]), 2)


if __name__ == "__main__":
    unittest.main()
