"""V4.1 adapter integration checks use tiny temporary ZIP/SQLite projects only."""
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("test_pack_v41", Path(__file__).resolve().parents[1] / "scripts" / "package_v41.py")
pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack)
PRIVATE = "test-operational-secret-91728304"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    storage = root / "data" / "live"
    raw = storage / "raw" / "original.xml"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"<original>revenue 12</original>")
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    with sqlite3.connect(storage / "data.sqlite3") as db:
        db.executescript("""CREATE TABLE raw_blobs(raw_id TEXT,sha256 TEXT,path TEXT,byte_size INTEGER);
            CREATE TABLE records(record_id TEXT,data_json TEXT);
            CREATE TABLE initialization_batches(batch_id TEXT,target_json TEXT,specification_json TEXT,result_json TEXT,last_error TEXT);
            CREATE TABLE initialization_events(event_id INTEGER,details_json TEXT);
            CREATE TABLE raw_import_events(import_id TEXT,source_root TEXT,sources_json TEXT,result_json TEXT);
            CREATE TABLE raw_import_provenance(import_id TEXT,retrieval_id TEXT,original_retrieved_at TEXT);""")
        db.execute("INSERT INTO raw_blobs VALUES(?,?,?,?)", ("sha256:"+digest,digest,"raw/original.xml",raw.stat().st_size))
        db.execute("INSERT INTO records VALUES('fact',?)", (json.dumps({"value":12,"unit":"USD","period_end":"2024-01-31"}),))
        db.execute("INSERT INTO initialization_batches VALUES('init',?,?,?,?)",
                   ('{"symbol":"EXAMPLE"}',json.dumps({"api_key":PRIVATE,"api_key_env":"API_KEY"}),'{"complete":false}',"Network error using "+PRIVATE))
        db.execute("INSERT INTO initialization_events VALUES(1,?)", (json.dumps({"diagnostic":PRIVATE}),))
        db.execute("INSERT INTO raw_import_events VALUES('import','C:/historical/source',?,?)", ('["sec_edgar"]',json.dumps({"diagnostic":PRIVATE,"network_requests":0})))
        db.execute("INSERT INTO raw_import_provenance VALUES('import','retrieval-1','2024-02-03T04:05:06Z')")
    for name in ("DELIVERY_MANIFEST_V3.json", "DELIVERY_MANIFEST_V4.json"):
        (root / name).write_text(json.dumps({"historical":name}),encoding="utf-8")
    (root / pack.MANIFEST).write_text('{"older_v41":true}',encoding="utf-8")
    backups = root / "backups" / "before-v41"; backups.mkdir(parents=True)
    (backups / "identity.local.toml").write_text('api_key="'+PRIVATE+'"')
    config = root / "configs"; config.mkdir()
    (config / "identity.local.toml").write_text('api_key="'+PRIVATE+'"')
    return root


def test_v41_default_name_retains_old_manifests_and_preserves_originals(project,tmp_path):
    dbpath = project / "data/live/data.sqlite3"
    before = dbpath.read_bytes()
    historical = {name:(project/name).read_bytes() for name in ("DELIVERY_MANIFEST_V3.json","DELIVERY_MANIFEST_V4.json")}
    result = pack.package(project)
    output = tmp_path / "equity-data-system-v4-1.zip"
    assert Path(result["archive"]) == output and result["zip_hash_validation"] == "passed"
    assert result["manifest"] == str(project / pack.MANIFEST)
    assert dbpath.read_bytes() == before
    for name,content in historical.items():assert (project/name).read_bytes() == content
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("project/"+pack.MANIFEST))
        assert manifest["manifest_version"] == "delivery-v4.1-sha256-v1"
        assert "DELIVERY_MANIFEST_V41.json is authoritative" in manifest["historical_manifests"]
        assert not any("/backups/" in name or name.endswith("identity.local.toml") for name in archive.namelist())
        for name,content in historical.items():assert archive.read("project/"+name) == content
        assert archive.read("project/data/live/raw/original.xml") == (project/"data/live/raw/original.xml").read_bytes()
        delivered = tmp_path/"snapshot.sqlite3";delivered.write_bytes(archive.read("project/data/live/data.sqlite3"))
        assert PRIVATE.encode() not in delivered.read_bytes()
        with closing(sqlite3.connect(delivered)) as db:
            spec=json.loads(db.execute("SELECT specification_json FROM initialization_batches").fetchone()[0])
            assert spec["api_key"] == "[REDACTED_FOR_DELIVERY]" and spec["api_key_env"] == "API_KEY"
            assert db.execute("SELECT * FROM raw_import_provenance").fetchone() == ("import","retrieval-1","2024-02-03T04:05:06Z")
            assert db.execute("SELECT source_root,sources_json FROM raw_import_events").fetchone() == ("C:/historical/source",'["sec_edgar"]')
            assert json.loads(db.execute("SELECT data_json FROM records").fetchone()[0]) == {"value":12,"unit":"USD","period_end":"2024-01-31"}
        for entry in manifest["files"]:
            assert hashlib.sha256(archive.read("project/"+entry["path"])).hexdigest() == entry["sha256"]


@pytest.mark.parametrize("name",["equity-data-system-v3.zip","equity-data-system-v4.zip","equity-data-system-v4-1.zip"])
def test_v41_never_overwrites_existing_delivery_or_manifests(project,tmp_path,name):
    path=tmp_path/name;path.write_bytes(b"historical ZIP evidence")
    manifests={p.name:p.read_bytes() for p in project.glob("DELIVERY_MANIFEST*.json")}
    with pytest.raises(FileExistsError):pack.package(project,path)
    assert path.read_bytes()==b"historical ZIP evidence"
    assert {p.name:p.read_bytes() for p in project.glob("DELIVERY_MANIFEST*.json")}==manifests


def test_isolated_v41_engine_does_not_change_v4_module_or_file():
    path=Path(__file__).resolve().parents[1]/"scripts"/"package_v4.py";before=path.read_bytes()
    engine=pack._load_engine()
    assert engine.MANIFEST==pack.MANIFEST
    spec=importlib.util.spec_from_file_location("original_v4",path)
    original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
    assert original.MANIFEST=="DELIVERY_MANIFEST_V4.json" and original.json is json
    assert path.read_bytes()==before
