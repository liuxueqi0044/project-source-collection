"""Reproducible backward adjustments from raw bars and evidenced actions.

These are arithmetic price series, not portfolio returns (no tax, reinvestment
execution, cash settlement, FX, delisting proceeds or action completeness claim).
"""
from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

from .store import Store, _timestamp

METHOD_VERSION = "backward-adjustment/1.0"


def _payload(row: dict) -> dict:
    return dict(row.get("data", row))


def _date(value: Any) -> str:
    if not value:
        raise ValueError("An explicit session/ex date is required")
    return date.fromisoformat(str(value)[:10]).isoformat()


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing/invalid " + name)
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError("Invalid " + name)
    return result


def compute_adjusted_bars(bars: list[dict], actions: list[dict], *,
                          method: str = "split", as_of: str | None = None) -> list[dict]:
    if method not in {"split", "total_return"}:
        raise ValueError("method must be split or total_return")
    cutoff = _timestamp(as_of, cutoff=True) if as_of else None
    prepared = []
    for original in bars:
        row = _payload(original)
        session = _date(row.get("session_date") or row.get("date") or original.get("event_date"))
        if cutoff and session > cutoff[:10]:
            continue
        currency = row.get("currency") or original.get("currency")
        if not currency:
            raise ValueError("Raw bar currency must be explicit")
        row["session_date"], row["currency"] = session, currency
        for field in ("open", "high", "low", "close"):
            row[field] = _number(row.get(field), field, positive=True)
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]) or row["low"] > row["high"]:
            raise ValueError("Raw OHLC range is inconsistent")
        if row.get("volume") is not None:
            row["volume"] = _number(row["volume"], "volume")
            if row["volume"] < 0:
                raise ValueError("Negative volume")
        row["raw_record_id"] = original.get("record_id")
        prepared.append(row)
    prepared.sort(key=lambda r: r["session_date"])
    if not prepared:
        return []
    if len({r["session_date"] for r in prepared}) != len(prepared):
        raise ValueError("Multiple raw bars for one session: select a source first")
    currencies = {r["currency"] for r in prepared}
    if len(currencies) != 1:
        raise ValueError("Currency changes require a separate FX/corporate-action model")
    currency = next(iter(currencies))
    reference_date = cutoff[:10] if cutoff else prepared[-1]["session_date"]
    active, identifiers = [], set()
    for original in actions:
        row = _payload(original)
        # Payment, processing and generic event dates are not ex-dates. A cash
        # payment date must never become an adjustment boundary by fallback.
        effective = _date(row.get("ex_date"))
        if effective > reference_date or effective <= prepared[0]["session_date"]:
            continue
        available = original.get("effective_available_at") or original.get("available_at") or row.get("available_at")
        if cutoff:
            if available is None:
                raise ValueError("Action lacks evidenced availability; cannot adjust a point-in-time series")
            if _timestamp(available) > cutoff:
                continue
        kind = row.get("action_type") or row.get("type")
        if kind not in {"split", "cash_dividend", "dividend"}:
            raise ValueError("Unsupported corporate action requires manual treatment: " + str(kind))
        kind = "cash_dividend" if kind == "dividend" else kind
        row.update(action_type=kind, ex_date=effective, _source=original.get("source") or row.get("source") or "unspecified")
        row["action_record_id"] = original.get("record_id") or row.get("action_id")
        identifier = row["action_record_id"] or json.dumps(row, sort_keys=True)
        if identifier in identifiers:
            raise ValueError("Duplicate corporate action evidence")
        identifiers.add(identifier)
        if kind == "split":
            if row.get("split_ratio") is None and row.get("new_shares") is not None and row.get("old_shares") is not None:
                row["split_ratio"] = _number(row["new_shares"], "new_shares", positive=True) / _number(row["old_shares"], "old_shares", positive=True)
            row["split_ratio"] = _number(row.get("split_ratio"), "split_ratio", positive=True)
        elif method == "total_return":
            if (row.get("currency") or original.get("currency")) != currency:
                raise ValueError("Dividend currency missing or different from raw prices")
            amount = row.get("cash_amount", row.get("amount"))
            row["cash_amount"] = _number(amount, "cash_amount")
            if row["cash_amount"] < 0:
                raise ValueError("Negative dividend")
        active.append(row)
    factors = []
    event_sources = {}
    for action in active:
        event_sources.setdefault((action["ex_date"], action["action_type"]), set()).add(action["_source"])
    if any(len(sources) > 1 for sources in event_sources.values()):
        raise ValueError("Multiple sources describe the same dated action type; choose one action_source before adjustment")
    for action_date in sorted({a["ex_date"] for a in active}):
        events = [a for a in active if a["ex_date"] == action_date]
        split = math.prod(a["split_ratio"] for a in events if a["action_type"] == "split")
        price_factor = 1.0 / split
        dividends = [a for a in events if a["action_type"] != "split"]
        if method == "total_return" and dividends:
            if split != 1 and any(a.get("dividend_share_basis") != "post_split" for a in dividends):
                raise ValueError("Same-date split/dividend requires explicit post_split dividend share basis")
            previous = [r for r in prepared if r["session_date"] < action_date]
            if not previous:
                raise ValueError("Dividend adjustment requires the preceding raw close")
            # A gap cannot silently substitute a stale price for previous trading close.
            previous_date = previous[-1]["session_date"]
            expected_dates = {a.get("previous_session_date") for a in dividends if a.get("previous_session_date")}
            if expected_dates and expected_dates != {previous_date}:
                raise ValueError("Previous close does not match declared previous trading session")
            if not expected_dates:
                raise ValueError("Cash dividend requires previous_session_date evidence to detect missing bars")
            amount = sum(a["cash_amount"] for a in dividends)
            base_close = previous[-1]["close"] / split
            if amount >= base_close:
                raise ValueError("Dividend >= reference close: simple adjustment is unsupported")
            price_factor *= 1.0 - amount / base_close
        factors.append((action_date, price_factor, split, [a["action_record_id"] for a in events]))
    result = []
    for raw in prepared:
        applicable = [factor for factor in factors if raw["session_date"] < factor[0]]
        price_factor = math.prod(f[1] for f in applicable)
        volume_factor = math.prod(f[2] for f in applicable)
        adjusted = dict(raw)
        for field in ("open", "high", "low", "close"):
            adjusted[field] = raw[field] * price_factor
        adjusted["volume"] = None if raw.get("volume") is None else raw["volume"] * volume_factor
        adjusted.update(price_factor=price_factor, volume_factor=volume_factor,
                        adjustment_method=method, calculation_version=METHOD_VERSION,
                        adjustment_as_of=cutoff, adjustment_reference_date=reference_date,
                        corporate_action_record_ids=[rid for f in applicable for rid in f[3] if rid],
                        amount_adjustment="unchanged raw turnover; not recomputed",
                        completeness="conditional_on_supplied_corporate_actions")
        result.append(adjusted)
    return result


