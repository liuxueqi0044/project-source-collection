"""Synthetic public source fixtures; all storage stays in temporary directories."""
import json
import tempfile
import unittest

from equity_data.research import collect_research
from equity_data.store import Store
from tests.test_providers import FakeClient


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.config = {"start": "2024-01-01", "end": "2024-12-31", "research": {"enabled": True,
                       "feeds": [{"url": "https://example.test/feed.xml"}]}}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_rss_repeated_content_and_changed_version(self):
        first = b'''<rss version="2.0"><channel><item><title>Report</title><link>https://example.test/item?utm_source=feed</link><pubDate>Mon, 03 Jun 2024 16:30:00 -0400</pubDate><description>Initial</description></item></channel></rss>'''
        changed = first.replace(b"Initial", b"Corrected")
        client = FakeClient(self.store, [first, first, changed])
        for _ in range(3):
            collect_research(self.store, client, self.config)
        rows = self.store.query("news", latest=False)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["record_key"], "https://example.test/item")
        self.assertEqual(rows[0]["published_at"], "2024-06-03T20:30:00+00:00")
        self.assertNotEqual(rows[0]["data"]["content_sha256"], rows[1]["data"]["content_sha256"])
        self.assertEqual(rows[1]["supersedes"], rows[0]["record_id"])
        self.assertEqual(rows[1]["data"]["event_cluster_status"], "needs_review")
        self.assertFalse(rows[1]["data"]["article_full_text_retrieved"])
        self.assertEqual(self.store.query("news", as_of="2024-06-04"), [])

    def test_atom_naive_timestamp_not_assigned_a_timezone(self):
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Event</title><link rel="alternate" href="https://example.test/event"/><published>2024-06-03T16:30:00</published><updated>2024-06-04T11:00:00Z</updated><content>Sample</content></entry></feed>'''
        collect_research(self.store, FakeClient(self.store, [atom]), self.config)
        row = self.store.query("news")[0]
        self.assertIsNone(row["published_at"])
        self.assertEqual(row["publication_precision"], "unknown")
        self.assertEqual(row["data"]["updated_at"], "2024-06-04T11:00:00+00:00")

    def test_reject_xml_entities_without_losing_raw(self):
        xml = b'<!DOCTYPE rss [<!ENTITY x "content">]><rss><channel><item/></channel></rss>'
        result = collect_research(self.store, FakeClient(self.store, [xml]), self.config)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.store.query("news"), [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM raw_blobs").fetchone()[0], 1)

    def test_policy_dates_full_text_and_current_rendition(self):
        config = dict(self.config, research={"enabled": True, "policy": {"enabled": True, "terms": ["semiconductors"], "max_documents": 1}})
        detail = {"document_number": "2024-12345", "publication_date": "2024-06-03", "effective_on": "2024-07-01",
                  "title": "Synthetic rule", "type": "Rule", "agencies": [{"name": "Synthetic agency"}],
                  "body_html_url": "https://www.federalregister.gov/documents/full_text/2024-12345.html", "pdf_url": "https://www.govinfo.gov/content/pkg/SYNTHETIC.pdf"}
        client = FakeClient(self.store, [{"count": 10, "results": [{"document_number": "2024-12345"}]}, detail, b"<p>Synthetic rule body</p>"])
        result = collect_research(self.store, client, config)
        self.assertEqual(result["policy"]["full_text_count"], 1)
        row = self.store.query("policy_documents")[0]
        self.assertEqual(row["published_at"], "2024-06-03")
        self.assertEqual(row["event_date"], "2024-07-01")
        self.assertEqual(row["data"]["full_text_status"], "acquired")
        self.assertEqual(row["raw_id"], row["data"]["full_text_raw_id"])
        self.assertEqual(row["retrieved_at"], "2026-10-09T00:03:00.000000Z")
        self.assertEqual(self.store.query("policy_documents", as_of="2024-07-02"), [])
        self.assertEqual(self.store.query("policy_documents", known_at="2026-10-09T00:02:30Z"), [])

    def test_policy_missing_body_retains_metadata_and_gap(self):
        config = dict(self.config, research={"enabled": True, "policy": {"enabled": True, "terms": ["semiconductors"]}})
        client = FakeClient(self.store, [{"count": 1, "results": [{"document_number": "2024-12345"}]},
            {"document_number": "2024-12345", "publication_date": "2024-06-03", "body_html_url": "https://www.federalregister.gov/a.html"}, RuntimeError("HTTP 403")])
        result = collect_research(self.store, client, config)
        self.assertEqual(result["status"], "partial")
        row = self.store.query("policy_documents")[0]
        self.assertEqual(row["data"]["full_text_status"], "missing")
        self.assertIsNone(row["data"]["full_text_raw_id"])
        self.assertEqual(row["quality"], "missing")


if __name__ == "__main__":
    unittest.main()
