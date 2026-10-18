"""Strict local evidence checks; no inference from a configured answer to a fact."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup
from lxml import etree, html


class EvidenceError(ValueError):
    """A claim cannot be traced to the specified immutable source version."""


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=16)
def evidence_text(content: bytes, media_type: str = "", locator: str = "document:text") -> str:
    """Keep numbers/negation. Cache exact bytes only after every-call hash checks.

    This bounded process cache avoids repeatedly parsing the same long annual
    report for each relationship. validate_evidence still rereads and verifies
    the actual file hash/size on every call; changed bytes never share a key.
    """
    decoded = content.decode("utf-8-sig", errors="replace")
    is_markup = "html" in media_type or "xml" in media_type or bool(re.match(r"\s*<(?:!doctype|html|[\w:]+[\s>])", decoded, re.I))
    if not is_markup:
        if locator.startswith(("css:", "xpath:")):
            raise EvidenceError("A markup locator cannot address a non-markup source")
        if locator.startswith("line:"):
            try:
                number = int(locator[5:])
                return normalized_text(decoded.splitlines()[number - 1]) if number > 0 else ""
            except (ValueError, IndexError) as exc:
                raise EvidenceError("Invalid line locator") from exc
        return normalized_text(decoded)
    soup = BeautifulSoup(decoded, "html.parser")
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    if locator.startswith("css:"):
        try:
            nodes = soup.select(locator[4:])
        except Exception as exc:
            raise EvidenceError("Invalid CSS evidence locator") from exc
        if not nodes:
            raise EvidenceError("CSS evidence locator has no matches")
        return normalized_text(" ".join(node.get_text(" ", strip=True) for node in nodes))
    if locator.startswith("xpath:"):
        try:
            tree = html.fromstring(str(soup))
            nodes = tree.xpath(locator[6:])
        except (etree.Error, ValueError) as exc:
            raise EvidenceError("Invalid XPath evidence locator") from exc
        if not isinstance(nodes, list) or not nodes:
            raise EvidenceError("XPath evidence locator has no node matches")
        return normalized_text(" ".join(" ".join(n.itertext()) if hasattr(n, "itertext") else str(n) for n in nodes))
    return normalized_text(soup.get_text(" ", strip=True))


def validate_evidence(store, refs, *, require_external: bool = False) -> list[dict]:
    if not isinstance(refs, list):
        raise EvidenceError("evidence_refs must be a list")
    verified = []
    for original in refs:
        if not isinstance(original, dict):
            raise EvidenceError("Each evidence reference must be an object")
        ref = dict(original)
        for field in ("raw_id", "sha256", "url", "locator", "excerpt"):
            if not isinstance(ref.get(field), str) or not ref[field].strip():
                raise EvidenceError("Evidence requires nonempty " + field)
        blob = store.db.execute("SELECT * FROM raw_blobs WHERE raw_id=?", (ref["raw_id"],)).fetchone()
        if blob is None:
            raise EvidenceError("Evidence raw version is not saved")
        blob = dict(blob)
        path = (store.root / blob["path"]).resolve()
        if not path.is_relative_to(store.root.resolve()) or not path.is_file():
            raise EvidenceError("Evidence path is missing or outside the evidence store")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != blob["sha256"] or digest != ref["sha256"] or blob["raw_id"] != "sha256:" + digest or len(content) != blob["byte_size"]:
            raise EvidenceError("Evidence SHA-256/size mismatch")
        if ref.get("retrieval_id"):
            row = store.db.execute("SELECT * FROM raw_retrievals WHERE retrieval_id=?", (ref["retrieval_id"],)).fetchone()
            if row is None or row["raw_id"] != ref["raw_id"] or row["url"] != ref["url"]:
                raise EvidenceError("Evidence retrieval/raw/URL mismatch")
        else:
            row = store.db.execute("SELECT * FROM raw_retrievals WHERE raw_id=? AND url=? ORDER BY retrieved_at,rowid LIMIT 1", (ref["raw_id"], ref["url"])).fetchone()
            if row is None:
                raise EvidenceError("Evidence URL has no matching saved retrieval")
        retrieval = dict(row)
        internal = retrieval["source"] in {"competition", "competition_config", "competition_review"} or retrieval["url"].startswith(("config:", "review:", "derived:"))
        metadata = json.loads(retrieval["metadata_json"])
        internal = internal or metadata.get("evidence_nature") in {"configuration", "review", "system_inference"}
        if require_external and internal:
            raise EvidenceError("Configuration/review JSON is not external fact evidence")
        excerpt = normalized_text(ref["excerpt"])
        text = evidence_text(content, blob["media_type"], ref["locator"])
        if excerpt not in text:
            raise EvidenceError("Evidence excerpt is absent from the specified source region")
        if ref.get("record_id"):
            record = store.db.execute("SELECT * FROM records WHERE record_id=?", (ref["record_id"],)).fetchone()
            if record is None:
                raise EvidenceError("Referenced evidence record is missing")
            linked = record["raw_id"] == ref["raw_id"] or store.db.execute("SELECT 1 FROM record_evidence WHERE record_id=? AND raw_id=?", (ref["record_id"], ref["raw_id"])).fetchone()
            # A derived record can cite an external source without making that raw
            # its primary generated JSON blob. Verify this exact raw in its refs.
            if not linked:
                linked = any(item.get("raw_id") == ref["raw_id"] for item in json.loads(record["data_json"]).get("evidence_refs", []))
            if not linked:
                raise EvidenceError("Evidence record does not cite the specified raw version")
        ref.update(retrieval_id=retrieval["retrieval_id"], source=retrieval["source"], retrieved_at=retrieval["retrieved_at"], evidence_nature="internal_review_or_configuration" if internal else "source_document", excerpt_match="whitespace_normalized_visible_text", hash_verified=True)
        verified.append(ref)
    if require_external and not verified:
        raise EvidenceError("At least one external source reference is required")
    return verified
