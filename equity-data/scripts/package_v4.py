"""Create a verified, stopped V4 delivery; never run on import.

python scripts/package_v4.py --output ../equity-data-system-v4.zip

All historical data/evidence are included except named duplicate local backup
and restore copies. Private files are omitted, operational credentials/contact
fields are scrubbed in delivery copies, and immutable raw bytes are untouched.
Source SQLite databases are read-only during snapshotting. A short final writer
reservation closes the independent Control.enqueue race; no source SQL writes
or migrations occur. Concurrent changes fail publication and preserve a partial.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import tomllib
import uuid
import zipfile

_spec=importlib.util.spec_from_file_location("_delivery_v3_helpers",Path(__file__).with_name("package_v3.py"))
v3=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v3)

MANIFEST="DELIVERY_MANIFEST_V4.json"
CHUNK=v3.BLOCK_SIZE
EXCLUDED_COPIES={"backups","data/v4_restore_validation"}
PRIVATE_DIRS={".ssh",".aws",".gnupg",".azure","secrets","credentials","private"}
PRIVATE_NAME=re.compile(r"(?i)(?:^|[._-])(?:secret|secrets|credentials|private|token|tokens|access_token|api_key)(?:[._-]|$)")
TEXT_SUFFIXES={".json",".jsonl",".csv",".toml",".yaml",".yml",".log",".txt",".md",".xml",".html",".htm",".ps1",".py"}
SOURCE_SUFFIXES={".py",".ps1"}
SENSITIVE=re.compile(r"(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|user[_-]?agent|contact[_-]?email|sec_user_agent)")
EMAIL=re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MARKER="[REDACTED_FOR_DELIVERY]"


def exclusion(path,root):
    relative=path.relative_to(root).as_posix()
    if relative==MANIFEST:return "v4_manifest_regenerated"
    if getattr(path,"is_junction",lambda:False)():return "junction_not_followed"
    if any(relative==p or relative.startswith(p+"/") for p in EXCLUDED_COPIES):
        return "duplicate_local_backup_or_restore_copy; original live and all historical datasets retained"
    if relative==v3.MANIFEST:return None  # preserve the historical V3 manifest
    reason=v3.exclusion(path,root)
    if reason:return reason
    lower=[p.lower() for p in path.relative_to(root).parts]
    name=lower[-1]
    if name in {".npmrc",".pypirc",".netrc"}:return "private_authentication_configuration"
    if any(p in PRIVATE_DIRS for p in lower):return "private_directory"
    if PRIVATE_NAME.search(name) or re.search(r"\.local\.(?:toml|json|ya?ml|ini|cfg)$",name):return "private_configuration_or_secret_file"
    if name in {"id_rsa","id_ed25519","id_dsa","id_ecdsa"} or name.endswith((".pem",".key",".pfx",".p12",".kdbx")):
        return "private_key_or_secret_file"
    if name.endswith((".zip",".7z",".tar",".gz")) and "raw" not in lower:
        # Nested archives are opaque to redaction. Never silently ship secrets.
        raise RuntimeError("Nested archive needs explicit review before packaging: "+relative)
    return None


def inventory(root):
    files=[];excluded=[];lock_dirs=set()
    for directory,dirs,names in os.walk(root,followlinks=False):
        parent=Path(directory);kept=[]
        for name in sorted(dirs):
            child=parent/name;reason=exclusion(child,root)
            if reason:excluded.append({"path":child.relative_to(root).as_posix()+"/","reason":reason})
            else:kept.append(name)
        dirs[:]=kept
        if {".local-collector.lock",".machine-worker.lock"}.intersection(names):lock_dirs.add(parent)
        for name in sorted(names):
            path=parent/name;reason=exclusion(path,root)
            if reason:excluded.append({"path":path.relative_to(root).as_posix(),"reason":reason})
            elif path.is_file():files.append(path)
    return sorted(files),sorted(excluded,key=lambda r:r["path"]),lock_dirs


@contextmanager
def existing_guard(directory,name):
    """Take exactly the existing lock; never create or rewrite source lock files."""
    path=directory/name
    if not path.exists():
        yield "not_present; source_change_checks_apply"
        return
    stream=path.open("r+b");owned=False
    try:
        if path.stat().st_size<1:raise RuntimeError("Empty collector lock: "+str(path))
        stream.seek(0)
        try:
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            owned=True
        except OSError as exc:raise RuntimeError("Collector is active: "+str(path)) from exc
        yield "existing_lock_held_until_publication"
    finally:
        if owned:
            stream.seek(0)
            if os.name=="nt":msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        stream.close()


def _sensitive(key):
    key=str(key).lower().replace("-","_")
    if key.endswith(("_env","_status","_required","_description","_requirement","_name")) or key.startswith(("requires_","has_","missing_","allow_","supports_")):return False
    return key in {"password","passwd","secret","api_key","apikey","access_token","refresh_token","authorization","cookie","user_agent","contact_email","sec_user_agent","credentials","secrets"} or key.endswith(("_password","_secret","_api_key","_access_token","_refresh_token"))


def collect_literals(value,key="",found=None):
    """Learn exact private literals from operational config, never print them."""
    found=found if found is not None else set()
    if isinstance(value,dict):
        for k,v in value.items():collect_literals(v,k,found)
    elif isinstance(value,list):
        for v in value:collect_literals(v,key,found)
    elif isinstance(value,str) and _sensitive(key):
        if re.search(r"(?i)(user[_-]?agent|email)",key):
            for email in EMAIL.findall(value):
                domain=email.rsplit("@",1)[-1].lower()
                if domain.endswith((".invalid",".test",".example")) or domain in {"example.com","example.org","example.net"}:continue
                found.add(email)
        elif len(value)>=8 and value not in {"REDACTED",MARKER} and not value.startswith(("$","env:","<")):
            found.add(value)
    return found


def scrub(value,literals,key=""):
    if _sensitive(key) and isinstance(value,(str,dict,list)) and value not in (None,""):
        return MARKER
    if isinstance(value,dict):return {k:scrub(v,literals,k) for k,v in value.items()}
    if isinstance(value,list):return [scrub(v,literals,key) for v in value]
    if isinstance(value,str):
        for literal in sorted(literals,key=len,reverse=True):value=value.replace(literal,MARKER)
    return value


def _quote(name):return '"'+name.replace('"','""')+'"'


def operational_columns(connection):
    # Facts, original material catalogs and acquisition evidence stay byte/value
    # faithful; sanitize configuration and scheduler diagnostics, not research.
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        if not (table.startswith(("machine_","local_")) or table in {"checkpoints","issues","quality_issues","sources"}):continue
        for info in connection.execute("PRAGMA table_info("+_quote(table)+")"):
            if "TEXT" in info[2].upper():yield table,info[1]


def learn_database_literals(connection,literals):
    for table,column in operational_columns(connection):
        if not column.endswith("_json"):continue
        for (text,) in connection.execute("SELECT "+_quote(column)+" FROM "+_quote(table)+" WHERE "+_quote(column)+" IS NOT NULL"):
            try:collect_literals(json.loads(text),found=literals)
            except (ValueError,TypeError):continue


def sanitize_database(path,literals):
    changes=[]
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA secure_delete=ON")
        for table,column in operational_columns(connection):
            changed=0
            rows=connection.execute("SELECT rowid,"+_quote(column)+" FROM "+_quote(table)+" WHERE "+_quote(column)+" IS NOT NULL").fetchall()
            for rowid,text in rows:
                if not isinstance(text,str):continue
                try:
                    parsed=json.loads(text) if column.endswith("_json") else None
                except ValueError:parsed=None
                if parsed is not None:
                    clean=scrub(parsed,literals)
                    replacement=json.dumps(clean,ensure_ascii=False,separators=(",",":")) if clean!=parsed else text
                else:replacement=scrub(text,literals)
                if replacement!=text:
                    connection.execute("UPDATE "+_quote(table)+" SET "+_quote(column)+"=? WHERE rowid=?",(replacement,rowid));changed+=1
            if changed:changes.append({"table":table,"column":column,"rows":changed})
        connection.commit()
        # Remove pre-redaction values from freed pages in the delivery snapshot.
        if changes:connection.execute("VACUUM")
        v3.inspect_database(connection,str(path))
    return changes


def learn_config_literals(root,excluded,literals):
    for item in excluded:
        path=root/item["path"]
        if not path.is_file() or path.stat().st_size>2*1024*1024:continue
        if "private" not in item["reason"]:continue
        try:
            text=path.read_text(encoding="utf-8-sig")
            if path.suffix.lower()==".toml":collect_literals(tomllib.loads(text),found=literals)
            elif path.suffix.lower()==".json":collect_literals(json.loads(text),found=literals)
            else:
                for line in text.splitlines():
                    if "=" in line:
                        key,value=line.split("=",1);collect_literals(value.strip().strip("\"'"),key.strip(),literals)
        except (UnicodeError,ValueError):continue


def learn_text_literals(files,root,literals):
    for path in files:
        if "raw" in path.relative_to(root).parts or path.suffix.lower() not in {".json",".jsonl",".toml"}:continue
        try:
            text=path.read_text(encoding="utf-8-sig")
            if path.suffix.lower()==".toml":collect_literals(tomllib.loads(text),found=literals)
            elif path.suffix.lower()==".jsonl":
                for line in text.splitlines():
                    try:collect_literals(json.loads(line),found=literals)
                    except ValueError:continue
            else:collect_literals(json.loads(text),found=literals)
        except (UnicodeError,ValueError):continue


def _raw_references(connection,database,root,included,digests):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_blobs'").fetchone():return []
    rows=[]
    for raw_id,sha,path,size in connection.execute("SELECT raw_id,sha256,path,byte_size FROM raw_blobs ORDER BY raw_id"):
        relative=Path(path)
        if relative.is_absolute() or ".." in relative.parts:raise RuntimeError("Unsafe raw catalog path in "+str(database.relative_to(root)))
        actual=database.parent/relative
        if actual.is_symlink() or not actual.resolve().is_relative_to(root) or actual not in included:
            raise RuntimeError("Missing/excluded/unsafe raw evidence: "+str(actual.relative_to(root)))
        if actual not in digests:digests[actual]=v3.digest_file(actual)
        if digests[actual]!=(sha,size):raise RuntimeError("Raw catalog hash/size mismatch: "+str(actual.relative_to(root)))
        if not re.fullmatch(r"[0-9a-f]{64}",sha) or raw_id!="sha256:"+sha:
            raise RuntimeError("Invalid raw catalog identity in "+str(database.relative_to(root)))
        rows.append({"raw_id":raw_id,"path":actual.relative_to(root).as_posix(),"sha256":sha,"bytes":size})
    return rows


def sanitize_text(path,target,literals):
    text=path.read_text(encoding="utf-8-sig")
    replacement=scrub(text,literals)
    if path.suffix.lower()==".json":
        try:
            parsed=json.loads(text);clean=scrub(parsed,literals)
            if clean!=parsed:replacement=json.dumps(clean,ensure_ascii=False,indent=2)
        except ValueError:pass
    if replacement==text:return False
    if path.suffix.lower() in SOURCE_SUFFIXES:
        raise RuntimeError("Private literal found in executable source; review explicitly before packaging: "+str(path))
    target.write_text(replacement,encoding="utf-8")
    return True


def _check_unchanged(root,files,excluded,connections,versions,digests):
    current,current_excluded,_=inventory(root)
    # Opening even mode=ro can create WAL/SHM files for a quiescent WAL database.
    # These are not delivery artifacts: the backup contains committed WAL pages,
    # and data_version below still detects independent commits such as enqueue.
    # Do not ignore arbitrary *-wal names, rollback journals, locks or private
    # config changes; only the exact helper paths of our opened WAL databases.
    transient_paths={database.relative_to(root).as_posix()+suffix
        for database,connection in connections.items()
        if connection.execute("PRAGMA journal_mode").fetchone()[0].lower()=="wal"
        for suffix in ("-wal","-shm")}
    def stable_excluded(items):
        return [item for item in items if not (
            item["reason"]=="sqlite_transient_sidecar" and item["path"] in transient_paths)]
    before_excluded=stable_excluded(excluded);after_excluded=stable_excluded(current_excluded)
    if current!=files or after_excluded!=before_excluded:
        before_files={p.relative_to(root).as_posix() for p in files}
        after_files={p.relative_to(root).as_posix() for p in current}
        before_items={item["path"]:item["reason"] for item in before_excluded}
        after_items={item["path"]:item["reason"] for item in after_excluded}
        delta={"included_added":sorted(after_files-before_files),"included_removed":sorted(before_files-after_files),
            "excluded_added":sorted(after_items.keys()-before_items.keys()),"excluded_removed":sorted(before_items.keys()-after_items.keys()),
            "excluded_reason_changed":sorted(k for k in before_items.keys()&after_items.keys() if before_items[k]!=after_items[k])}
        raise RuntimeError("Project inventory changed during packaging; archive not published: "+json.dumps(delta,sort_keys=True))
    for database,connection in connections.items():
        if connection.execute("PRAGMA data_version").fetchone()[0]!=versions[database]:
            raise RuntimeError("SQLite changed during packaging (including independent request enqueue): "+str(database.relative_to(root)))
        v3.inspect_database(connection,database.relative_to(root).as_posix())
    for path in files:
        if path in connections:continue
        if v3.digest_file(path)!=digests[path]:raise RuntimeError("Source file changed during packaging: "+str(path.relative_to(root)))


def _validate_archive(path,root_name,entries,manifest_bytes):
    with zipfile.ZipFile(path) as archive:
        expected={root_name+"/"+r["path"] for r in entries}|{root_name+"/"+MANIFEST}
        if set(archive.namelist())!=expected or len(archive.namelist())!=len(expected):raise RuntimeError("ZIP member inventory mismatch")
        for entry in entries:
            digest=hashlib.sha256();size=0
            with archive.open(root_name+"/"+entry["path"]) as stream:
                for block in iter(lambda:stream.read(CHUNK),b""):digest.update(block);size+=len(block)
            if (digest.hexdigest(),size)!=(entry["sha256"],entry["bytes"]):raise RuntimeError("ZIP hash verification failed: "+entry["path"])
        if archive.read(root_name+"/"+MANIFEST)!=manifest_bytes:raise RuntimeError("ZIP manifest mismatch")


def package(project_root,output):
    root=Path(project_root).resolve();output=Path(output).resolve()
    if not root.is_dir():raise ValueError("Project root missing")
    if output.is_relative_to(root):raise ValueError("Archive must be outside source project")
    if output.exists():raise FileExistsError("Existing archive is never replaced: "+str(output))
    output.parent.mkdir(parents=True,exist_ok=True)
    files,excluded,lock_dirs=inventory(root);databases=[p for p in files if v3.is_sqlite(p)]
    partial=output.with_name(output.name+".partial-"+uuid.uuid4().hex)
    with ExitStack() as stack:
        guards={}
        for directory in sorted(lock_dirs|{p.parent for p in databases}):
            guards[directory.relative_to(root).as_posix()]={name:stack.enter_context(existing_guard(directory,name)) for name in (".local-collector.lock",".machine-worker.lock")}
        current,current_excluded,_=inventory(root)
        if current!=files or current_excluded!=excluded:raise RuntimeError("Project changed while acquiring collector locks")
        staging=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="equity-v4-package-"))).resolve()
        if not staging.is_relative_to(Path(tempfile.gettempdir()).resolve()):raise RuntimeError("Unexpected staging directory")
        connections={};versions={};snapshots={};checks={};literals=set();digests={}
        learn_config_literals(root,excluded,literals)
        learn_text_literals(files,root,literals)
        for index,database in enumerate(databases):
            relative=database.relative_to(root).as_posix()
            connection=stack.enter_context(closing(v3.open_readonly(database)))
            connections[database]=connection;versions[database]=connection.execute("PRAGMA data_version").fetchone()[0]
            check=v3.inspect_database(connection,relative)
            snapshot=staging/(str(index)+".sqlite3")
            with closing(sqlite3.connect(snapshot)) as saved:connection.backup(saved)
            with closing(v3.open_readonly(snapshot)) as saved:
                learn_database_literals(saved,literals)
                references=_raw_references(saved,database,root,set(files),digests)
                v3.inspect_database(saved,relative+" snapshot")
            checks[relative]={**check,"raw_references":references,"unredacted_snapshot_sha256":v3.digest_file(snapshot)[0]}
            snapshots[database]=snapshot
        redactions={};prepared=dict(snapshots)
        for database,snapshot in snapshots.items():
            changes=sanitize_database(snapshot,literals)
            if changes:redactions[database.relative_to(root).as_posix()]={"kind":"operational_SQLite_fields_only","changes":changes}
        entries=[]
        for index,path in enumerate(files):
            relative=path.relative_to(root).as_posix()
            if path not in snapshots:
                if path not in digests:digests[path]=v3.digest_file(path)
                if "raw" not in path.relative_to(root).parts and path.suffix.lower() in TEXT_SUFFIXES:
                    target=staging/("text-"+str(index))
                    try:changed=sanitize_text(path,target,literals)
                    except UnicodeError:changed=False
                    if changed:prepared[path]=target;redactions[relative]={"kind":"delivery_copy_private_literals_or_config_fields"}
            packaged=prepared.get(path,path);sha,size=v3.digest_file(packaged)
            entries.append({"path":relative,"sha256":sha,"bytes":size,"sqlite_snapshot":path in snapshots,
                **({"redacted":True,"source_sha256":digests.get(path,(checks.get(relative,{}).get("unredacted_snapshot_sha256"),))[0]} if relative in redactions else {})})
        manifest={"manifest_version":"delivery-v4-sha256-v1","created_at":datetime.now(timezone.utc).isoformat(),"archive_root":root.name,
            "files":entries,"included_file_count_excluding_manifest":len(entries),"included_bytes_excluding_manifest":sum(r["bytes"] for r in entries),
            "manifest_self_hash":"intentionally_omitted","excluded_paths":excluded,"collector_guards":guards,"sqlite_checks":checks,
            "redactions":redactions,"redaction_policy":"Delivery copies only; original raw bytes unchanged; operational credentials/contact config scrubbed; source DBs not migrated or changed.",
            "historical_manifests":"Retained as historical evidence; V4 manifest is authoritative for current packaged bytes.",
            "source_files_deleted":False,"source_databases_modified":False,"source_manifest_written":MANIFEST,
            "consistency":"Both existing collector locks held; atomic SQLite snapshots include committed WAL; source data_version/inventory/hashes checked before publication; only WAL/SHM existence changes for opened WAL databases ignored; short final writer reservation closes independent control-enqueue race."}
        manifest_bytes=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
        with zipfile.ZipFile(partial,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
            for entry in entries:
                original=root/entry["path"];path=prepared.get(original,original)
                digest=hashlib.sha256();size=0
                with path.open("rb") as stream,archive.open(root.name+"/"+entry["path"],"w",force_zip64=True) as member:
                    for block in iter(lambda:stream.read(CHUNK),b""):member.write(block);digest.update(block);size+=len(block)
                if (digest.hexdigest(),size)!=(entry["sha256"],entry["bytes"]):raise RuntimeError("File changed during ZIP write: "+entry["path"])
            archive.writestr(root.name+"/"+MANIFEST,manifest_bytes)
        _validate_archive(partial,root.name,entries,manifest_bytes)
        _check_unchanged(root,files,excluded,connections,versions,digests)
        # Read-only snapshots above never alter originals. These final no-write
        # transactions briefly reserve writers so a Control enqueue cannot commit
        # between the last version check and publication. Rollback is mandatory.
        writers=[]
        try:
            for database in databases:
                writer=sqlite3.connect(database.as_uri()+"?mode=rw",uri=True,timeout=2)
                writers.append(writer);writer.execute("BEGIN IMMEDIATE")
            for database,connection in connections.items():
                if connection.execute("PRAGMA data_version").fetchone()[0]!=versions[database]:raise RuntimeError("SQLite changed before final publication: "+str(database.relative_to(root)))
            if output.exists():raise FileExistsError("Destination appeared during packaging")
            if os.name=="nt":partial.rename(output)
            else:
                os.link(partial,output)  # fail if a target appeared; never replace
                partial.unlink()  # only this invocation's successful staging link
        finally:
            for writer in writers:writer.rollback();writer.close()
        (root/MANIFEST).write_bytes(manifest_bytes)
    return {"archive":str(output),"manifest":str(root/MANIFEST),"files":len(entries),"archive_bytes":output.stat().st_size,
        "zip_hash_validation":"passed","sqlite_databases_checked":len(databases),"redacted_paths":len(redactions),"source_databases_modified":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output",type=Path)
    args=parser.parse_args();output=args.output or args.project_root.resolve().parent/"equity-data-system-v4.zip"
    print(json.dumps(package(args.project_root,output),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
