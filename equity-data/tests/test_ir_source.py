"""Synthetic official-IR adaptation and current-copy time safety tests."""
import json
import tempfile
import unittest

from equity_data.adapters.ir_source import OfficialIRAdapter
from equity_data.engine import run_cycle, run_pending, status_report
from equity_data.store import Store


class Client:
    def __init__(self, store, fixtures):
        self.store, self.fixtures, self.calls = store, fixtures, []
        self.retrieved_at = "2026-10-09T01:00:00Z"

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        content = self.fixtures[url]
        if isinstance(content, Exception):
            raise content
        return content, self.store.put_raw(content, source=kwargs["source"], url=url, retrieved_at=self.retrieved_at)


def settings(root="https://ir.synthetic.example.test/"):
    return {"market": "US", "start": "2024-01-01", "end": "2024-12-31", "sources": ["official_ir"],
            "data_types": ["guidance", "documents"], "ir": {"max_depth": 3},
            "scheduler": {"max_task_attempts_per_run": 1, "retry_seconds": 0},
            "securities": [{"company_id": "sec:0000000789", "cik": "0000000789", "official_ir": root}]}


class IRTests(unittest.TestCase):
    def run_fixture(self, store, config, fixtures, **kwargs):
        client = Client(store, fixtures)
        result = run_cycle(store, client, config, {"official_ir": OfficialIRAdapter()}, **kwargs)
        return result, client, status_report(store)

    def test_static_discovery_pagination_and_no_backfill_time_fabrication(self):
        root = "https://ir.synthetic.example.test/"
        fixtures = {root: b'<html><a href="/news">Press Releases</a><a href="javascript:void(0)">Financial results</a></html>',
            root + "news": b'<html><article><time datetime="2024-08-01"></time><a href="/release/q2">Second quarter financial results</a></article><a rel="next" href="/news?page=2">Next</a></html>',
            root + "news?page=2": b'<html><article><time datetime="2024-05-01"></time><a href="/release/q1">First quarter financial results</a></article><a rel="prev" href="/news">Previous</a></html>',
            root + "release/q2": b'<html><meta property="article:published_time" content="2024-08-01T16:30:00-04:00"><p>We expect revenue for fiscal 2025 to be $2.5 billion.</p></html>',
            root + "release/q1": b'<html><p>We expect revenue for fiscal 2025 to be $2.3 billion.</p></html>'}
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            result, client, report = self.run_fixture(store, settings(), fixtures)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(set(client.calls), set(fixtures))
            self.assertEqual(len(report["resources"]), 5)
            document = next(record for record in store.query("documents") if record["data"]["url"].endswith("/q2"))
            self.assertEqual(document["published_at"], "2024-08-01T16:30:00-04:00")
            self.assertEqual(document["available_at"], "2026-10-09T01:00:00.000000Z")
            self.assertEqual(store.query("guidance", as_of="2024-12-31"), [])
            guidance = store.query("guidance")
            self.assertEqual(len(guidance), 2)
            self.assertTrue(all(record["availability_basis"] == "first_observed_current_document_version" for record in guidance))

    def test_depth_limit_retains_expected_link_and_can_resume(self):
        root = "https://ir.synthetic.example.test/"
        fixtures = {root: b'<html><a href="/news">News releases</a></html>',
                    root + "news": b'<html><a href="/report">Annual report 2024</a></html>',
                    root + "report": b'<html><p>No guidance disclosed in this synthetic document.</p></html>'}
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            config = settings()
            config["ir"]["max_depth"] = 0
            result, client, report = self.run_fixture(store, config, fixtures)
            self.assertEqual(result["blocked"], 1)
            self.assertEqual(client.calls, [root])
            self.assertEqual(len(report["resources"]), 2)
            self.assertIn("IR_TRAVERSAL_LIMIT", {gap["code"] for gap in report["gaps"]})
            self.assertEqual(next(resource for resource in report["resources"] if resource["url"].endswith("/news"))["state"], "discovered_not_downloaded")
            config["ir"]["max_depth"] = 3
            result = run_pending(store, client, config, {"official_ir": OfficialIRAdapter()})
            self.assertEqual(result["blocked"], 0)
            self.assertIn(root + "report", client.calls)
            self.assertNotIn("IR_TRAVERSAL_LIMIT", {gap["code"] for gap in status_report(store)["gaps"]})

    def test_javascript_empty_list_and_webcast_are_explicit_gaps(self):
        root = "https://ir.synthetic.example.test/"
        fixtures = {root: b'<html><a href="https://events.synthetic.example.test/live">Quarterly results webcast</a><a href="/archive">Archive</a></html>',
                    "https://events.synthetic.example.test/live": b'<html><script>loadVideo()</script></html>',
                    root + "archive": b'<html><div id="events"></div><script>fetchResults()</script></html>'}
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            result, _, report = self.run_fixture(store, settings(), fixtures)
            codes = {gap["code"] for gap in report["gaps"]}
            self.assertIn("IR_DYNAMIC_OR_UNRECOGNIZED_LISTING", codes)
            self.assertIn("IR_WEBCAST_NOT_TRANSCRIPT", codes)
            self.assertFalse(any(resource["state"] == "not_disclosed" for resource in report["resources"]))
            webcast = next(row for row in store.query("documents") if row["data"]["document_kind"] == "webcast_landing_page")
            self.assertFalse(webcast["data"]["transcript_obtained"])

    def test_rss_items_date_filter_unknown_dates_retained(self):
        root = "https://ir.synthetic.example.test/"
        fixtures = {root: b'<rss><channel><item><title>Quarterly earnings 2024</title><link>https://ir.synthetic.example.test/current</link><pubDate>Thu, 01 Aug 2024 16:30:00 -0400</pubDate></item><item><title>Old earnings</title><link>https://ir.synthetic.example.test/old</link><pubDate>Fri, 01 Jan 2021 12:00:00 -0500</pubDate></item><item><title>Undated earnings</title><link>https://ir.synthetic.example.test/unknown</link></item></channel></rss>',
                    root + "current": b'<html><p>We expect revenue for fiscal 2025 to be $2.5 billion.</p></html>',
                    root + "unknown": b'<html><p>Unknown publication date is preserved.</p></html>'}
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            result, client, report = self.run_fixture(store, settings(), fixtures)
            self.assertEqual(result["failed"], 0)
            self.assertNotIn(root + "old", client.calls)
            self.assertIn(root + "unknown", client.calls)
            self.assertIn("IR_PUBLICATION_DATE_UNKNOWN", {gap["code"] for gap in report["gaps"]})

    def test_filing_disclosed_official_entry_without_company_template(self):
        root = "https://investors.novel.example.test/"
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            content = ('<html><p>Our investor relations website is <a href="' + root + '">this website</a>.</p><p>The SEC website is <a href="https://www.sec.gov">www.sec.gov</a>.</p></html>').encode()
            raw = store.put_raw(content, source="sec_edgar", url="https://www.sec.gov/Archives/synthetic.htm")
            store.put_record("documents", "synthetic-filing", {"document_kind": "primary_filing"}, source="sec_edgar", raw_id=raw["raw_id"], company_id="sec:0000000789")
            config = settings()
            config["securities"][0].pop("official_ir")
            result, client, report = self.run_fixture(store, config, {root: b'<html><script>dynamic listing</script></html>'})
            self.assertEqual(client.calls, [root])
            candidates = store.query("source_candidates")
            self.assertEqual(len(candidates), 1)
            self.assertTrue(candidates[0]["data"]["candidate_only"])
            self.assertEqual(candidates[0]["raw_id"], raw["raw_id"])

    def test_missing_root_no_guessed_url_and_source_failure_isolated(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            config = settings()
            config["securities"][0].pop("official_ir")
            result, client, report = self.run_fixture(store, config, {})
            self.assertFalse(client.calls)
            self.assertIn("IR_ENTRYPOINT_UNRESOLVED", {gap["code"] for gap in report["gaps"]})
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            config = settings()
            config["securities"][0]["official_ir"] = ["https://ir.synthetic.example.test/", "https://ir.other.example.test/"]
            result, client, report = self.run_fixture(store, config, {"https://ir.synthetic.example.test/": RuntimeError("synthetic source failure"),
                "https://ir.other.example.test/": b'<html><script>dynamic</script></html>'})
            self.assertEqual(len(client.calls), 2)
            self.assertGreater(result["failed"], 0)
            self.assertTrue(any(resource["state"] == "partial_parse" for resource in report["resources"]))

    def test_plain_text_company_stated_ir_url_is_evidenced_and_checkpointed(self):
        root = "https://www.novel.example.test/investors/"
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            content = b'<html><p>The Company posted additional results on its Investor Relations website (www.novel.example.test/investors/).</p></html>'
            raw = store.put_raw(content, source="sec_edgar", url="https://www.sec.gov/Archives/synthetic.htm")
            store.put_record("documents", "synthetic-filing", {"document_kind": "primary_filing"}, source="sec_edgar", raw_id=raw["raw_id"], company_id="sec:0000000789")
            config = settings()
            config["securities"][0].pop("official_ir")
            result, client, report = self.run_fixture(store, config, {root: b'<html><script>dynamic listing</script></html>'})
            self.assertEqual(client.calls, [root])
            candidates = store.query("source_candidates", latest=False)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["data"]["url"], root)
            self.run_fixture(store, config, {root: b'<html><script>dynamic listing</script></html>'}, mode="incremental")
            self.assertEqual(len(store.query("source_candidates", latest=False)), 1)

    def test_changed_current_copy_reparsed_and_previous_version_retained(self):
        root = "https://ir.synthetic.example.test/"
        fixtures = {root: b'<html><a href="/release">Annual financial results</a></html>',
                    root + "release": b'<html><p>We expect revenue for fiscal 2025 to be $2.5 billion.</p></html>'}
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            config = settings()
            _, client, _ = self.run_fixture(store, config, fixtures)
            before = store.query("guidance", latest=False)
            self.assertEqual(len(before), 1)
            client.fixtures[root + "release"] = fixtures[root + "release"].replace(b"2.5", b"2.8")
            client.retrieved_at = "2026-10-10T01:00:00Z"
            run_cycle(store, client, config, {"official_ir": OfficialIRAdapter()}, mode="incremental")
            self.assertEqual(len(store.query("guidance", latest=False)), 2)
            old = store.query("guidance", as_of="2026-10-09T23:00:00Z")
            self.assertEqual(old[0]["record_id"], before[0]["record_id"])
            self.assertNotEqual(store.query("guidance")[0]["record_id"], before[0]["record_id"])


if __name__ == "__main__":
    unittest.main()
