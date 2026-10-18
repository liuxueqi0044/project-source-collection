"""Optional sources. Credentials never go into normalized records.

These adapters preserve vendor bytes and report gaps instead of substituting a
different market/feed. Network transport and immutable storage are injected.
"""
from __future__ import annotations

import hashlib
import csv
import io
import importlib.metadata
import json
import os
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PARSER_VERSION = "providers-1.0"


def _skip(store, source, reason, dataset=None):
    store.issue("source_unavailable", reason, dataset=dataset, details={"source": source})
    return {"status": "skipped", "count": 0, "reason": reason}


def _json(client, url, source, **kwargs):
    content, raw = client.fetch(url, source=source, **kwargs)
    return json.loads(content), raw


def _next_day(day, zone="America/New_York"):
    d = date.fromisoformat(day) + timedelta(days=1)
    return datetime.combine(d, time(), ZoneInfo(zone)).astimezone(timezone.utc).isoformat()


def _checkpoint_key(source, params):
    # Exclude secrets even from checkpoint hashes, for transparent provenance.
    public = {k: v for k, v in params.items() if k not in {"api_key", "page_token", "offset"}}
    return source + ":" + hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()


def _put(store, dataset, record_key, data, **kwargs):
    """Repeated current snapshots do not acquire a new public time/version."""
    if kwargs.get("availability_basis", "").startswith("first_observed"):
        previous = store.db.execute("SELECT data_json,available_at,source_locator FROM records WHERE dataset=? AND source=? AND record_key=? ORDER BY sequence DESC LIMIT 1", (dataset, kwargs["source"], record_key)).fetchone()
        if previous is not None and json.loads(previous["data_json"]) == data:
            kwargs["available_at"] = previous["available_at"]
            kwargs["source_locator"] = previous["source_locator"]
    return store.put_record(dataset, record_key, data, **kwargs)


def _pages(store, client, url, source, params, headers=None):
    key = _checkpoint_key(url, params)
    cp = store.checkpoint(key) or {}
    token = cp.get("next_page_token") if not cp.get("complete") else None
    seen = set()
    while True:
        query = dict(params)
        if token:
            query["page_token"] = token
        payload, raw = _json(client, url, source, params=query, headers=headers)
        yield payload, raw
        token = payload.get("next_page_token")
        store.checkpoint(key, {"next_page_token": token, "complete": not bool(token)})
        if not token:
            break
        if token in seen:
            raise ValueError(f"{source}: repeated page token")
        seen.add(token)


