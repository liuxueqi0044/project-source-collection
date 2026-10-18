"""Append-only evidence and point-in-time records; no network dependencies."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

DATASETS = (
    "company_profiles", "securities", "identity_history", "calendar_sessions", "security_status",
    "market_rules", "daily_bars", "adjusted_bars", "corporate_actions", "financial_facts", "filings",
    "documents", "guidance", "analyst_estimates", "earnings_actuals", "event_calendar", "benchmark_bars",
    "relations", "industry_metrics", "macro_observations", "policy_documents", "news", "research_materials",
    "operating_metrics", "commitments", "debt_maturities", "risk_disclosures", "source_candidates", "universe_snapshots",
    "entities", "entity_links", "competition_edges", "competitive_events", "competitive_event_links",
    "analysis_hypotheses", "watch_endpoints", "competition_snapshots", "review_actions",
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _timestamp(value: str, *, cutoff: bool = False) -> str:
    """Canonical UTC timestamps; date-only query cutoffs mean end of UTC day."""
    if len(value) == 10:
        if not cutoff:
            raise ValueError("Evidence timestamp must include an explicit time and UTC offset")
        dt = datetime.combine(date.fromisoformat(value), time.max, tzinfo=timezone.utc)
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timestamp must include UTC offset: " + value)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 source TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_blobs (
 raw_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
 byte_size INTEGER NOT NULL, media_type TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_retrievals (
 retrieval_id TEXT PRIMARY KEY, raw_id TEXT NOT NULL REFERENCES raw_blobs(raw_id),
 source TEXT NOT NULL REFERENCES sources(source), url TEXT NOT NULL,
 retrieved_at TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS retrieval_source_url ON raw_retrievals(source,url,retrieved_at);
CREATE INDEX IF NOT EXISTS retrieval_raw_source_time ON raw_retrievals(raw_id,source,retrieved_at);
CREATE TABLE IF NOT EXISTS records (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL UNIQUE,
 dataset TEXT NOT NULL, record_key TEXT NOT NULL, data_json TEXT NOT NULL,
 semantic_hash TEXT NOT NULL, source TEXT NOT NULL REFERENCES sources(source),
 raw_id TEXT NOT NULL REFERENCES raw_blobs(raw_id),
 retrieval_id TEXT NOT NULL REFERENCES raw_retrievals(retrieval_id),
 company_id TEXT, security_id TEXT, published_at TEXT, publication_precision TEXT NOT NULL,
 publication_timezone TEXT, available_at TEXT, availability_basis TEXT NOT NULL,
 retrieved_at TEXT NOT NULL, period_start TEXT, period_end TEXT, event_date TEXT,
 source_locator TEXT, unit TEXT, currency TEXT, quality TEXT NOT NULL,
 knowledge_type TEXT NOT NULL, parser_version TEXT NOT NULL,
 supersedes TEXT REFERENCES records(record_id), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS record_lookup ON records(dataset,source,record_key,available_at,retrieved_at);
CREATE INDEX IF NOT EXISTS record_security ON records(dataset,security_id);
CREATE INDEX IF NOT EXISTS record_company ON records(dataset,company_id);
CREATE INDEX IF NOT EXISTS records_source_resource ON records(source,json_extract(data_json,'$.resource_key'));
CREATE TABLE IF NOT EXISTS record_evidence (
 record_id TEXT NOT NULL REFERENCES records(record_id),
 retrieval_id TEXT NOT NULL REFERENCES raw_retrievals(retrieval_id),
 raw_id TEXT NOT NULL REFERENCES raw_blobs(raw_id),
 PRIMARY KEY(record_id,retrieval_id)
);
CREATE TABLE IF NOT EXISTS quality_issues (
 issue_id TEXT PRIMARY KEY, code TEXT NOT NULL, message TEXT NOT NULL, dataset TEXT,
 company_id TEXT, security_id TEXT, severity TEXT NOT NULL,
 details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
 key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS availability_constraints (
 constraint_id TEXT PRIMARY KEY, dataset TEXT NOT NULL, source TEXT NOT NULL,
 record_key TEXT NOT NULL, not_before TEXT NOT NULL, learned_at TEXT NOT NULL,
 raw_id TEXT NOT NULL REFERENCES raw_blobs(raw_id), basis TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS record_validations (
 validation_id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(record_id),
 verdict TEXT NOT NULL, reason TEXT NOT NULL, raw_id TEXT NOT NULL REFERENCES raw_blobs(raw_id),
 validated_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS validations_no_update BEFORE UPDATE ON record_validations
 BEGIN SELECT RAISE(ABORT,'validations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS validations_no_delete BEFORE DELETE ON record_validations
 BEGIN SELECT RAISE(ABORT,'validations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS availability_no_update BEFORE UPDATE ON availability_constraints
 BEGIN SELECT RAISE(ABORT,'availability evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS availability_no_delete BEFORE DELETE ON availability_constraints
 BEGIN SELECT RAISE(ABORT,'availability evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS records_no_update BEFORE UPDATE ON records
 BEGIN SELECT RAISE(ABORT,'records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records
 BEGIN SELECT RAISE(ABORT,'records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS raw_no_update BEFORE UPDATE ON raw_blobs
 BEGIN SELECT RAISE(ABORT,'raw blobs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS raw_no_delete BEFORE DELETE ON raw_blobs
 BEGIN SELECT RAISE(ABORT,'raw blobs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS retrieval_no_update BEFORE UPDATE ON raw_retrievals
 BEGIN SELECT RAISE(ABORT,'retrievals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS retrieval_no_delete BEFORE DELETE ON raw_retrievals
 BEGIN SELECT RAISE(ABORT,'retrievals are append-only'); END;
"""


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "raw").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "data.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        for dataset in DATASETS:
            # Static names only. Views expose every version; PIT filtering must
            # still use query(), not a bare SELECT against these convenience views.
            self.db.execute(f"CREATE VIEW IF NOT EXISTS {dataset} AS SELECT * FROM records WHERE dataset='{dataset}'")
        self.db.commit()

    def register_source(self, source: str, **metadata: Any) -> None:
        old = self.db.execute("SELECT metadata_json FROM sources WHERE source=?", (source,)).fetchone()
        merged = json.loads(old[0]) if old else {}
        merged.update(metadata)
        self.db.execute("INSERT INTO sources VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET metadata_json=excluded.metadata_json,updated_at=excluded.updated_at", (source, _json(merged), now_utc()))
        self.db.commit()

    def put_raw(self, content: bytes, *, source: str, url: str,
                media_type: str = "application/octet-stream", retrieved_at: str | None = None,
                metadata: dict | None = None) -> dict:
        if not isinstance(content, bytes):
            raise TypeError("put_raw requires exact bytes, not decoded text")
        acquired = _timestamp(retrieved_at or now_utc())
        digest = hashlib.sha256(content).hexdigest()
        relative = Path("raw") / digest[:2] / digest
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Existing raw blob fails SHA-256 verification: " + str(path))
        else:
            # Atomically link a complete file without replacing existing evidence.
            # Readers never observe an empty/partly written blob, even when two
            # acquisition workers simultaneously fetch the same content.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".acquire-", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        raise ValueError("Concurrent raw write failed verification")
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
        self.register_source(source)
        raw_id = "sha256:" + digest
        self.db.execute("INSERT OR IGNORE INTO raw_blobs VALUES(?,?,?,?,?,?)", (raw_id, digest, relative.as_posix(), len(content), media_type, acquired))
        retrieval_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO raw_retrievals VALUES(?,?,?,?,?,?)", (retrieval_id, raw_id, source, url, acquired, _json(metadata or {})))
        self.db.commit()
        return {"raw_id": raw_id, "sha256": digest, "path": str(path), "retrieved_at": acquired, "source": source, "url": url, "retrieval_id": retrieval_id}

    def latest_raw(self, source: str, url: str) -> dict | None:
        row = self.db.execute("SELECT b.*,r.retrieval_id,r.source,r.url,r.retrieved_at,r.metadata_json FROM raw_retrievals r JOIN raw_blobs b USING(raw_id) WHERE r.source=? AND r.url=? ORDER BY r.retrieved_at DESC,r.rowid DESC LIMIT 1", (source, url)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["path"] = str(self.root / result["path"])
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def put_record(self, dataset: str, record_key: str, data: dict, *, source: str, raw_id: str,
                   company_id: str | None = None, security_id: str | None = None,
                   published_at: str | None = None, publication_precision: str = "unknown",
                   publication_timezone: str | None = None, available_at: str | None = None,
                   availability_basis: str = "unknown", period_start: str | None = None,
                   period_end: str | None = None, event_date: str | None = None,
                   source_locator: str | None = None, unit: str | None = None, currency: str | None = None,
                   quality: str = "unverified", knowledge_type: str = "fact_observation",
                   parser_version: str = "1", supersedes: str | None = None) -> str:
        if not isinstance(data, dict):
            raise TypeError("Normalized payload must be a dictionary")
        retrieval = self.db.execute("SELECT * FROM raw_retrievals WHERE raw_id=? AND source=? ORDER BY retrieved_at DESC,rowid DESC LIMIT 1", (raw_id, source)).fetchone()
        if retrieval is None:
            raise ValueError("raw_id must reference successful acquisition from this source")
        if available_at is not None:
            available_at = _timestamp(available_at)
            if availability_basis == "unknown":
                raise ValueError("A known available_at requires an explicit availability_basis")
        values = dict(dataset=dataset, record_key=record_key, data_json=_json(data), source=source,
                      company_id=company_id, security_id=security_id, published_at=published_at,
                      publication_precision=publication_precision, publication_timezone=publication_timezone,
                      available_at=available_at, availability_basis=availability_basis,
                      period_start=period_start, period_end=period_end, event_date=event_date,
                      source_locator=source_locator, unit=unit, currency=currency, quality=quality,
                      knowledge_type=knowledge_type, parser_version=parser_version)
        semantic_hash = hashlib.sha256(_json(values).encode()).hexdigest()
        # Lock before reading the latest version. Concurrent parsers cannot both
        # append the same transition, and A -> B -> A remains three versions.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            previous = self.db.execute("SELECT * FROM records WHERE dataset=? AND source=? AND record_key=? ORDER BY sequence DESC LIMIT 1", (dataset, source, record_key)).fetchone()
            if previous and previous["semantic_hash"] == semantic_hash:
                self.db.execute("INSERT OR IGNORE INTO record_evidence VALUES(?,?,?)", (previous["record_id"], retrieval["retrieval_id"], raw_id))
                return previous["record_id"]
            # Explicit older disclosure vintages replayed during an archive scan
            # are not new reversion events. Current snapshots and unknown clocks
            # do not earn this exception, even when they quote an old date.
            snapshot_markers = ("unknown", "first_observed", "snapshot", "current_copy", "current_web_copy", "calculation", "acquisition", "retriev")
            dated_replay = (previous and available_at and previous["available_at"]
                            and available_at < previous["available_at"] and published_at
                            and publication_precision != "unknown"
                            and not any(marker in availability_basis.lower() for marker in snapshot_markers))
            if dated_replay:
                historical = self.db.execute("SELECT record_id FROM records WHERE dataset=? AND source=? AND record_key=? AND semantic_hash=? ORDER BY sequence DESC LIMIT 1", (dataset, source, record_key, semantic_hash)).fetchone()
                if historical:
                    self.db.execute("INSERT OR IGNORE INTO record_evidence VALUES(?,?,?)", (historical["record_id"], retrieval["retrieval_id"], raw_id))
                    return historical["record_id"]
            if supersedes is not None:
                parent = self.db.execute("SELECT dataset,source,record_key FROM records WHERE record_id=?", (supersedes,)).fetchone()
                if parent is None or tuple(parent) != (dataset, source, record_key):
                    raise ValueError("supersedes must identify this dataset/source/key")
            else:
                supersedes = previous["record_id"] if previous else None
            predecessor = previous["record_id"] if previous else "first"
            record_id = "rec:" + hashlib.sha256((semantic_hash + "|" + predecessor).encode()).hexdigest()
            values.update(record_id=record_id, semantic_hash=semantic_hash, raw_id=raw_id,
                          retrieval_id=retrieval["retrieval_id"], retrieved_at=retrieval["retrieved_at"],
                          supersedes=supersedes, created_at=now_utc())
            columns = list(values)
            self.db.execute(f"INSERT INTO records ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(values.values()))
            self.db.execute("INSERT INTO record_evidence VALUES(?,?,?)", (record_id, retrieval["retrieval_id"], raw_id))
        if previous and previous["available_at"] == available_at:
            self.issue("AMBIGUOUS_REVISION_TIME", "Changed versions claim the same public availability; historical selection requires acquisition evidence.", dataset=dataset, company_id=company_id, security_id=security_id, details={"record_key": record_key, "previous": previous["record_id"], "new": record_id})
        return record_id

    def query(self, dataset: str, *, as_of: str | None = None, known_at: str | None = None,
              company_id: str | None = None, security_id: str | None = None, latest: bool = True,
              include_rejected: bool = False, processed_at: str | None = None) -> list[dict]:
        as_cutoff = _timestamp(as_of, cutoff=True) if as_of else None
        known_cutoff = _timestamp(known_at, cutoff=True) if known_at else None
        processed_cutoff = _timestamp(processed_at, cutoff=True) if processed_at else None
        if processed_cutoff:
            known_cutoff = min(known_cutoff, processed_cutoff) if known_cutoff else processed_cutoff
        clauses, parameters = ["dataset=?"], [dataset]
        for name, value in (("company_id", company_id), ("security_id", security_id)):
            if value is not None:
                clauses.append(name + "=?")
                parameters.append(value)
        if known_cutoff:
            clauses.append("retrieved_at<=?")
            parameters.append(known_cutoff)
        if processed_cutoff:
            clauses.append("created_at<=?")
            parameters.append(processed_cutoff)
        candidates = [dict(r) for r in self.db.execute("SELECT * FROM records WHERE " + " AND ".join(clauses) + " ORDER BY sequence", parameters)]
        groups: dict[tuple, list[dict]] = {}
        for row in candidates:
            groups.setdefault((row["source"], row["record_key"]), []).append(row)
        result = []
        for rows in groups.values():
            constraint_sql='SELECT * FROM availability_constraints WHERE dataset=? AND source=? AND record_key=?'
            constraint_params=[dataset,rows[0]['source'],rows[0]['record_key']]
            if known_cutoff:
                constraint_sql+=' AND learned_at<=?'
                constraint_params.append(known_cutoff)
            constraints=[dict(r) for r in self.db.execute(constraint_sql,constraint_params)]
            floor=max((r['not_before'] for r in constraints),default=None)
            ties = {}
            for row in rows:
                claim=json.loads(row['data_json'])
                claim.pop('parser_version',None)
                signature=_json([claim,row['period_start'],row['period_end'],row['unit'],row['currency'],row['knowledge_type']])
                ties.setdefault(row['available_at'],set()).add(signature)
            eligible = []
            for row in rows:
                decoded=json.loads(row['data_json'])
                validation_sql='SELECT * FROM record_validations WHERE record_id=?'
                validation_params=[row['record_id']]
                if known_cutoff:
                    validation_sql+=' AND validated_at<=?'
                    validation_params.append(known_cutoff)
                validation=self.db.execute(validation_sql+' ORDER BY validated_at DESC,rowid DESC LIMIT 1',validation_params).fetchone()
                if validation is not None:
                    row['validation']=dict(validation)
                    if validation['verdict']=='rejected' and not include_rejected:
                        continue
                dependencies=decoded.get('supporting_raw_ids',[])
                dependency_times=[]
                missing_dependency=False
                for raw_id in dependencies:
                    stamp=self.db.execute('SELECT MIN(r.retrieved_at) FROM record_evidence e JOIN raw_retrievals r USING(retrieval_id) WHERE e.record_id=? AND e.raw_id=?',(row['record_id'],raw_id)).fetchone()[0]
                    if not stamp:
                        missing_dependency=True
                    else:
                        dependency_times.append(stamp)
                if dependencies and (missing_dependency or known_cutoff and any(t>known_cutoff for t in dependency_times)):
                    if as_cutoff or known_cutoff:
                        continue
                available = row["available_at"]
                ambiguous = available is not None and len(ties[available]) > 1
                mutable_copy='current_copy' in row['availability_basis'] or 'current_web_copy' in row['availability_basis']
                effective = max(available, row["retrieved_at"]) if available and (ambiguous or mutable_copy) else available
                if floor and effective:
                    effective=max(effective,floor)
                if as_cutoff and (effective is None or effective > as_cutoff):
                    continue
                row.pop('data_json')
                row["data"] = decoded
                row["system_time_semantics"] = "raw_and_parsed_record_available" if processed_cutoff else "raw_evidence_acquisition_only; use processed_at to reconstruct parsed system state"
                if dependencies:
                    row['required_evidence_available_from']=max(dependency_times+[row['retrieved_at']])
                    row['required_evidence_complete']=not missing_dependency
                row["effective_available_at"] = effective
                row["point_in_time_status"] = "acquisition_only_ambiguous_revision" if ambiguous else ("unknown_public_time" if available is None else "source_evidenced")
                if mutable_copy and not ambiguous:
                    row['point_in_time_status']='acquisition_only_current_copy'
                if floor:
                    row['availability_constraints']=constraints
                    row['point_in_time_status']='corrected_availability_lower_bound'
                eligible.append(row)
            if latest and eligible:
                result.append(max(eligible, key=lambda r: (r["effective_available_at"] or "", r["retrieved_at"], r["sequence"])))
            elif not latest:
                result.extend(eligible)
        return sorted(result, key=lambda r: (r["record_key"], r["source"], r["sequence"]))

    def validate_record(self,record_id,*,verdict,reason,raw_id,validated_at=None):
        """Preserve an audit decision without rewriting the original parsed row."""
        if verdict not in {'rejected','verified','needs_review'}:raise ValueError('Invalid validation verdict')
        if not self.db.execute('SELECT 1 FROM records WHERE record_id=?',(record_id,)).fetchone():raise ValueError('record_id not found')
        stamp=_timestamp(validated_at or now_utc())
        identity='validation:'+hashlib.sha256(_json([record_id,verdict,reason,raw_id]).encode()).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO record_validations VALUES(?,?,?,?,?,?)',(identity,record_id,verdict,reason,raw_id,stamp))
        self.db.commit()
        return identity

    def restrict_availability(self,dataset,record_key,*,source,not_before,raw_id,basis):
        """Append evidence that an earlier availability assumption was too early.

        Applies to an immutable disclosure identity, not unrelated revisions.
        known_at queries see only constraints actually learned by that cutoff.
        """
        stamp=_timestamp(not_before)
        raw=self.db.execute('SELECT retrieved_at FROM raw_retrievals WHERE raw_id=? ORDER BY retrieved_at DESC LIMIT 1',(raw_id,)).fetchone()
        if raw is None:raise ValueError('constraint raw evidence missing')
        identity='time:'+hashlib.sha256(_json([dataset,source,record_key,stamp,raw_id,basis]).encode()).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO availability_constraints VALUES(?,?,?,?,?,?,?,?)',(identity,dataset,source,record_key,stamp,raw[0],raw_id,basis))
        self.db.commit()
        return identity

    def issue(self, code: str, message: str, *, dataset: str | None = None,
              company_id: str | None = None, security_id: str | None = None,
              severity: str = "warning", details: dict | None = None) -> str:
        issue_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO quality_issues VALUES(?,?,?,?,?,?,?,?,?)", (issue_id, code, message, dataset, company_id, security_id, severity, _json(details or {}), now_utc()))
        self.db.commit()
        return issue_id

    def checkpoint(self, key: str, value: Any = None) -> Any:
        if value is None:
            row = self.db.execute("SELECT value_json FROM checkpoints WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
        self.db.execute("INSERT INTO checkpoints VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at", (key, _json(value), now_utc()))
        self.db.commit()
        return value

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
