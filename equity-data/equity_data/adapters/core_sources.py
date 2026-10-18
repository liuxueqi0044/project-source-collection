"""Small explicit task adapters over existing open-source/provider normalizers."""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
import os
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .base import BaseAdapter, DependencyMissing, UnsupportedCapability
from ..providers import _put, _next_day

VERSION = "core-adapters-2.0"


def emit(ctx, source, key, kind, dataset, url, metadata, scope=None):
    scope = scope or {}
    return ctx.emit(dict(source_id=source, resource_key=key, kind=kind, data_type=dataset, url=url,
                         company_id=scope.get("company_id"), security_id=scope.get("security_id"), metadata=metadata))


class CalendarAdapter(BaseAdapter):
    source_id = "exchange_calendars"
    data_types = ("calendar_sessions",)

    def discover(self, ctx, scope):
        exchanges = ctx.config.get("calendar", {}).get("exchanges", ["XNYS" if scope["market"] == "US" else "XSHG"])
        for exchange in exchanges:
            market = scope["market"]
            if (market == "US" and exchange not in ("XNYS", "XNAS")) or (market == "CN" and exchange not in ("XSHG", "XSHE")):
                raise UnsupportedCapability("Exchange/market pair is not configured independently: " + exchange)
            emit(ctx, self.source_id, f"{exchange}:{scope['start']}:{scope['end']}", "calendar", "calendar_sessions",
                 "library://exchange_calendars/" + exchange, {**scope, "exchange": exchange})
        ctx.gap("CALENDAR_KNOWLEDGE_LIMIT", "Library sessions do not establish historical closure announcements, per-security halts or effective-date trading rules.",
                source_id=self.source_id, data_type="security_status")
        return {"enumeration_complete": True, "discovered": len(exchanges)}

    def acquire(self, ctx, resource):
        import exchange_calendars as xc
        import pandas as pd
        meta = resource["metadata"]
        cal = xc.get_calendar(meta["exchange"], start=meta["start"], end=meta["end"])
        schedule = {str(i.date()): row for i, row in cal.schedule.iterrows()}
        rows = []
        for day in pd.date_range(meta["start"], meta["end"]):
            row = schedule.get(str(day.date()))
            def stamp(name):
                val = row.get(name) if row is not None else None
                return val.isoformat() if val is not None and not pd.isna(val) else None
            rows.append({"date": str(day.date()), "exchange": meta["exchange"], "is_open": row is not None,
                         "open": stamp("open"), "close": stamp("close"), "break_start": stamp("break_start"),
                         "break_end": stamp("break_end"), "timezone": str(cal.tz), "market": meta["market"],
                         "knowledge_scope": "current_library_rules", "library_version": importlib.metadata.version("exchange_calendars")})
        content = json.dumps({"sessions": rows}, sort_keys=True).encode()
        return content, ctx.store.put_raw(content, source=self.source_id, url=resource["url"], media_type="application/json")

    def parse(self, ctx, resource, content, raw):
        ids = []
        for index, row in enumerate(json.loads(content)["sessions"]):
            ids.append(_put(ctx.store, "calendar_sessions", row["exchange"] + "|" + row["date"], row,
                            source=self.source_id, raw_id=raw["raw_id"], available_at=raw["retrieved_at"],
                            availability_basis="first_observed_current_library_rules", event_date=row["date"],
                            source_locator=f"/sessions/{index}", knowledge_type="system_inference", parser_version=VERSION))
        return {"status": "parsed", "record_ids": ids, "parsed_count": len(ids)}


