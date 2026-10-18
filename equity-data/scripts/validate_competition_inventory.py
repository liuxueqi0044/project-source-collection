"""Independent saved-entry material inventory; no network or production imports.

The denominator comes from BeautifulSoup CSS selection over hash-checked saved
entry bytes and urllib URL resolution, before inspecting production inventory.
It describes the configured entry snapshot, never the whole website.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


def canonical(value, base, domains):
    parts = urlsplit(urljoin(base, value))
    hostname = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in {"http", "https"} or parts.username or parts.password:
        raise ValueError("not_public_http_url")
    if not any(hostname == domain or hostname.endswith("." + domain) for domain in domains):
        raise ValueError("outside_configured_domains")
    if parts.port not in {None, 80, 443}:
        raise ValueError("unsupported_port")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def reference_links(content, endpoint):
    """Independent ordered reference list from one entry, before database joins."""
    soup = BeautifulSoup(content, "html.parser")
    selector = endpoint.get("link_selector")
    if not selector:
        raise ValueError("Explicit selector required for this independent reference audit")
    domains = [value.lower().rstrip(".") for value in endpoint["allowed_domains"]]
    unique, rejected, duplicates = [], [], 0
    seen = {endpoint["url"]}
    nodes = soup.select(selector)
    for ordinal, node in enumerate(nodes, 1):
        href = node.get("href")
        proof = {"href": href, "locator": f"css:{selector};match[{ordinal}]@href",
                 "anchor_text": node.get_text(" ", strip=True)[:240]}
        if not href:
            rejected.append({**proof, "reason": "missing_href"})
            continue
        try:
            url = canonical(href, endpoint["url"], domains)
        except ValueError as error:
            rejected.append({**proof, "reason": str(error)})
            continue
        if url in seen:
            duplicates += 1
            continue
        seen.add(url)
        unique.append({**proof, "url": url})
    maximum = max(0, int(endpoint.get("max_documents", 12)))
    depth = int(endpoint.get("max_depth", 1))
    admitted = unique[:maximum] if depth >= 1 else []
    omitted = unique[len(admitted):]
    pagination = []
    for ordinal, node in enumerate(soup.find_all(["a", "button"]), 1):
        text = node.get_text(" ", strip=True)
        rel = node.get("rel", [])
        classes = " ".join(node.get("class", [])) + " " + str(node.get("id", ""))
        href = node.get("href", "")
        label = node.get("aria-label", "")
        if ("next" in rel or re.fullmatch(r"(?:next(?: page)?|previous(?: page)?|load more|show more|older(?: posts| news)?|more (?:news|stories|posts|results))", text, re.I)
                or re.search(r"pagination|pager|load.?more", classes + " " + label, re.I)
                or re.search(r"[?&](?:page|offset)=\d+|/page/\d+", href, re.I)):
            pagination.append({"locator": f"document:a-or-button[{ordinal}]", "text": text[:160],
                               "href": href or None, "resolved_url": urljoin(endpoint["url"], href) if href else None,
                               "assessment": "not_followed_by_this_reference_audit; further pages unknown"})
    return {"selector": selector, "selector_matches": len(nodes), "duplicate_or_self_links": duplicates,
            "all_unique_allowed_links": unique, "admitted_links": admitted, "excluded_links": rejected,
            "cap_omitted_links": omitted, "max_depth": depth, "max_documents": maximum,
            "pagination_signals": pagination, "pagination_complete": None,
            "pagination_limit": "Static signals only. Absence of a signal does not prove no archive/API/dynamic pagination."}


def latest_raw(db, root, url):
    row = db.execute("""SELECT r.retrieval_id,r.retrieved_at,r.url,b.* FROM raw_retrievals r
        JOIN raw_blobs b USING(raw_id) WHERE r.source='competition_web' AND r.url=?
        ORDER BY r.retrieved_at DESC,r.rowid DESC LIMIT 1""", (url,)).fetchone()
    if not row:
        return None, None
    result = dict(row)
    path = root / result["path"]
    result["file_exists"] = path.is_file()
    content = path.read_bytes() if path.is_file() else None
    result["hash_verified"] = content is not None and hashlib.sha256(content).hexdigest() == result["sha256"]
    return result, content


def actual(db, root, endpoint, expected):
    raw, _ = latest_raw(db, root, expected["url"])
    rows = db.execute("""SELECT record_id,raw_id,data_json,quality,parser_version,created_at FROM records
        WHERE dataset='competition_snapshots' AND source='competition_web'
        AND json_extract(data_json,'$.endpoint_id')=? AND json_extract(data_json,'$.url')=?
        ORDER BY sequence DESC""", (endpoint["endpoint_id"], expected["url"])).fetchall()
    snapshot = next((dict(row) for row in rows if raw and row["raw_id"] == raw["raw_id"]), None)
    if snapshot:
        parsed = json.loads(snapshot.pop("data_json"))
        snapshot.update(valid=parsed.get("valid", False), issues=parsed.get("issues", []),
                        semantic_stage=parsed.get("semantic_stage"))
    inv = db.execute("""SELECT resource_id,resource_key,raw_state,refresh_state,parse_state,upstream_state,last_error,
        metadata_json FROM machine_inventory WHERE source_id='competition_web' AND url=?
        AND json_extract(metadata_json,'$.endpoint.endpoint_id')=? ORDER BY last_discovered_at DESC""",
                     (expected["url"], endpoint["endpoint_id"])).fetchall()
    matched = [dict(row) for row in inv if json.loads(row["metadata_json"])["endpoint"].get("link_selector") == endpoint.get("link_selector")]
    inventory = matched[0] if matched else None
    if inventory:
        inventory.pop("metadata_json", None)
        inventory["unresolved_gaps"] = [{**dict(row), "details": json.loads(row["details_json"])} for row in db.execute(
            """SELECT code,status,details_json FROM machine_gaps WHERE source_id='competition_web'
            AND resolved_at IS NULL AND json_extract(details_json,'$.resource_key')=?""", (inventory["resource_key"],))]
        for gap in inventory["unresolved_gaps"]:
            gap.pop("details_json", None)
    obtained = bool(raw and raw["file_exists"] and raw["hash_verified"])
    return {**expected, "raw": raw, "obtained_verified_bytes": obtained,
            "normalization": snapshot, "normalization_valid": bool(snapshot and snapshot["valid"]),
            "production_inventory": inventory,
            "parse_complete": bool(snapshot and snapshot["valid"] and inventory and inventory["parse_state"] == "parsed"),
            "missing_reason": None if obtained else "No exact-URL saved bytes or file/hash verification failed"}


def build_report(storage, config):
    root, config_path = Path(storage).resolve(), Path(config).resolve()
    config_bytes = config_path.read_bytes()
    bundle = json.loads(config_bytes.decode("utf-8-sig"))
    database = root / "data.sqlite3"
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "database": str(database),
              "config": str(config_path), "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
              "method": "Independent BeautifulSoup CSS reference extraction plus urllib; no production enumeration imports and no network",
              "reference_scope": "Current saved entry snapshots, configured selectors, depth 1, at most 12 children per entry; entry itself is included",
              "website_coverage_complete": False, "semantic_coverage_evaluated": False, "endpoints": [], "out_of_scope_endpoints": []}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        for endpoint in bundle["endpoints"]:
            if not endpoint.get("enumerate_links"):
                result["out_of_scope_endpoints"].append({"endpoint_id": endpoint["endpoint_id"], "url": endpoint["url"], "reason": "No configured link enumeration selector; not included in this four-entry reference denominator"})
                continue
            raw, content = latest_raw(db, root, endpoint["url"])
            item = {"endpoint_id": endpoint["endpoint_id"], "entity_id": endpoint["entity_id"], "url": endpoint["url"], "entry_raw": raw}
            if not raw or not raw["hash_verified"]:
                item.update(reference_available=False, reason="Entry bytes unavailable or hash mismatch", counts={"expected": None})
                result["endpoints"].append(item)
                continue
            reference = reference_links(content, endpoint)
            expected = [{"url": endpoint["url"], "role": "entry", "locator": "configured_entry"}] + [{**row, "role": "child"} for row in reference["admitted_links"]]
            materials = [actual(db, root, endpoint, row) for row in expected]
            counts = {"expected": len(materials), "obtained": sum(row["obtained_verified_bytes"] for row in materials),
                      "normalization_valid": sum(row["normalization_valid"] for row in materials),
                      "parse_complete": sum(row["parse_complete"] for row in materials),
                      "missing_raw": sum(not row["obtained_verified_bytes"] for row in materials),
                      "raw_obtained_but_not_parse_complete": sum(row["obtained_verified_bytes"] and not row["parse_complete"] for row in materials),
                      "cap_omitted": len(reference["cap_omitted_links"])}
            item.update(reference_available=True, reference=reference, materials=materials, counts=counts,
                        bounded_acquisition_complete=counts["expected"] == counts["obtained"],
                        full_website_or_archive_complete=False, deeper_links="Child-page outgoing links are outside depth 1; not assumed absent or acquired.")
            result["endpoints"].append(item)
    keys = ("expected", "obtained", "normalization_valid", "parse_complete", "missing_raw", "raw_obtained_but_not_parse_complete", "cap_omitted")
    result["totals"] = {key: sum(item["counts"].get(key, 0) or 0 for item in result["endpoints"]) for key in keys}
    return result


def markdown(report):
    lines = ["# 独立资料参考清单核验", "", f"生成时间：{report['generated_at']}。只读核对保存原文，未请求网页。", "",
             "分母先从保存的入口原文独立建立：BeautifulSoup 读取配置 CSS 选择器，urllib 解析 URL，去重后按深度 1、每入口最多 12 个子链接选取，另计入口本身。未调用生产适配器枚举函数。", "",
             "|入口|应得（含入口）|原文已得且哈希正确|规范化有效|解析完成|已有原文但解析未完成|上限遗漏|",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for item in report["endpoints"]:
        counts = item["counts"]
        lines.append("|" + item["endpoint_id"] + "|" + "|".join(str(counts.get(key, "未知")) for key in ("expected", "obtained", "normalization_valid", "parse_complete", "raw_obtained_but_not_parse_complete", "cap_omitted")) + "|")
    lines += ["", "这只是当前四个入口快照和已配置选择器的有界资料核验，不是网站全量、历史档案、所有新文章或语义事实覆盖率。未跟随分页；即使没有可见分页按钮，动态接口、归档和更深层链接仍未知。详细 URL、原文哈希、定位、抓取时间、解析状态和拒绝原因见 JSON。", "", "分页与范围限制："]
    for item in report["endpoints"]:
        signals = item.get("reference", {}).get("pagination_signals", [])
        lines.append(f"- {item['endpoint_id']}：发现 {len(signals)} 个可能分页/更多结果入口，未跟随；子页外链因深度 1 不纳入本分母。")
        for row in signals:
            lines.append(f"  - {row['text'] or '无文本'}：{row['resolved_url'] or '无静态 URL'}")
    lines += ["", "已有原文但生产解析未标完成："]
    for item in report["endpoints"]:
        for material in item.get("materials", []):
            if material["obtained_verified_bytes"] and not material["parse_complete"]:
                codes = [gap["code"] for gap in (material.get("production_inventory") or {}).get("unresolved_gaps", [])]
                lines.append(f"- {material['url']}：{', '.join(codes) or '见 JSON 状态'}；规范化有效={material['normalization_valid']}。")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", default="data/competition-validation")
    parser.add_argument("--config", default="configs/competition-trial.json")
    parser.add_argument("--out", default="reports/v5/independent-material-inventory")
    args = parser.parse_args()
    report = build_report(args.storage, args.config)
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False))
