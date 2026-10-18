"""Document-set arrival-order regression; all filings and values are synthetic."""
import json
import tempfile
import unittest
from pathlib import Path

from equity_data.adapters.sec_source import SecAdapter
from equity_data.engine import run_cycle, run_pending, status_report
from equity_data.store import Store
from equity_data.queue import Queue


DECLARATIONS = b'<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:t="https://synthetic.example.test/taxonomy"><body>'
CONTEXTS = b'<ix:header><ix:resources><xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="https://www.sec.gov/CIK">789</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-06-30</xbrli:endDate></xbrli:period></xbrli:context><xbrli:unit id="u1"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit></ix:resources></ix:header>'
FACT = b'<ix:nonFraction name="t:OperatingMetric" contextRef="c1" unitRef="u1" scale="3">9</ix:nonFraction>'


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


def fixture(cik="0000000789", support_type="EX-99.1", support_filename="support.htm"):
    accession = cik + "-24-000001"
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    filing = {"accessionNumber": accession, "form": "10-Q", "filingDate": "2024-08-01",
              "reportDate": "2024-06-30", "primaryDocument": "report.htm"}
    fixtures = {f"https://data.sec.gov/submissions/CIK{cik}.json": {
        "cik": int(cik), "name": "Synthetic issuer", "tickers": ["SYNTH"],
        "filings": {"recent": {key: [value] for key, value in filing.items()}, "files": []}},
        base + accession + "-index-headers.html": (
            f"<ACCEPTANCE-DATETIME>20240801163028\n<DOCUMENT>\n<TYPE>10-Q\n<FILENAME>report.htm\n</DOCUMENT>\n"
            f"<DOCUMENT>\n<TYPE>{support_type}\n<FILENAME>{support_filename}\n</DOCUMENT>\n").encode(),
        base + "report.htm": DECLARATIONS + FACT + b"</body></html>",
        base + support_filename: DECLARATIONS + CONTEXTS + b"</body></html>"}
    config = {"market": "US", "start": "2024-01-01", "end": "2024-12-31", "sources": ["sec_edgar"],
              "data_types": ["documents", "financial_facts"], "sec": {"user_agent": "SyntheticTest contact@example.test"},
              "scheduler": {"max_task_attempts_per_run": 1, "retry_seconds": 0},
              "securities": [{"cik": cik, "company_id": "sec:" + cik, "symbol": "SYNTH"}]}
    # This test uses documents only so no separate Companyfacts value can mask a
    # missing inline fact. No issuer or value is inserted into production rules.
    config["data_types"] = ["documents"]
    return fixtures, config, base


def facts(store):
    return [row for row in store.query("financial_facts") if row["data"].get("extraction_method") == "inline_xbrl_local_no_taxonomy_network"]


def arrival_probe(root):
    fixtures, config, base = fixture()
    with Store(root) as store:
        first = run_cycle(store, Client(store, fixtures), config, {"sec_edgar": SecAdapter()}, max_tasks=4)
        initial_facts = facts(store)
        initial_report = status_report(store)
    # A fresh process/store connection must recover dependencies without memory.
    with Store(root) as store:
        client = Client(store, fixtures)
        resumed = run_pending(store, client, config, {"sec_edgar": SecAdapter()})
        final_facts = facts(store)
        report = status_report(store)
        repeat = run_cycle(store, client, config, {"sec_edgar": SecAdapter()})
        return {"case": "fact document precedes same-accession context/unit file; reopen then resume",
                "synthetic": True, "expected_value": "9000", "first_run": first,
                "initial_facts": initial_facts, "initial_resources": initial_report["resources"],
                "resumed_run": resumed, "facts": final_facts, "resources": report["resources"],
                "tasks": report["tasks"], "gaps": report["gaps"], "requests_after_restart": client.calls,
                "repeat_run": repeat, "correct": len(final_facts) == 1 and final_facts[0]["data"].get("value") == "9000"}