class FredAdapter(BaseAdapter):
    source_id = "fred"
    data_types = ("macro_observations",)

    def discover(self, ctx, scope):
        cfg = ctx.config.get("fred", {})
        authenticated = bool(os.environ.get(cfg.get("api_key_env", "FRED_API_KEY")))
        if not authenticated and not cfg.get("allow_public_snapshot", False):
            raise DependencyMissing("FRED_API_KEY required for ALFRED vintages; explicitly enable allow_public_snapshot for current-only CSV")
        series = cfg.get("series", [])
        if not series:
            ctx.gap("SERIES_NOT_CONFIGURED", "Configure fred.series identifiers.", source_id=self.source_id)
        for entry in series:
            meta = {**scope, **({"id": entry} if isinstance(entry, str) else entry)}
            # Macro revisions can affect old observation periods on every run.
            meta["start"] = cfg.get("observation_start", ctx.config["start"])
            sid = meta["id"]
            if authenticated:
                emit(ctx, self.source_id, "metadata:" + sid, "metadata", "macro_observations", "https://api.stlouisfed.org/fred/series", {**meta, "params": {"series_id": sid, "file_type": "json"}})
            else:
                emit(ctx, self.source_id, f"csv:{sid}:{meta['start']}:{meta['end']}", "csv", "macro_observations", "https://fred.stlouisfed.org/graph/fredgraph.csv",
                     {**meta, "params": {"id": sid, "cosd": meta["start"], "coed": meta["end"]}})
                ctx.gap("HISTORICAL_VINTAGES_UNAVAILABLE", "FRED public CSV is today's snapshot; release/revision vintages and verified units require the API metadata.", source_id=self.source_id, data_type="macro_observations", details={"series_id": sid})
        return {"enumeration_complete": bool(series), "discovered": len(series)}

    def acquire(self, ctx, resource):
        params = dict(resource["metadata"].get("params", {}))
        if resource["kind"] != "csv":
            key = os.environ.get(ctx.config.get("fred", {}).get("api_key_env", "FRED_API_KEY"))
            if not key:
                raise DependencyMissing("FRED_API_KEY required for queued vintage task")
            params["api_key"] = key
        return ctx.client.fetch(resource["url"], source=self.source_id, params=params)

    def parse(self, ctx, resource, content, raw):
        meta, kind = resource["metadata"], resource["kind"]
        sid = meta["id"]
        if kind == "metadata":
            info = json.loads(content)["seriess"][0]
            cfg = ctx.config.get("fred", {})
            params = {"series_id": sid, "file_type": "json", "units": "lin", "output_type": 1,
                      "observation_start": meta["start"], "observation_end": meta["end"],
                      "realtime_start": cfg.get("vintage_start", "1776-07-04"),
                      "realtime_end": cfg.get("vintage_end", date.today().isoformat()), "limit": 100000,
                      "sort_order": "asc", "offset": 0}
            emit(ctx, self.source_id, f"observations:{sid}:{meta['start']}:{meta['end']}:0", "observations", "macro_observations",
                 "https://api.stlouisfed.org/fred/series/observations", {**meta, "series_metadata": info, "metadata_raw_id": raw["raw_id"], "params": params})
            return {"status": "parsed", "parsed_count": 0, "emitted_pages": 1}
        if kind == "csv":
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
            if not reader.fieldnames or sid not in reader.fieldnames:
                raise ValueError("Unexpected FRED CSV columns")
            rows = [{"date": row.get("observation_date", row.get("DATE")), "value": row[sid]} for row in reader]
        else:
            payload = json.loads(content)
            rows = payload["observations"]
            offset = int(meta["params"].get("offset", 0)) + len(rows)
            if offset < int(payload["count"]):
                if not rows:
                    raise ValueError("FRED returned empty page before declared total")
                params = {**meta["params"], "offset": offset}
                emit(ctx, self.source_id, f"observations:{sid}:{meta['start']}:{meta['end']}:{offset}", kind, "macro_observations", resource["url"], {**meta, "params": params})
        ids = []
        for i, row in enumerate(rows):
            day = row["date"]
            if not meta["start"] <= day <= meta["end"]:
                continue
            value = None if row["value"] in (".", "") else str(Decimal(row["value"]))
            if value is not None and not Decimal(value).is_finite():
                raise ValueError("Non-finite FRED observation")
            info = meta.get("series_metadata", {})
            data = {"series_id": sid, "observation_date": day, "value": value, "value_type": "decimal_string",
                    "units": info.get("units", meta.get("unit")), "unit_basis": "API_current_metadata" if info else "configured_unverified",
                    "frequency": info.get("frequency"), "seasonal_adjustment": info.get("seasonal_adjustment"),
                    "metadata_raw_id": meta.get("metadata_raw_id"), "vintage_status": "unknown_current_snapshot" if kind == "csv" else "ALFRED_day_precision",
                    "missing_reason": "source_missing" if value is None else None, "evidence_excerpt": json.dumps(row, sort_keys=True)}
            timing = dict(available_at=raw["retrieved_at"], availability_basis="first_observed_current_snapshot")
            if kind != "csv":
                vintage = row["realtime_start"]
                data["realtime_start"] = vintage
                timing = dict(published_at=datetime.combine(date.fromisoformat(vintage), time(), ZoneInfo("America/New_York")).isoformat(), publication_precision="day", publication_timezone="America/New_York",
                              available_at=_next_day(vintage), availability_basis="alfred_realtime_start_next_day_conservative")
            ids.append(_put(ctx.store, "macro_observations", sid + "|" + day, data, source=self.source_id, raw_id=raw["raw_id"],
                            event_date=day, period_start=day, unit=data["units"], source_locator=f"CSV row {i+2}" if kind == "csv" else f"/observations/{i}",
                            quality="missing" if value is None else "unverified", parser_version=VERSION, **timing))
        return {"status": "parsed", "record_ids": ids, "parsed_count": len(ids), "vintages": kind != "csv"}


