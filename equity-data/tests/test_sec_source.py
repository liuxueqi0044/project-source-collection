"""Synthetic generalization/graph tests; no real issuer answers in fixtures."""
import json
import tempfile
import unittest
from unittest.mock import patch

from equity_data.adapters.sec_source import SecAdapter
from equity_data.engine import run_cycle, run_pending, status_report
from equity_data.store import Store
from equity_data.sec import _time_evidence
from equity_data.universe import (DIRECTORY_URL, DIRECTORY_FALLBACK_URL, normalize_cik,
                                  resolve_universe, security_identity)


def columns(rows):
    return {key: [row.get(key) for row in rows] for key in set().union(*(row.keys() for row in rows))} if rows else {"accessionNumber": []}


class Client:
    def __init__(self, store, fixtures):
        self.store, self.fixtures, self.calls = store, fixtures, []

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        result = self.fixtures[url]
        if isinstance(result, Exception):
            raise result
        content = result if isinstance(result, bytes) else json.dumps(result).encode()
        return content, self.store.put_raw(content, source=kwargs["source"], url=url,
            retrieved_at="2026-10-09T01:00:00Z")


def fixture(cik="0000000789", symbol="NEWCO"):
    accession = cik + "-24-000001"
    amendment = cik + "-24-000002"
    old = {"accessionNumber": accession, "form": "10-Q", "filingDate": "2024-08-01", "reportDate": "2024-06-30", "primaryDocument": "xsl/report.htm"}
    amended = {"accessionNumber": amendment, "form": "10-Q/A", "filingDate": "2024-09-01", "reportDate": "2024-06-30", "primaryDocument": "amendment.htm"}
    archive = "CIK" + cik + "-submissions-001.json"
    fixtures = {f"https://data.sec.gov/submissions/CIK{cik}.json": {
        "cik": int(cik), "name": "Synthetic New Company", "tickers": [symbol], "exchanges": ["Synthetic Venue"],
        "website": "https://synthetic.example.test", "investorWebsite": "https://synthetic.example.test/investors",
        "formerNames": [{"name": "Synthetic Prior Name", "from": "2000-01-01", "to": "2005-01-01"}],
        "filings": {"recent": columns([amended]), "files": [{"name": archive, "filingFrom": "2024-01-01", "filingTo": "2024-08-01"},
            {"name": "CIK" + cik + "-submissions-002.json", "filingFrom": "1990-01-01", "filingTo": "1990-12-31"}]}},
        "https://data.sec.gov/submissions/" + archive: columns([old]),
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json": {"cik": int(cik), "facts": {"us-gaap": {"Revenue": {
            "label": "Synthetic Revenue", "units": {"USD": [{"accn": accession, "form": "10-Q", "filed": "2024-08-01", "start": "2024-01-01", "end": "2024-06-30", "val": 1000000},
                {"accn": amendment, "form": "10-Q/A", "filed": "2024-09-01", "start": "2024-01-01", "end": "2024-06-30", "val": 950000}]}}}}}}
    for filing in (old, amended):
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{filing['accessionNumber'].replace('-', '')}/"
        fixtures[base + filing["accessionNumber"] + "-index-headers.html"] = (
            f"<ACCEPTANCE-DATETIME>{filing['filingDate'].replace('-', '')}163028\n<DOCUMENT>\n<TYPE>{filing['form']}\n<FILENAME>{filing['primaryDocument']}\n</DOCUMENT>\n"
            "<DOCUMENT>\n<TYPE>EX-99.1\n<FILENAME>release.htm\n</DOCUMENT>\n<DOCUMENT>\n<TYPE>EX-10.2\n<FILENAME>lease.htm\n</DOCUMENT>\n"
            "<DOCUMENT>\n<TYPE>XML\n<FILENAME>taxonomy.xml\n</DOCUMENT>\n").encode()
        fixtures[base + filing["primaryDocument"]] = b"<html><body><p>Synthetic company document for parser fixture</p></body></html>"
        fixtures[base + "release.htm"] = b"<html><p>We expect revenue for fiscal 2025 to be $2.5 billion.</p></html>"
        fixtures[base + "lease.htm"] = b"<html><p>Purchase commitments expire in 2030.</p></html>"
        fixtures[base + "taxonomy.xml"] = b'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"/>'
    config = {"market": "US", "start": "2024-01-01", "end": "2024-12-31", "sources": ["sec_edgar"],
              "data_types": ["financial_facts", "documents"], "sec": {"user_agent": "SyntheticTest contact@example.test"},
              "scheduler": {"max_task_attempts_per_run": 1, "retry_seconds": 0},
              "securities": [{"cik": cik, "company_id": "sec:" + cik, "symbol": symbol}]}
    return fixtures, config


