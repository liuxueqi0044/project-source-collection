"""Isolated, prelabelled V5 holdout. Never touches a production data store.

fetch writes official raw bytes and readable blocks; evaluate requires an
already frozen reference list and unchanged production parser hashes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/v5/semantic_holdout"
SOURCES = [
    ("siemens_dotmatics", "Siemens", "https://press.siemens.com/global/en/pressrelease/siemens-completes-acquisition-dotmatics"),
    ("siemens_us_factory", "Siemens", "https://press.siemens.com/global/en/node/7117"),
    ("siemens_quarter", "Siemens", "https://press.siemens.com/global/en/pressrelease/successful-second-quarter-outlook-confirmed"),
    ("oracle_ai_center", "Oracle", "https://www.oracle.com/news/announcement/oracle-launches-an-ai-center-of-excellence-for-healthcare-2025-09-10/"),
    ("oracle_ai_agents", "Oracle", "https://www.oracle.com/uk/news/announcement/ai-world-oracle-launches-fusion-applications-ai-agent-marketplace-to-accelerate-enterprise-ai-adoption-2025-10-15/"),
    ("oracle_health_partner", "Oracle", "https://www.oracle.com/news/announcement/oracle-cleveland-clinic-g42-announce-strategic-partnership-to-launch-ai-based-global-healthcare-delivery-platform-2025-05-16/"),
    ("oracle_cloud_regions", "Oracle", "https://blogs.oracle.com/cloud-infrastructure/oracle-database-google-cloud-more-regions-zones"),
    ("oracle_quarter", "Oracle", "https://www.oracle.com/apac/news/announcement/q4fy25-earnings-release-2025-06-11/"),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(content):
    return hashlib.sha256(content).hexdigest()


def save(path, data):
    if path.exists():
        raise RuntimeError("Refuse to overwrite evidence: " + str(path))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def frozen():
    data = json.loads((OUT / "code-freeze.json").read_text(encoding="utf-8"))
    for name, expected in data["files"].items():
        if sha((ROOT / name).read_bytes()) != expected:
            raise RuntimeError("Production semantics changed after freeze: " + name)
    return data


def visible_blocks(content):
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.select("script,style,noscript,template,nav,footer"):
        tag.decompose()
    blocks, seen = [], set()
    for tag in soup.select("h1,h2,h3,p,li"):
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        if len(text) < 20 or text in seen:
            continue
        seen.add(text)
        blocks.append({"block_id": len(blocks), "tag": tag.name, "text": text})
    return blocks


def fetch():
    frozen()
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    records = []
    for key, company, url in SOURCES:
        record = {"document_id": key, "company": company, "requested_url": url,
                  "attempted_at": now(), "selection": "purposive official-source event-class sample; no extractor output used"}
        started = time.monotonic()
        try:
            current = url
            for _ in range(6):
                response = requests.get(current, timeout=30, allow_redirects=False,
                    headers={"User-Agent": "EquityEvidenceResearch/0.5 public-source validation"})
                if response.is_redirect:
                    from urllib.parse import urljoin
                    candidate = urljoin(current, response.headers["Location"])
                    host = urlparse(candidate).hostname or ""
                    if not (host == "oracle.com" or host.endswith(".oracle.com") or host == "siemens.com" or host.endswith(".siemens.com")):
                        raise ValueError("Unapproved redirect: " + candidate)
                    current = candidate
                    continue
                break
            body = response.content
            if len(body) > 4_000_000:
                raise ValueError("Evidence response exceeded 4 MB per-page budget")
            path = OUT / "raw" / (key + ".html")
            if path.exists():
                raise RuntimeError("Existing raw holdout refuses overwrite")
            path.write_bytes(body)
            record.update(url=current, status_code=response.status_code, retrieved_at=now(),
                          sha256=sha(body), byte_size=len(body), raw_path=path.relative_to(ROOT).as_posix(),
                          content_type=response.headers.get("Content-Type"), response_date=response.headers.get("Date"),
                          etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"))
            blocks = visible_blocks(body)
            save(OUT / (key + ".blocks.json"), blocks)
            record["block_count"] = len(blocks)
        except Exception as exc:
            record.update(error=type(exc).__name__ + ": " + str(exc), failed_at=now())
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        records.append(record)
        print(json.dumps({k: record.get(k) for k in ("document_id", "status_code", "byte_size", "block_count", "error")}), flush=True)
        time.sleep(0.5)
    save(OUT / "acquisition-manifest.json", {"finished_at": now(), "documents": records})


def evaluate():
    freeze = frozen()
    reference_bytes = (OUT / "reference.json").read_bytes()
    reference = json.loads(reference_bytes)
    reference_freeze = json.loads((OUT / "reference-freeze.json").read_text(encoding="utf-8"))
    if sha(reference_bytes) != reference_freeze["sha256"]:
        raise RuntimeError("Reference labels changed after freeze")
    # Only import/run production extraction after frozen independent labels.
    from equity_data.competition.change_detector import normalize_snapshot, clean
    from equity_data.competition.events import extract_events
    from equity_data.competition.evidence import evidence_text
    acquisition = json.loads((OUT / "acquisition-manifest.json").read_text(encoding="utf-8"))
    documents = {doc["document_id"]: doc for doc in acquisition["documents"]}
    outputs, results = {}, []
    for key, doc in documents.items():
        if doc.get("status_code") != 200:
            continue
        content = (ROOT / doc["raw_path"]).read_bytes()
        if sha(content) != doc["sha256"]:
            raise RuntimeError("Holdout raw changed")
        endpoint = {"format": "html", "entity_id": "holdout:" + doc["company"].lower(),
                    "entity_verified": True, "endpoint_id": "holdout:" + key}
        snapshot = normalize_snapshot(content, endpoint)
        snapshot.update(raw_id="sha256:" + doc["sha256"], sha256=doc["sha256"], retrieval_id="holdout:" + key,
            url=doc["url"], observed_at=doc["retrieved_at"], endpoint_id=endpoint["endpoint_id"], entity_id=endpoint["entity_id"])
        extracted = extract_events(snapshot, endpoint)
        raw_visible = evidence_text(content, doc.get("content_type", ""))
        for event in extracted["events"] + extracted["candidates"]:
            event["holdout_excerpt_validated"] = all(clean(ref["excerpt"]) in raw_visible for ref in event["evidence_refs"])
        outputs[key] = {"snapshot_status": {k: snapshot[k] for k in ("valid", "title", "publication_time", "publication_precision", "issues")}, "extracted": extracted}
    for label in reference["labels"]:
        key, text = label["document_id"], clean(label["excerpt"])
        doc = documents[key]
        raw_visible = evidence_text((ROOT / doc["raw_path"]).read_bytes(), doc.get("content_type", ""))
        if text not in raw_visible:
            raise RuntimeError("Independent reference excerpt not in acquired raw: " + label["id"])
        extracted = outputs.get(key, {}).get("extracted", {"events": [], "candidates": []})
        hits = []
        for stage in ("events", "candidates"):
            for event in extracted[stage]:
                excerpt = clean(event["evidence_refs"][0]["excerpt"])
                # This is mention retrieval, not a semantic correctness metric:
                # source overlap plus class, separately checked against stage.
                if excerpt in text or (len(excerpt) >= 30 and excerpt[:min(60, len(excerpt))] in text):
                    hits.append({"stage": stage, "event_id": event["event_id"], "event_type": event["event_type"],
                        "status": event["status"], "excerpt": excerpt, "evidence_valid": event["holdout_excerpt_validated"]})
        correct = [h for h in hits if h["event_type"] in label.get("expected_event_types", []) and h["evidence_valid"]]
        results.append({**label, "hits": hits, "expected_type_candidate_or_fact_detected": bool(correct),
            "expected_type_extracted_fact": any(h["stage"] == "events" for h in correct),
            "negative_emitted_as_fact": label["label"] == "negative" and any(h["stage"] == "events" for h in hits),
            "negative_emitted_as_candidate": label["label"] == "negative" and any(h["stage"] == "candidates" for h in hits)})
    positives = [r for r in results if r["label"] == "positive"]
    negatives = [r for r in results if r["label"] == "negative"]
    nmentions = sum(r["expected_type_candidate_or_fact_detected"] for r in positives)
    nfacts = sum(r["expected_type_extracted_fact"] for r in positives)
    summary = {"finished_at": now(), "annotation_author": reference["annotation_author"], "reference_frozen_at": reference_freeze["frozen_at"],
        "code_frozen_at": freeze["frozen_at"], "unchanged_production_files": True,
        "documents_requested": len(documents), "documents_http_200": sum(d.get("status_code") == 200 for d in documents.values()),
        "positive_denominator": len(positives), "negative_denominator": len(negatives),
        "positive_candidate_or_fact_mention_hits": nmentions, "positive_candidate_or_fact_mention_recall": nmentions/len(positives) if positives else None,
        "positive_extracted_fact_hits": nfacts, "positive_extracted_fact_recall": nfacts/len(positives) if positives else None,
        "negative_fact_false_positives": sum(r["negative_emitted_as_fact"] for r in negatives),
        "negative_candidate_flags": sum(r["negative_emitted_as_candidate"] for r in negatives),
        "core_brief_precision": None, "core_brief_precision_reason": "No reviewed competition edges or core brief sample in this extraction-only holdout",
        "all_document_outputs": {"events": sum(len(v["extracted"]["events"]) for v in outputs.values()), "candidates": sum(len(v["extracted"]["candidates"]) for v in outputs.values())},
        "types_in_reference": dict(Counter(t for r in positives for t in r["expected_event_types"])),
        "limitations": reference["limitations"], "label_results": results}
    save(OUT / "extractor-outputs.json", outputs)
    save(OUT / "results.json", summary)
    print(json.dumps({k:v for k,v in summary.items() if k != "label_results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["fetch", "evaluate"])
    args = parser.parse_args()
    fetch() if args.phase == "fetch" else evaluate()