class AlpacaAdapter(BaseAdapter):
    source_id = "alpaca"
    scope_kind = "company"
    data_types = ("daily_bars", "benchmark_bars", "corporate_actions")

    def _headers(self, ctx):
        cfg = ctx.config.get("alpaca", {})
        key, secret = os.environ.get(cfg.get("key_env", "APCA_API_KEY_ID")), os.environ.get(cfg.get("secret_env", "APCA_API_SECRET_KEY"))
        if not key or not secret:
            raise DependencyMissing("APCA_API_KEY_ID / APCA_API_SECRET_KEY and explicit data feed entitlement required")
        return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def discover(self, ctx, scope):
        self._headers(ctx)
        if scope["market"] != "US":
            raise UnsupportedCapability("Alpaca supports US scope only")
        cfg = ctx.config.get("alpaca", {})
        feed = cfg.get("feed")
        if feed not in ("iex", "sip", "otc", "boats"):
            raise ValueError("Choose an explicit Alpaca feed")
        for security in scope.get("securities", [scope]):
            symbol = security.get("alpaca_symbol", security.get("symbol"))
            if not symbol or not security.get("security_id"):
                ctx.gap("SECURITY_IDENTITY_REQUIRED", "Historical bars need an evidenced security ID and symbol mapping.", source_id=self.source_id, company_id=scope.get("company_id"))
                continue
            meta = {"security": security, "start": scope["start"], "end": scope["end"], "feed": feed, "symbol": symbol}
            types = set(scope.get("data_types") or self.data_types)
            dataset = "benchmark_bars" if security.get("is_benchmark") else "daily_bars"
            if dataset in types:
                params = {"symbols": symbol, "timeframe": "1Day", "adjustment": "raw", "feed": feed, "start": scope["start"],
                          "end": scope["end"] + "T23:59:59Z", "asof": "-", "limit": 10000, "sort": "asc"}
                emit(ctx, self.source_id, f"bars:{security['security_id']}:{feed}:{scope['start']}:{scope['end']}", "bars", dataset,
                     "https://data.alpaca.markets/v2/stocks/bars", {**meta, "params": params}, security)
            if "corporate_actions" in types:
                params = {"symbols": symbol, "start": scope["start"], "end": scope["end"], "limit": 1000, "sort": "asc", "data_quality": "all"}
                emit(ctx, self.source_id, f"actions:{security['security_id']}:{scope['start']}:{scope['end']}", "actions", "corporate_actions",
                     "https://data.alpaca.markets/v1/corporate-actions", {**meta, "params": params}, security)
            ctx.gap("HISTORICAL_IDENTITY_UNVERIFIED", "Current ticker mapping does not establish historical listing membership; Alpaca asof symbol stitching disabled.", source_id=self.source_id, company_id=scope.get("company_id"), data_type="securities")
        return {"enumeration_complete": True}

    def acquire(self, ctx, resource):
        return ctx.client.fetch(resource["url"], source=self.source_id, headers=self._headers(ctx), params=resource["metadata"]["params"])

    def parse(self, ctx, resource, content, raw):
        # Reuse the v1 audited normalizer on exactly one saved page. The replay
        # transport prohibits hidden network calls and removes pagination tokens.
        from ..providers import collect_alpaca
        payload = json.loads(content)
        meta = resource["metadata"]
        token = payload.get("next_page_token")
        if token:
            if token == meta["params"].get("page_token"):
                raise ValueError("Alpaca repeated pagination token")
            rootkey = meta.get("root_key", resource["resource_key"])
            emit(ctx, self.source_id, rootkey + ":page:" + hashlib.sha256(token.encode()).hexdigest(), resource["kind"], resource["data_type"], resource["url"],
                 {**meta, "root_key": rootkey, "params": {**meta["params"], "page_token": token}}, resource)
        payload["next_page_token"] = None
        class Replay:
            def fetch(self, url, **kwargs):
                match = (resource["kind"] == "bars" and url.endswith("/bars")) or (resource["kind"] == "actions" and url.endswith("/corporate-actions"))
                data = payload if match else {"bars": {}, "corporate_actions": {}, "next_page_token": None}
                return json.dumps(data).encode(), raw
        local = {**ctx.config, "start": meta["start"], "end": meta["end"], "securities": [meta["security"]],
                 "alpaca": {**ctx.config.get("alpaca", {}), "enabled": True, "feed": meta["feed"], "corporate_actions": resource["kind"] == "actions"}}
        result = collect_alpaca(ctx.store, Replay(), local)
        if result["status"] == "skipped":
            raise DependencyMissing(result["reason"])
        return {"status": "partial" if result.get("failures") else "parsed", "parsed_count": result["count"], "normalizer": result}


class DependencyAdapter(BaseAdapter):
    """Honest boundary for unsupported contractual data, never a mock connector."""
    def __init__(self, source_id, data_types, reason):
        self.source_id, self.data_types, self.reason = source_id, tuple(data_types), reason

    def discover(self, ctx, scope):
        for dataset in set(scope.get("data_types", self.data_types)).intersection(self.data_types):
            ctx.gap("PROVIDER_DEPENDENCY", self.reason, source_id=self.source_id, data_type=dataset, status="missing_authorization")
        raise DependencyMissing(self.reason)

    def health(self, ctx):
        return {"status": "missing_dependency", "network_checked": False, "reason": self.reason, "implemented": [], "interface_only": list(self.data_types)}