def materialize_adjustments(store: Store, security_id: str, *, method: str = "split",
                            as_of: str | None = None, known_at: str | None = None,
                            currency: str | None = None, action_source: str | None = None) -> dict:
    if as_of is None:
        raise ValueError("Persisted adjustments require explicit as_of")
    if method not in {"split", "total_return"}:
        raise ValueError("method must be split or total_return")
    bars = store.query("daily_bars", security_id=security_id, as_of=as_of, known_at=known_at)
    if not bars:
        return {"status": "no_data", "records": 0, "method": method, "as_of": as_of,
                "raw_id": None, "reason": "No input raw bars available at the requested cutoffs."}
    # Unknown action timing must not disappear silently from a successful adjustment.
    all_actions = store.query("corporate_actions", security_id=security_id, known_at=known_at)
    if action_source:
        chosen = [r for r in all_actions if r["source"] == action_source]
        if all_actions and not chosen:
            raise ValueError("Requested action_source has no corporate-action records for this security")
        all_actions = chosen
    for row in all_actions:
        if row["available_at"] is None:
            raise ValueError("Corporate-action availability is unknown; review before adjustment")
    actions = store.query("corporate_actions", security_id=security_id, as_of=as_of, known_at=known_at)
    if action_source:
        actions = [r for r in actions if r["source"] == action_source]
    if currency:
        if any((b["data"].get("currency") or b["currency"]) != currency for b in bars):
            raise ValueError("Requested currency does not match raw bars")
    result = compute_adjusted_bars(bars, actions, method=method, as_of=as_of)
    if not result:
        return {"status": "no_data", "records": 0, "method": method, "as_of": as_of,
                "raw_id": None, "reason": "No raw bar sessions on or before the adjustment horizon."}
    manifest = {"calculation_version": METHOD_VERSION, "security_id": security_id,
                "method": method, "as_of": as_of, "known_at": known_at, "action_source": action_source,
                "raw_bar_record_ids": [r["record_id"] for r in bars],
                "corporate_action_record_ids": [r["record_id"] for r in actions]}
    raw = store.put_raw(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode(),
                        source="derived:adjustments", url="derived://adjustments/" + security_id,
                        media_type="application/json", metadata={"kind": "calculation_input_manifest"})
    count = 0
    raw_bar_by_id = {r["record_id"]: r for r in bars}
    for row in result:
        row["action_source"] = action_source
        key = "|".join((security_id, row["session_date"], method, _timestamp(as_of, cutoff=True), known_at or "public_only", action_source or "all_nonconflicting_sources"))
        available = raw["retrieved_at"]
        previous = store.db.execute("SELECT data_json,raw_id,available_at,parser_version FROM records WHERE dataset='adjusted_bars' AND source='derived:adjustments' AND record_key=? ORDER BY sequence DESC LIMIT 1", (key,)).fetchone()
        # A repeated computation has a fresh run/acquisition, but the identical
        # manifest/output version was already available after its first run.
        # Inspect only the latest version: an A -> B -> A reversion is new.
        if (previous and previous["raw_id"] == raw["raw_id"]
                and previous["parser_version"] == METHOD_VERSION
                and json.loads(previous["data_json"]) == row):
            available = previous["available_at"]
        store.put_record("adjusted_bars", key, row, source="derived:adjustments", raw_id=raw["raw_id"],
                         company_id=raw_bar_by_id[row["raw_record_id"]].get("company_id"),
                         security_id=security_id, event_date=row["session_date"], currency=row["currency"],
                         unit="price_per_share;shares", available_at=available,
                         availability_basis="system_calculation_time", quality="derived_conditional",
                         knowledge_type="system_inference", parser_version=METHOD_VERSION,
                         source_locator="input manifest and raw_bar_record_id")
        count += 1
    return {"status": "ok" if count else "no_data", "records": count,
            "method": method, "as_of": as_of, "raw_id": raw["raw_id"],
            "limitation": "Action completeness is not certified; no taxes, FX, execution or delisting proceeds."}
