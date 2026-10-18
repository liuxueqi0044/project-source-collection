"""Resolve configuration into evidenced issuer/current-listing identities.

SEC CIK identifies a filing entity, not an exchange security. Current directory
snapshots never establish historical pool membership or ticker continuity.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

DIRECTORY_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
DIRECTORY_FALLBACK_URL = "https://www.sec.gov/files/company_tickers.json"
PARSER_VERSION = "sec-universe-2.0"


def normalize_cik(value) -> str:
    value = str(value).strip()
    if value.lower().startswith("sec:"):
        value = value[4:]
    if not re.fullmatch(r"\d{1,10}", value) or int(value) == 0:
        raise ValueError("CIK must contain 1-10 digits and be nonzero")
    return value.zfill(10)


def sec_headers(config) -> dict:
    import os
    settings = config.get("sec", {})
    identity = settings.get("user_agent") or os.environ.get(settings.get("user_agent_env", "SEC_USER_AGENT"), "")
    if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", identity) or "example.com" in identity:
        raise ValueError("Configure SEC_USER_AGENT or sec.user_agent with a real contact email")
    return {"User-Agent": identity, "Accept": "application/json,text/html,*/*"}


def directory_rows(content: bytes) -> list[dict]:
    payload = json.loads(content)
    if isinstance(payload, dict) and isinstance(payload.get("fields"), list) and isinstance(payload.get("data"), list):
        rows = [dict(zip(payload["fields"], row)) for row in payload["data"]]
    elif isinstance(payload, dict) and all(isinstance(row, dict) for row in payload.values()):
        rows = list(payload.values())
    else:
        raise ValueError("Unsupported SEC directory response; not a ticker list")
    normalized = []
    for row in rows:
        try:
            cik = normalize_cik(row.get("cik", row.get("cik_str")))
        except ValueError:
            continue
        symbol = str(row.get("ticker") or "").upper().strip()
        if symbol:
            normalized.append({"cik": cik, "symbol": symbol, "name": row.get("name", row.get("title")),
                               "exchange": row.get("exchange"), "source_row": row})
    if not normalized:
        raise ValueError("SEC directory has no valid CIK/ticker rows")
    return normalized


def security_identity(row: dict) -> dict:
    """A local listing surrogate is explicit; do not claim a global security ID."""
    cik = normalize_cik(row["cik"])
    company_id = "sec:" + cik
    explicit_class = row.get("share_class_id") or row.get("figi") or row.get("isin")
    symbol = str(row.get("symbol") or "").upper().strip() or None
    if row.get("security_id"):
        security_id = str(row["security_id"])
        basis = "explicit_user_security_identifier_not_independently_verified"
    elif explicit_class:
        digest = hashlib.sha256(str(explicit_class).encode()).hexdigest()[:20]
        security_id = company_id + ":class:" + digest
        basis = "explicit_user_share_class_identifier_not_independently_verified"
    elif symbol:
        # Exchange is mutable; excluding it preserves IDs across venue transfers.
        digest = hashlib.sha256(symbol.encode()).hexdigest()[:20]
        security_id = company_id + ":listing:" + digest
        basis = "issuer_and_current_symbol_surrogate; cross_symbol_continuity_unknown"
    else:
        security_id = None
        basis = "issuer_only; security_identity_unknown"
    return {**row, "cik": cik, "company_id": company_id, "security_id": security_id,
            "symbol": symbol, "security_mapping_basis": basis, "snapshot_only": True,
            "historical_membership_verified": False}


def persist_listing(store, row, raw, *, source="sec_directory", locator=None):
    if not row.get("security_id"):
        return None
    return store.put_record("securities", row["security_id"], {
        **{key: row.get(key) for key in ("company_id", "security_id", "symbol", "name", "exchange", "currency", "security_type", "share_class_id", "security_mapping_basis")},
        "snapshot_only": True, "historical_membership_verified": False,
        "listing_date": None, "delisting_date": None, "ticker_valid_from": None, "ticker_valid_to": None,
        "missing_reason": "SEC current directory does not establish security class, currency, listing dates or historical membership",
        "evidence_excerpt": json.dumps(row.get("source_row", {k: row.get(k) for k in ("cik", "symbol", "exchange")}), sort_keys=True),
    }, source=source, raw_id=raw["raw_id"], company_id=row["company_id"], security_id=row["security_id"],
        parser_version=PARSER_VERSION, quality="unverified", source_locator=locator or "directory row matching CIK and ticker",
        availability_basis="unknown_current_directory_snapshot")


def resolve_universe(store, client, config: dict) -> dict:
    settings = config.get("universe", {})
    market = str(config.get("market", "US")).upper()
    gaps = []
    result = {"securities": [], "gaps": gaps, "snapshot_only": True, "historical_pool_complete": False}

    def gap(code, message, **details):
        item = {"code": code, "message": message, "source_id": "sec_directory", "details": details}
        gaps.append(item)
        store.issue(code, message, dataset="securities", details=details)

    if market not in ("US", "USA", "NYSE", "NASDAQ"):
        gap("UNIVERSE_MARKET_UNSUPPORTED", "SEC universe discovery is implemented only for US SEC issuers", market=market)
        return result
    mode = settings.get("mode", "configured")
    if mode not in ("configured", "sec_current"):
        raise ValueError("universe.mode must be configured or sec_current")
    configured = [dict(row) for row in config.get("securities", [])]
    symbols = [str(symbol).upper().strip() for symbol in settings.get("symbols", [])]
    configured.extend({"cik": value} for value in settings.get("ciks", []))
    if settings.get("csv_file"):
        path = Path(settings["csv_file"])
        if not path.is_absolute():
            path = Path(config.get("_config_dir", ".")) / path
        content = path.read_bytes()
        raw = store.put_raw(content, source="configured_universe", url=path.resolve().as_uri(), media_type="text/csv")
        csv_rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        configured.extend({**row, "_config_raw": raw} for row in csv_rows)
    symbols.extend(str(row["symbol"]).upper().strip() for row in configured if row.get("symbol") and not row.get("cik"))
    directory, directory_raw = [], None
    if mode == "sec_current" or symbols:
        errors = []
        try:
            headers = sec_headers(config)
        except ValueError as exc:
            headers = None
            errors.append(str(exc))
        if headers:
            for url in (DIRECTORY_URL, DIRECTORY_FALLBACK_URL):
                try:
                    content, directory_raw = client.fetch(url, source="sec_directory", headers=headers,
                        refresh=bool(settings.get("refresh", True)))
                    directory = directory_rows(content)
                    break
                except Exception as exc:
                    if type(exc).__name__ == "BudgetExceeded":
                        raise
                    errors.append(f"{url}: {type(exc).__name__}: {exc}")
        if not directory:
            gap("SEC_DIRECTORY_UNAVAILABLE", "Current symbol discovery unavailable; explicit CIKs may continue", failures=errors)
    selected = []
    if mode == "sec_current":
        selected.extend({**row, "_directory_raw": directory_raw} for row in directory)
    else:
        by_symbol = {}
        for row in directory:
            by_symbol.setdefault(row["symbol"], []).append(row)
        for symbol in sorted(set(symbols)):
            matches = by_symbol.get(symbol, [])
            if len(matches) != 1:
                gap("UNIVERSE_SYMBOL_UNRESOLVED", "Symbol absent, ambiguous, or directory unavailable; provide explicit CIK", symbol=symbol, match_count=len(matches))
                continue
            override = next((row for row in configured if str(row.get("symbol", "")).upper() == symbol and not row.get("cik")), {})
            selected.append({**matches[0], **override, "_directory_raw": directory_raw})
    selected.extend(row for row in configured if row.get("cik"))
    identified = {}
    for row in selected:
        try:
            clean = security_identity({k: v for k, v in row.items() if not k.startswith("_")})
        except ValueError as exc:
            gap("UNIVERSE_CIK_INVALID", str(exc), configured_cik=str(row.get("cik")))
            continue
        key = (clean["company_id"], clean["security_id"])
        identified[key] = clean
        raw = row.get("_directory_raw") or row.get("_config_raw")
        if raw:
            clean["identity_evidence_raw_id"] = raw["raw_id"]
            persist_listing(store, clean, raw, source=raw["source"])
    all_rows = sorted(identified.values(), key=lambda r: (r["company_id"], r["security_id"] or ""))
    offset = int(settings.get("offset", 0))
    limit = settings.get("limit")
    if offset < 0 or (limit is not None and int(limit) < 0):
        raise ValueError("Universe offset and limit must be nonnegative")
    stop = offset + int(limit) if limit is not None else len(all_rows)
    result["securities"] = all_rows[offset:stop]
    result["discovered_count"] = len(all_rows)
    result["selected_count"] = len(result["securities"])
    if offset or stop < len(all_rows):
        gap("UNIVERSE_RESOURCE_LIMIT", "Universe selection is incomplete; omitted identities remain in this explicit continuation inventory",
            total=len(all_rows), offset=offset, limit=limit, next_offset=min(stop, len(all_rows)),
            omitted=[{k: r.get(k) for k in ("company_id", "security_id", "symbol", "cik")} for r in all_rows[:offset] + all_rows[stop:]])
    gap("HISTORICAL_UNIVERSE_UNAVAILABLE", "Current listings are not an unbiased historical pool; delisted issuers can be configured by CIK but listing history requires another source")
    return result