def collect_alpaca(store, client, config):
    """Raw bars and corporate actions; requires account keys and explicit feed.

    Bar timestamps denote their interval, NOT when this returned vendor version
    became public. Both bars and actions are only visible from acquisition absent
    independent announcement evidence. This intentionally limits historical PIT.
    """
    cfg = config.get("alpaca", {})
    if str(config.get("market", "US")).upper() not in {"US", "USA"}:
        return _skip(store, "alpaca", "Alpaca adapter is US-only; configure an A-share provider separately.")
    if not cfg.get("enabled", False):
        return _skip(store, "alpaca", "Alpaca disabled; daily bars and vendor corporate actions not acquired.", "daily_bars")
    key = os.environ.get(cfg.get("key_env", "APCA_API_KEY_ID"))
    secret = os.environ.get(cfg.get("secret_env", "APCA_API_SECRET_KEY"))
    if not key or not secret:
        return _skip(store, "alpaca", "Missing Alpaca credentials; no licensed market data was fetched.", "daily_bars")
    feed = cfg.get("feed")
    if feed not in {"iex", "sip", "otc", "boats"}:
        raise ValueError("alpaca.feed must explicitly be iex, sip, otc or boats")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    counts = {"daily_bars": 0, "benchmark_bars": 0, "corporate_actions": 0}
    failures = []
    for security in config.get("securities", []):
        symbol = security.get("alpaca_symbol", security["symbol"])
        start, end = security.get("start", config["start"]), security.get("end", config["end"])
        sec_id, company_id = security["security_id"], security.get("company_id")
        dataset = "benchmark_bars" if security.get("is_benchmark", False) else "daily_bars"
        params = {"symbols": symbol, "timeframe": "1Day", "adjustment": "raw", "feed": feed,
                  "start": start, "end": end + "T23:59:59Z", "asof": "-",
                  "limit": 10000, "sort": "asc", "currency": security.get("currency", "USD")}
        store.issue("historical_identity_unverified", "Symbol mapping is disabled; configured identity and historical ticker validity require evidence.",
                    dataset=dataset, security_id=sec_id, details={"symbol": symbol, "asof": "-"})
        try:
            for payload, raw in _pages(store, client, "https://data.alpaca.markets/v2/stocks/bars", "alpaca", params, headers):
                for i, bar in enumerate(payload.get("bars", {}).get(symbol, []) or []):
                    stamp = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
                    event_day = stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat()
                    if not start <= event_day <= end:
                        continue
                    values = {name: bar.get(field) for name, field in
                              (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v"), ("vwap", "vw"), ("trade_count", "n"))}
                    values.update({"symbol": symbol, "date": event_day, "bar_timestamp": bar["t"], "feed": feed,
                                   "adjustment": "raw", "volume_unit": "shares", "price_unit": "currency_per_share",
                                   "turnover": None, "turnover_missing_reason": "vendor_bar_has_no_exact_turnover",
                                   "session": "provider_daily_aggregation_trade_condition_rules",
                                   "session_timezone": "America/New_York", "coverage": "IEX only" if feed == "iex" else feed,
                                   "return_basis": "price_only_excludes_cash_distributions", "symbol_mapping": "disabled",
                                   "historical_revision_timestamp": None})
                    _put(store, dataset, f"{sec_id}|{feed}|{event_day}", values,
                                     source="alpaca", raw_id=raw["raw_id"], company_id=company_id, security_id=sec_id,
                                     available_at=raw["retrieved_at"], availability_basis="first_observed_vendor_version",
                                     event_date=event_day, source_locator=f"/bars/{symbol}/{i}",
                                     unit="currency_per_share;shares", currency=security.get("currency", "USD"),
                                     parser_version=PARSER_VERSION)
                    counts[dataset] += 1
        except Exception as exc:
            failures.append({"symbol": symbol, "dataset": dataset, "error_type": type(exc).__name__})
            store.issue("adapter_failed", "Alpaca bar collection failed; completed pages remain saved.", dataset=dataset,
                        security_id=sec_id, details={"error_type": type(exc).__name__})
        if not cfg.get("corporate_actions", True):
            continue
        action_params = {"symbols": symbol, "start": cfg.get("actions_start", start),
                         "end": cfg.get("actions_end", end), "limit": 1000, "sort": "asc", "data_quality": "all"}
        try:
            for payload, raw in _pages(store, client, "https://data.alpaca.markets/v1/corporate-actions", "alpaca", action_params, headers):
                groups = payload.get("corporate_actions", {})
                if not isinstance(groups, dict):
                    raise ValueError("Unexpected corporate_actions response shape")
                for kind, actions in groups.items():
                    for i, action in enumerate(actions or []):
                        action_id = action.get("id")
                        if not action_id:
                            store.issue("missing_source_id", "Corporate action without vendor ID retained only in raw JSON.", dataset="corporate_actions", security_id=sec_id)
                            continue
                        values = dict(action)
                        values.update({"action_type": kind.rstrip("s"), "vendor_group": kind,
                                       "vendor_action_type": kind.rstrip("s"),
                                       "query_date_basis": "process_date", "announcement_time_verified": False,
                                       "date_precision": "day", "source_creation_time_guaranteed": False})
                        if kind in {"forward_splits", "reverse_splits"}:
                            try:
                                new, old = Decimal(str(action.get("new_rate"))), Decimal(str(action.get("old_rate")))
                                if not new.is_finite() or not old.is_finite() or new <= 0 or old <= 0:
                                    raise ValueError("Split rates must be finite and positive")
                                values.update({"action_type": "split", "split_ratio": str(new / old),
                                               "split_ratio_basis": "new_rate/old_rate_new_shares_per_old_share",
                                               "normalization_status": "mapped_vendor_rates"})
                            except (InvalidOperation, ValueError, ZeroDivisionError):
                                values.update({"split_ratio": None, "normalization_status": "invalid_or_missing_vendor_rates"})
                                store.issue("invalid_corporate_action_ratio", "Split retained but ratio cannot be normalized from positive finite vendor rates.", dataset="corporate_actions", security_id=sec_id)
                        elif kind == "cash_dividends":
                            # The vendor rate is the per-share cash distribution;
                            # the current API schema does not guarantee currency.
                            # Missing currency remains missing and blocks use in
                            # strict adjustments. Never infer prior trading day.
                            values.update({"cash_amount": action.get("rate"), "cash_amount_unit": "currency_per_share",
                                           "currency": action.get("currency"), "previous_session_date": None,
                                           "normalization_status": "mapped_rate_currency_requires_source_evidence"})
                        event_day = action.get("ex_date") or action.get("effective_date") or action.get("process_date")
                        _put(store, "corporate_actions", f"{sec_id}|{action_id}", values,
                                         source="alpaca", raw_id=raw["raw_id"], company_id=company_id, security_id=sec_id,
                                         available_at=raw["retrieved_at"], availability_basis="first_observed_vendor_version",
                                         event_date=event_day, source_locator=f"/corporate_actions/{kind}/{i}",
                                         currency=action.get("currency"), parser_version=PARSER_VERSION)
                        counts["corporate_actions"] += 1
        except Exception as exc:
            failures.append({"symbol": symbol, "dataset": "corporate_actions", "error_type": type(exc).__name__})
            store.issue("adapter_failed", "Alpaca corporate-action collection failed; completed pages remain saved.",
                        dataset="corporate_actions", security_id=sec_id, details={"error_type": type(exc).__name__})
    return {"status": "partial" if failures else "ok", "count": sum(counts.values()), "counts": counts, "failures": failures,
            "limitations": ["No original release timestamps for vendor versions", "Exact turnover absent", "Full delisted-universe coverage unverified"]}


def _fred_public_snapshot(store, client, config, cfg):
    """Explicitly requested no-key demonstration; never an ALFRED substitute."""
    count, failures = 0, []
    for series in cfg.get("series", []):
        sid = series if isinstance(series, str) else series["id"]
        try:
            content, raw = client.fetch("https://fred.stlouisfed.org/graph/fredgraph.csv", source="fred_public_snapshot",
                                         params={"id": sid, "cosd": config["start"], "coed": config["end"]})
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
            if not reader.fieldnames or sid not in reader.fieldnames:
                raise ValueError("Unexpected FRED public CSV headers")
            date_field = "observation_date" if "observation_date" in reader.fieldnames else "DATE"
            for i, row in enumerate(reader):
                obs_date = row[date_field]
                if not config["start"] <= obs_date <= config["end"]:
                    continue
                value = None if row[sid] in {".", ""} else row[sid]
                data = {"series_id": sid, "observation_date": obs_date, "value": value, "value_type": "decimal_string",
                        "units": series.get("unit") if isinstance(series, dict) else None,
                        "unit_basis": "configured_not_verified_against_series_metadata",
                        "vintage_status": "unknown_current_snapshot", "historical_revision_timestamp": None,
                        "missing_reason": "source_missing" if value is None else None}
                _put(store, "macro_observations", f"{sid}|{obs_date}", data, source="fred_public_snapshot", raw_id=raw["raw_id"],
                     event_date=obs_date, period_start=obs_date, available_at=raw["retrieved_at"],
                     availability_basis="first_observed_current_snapshot", source_locator=f"CSV row {i + 2}, column {sid}",
                     unit=data["units"], quality="missing" if value is None else "unverified", parser_version=PARSER_VERSION)
                count += 1
        except Exception as exc:
            failures.append({"series": sid, "error_type": type(exc).__name__})
            store.issue("adapter_failed", "FRED public snapshot collection failed.", dataset="macro_observations",
                        details={"series": sid, "error_type": type(exc).__name__})
    store.issue("historical_vintages_unavailable", "Public FRED CSV is a current snapshot. Historical release/revision states cannot be reconstructed.", dataset="macro_observations")
    return {"status": "partial" if failures else "snapshot_only", "count": count, "failures": failures,
            "reason": "No API key: explicit public CSV mode; vintage unknown and usable only from acquisition."}


def collect_fred(store, client, config):
    """ALFRED real-time periods; optional explicit current-only CSV demonstration."""
    cfg = config.get("fred", {})
    if not cfg.get("enabled", False):
        return _skip(store, "fred", "FRED/ALFRED disabled; historical vintages not acquired.", "macro_observations")
    key = os.environ.get(cfg.get("key_env", cfg.get("api_key_env", "FRED_API_KEY")))
    if not key:
        if cfg.get("allow_public_snapshot", False):
            return _fred_public_snapshot(store, client, config, cfg)
        return _skip(store, "fred", "Missing FRED_API_KEY; current-value CSV is not a vintage substitute.", "macro_observations")
    count, failures = 0, []
    for series in cfg.get("series", []):
        sid = series if isinstance(series, str) else series["id"]
        try:
            meta, meta_raw = _json(client, "https://api.stlouisfed.org/fred/series", "fred",
                                   params={"series_id": sid, "api_key": key, "file_type": "json"})
            info = meta["seriess"][0]
            params = {"series_id": sid, "api_key": key, "file_type": "json", "units": "lin", "output_type": 1,
                      "observation_start": config["start"], "observation_end": config["end"],
                      "realtime_start": cfg.get("vintage_start", "1776-07-04"),
                      "realtime_end": cfg.get("vintage_end", datetime.now(timezone.utc).date().isoformat()),
                      "limit": 100000, "sort_order": "asc"}
            cp_key = _checkpoint_key("fred:" + sid, params)
            cp = store.checkpoint(cp_key) or {}
            offset = 0 if cp.get("complete") else int(cp.get("offset", 0))
            while True:
                query = dict(params, offset=offset)
                payload, raw = _json(client, "https://api.stlouisfed.org/fred/series/observations", "fred", params=query)
                observations = payload.get("observations", [])
                for i, observation in enumerate(observations):
                    vintage = observation["realtime_start"]
                    obs_date = observation["date"]
                    value = None if observation["value"] == "." else observation["value"]
                    # A finite requested realtime_start clips older vintages. They
                    # become safely visible at the requested boundary, not earlier.
                    values = {"series_id": sid, "observation_date": obs_date, "value": value,
                              "value_type": "decimal_string", "units": info.get("units"),
                              "frequency": info.get("frequency"), "seasonal_adjustment": info.get("seasonal_adjustment"),
                              "realtime_start": vintage, "metadata_as_of": "current_retrieval",
                              "realtime_end_location": "raw_response_only_query_window_can_clip",
                              "availability_assumption": "next_day_midnight_America_New_York",
                              "missing_reason": "source_dot_missing" if value is None else None}
                    store.put_record("macro_observations", f"{sid}|{obs_date}", values,
                                     source="fred", raw_id=raw["raw_id"], published_at=datetime.combine(date.fromisoformat(vintage), time(), ZoneInfo("America/New_York")).isoformat(),
                                     publication_precision="day", publication_timezone="America/New_York",
                                     available_at=_next_day(vintage), availability_basis="alfred_realtime_start_next_day_conservative",
                                     event_date=obs_date, period_start=obs_date,
                                     source_locator=f"series_id={sid};observation_date={obs_date};realtime_start={vintage}", unit=info.get("units"),
                                     quality="missing" if value is None else "unverified", parser_version=PARSER_VERSION)
                    count += 1
                offset += len(observations)
                total = int(payload.get("count", offset))
                complete = offset >= total
                store.checkpoint(cp_key, {"offset": offset, "complete": complete})
                if complete:
                    break
                if not observations:
                    raise ValueError("FRED empty page before declared count")
        except Exception as exc:
            failures.append({"series": sid, "error_type": type(exc).__name__})
            store.issue("adapter_failed", "FRED vintage collection failed; completed pages remain saved.", dataset="macro_observations",
                        details={"series": sid, "error_type": type(exc).__name__})
    return {"status": "partial" if failures else "ok", "count": count, "failures": failures,
            "limitations": ["ALFRED vintage dates have day precision; intraday release time unverified", "Series metadata is a current snapshot"]}


def collect_calendar(store, client, config):
    """Generate exchange sessions with versioned OSS rules, including closed days.

    This is today's library view of the calendar, not a historical announcement
    archive. It cannot establish when emergency closures became public.
    """
    cfg = config.get("calendar", {})
    if not cfg.get("enabled", True):
        return _skip(store, "exchange_calendars", "Calendar disabled.", "calendar_sessions")
    try:
        import exchange_calendars as xcals
        import pandas as pd
    except ImportError:
        return _skip(store, "exchange_calendars", "Install the calendar optional dependencies to generate sessions.", "calendar_sessions")
    market = str(config.get("market", "US")).upper()
    default = ["XNYS"] if market in {"US", "USA"} else ["XSHG"]
    names = cfg.get("exchanges", default)
    version = importlib.metadata.version("exchange_calendars")
    count, failures = 0, []
    for name in names:
        try:
            calendar = xcals.get_calendar(name, start=config["start"], end=config["end"])
            session_map = {str(index.date()): row for index, row in calendar.schedule.iterrows()}
            rows = []
            for day in pd.date_range(config["start"], config["end"], freq="D"):
                label = str(day.date())
                row = session_map.get(label)
                def stamp(*columns):
                    if row is None:
                        return None
                    value = next((row.get(c) for c in columns if c in row), None)
                    if value is None or pd.isna(value):
                        return None
                    value = pd.Timestamp(value)
                    if value.tzinfo is None:
                        value = value.tz_localize("UTC")
                    return value.tz_convert("UTC").isoformat()
                rows.append({"exchange": name, "date": label, "is_session": row is not None,
                             "open": stamp("open", "market_open"), "close": stamp("close", "market_close"),
                             "break_start": stamp("break_start"), "break_end": stamp("break_end"),
                             "timezone": str(calendar.tz), "calendar_package": "exchange_calendars",
                             "calendar_version": version, "coverage": "regular_exchange_sessions_only",
                             "historical_announcement_time": None, "security_specific_halts_included": False})
            material = {"exchange": name, "version": version, "start": config["start"], "end": config["end"], "sessions": rows}
            raw = store.put_raw(json.dumps(material, sort_keys=True).encode(), source="exchange_calendars",
                                url=f"https://github.com/gerrymanoim/exchange_calendars?calendar={name}&version={version}",
                                media_type="application/json", metadata={"generated": True, "library_version": version})
            for i, row in enumerate(rows):
                _put(store, "calendar_sessions", name + "|" + row["date"], row, source="exchange_calendars",
                                 raw_id=raw["raw_id"], available_at=raw["retrieved_at"],
                                 availability_basis="first_observed_current_library_rules", event_date=row["date"],
                                 source_locator=f"/sessions/{i}", quality="unverified", knowledge_type="system_inference",
                                 parser_version=PARSER_VERSION)
                count += 1
        except Exception as exc:
            failures.append({"exchange": name, "error_type": type(exc).__name__, "reason": str(exc)})
            store.issue("calendar_generation_failed", f"Unable to generate {name} within configured dates.", dataset="calendar_sessions",
                        details={"error_type": type(exc).__name__, "reason": str(exc)})
    store.issue("calendar_historical_knowledge_gap", "Library calendars do not prove historical announcement time, security halts or dated trading rules.", dataset="calendar_sessions")
    return {"status": "partial" if failures else "ok", "count": count, "version": version, "failures": failures}
