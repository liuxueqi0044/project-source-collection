"""Generic title identity regressions; synthetic issuers and offline original bytes."""
import tempfile
import unittest
from types import SimpleNamespace

from equity_data.adapters.ir_source import OfficialIRAdapter, DOCUMENT_PARSER_VERSION, _document_title
from equity_data.store import Store


class IRTitleStabilityTests(unittest.TestCase):
    url = "https://issuer.example.test/news"

    def resource(self, label=""):
        return {"resource_key": "page:" + self.url, "url": self.url,
                "company_id": "synthetic:issuer", "metadata": {"title": label,
                "discovery_locator": "a/link[0]@href"}}

    def save(self, store, content, label="", at="2026-10-10T01:00:00Z"):
        raw = store.put_raw(content, source="official_ir", url=self.url, retrieved_at=at)
        record_id = OfficialIRAdapter()._save_document(
            SimpleNamespace(store=store), self.resource(label), raw, "ir_document", content=content)
        return record_id, raw

    def test_navigation_label_changes_do_not_version_identical_original(self):
        content = b'<html><head><title>Issuer Newsroom</title></head><body>Same original.</body></html>'
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            ids = [self.save(store, content, label)[0] for label in ("Explore news", "", "Explore news")]
            self.assertEqual(len(set(ids)), 1)
            records = store.query("documents", latest=False)
            self.assertEqual(len(records), 1)
            doc = records[0]
            self.assertEqual(doc["data"]["title"], "Issuer Newsroom")
            self.assertEqual(doc["parser_version"], DOCUMENT_PARSER_VERSION)
            self.assertEqual(doc["data"]["title_evidence"]["raw_id"], doc["raw_id"])
            self.assertEqual(doc["data"]["title_evidence"]["locator"], "head/title")

    def test_missing_title_remains_unknown_instead_of_discovery_answer(self):
        content = b'<html><p>Source did not supply a supported title.</p></html>'
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            ids = [self.save(store, content, label)[0] for label in ("Known answer", "Different answer")]
            self.assertEqual(len(set(ids)), 1)
            data = store.query("documents")[0]["data"]
            self.assertIsNone(data["title"])
            self.assertEqual(data["title_evidence"]["status"], "unknown")

    def test_real_source_title_change_creates_traceable_version_and_keeps_old(self):
        before = b'<html><head><title>Quarterly Results</title></head></html>'
        after = before.replace(b"Results", b"Results - Corrected")
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            first, old_raw = self.save(store, before, "Same label")
            second, new_raw = self.save(store, after, "Same label", "2026-10-11T01:00:00Z")
            self.assertNotEqual(first, second)
            latest = store.query("documents")[0]
            self.assertEqual(latest["supersedes"], first)
            self.assertNotEqual(old_raw["raw_id"], new_raw["raw_id"])
            self.assertEqual(latest["data"]["title"], "Quarterly Results - Corrected")
            self.assertEqual(len(store.query("documents", latest=False)), 2)
            self.assertEqual(store.query("documents", as_of="2026-10-10T23:00:00Z")[0]["record_id"], first)

    def test_feed_container_and_individual_item_use_own_direct_title(self):
        rss = b'<rss><channel><title>Issuer Feed</title><item><title>Specific Results</title></item></channel></rss>'
        title, evidence = _document_title(rss)
        self.assertEqual((title, evidence["scope"], evidence["locator"]),
                         ("Issuer Feed", "whole_feed", "/rss/channel/title"))
        title, evidence = _document_title(b'<item><title>Specific Results</title></item>')
        self.assertEqual((title, evidence["scope"]), ("Specific Results", "feed_item"))
        self.assertIsNone(_document_title(rss.replace(b'<title>Issuer Feed</title>', b''))[0])
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Feed</title><entry><title>Item</title></entry></feed>'
        self.assertEqual(_document_title(atom)[0], "Atom Feed")

    def test_title_fallback_has_exact_original_locator_and_files_stay_unknown(self):
        title, evidence = _document_title(b'<html><head><meta property="og:title" content="Issuer &amp; Results"></head></html>')
        self.assertEqual(title, "Issuer & Results")
        self.assertEqual(evidence["locator"], "meta[property='og:title']@content")
        self.assertIsNone(_document_title(b'%PDF-1.7 <title>Not HTML</title>')[0])
        self.assertIsNone(_document_title(b'PK\x03\x04<title>Not HTML</title>')[0])
        self.assertIsNone(_document_title(b'<html><svg><title>Chart label</title></svg></html>')[0])

    def test_discovery_keeps_label_and_referrer_evidence_outside_document_identity(self):
        emitted = []
        resource = {"url": "https://issuer.example.test/", "company_id": "synthetic:issuer", "metadata": {}}
        self.assertTrue(OfficialIRAdapter()._emit_link(SimpleNamespace(emit=emitted.append), resource,
            self.url, "Explore our news", kind="ir_document", locator="a[3]@href"))
        self.assertEqual(emitted[0]["metadata"]["title"], "Explore our news")
        self.assertEqual(emitted[0]["metadata"]["parent_url"], resource["url"])
        self.assertEqual(emitted[0]["metadata"]["discovery_locator"], "a[3]@href")


if __name__ == "__main__":
    unittest.main()