class SecDependencyV3Tests(unittest.TestCase):
    def test_late_support_wakes_fact_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            result = arrival_probe(temp)
            self.assertEqual(result["initial_facts"], [])
            self.assertTrue(result["correct"], "Late context/unit support never reparsed the earlier fact document")
            self.assertEqual(result["repeat_run"]["executed"], 0)
            self.assertEqual(len(result["facts"][0]["data"]["supporting_raw_ids"]), 1)

    def test_support_first_needs_no_dependency_reparse(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config, base = fixture()
            fixtures[base + "report.htm"], fixtures[base + "support.htm"] = fixtures[base + "support.htm"], fixtures[base + "report.htm"]
            result = run_cycle(store, Client(store, fixtures), config, {"sec_edgar": SecAdapter()})
            self.assertEqual(result["failed"], 0)
            self.assertEqual(len(facts(store)), 1)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM machine_tasks WHERE generation LIKE 'sec-support:%'").fetchone()[0], 0)

    def test_failed_support_download_retries_and_wakes_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            fixtures, config, base = fixture()
            original = fixtures[base + "support.htm"]
            fixtures[base + "support.htm"] = RuntimeError("synthetic network interruption")
            with Store(temp) as store:
                result = run_cycle(store, Client(store, fixtures), config, {"sec_edgar": SecAdapter()})
                self.assertEqual(result["failed"], 1)
                self.assertEqual(facts(store), [])
                self.assertIn("SEC_DOCUMENT_SUPPORT_MISSING", {row["code"] for row in status_report(store)["gaps"]})
            fixtures[base + "support.htm"] = original
            with Store(temp) as store:
                result = run_pending(store, Client(store, fixtures), config, {"sec_edgar": SecAdapter()})
                self.assertEqual(result["failed"], 0)
                self.assertEqual(len(facts(store)), 1)
                self.assertNotIn("SEC_DOCUMENT_SUPPORT_MISSING", {row["code"] for row in status_report(store)["gaps"]})

    def test_changed_support_invalidates_then_restores_without_duplicate_loop(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config, base = fixture()
            client = Client(store, fixtures)
            run_cycle(store, client, config, {"sec_edgar": SecAdapter()})
            self.assertEqual(facts(store)[0]["currency"], "USD")
            fixtures[base + "support.htm"] = fixtures[base + "support.htm"].replace(b"iso4217:USD", b"iso4217:EUR")
            changed = run_cycle(store, client, config, {"sec_edgar": SecAdapter()}, mode="update")
            self.assertEqual(changed["failed"], 0)
            self.assertEqual(facts(store)[0]["currency"], "EUR")
            historical = [row for row in store.query("financial_facts", latest=False) if row["data"].get("extraction_method")]
            self.assertEqual({row["currency"] for row in historical}, {"USD", "EUR"})
            fixture_support = fixtures[base + "support.htm"]
            fixtures[base + "support.htm"] = b"<html><p>Declarations unavailable in this revised document</p></html>"
            removed = run_cycle(store, client, config, {"sec_edgar": SecAdapter()}, mode="update")
            self.assertEqual(removed["failed"], 0)
            self.assertEqual(facts(store), [])
            self.assertIn("SEC_DOCUMENT_SUPPORT_MISSING", {row["code"] for row in status_report(store)["gaps"]})
            fixtures[base + "support.htm"] = fixture_support
            restored = run_cycle(store, client, config, {"sec_edgar": SecAdapter()}, mode="update")
            self.assertEqual(restored["failed"], 0)
            self.assertEqual(facts(store)[0]["currency"], "EUR")
            self.assertEqual(facts(store)[0]["availability_basis"], "first_observed_parser_field_recovery")
            self.assertGreater(facts(store)[0]["available_at"], "2024-08-02T04:00:00.000000Z")
            self.assertGreater(store.db.execute("SELECT COUNT(*) FROM record_validations WHERE verdict='rejected'").fetchone()[0], 0)
            self.assertLess(restored["executed"], 15)
            before = store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            no_change = run_cycle(store, client, config, {"sec_edgar": SecAdapter()}, mode="update")
            self.assertEqual(no_change["failed"], 0)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0], before)

    def test_xml_support_is_explicitly_enumerated_from_manifest(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config, base = fixture(support_type="XML", support_filename="instance.xml")
            result = run_cycle(store, Client(store, fixtures), config, {"sec_edgar": SecAdapter()})
            self.assertEqual(result["failed"], 0)
            self.assertEqual(facts(store)[0]["data"]["value"], "9000")
            resource = next(item for item in Queue(store).resources() if item["url"] == base + "instance.xml")
            self.assertTrue(resource["metadata"]["document_set_support_candidate"])

    def test_disabled_support_type_remains_explicit_missing(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config, base = fixture(support_type="XML", support_filename="instance.xml")
            config["sec"]["support_document_types"] = []
            client = Client(store, fixtures)
            run_cycle(store, client, config, {"sec_edgar": SecAdapter()})
            self.assertEqual(facts(store), [])
            self.assertNotIn(base + "instance.xml", client.calls)
            self.assertIn("SEC_DOCUMENT_SUPPORT_MISSING", {row["code"] for row in status_report(store)["gaps"]})

    def test_support_does_not_cross_company_or_accession(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config, base = fixture()
            fixtures[base + "support.htm"] = b"<html><p>No context in this filing</p></html>"
            other_fixtures, other_config, other_base = fixture(cik="0000000891")
            fixtures.update(other_fixtures)
            config["securities"].extend(other_config["securities"])
            # A second accession for the original company has the same ids.
            another_accession = "0000000789-24-000002"
            another_base = base.replace("000000078924000001", "000000078924000002")
            submissions = fixtures["https://data.sec.gov/submissions/CIK0000000789.json"]["filings"]["recent"]
            for key, values in submissions.items():
                values.append(another_accession if key == "accessionNumber" else values[0])
            fixtures[another_base + another_accession + "-index-headers.html"] = fixtures[base + "0000000789-24-000001-index-headers.html"]
            fixtures[another_base + "report.htm"] = DECLARATIONS + CONTEXTS + b"</body></html>"
            fixtures[another_base + "support.htm"] = b"<html><p>No fact</p></html>"
            run_cycle(store, Client(store, fixtures), config, {"sec_edgar": SecAdapter()})
            self.assertEqual(len(facts(store)), 1)
            self.assertEqual(facts(store)[0]["company_id"], "sec:0000000891")
            missing = store.db.execute("SELECT * FROM sec_document_dependencies WHERE company_id='sec:0000000789'").fetchone()
            self.assertEqual(missing["state"], "missing_support")


if __name__ == "__main__":
    unittest.main()