class UniverseTests(unittest.TestCase):
    def test_symbol_resolution_raw_evidence_and_history_unknown(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            client = Client(store, {DIRECTORY_URL: {"fields": ["cik", "name", "ticker", "exchange"],
                "data": [[789, "Synthetic A", "AAA", "NYSE"], [789, "Synthetic A", "AAA.B", "NYSE"], [891, "Synthetic B", "BBB", "Nasdaq"]]}})
            result = resolve_universe(store, client, {"universe": {"symbols": ["AAA", "AAA.B", "MISSING"]},
                "sec": {"user_agent": "SyntheticTest contact@example.test"}})
            self.assertEqual(result["selected_count"], 2)
            self.assertEqual(len({row["security_id"] for row in result["securities"]}), 2)
            self.assertEqual(len({row["company_id"] for row in result["securities"]}), 1)
            self.assertIn("UNIVERSE_SYMBOL_UNRESOLVED", {gap["code"] for gap in result["gaps"]})
            self.assertTrue(all(row["available_at"] is None for row in store.query("securities")))
            self.assertEqual(store.query("securities", as_of="2025-01-01"), [])
            self.assertEqual(len(store.query("securities")), 2)
            self.assertEqual(security_identity({"cik": 789, "symbol": "AAA", "exchange": "NYSE"})["security_id"],
                             security_identity({"cik": 789, "symbol": "AAA", "exchange": "Nasdaq"})["security_id"])

    def test_cik_only_survives_directory_failure_and_limit_reports_remainder(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            client = Client(store, {})
            result = resolve_universe(store, client, {"universe": {"ciks": [789, 891], "limit": 1}})
            self.assertFalse(client.calls)
            self.assertEqual(result["discovered_count"], 2)
            self.assertEqual(result["selected_count"], 1)
            self.assertIsNone(result["securities"][0]["security_id"])
            gap = next(g for g in result["gaps"] if g["code"] == "UNIVERSE_RESOURCE_LIMIT")
            self.assertEqual(len(gap["details"]["omitted"]), 1)
            self.assertEqual(gap["details"]["next_offset"], 1)

    def test_fallback_directory_and_unavailable_market(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            client = Client(store, {DIRECTORY_URL: RuntimeError("blocked"), DIRECTORY_FALLBACK_URL: {"0": {"cik_str": 789, "ticker": "AAA", "title": "Synthetic A"}}})
            result = resolve_universe(store, client, {"universe": {"symbols": ["AAA"]}, "sec": {"user_agent": "SyntheticTest contact@example.test"}})
            self.assertEqual(result["selected_count"], 1)
            self.assertIsNone(result["securities"][0]["exchange"])
            self.assertEqual(resolve_universe(store, client, {"market": "CN"})["securities"], [])
            for value in (0, "../123", "12345678901", "n/a"):
                with self.assertRaises(ValueError):
                    normalize_cik(value)


class SecGraphTests(unittest.TestCase):
    def run_fixture(self, store, fixtures, config, **kwargs):
        client = Client(store, fixtures)
        result = run_cycle(store, client, config, {"sec_edgar": SecAdapter()}, **kwargs)
        return result, client, status_report(store)

    def test_archive_manifest_all_exhibits_and_primary_no_company_logic(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            result, client, report = self.run_fixture(store, fixtures, config)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(len(store.query("filings")), 2)
            self.assertEqual(len(store.query("financial_facts")), 2)
            resources = report["resources"]
            self.assertEqual(sum(r["kind"] == "primary_filing" for r in resources), 2)
            # XML manifest entries are explicit support candidates in V3, even
            # when their content ultimately contributes no context definitions.
            self.assertEqual(sum(r["kind"] == "filing_attachment" for r in resources), 6)
            self.assertFalse(any("submissions-002" in url for url in client.calls))
            self.assertTrue(any("taxonomy.xml" in url for url in client.calls))
            filing = next(row for row in store.query("filings") if row["data"]["is_amendment"])
            self.assertIsNone(filing["data"]["amends_accession"])
            self.assertEqual(len(store.query("source_candidates")), 2)
            primary = next(row for row in store.query("documents") if row["data"].get("filename") == "xsl/report.htm")
            self.assertEqual(primary["published_at"], "2024-08-01T20:30:28Z")
            self.assertEqual(primary["available_at"], "2024-08-02T04:00:00.000000Z")
            self.assertTrue(all(row["security_id"] is None for row in store.query("guidance")))

    def test_repeat_backfill_idempotent_and_new_issuer_configuration_only(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture("0000000891", "NOVEL")
            first, _, _ = self.run_fixture(store, fixtures, config)
            count = store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            second, client, _ = self.run_fixture(store, fixtures, config)
            self.assertEqual(second["executed"], 0)
            self.assertFalse(client.calls)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0], count)
            self.assertTrue(all(row["company_id"] == "sec:0000000891" for row in store.query("financial_facts")))

    def test_companyfacts_contract_does_not_revise_unchanged_facts_on_snapshot_change(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            config["data_types"] = ["financial_facts"]
            self.run_fixture(store, fixtures, config)
            before = {row["record_key"]: row["record_id"] for row in store.query("financial_facts")}
            financial = store.query("financial_facts")[0]
            contract = financial["data"]["semantic_contract"]
            self.assertEqual(contract["stage"], "extracted_fact")
            self.assertEqual(contract["applicable_entity"]["identifier"], "0000000789")
            self.assertEqual(contract["unit"], "USD")
            self.assertEqual(contract["evidence"]["content_sha256_reference"], "records.raw_id -> raw_blobs.sha256")
            self.assertIsNone(contract.get("normalized_metric"))
            snapshot = fixtures["https://data.sec.gov/api/xbrl/companyfacts/CIK0000000789.json"]
            snapshot["facts"]["us-gaap"]["Assets"] = {"label": "Assets", "units": {"USD": [
                {"accn": "0000000789-24-000001", "form": "10-Q", "filed": "2024-08-01", "end": "2024-06-30", "val": 3000000}]}}
            result, _, _ = self.run_fixture(store, fixtures, config, mode="update")
            self.assertEqual(result["failed"], 0)
            after = {row["record_key"]: row["record_id"] for row in store.query("financial_facts")}
            self.assertEqual(len(after), len(before) + 1)
            self.assertTrue(all(after[key] == record_id for key, record_id in before.items()))

    def test_budget_truncation_resume_and_header_failure_does_not_hide_primary(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            for key in list(fixtures):
                if key.endswith("-index-headers.html"):
                    fixtures[key] = RuntimeError("synthetic header failure")
            result, client, report = self.run_fixture(store, fixtures, config, max_tasks=3)
            self.assertEqual(result["stop_reason"], "max_tasks")
            self.assertGreater(report["summary"]["resource_states"].get("discovered_not_downloaded", 0), 0)
            resumed = run_pending(store, client, config, {"sec_edgar": SecAdapter()})
            report = status_report(store)
            self.assertEqual(sum(r["kind"] == "primary_filing" and r["state"] in ("parsed", "partial_parse") for r in report["resources"]), 2)
            self.assertEqual(sum(r["kind"] == "filing_index" and r["state"] == "download_failed" for r in report["resources"]), 2)
            self.assertFalse(report["summary"]["coverage_complete"])

    def test_missing_primary_recovered_from_header_and_missing_manifest_partial(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            payload = fixtures["https://data.sec.gov/submissions/CIK0000000789.json"]
            payload["filings"]["recent"]["primaryDocument"] = [None]
            first, _, report = self.run_fixture(store, fixtures, config)
            self.assertEqual(first["failed"], 0)
            self.assertEqual(sum(r["kind"] == "primary_filing" for r in report["resources"]), 2)
            self.assertIn("SEC_PRIMARY_FILENAME_MISSING", {g["code"] for g in report["gaps"]})
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            for url in list(fixtures):
                if url.endswith("-index-headers.html"):
                    fixtures[url] = b"<html>Changed header layout without known manifest</html>"
            _, _, report = self.run_fixture(store, fixtures, config)
            self.assertIn("SEC_HEADER_MANIFEST_MISSING", {g["code"] for g in report["gaps"]})
            self.assertEqual(sum(r["kind"] == "filing_index" and r["state"] == "partial_parse" for r in report["resources"]), 2)

    def test_companyfacts_snapshot_addition_not_false_revision_and_changed_value_retained(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            self.run_fixture(store, fixtures, config)
            facts_before = {row["record_id"] for row in store.query("financial_facts", latest=False)}
            url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000789.json"
            fixtures[url]["entityName"] = "Extra API field does not revise numbers"
            self.run_fixture(store, fixtures, config, mode="incremental")
            self.assertEqual(facts_before, {row["record_id"] for row in store.query("financial_facts", latest=False)})
            fixtures[url]["facts"]["us-gaap"]["Revenue"]["units"]["USD"][0]["val"] = 999999
            self.run_fixture(store, fixtures, config, mode="incremental")
            facts_after = store.query("financial_facts", latest=False)
            self.assertEqual(len(facts_after), 3)
            historical = store.query("financial_facts", as_of="2024-08-20", latest=False)
            self.assertEqual(historical, [])  # undated same-accession replacement is fail-closed

    def test_no_contact_blocked_without_requests(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            config["sec"] = {}
            with patch.dict("os.environ", {"SEC_USER_AGENT": ""}):
                result, client, report = self.run_fixture(store, fixtures, config)
            self.assertEqual(result["blocked"], 1)
            self.assertFalse(client.calls)

    def test_known_header_day_later_than_filed_tightens_fact_availability(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            header_url = next(url for url in fixtures if url.endswith("0000000789-24-000001-index-headers.html"))
            fixtures[header_url] = fixtures[header_url].replace(b"20240801163028", b"20240802163028")
            # First preserve authoritative timing evidence; then ask the same
            # source adapter for facts, using only configuration data types.
            config["data_types"] = ["documents"]
            self.run_fixture(store, fixtures, config)
            config["data_types"] = ["financial_facts"]
            self.run_fixture(store, fixtures, config)
            original = next(row for row in store.query("financial_facts") if row["data"]["form"] == "10-Q")
            self.assertEqual(original["available_at"], "2024-08-03T04:00:00.000000Z")
            self.assertEqual(store.query("financial_facts", as_of="2024-08-02T23:59:59Z"), [])

    def test_header_arriving_after_facts_restricts_earlier_assumption(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            header_url = next(url for url in fixtures if url.endswith("0000000789-24-000001-index-headers.html"))
            fixtures[header_url] = fixtures[header_url].replace(b"20240801163028", b"20240802163028")
            config["data_types"] = ["financial_facts"]
            self.run_fixture(store, fixtures, config)
            self.assertEqual(len(store.query("financial_facts", as_of="2024-08-02T23:59:59Z")), 1)
            config["data_types"] = ["documents"]
            self.run_fixture(store, fixtures, config)
            self.assertEqual(store.query("financial_facts", as_of="2024-08-02T23:59:59Z"), [])
            later = store.query("financial_facts", as_of="2024-08-04T00:00:00Z")
            self.assertEqual(len(later), 1)
            self.assertTrue(later[0].get("availability_constraints"))

    def test_document_set_context_support_is_local_and_evidence_linked(self):
        declarations = b'<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:t="https://synthetic.example.test/taxonomy"><body>'
        contexts = b'<ix:header><ix:resources><xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="https://www.sec.gov/CIK">789</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-06-30</xbrli:endDate></xbrli:period></xbrli:context><xbrli:unit id="u1"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit></ix:resources></ix:header>'
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            base = "https://www.sec.gov/Archives/edgar/data/789/000000078924000001/"
            fixtures[base + "xsl/report.htm"] = declarations + contexts + b'<ix:nonFraction name="t:MainMetric" contextRef="c1" unitRef="u1" scale="3">7</ix:nonFraction></body></html>'
            fixtures[base + "release.htm"] = declarations + b'<ix:nonFraction name="t:CustomMetric" contextRef="c1" unitRef="u1" scale="3">9</ix:nonFraction></body></html>'
            result, client, report = self.run_fixture(store, fixtures, config)
            self.assertEqual(result["failed"], 0)
            facts = [row for row in store.query("financial_facts") if row["data"].get("extraction_method") == "inline_xbrl_local_no_taxonomy_network"]
            self.assertEqual(len(facts), 2)
            custom = next(row for row in facts if row["data"].get("concept_qname") == "t:CustomMetric")
            self.assertEqual(custom["data"]["value"], "9000")
            self.assertEqual(len(custom["data"]["supporting_raw_ids"]), 1)
            self.assertEqual(store.db.execute("SELECT COUNT(DISTINCT raw_id) FROM record_evidence WHERE record_id=?", (custom["record_id"],)).fetchone()[0], 2)
            self.assertTrue(custom.get("required_evidence_complete", True))
            self.assertEqual(sum("taxonomy.xml" in url for url in client.calls), 2)

    def test_retained_header_overrides_downgraded_evidence_on_reprocess(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config = fixture()
            _, _, report = self.run_fixture(store, fixtures, config)
            baseline = store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            # Reproduce a submissions/header queue merge: an intact saved
            # header coexists with freshly derived, less precise filed-day time.
            for resource in report["resources"]:
                if resource["kind"] not in ("primary_filing", "filing_attachment"):
                    continue
                metadata = resource["metadata"]
                self.assertTrue(metadata.get("header"))
                metadata["evidence"] = _time_evidence(metadata["filing"])
                store.db.execute("UPDATE machine_inventory SET metadata_json=? WHERE resource_id=?",
                                 (json.dumps(metadata), resource["resource_id"]))
            store.db.commit()
            client = Client(store, {})  # Offline replay must not fetch anything.
            for _ in range(3):
                result = run_cycle(store, client, config, {"sec_edgar": SecAdapter()}, reprocess=True)
                self.assertEqual(result["failed"], 0)
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0], baseline)
            self.assertFalse(client.calls)
            primary = next(row for row in store.query("documents") if row["data"].get("filename") == "xsl/report.htm")
            self.assertEqual(primary["published_at"], "2024-08-01T20:30:28Z")
            self.assertEqual(primary["publication_precision"], "second")


if __name__ == "__main__":
    unittest.main()
