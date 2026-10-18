"""Synthetic SEC manifest and cache probes; no issuer answers or real holdouts."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from equity_data.adapters.sec_source import SecAdapter
from equity_data.adapters.base import Context
from equity_data.engine import run_cycle
from equity_data.queue import Queue
from equity_data.store import Store
from test_sec_dependencies_v3 import Client, fixture, facts, DECLARATIONS, CONTEXTS, FACT


def original_xml_probe(root, form="4", filename="arbitrary-original.xml"):
    fixtures, config, base = fixture()
    config["sec"].update(forms=[form], attachment_types=[], support_document_types=[])
    filing = fixtures["https://data.sec.gov/submissions/CIK0000000789.json"]["filings"]["recent"]
    filing["primaryDocument"] = ["xsl-renderer/" + filename]
    filing["form"] = [form]
    accession = filing["accessionNumber"][0]
    header = f"<ACCEPTANCE-DATETIME>20240801163028\n<DOCUMENT>\n<TYPE>{form}\n<FILENAME>{filename}\n</DOCUMENT>\n".encode()
    original = b'<?xml version="1.0"?><ownershipDocument><documentType>4</documentType></ownershipDocument>'
    fixtures[base + accession + "-index-headers.html"] = header
    fixtures[base + "xsl-renderer/" + filename] = b"<html><body>Rendered presentation only</body></html>"
    fixtures[base + filename] = original
    with Store(root) as store:
        client = Client(store, fixtures)
        result = run_cycle(store, client, config, {"sec_edgar": SecAdapter()})
        inventory = Queue(store).resources()
        raw = store.db.execute("SELECT b.sha256 FROM raw_retrievals r JOIN raw_blobs b USING(raw_id) WHERE r.url=?", (base + filename,)).fetchall()
        return {"synthetic": True, "form": form, "manifest_sha256": hashlib.sha256(header).hexdigest(),
                "expected_original_url": base + filename, "expected_sha256": hashlib.sha256(original).hexdigest(),
                "original_saved": any(row["sha256"] == hashlib.sha256(original).hexdigest() for row in raw),
                "requests": client.calls, "result": result,
                "resources": [{k: row.get(k) for k in ("url", "kind", "state", "raw_id", "metadata")} for row in inventory]}


class SecV4Tests(unittest.TestCase):
    def test_form_typed_original_xml_is_acquired_beside_rendering(self):
        for form, filename in (("4", "random.xml"), ("13F-HR", "different-filing.xml"), ("6-K", "another.xml")):
            with self.subTest(form=form), tempfile.TemporaryDirectory() as temp:
                result = original_xml_probe(temp, form, filename)
                self.assertTrue(result["original_saved"], "Manifest form-typed original XML was replaced by an XSL presentation URL")
                self.assertEqual(result["result"]["failed"], 0)
                original = next(row for row in result["resources"] if row["url"] == result["expected_original_url"])
                presentation = next(row for row in result["resources"] if "/xsl-renderer/" in row["url"])
                self.assertTrue(original["metadata"]["manifest_form_original"])
                self.assertFalse(presentation["metadata"]["manifest_form_original"])

    def test_accession_support_index_parses_each_original_ids_once(self):
        import equity_data.adapters.sec_source as sec
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config, base = fixture()
            accession = "0000000789-24-000001"
            # A support declaration precedes 24 separately saved rendered
            # reports. Each reports different original bytes, same context IDs.
            fixtures[base + "report.htm"] = DECLARATIONS + CONTEXTS + b"</body></html>"
            manifest = b"<ACCEPTANCE-DATETIME>20240801163028\n<DOCUMENT>\n<TYPE>10-Q\n<FILENAME>report.htm\n</DOCUMENT>\n"
            for index in range(24):
                filename = "arbitrary-rendered-" + str(index) + ".htm"
                fixtures[base + filename] = DECLARATIONS + FACT + ("<p>Part %s</p>" % index).encode() + b"</body></html>"
                manifest += f"<DOCUMENT>\n<TYPE>EX-99.1\n<FILENAME>{filename}\n</DOCUMENT>\n".encode()
            fixtures[base + accession + "-index-headers.html"] = manifest
            class CountingAdapter(SecAdapter):
                raw_lookups = 0
                def _read_saved_raw(self, *args, **kwargs):
                    self.raw_lookups += 1
                    return super()._read_saved_raw(*args, **kwargs)
            adapter = CountingAdapter()
            with patch.object(sec, "_document_ids", wraps=sec._document_ids) as scan:
                result = run_cycle(store, Client(store, fixtures), config, {"sec_edgar": adapter})
            self.assertEqual(result["failed"], 0)
            self.assertLessEqual(scan.call_count, 25, "Supporting declarations were rescanned per rendered report")
            self.assertLessEqual(adapter.raw_lookups, 25 * 8, "Each newly saved report caused a repeated scan of all earlier originals")
            self.assertEqual(len(facts(store)), 24)

    def test_parse_fingerprint_tracks_versions_support_and_source_not_refresh(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config, base = fixture()
            run_cycle(store, Client(store, fixtures), config, {"sec_edgar": SecAdapter()})
            queue = Queue(store)
            resource = next(row for row in queue.resources() if row["url"] == base + "report.htm")
            _, raw = queue.raw_for_task(resource)
            adapter = SecAdapter()
            ctx = Context(store, Client(store, fixtures), config, queue, "synthetic-fingerprint")
            initial = adapter.parse_input_fingerprint(ctx, resource, raw)
            resource["metadata"]["refresh"] = not resource["metadata"].get("refresh")
            self.assertEqual(initial, adapter.parse_input_fingerprint(ctx, resource, raw))
            self.assertEqual(initial, SecAdapter().parse_input_fingerprint(ctx, resource, raw))
            with patch("equity_data.parsers.PARSER_VERSION", "synthetic-new-parser"):
                self.assertNotEqual(initial, adapter.parse_input_fingerprint(ctx, resource, raw))
            resource["metadata"]["filing"]["reportDate"] = "2024-09-30"
            self.assertNotEqual(initial, adapter.parse_input_fingerprint(ctx, resource, raw))
            resource["metadata"]["filing"]["reportDate"] = "2024-06-30"
            support = next(row for row in queue.resources() if row["url"] == base + "support.htm")
            _, support_raw = queue.raw_for_task(support)
            Path(support_raw["path"]).write_bytes(b"modified source bytes")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                adapter.parse_input_fingerprint(ctx, resource, raw)

    def test_acquisition_archive_cache_and_explicit_revision_sweep(self):
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            fixtures, config, base = fixture()
            class RecordingClient(Client):
                def fetch(self, url, **kwargs):
                    self.last_refresh = kwargs["refresh"]
                    return super().fetch(url, **kwargs)
            client = RecordingClient(store, fixtures)
            adapter = SecAdapter()
            ctx = Context(store, client, config, Queue(store), "cache-policy")
            resource = {"url": base + "report.htm", "metadata": {"immutable_archive": True, "refresh": True}}
            adapter.acquire(ctx, resource)
            self.assertFalse(client.last_refresh)
            config["_refresh_archives"] = True
            adapter.acquire(ctx, resource)
            self.assertTrue(client.last_refresh)
            config.pop("_refresh_archives")
            resource["metadata"].pop("immutable_archive")
            adapter.acquire(ctx, resource)
            self.assertTrue(client.last_refresh)


if __name__ == "__main__":
    unittest.main()
