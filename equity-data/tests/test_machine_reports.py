import csv
import json
from pathlib import Path
from types import SimpleNamespace

from equity_data.adapters.base import BaseAdapter
from equity_data.engine import run_cycle
from equity_data.machine_reports import generate_machine_reports
from equity_data.queue import Queue
from equity_data.store import Store


def test_empty_machine_never_claims_complete_or_verified(tmp_path):
    with Store(tmp_path/"db") as store:
        paths=generate_machine_reports(store,tmp_path/"reports",registry=[])
    report=json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert not report["enumeration"]["discovery_frontier_closed"]
    assert not report["summary"]["coverage_complete"]
    assert report["acceptance"]["real_field_accuracy"]["result"]["status"]=="not_provided"
    assert all(Path(path).is_absolute() and Path(path).exists() for path in paths.values())


def test_full_inventory_preserved_in_json_and_csv(tmp_path):
    with Store(tmp_path/"db") as store:
        queue=Queue(store)
        for index in range(120):
            queue.emit({"source_id":"source","resource_key":str(index),"kind":"doc","data_type":"documents","url":"https://example.invalid/"+str(index)})
            queue.gap("MISSING","Not parsed",source_id="source",details={"index":index})
        paths=generate_machine_reports(store,tmp_path/"reports",registry=[])
    report=json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert len(report["resources"])==len(report["tasks"])==len(report["gaps"])==120
    with Path(paths["inventory_csv"]).open(encoding="utf-8-sig",newline="") as handle:
        assert len(list(csv.DictReader(handle)))==120
    markdown=Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "全部 120 项" in markdown


def test_registry_and_acceptance_are_separate_from_runtime_success(tmp_path):
    class Roots(BaseAdapter):
        source_id="source"
        def discover(self,ctx,scope):
            ctx.emit({"source_id":"source","resource_key":"future-child","kind":"doc","data_type":"documents"})
            return {"enumeration_complete":True,"discovered":1}
    with Store(tmp_path/"db") as store:
        run_cycle(store,SimpleNamespace(),{"sources":["source"]},{"source":Roots()},discover_only=True)
        paths=generate_machine_reports(store,tmp_path/"reports",registry={"source":{"status":"implemented","markets":["US"]}},
            acceptance={"software_tests":{"passed":22},"real_field_accuracy":{"status":"pending"}})
    report=json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert report["source_registry"][0]["source_id"]=="source"
    assert report["acceptance"]["software_tests"]["result"]["passed"]==22
    assert not report["enumeration"]["discovery_frontier_closed"]
    assert report["summary"]["resource_states"]=={"discovered_not_downloaded":1}


def test_manual_results_excluded_from_automatic_label_and_html_escaped(tmp_path):
    with Store(tmp_path/"db") as store:
        raw=store.put_raw(b"manual",source="manual",url="local:manual")
        store.put_record("guidance","manual",{"amount":12,"extraction_mode":"manual"},source="manual",raw_id=raw["raw_id"],quality="manual_import_unverified")
        Queue(store).gap("TEST","<script>alert(1)</script>",source_id="manual")
        paths=generate_machine_reports(store,tmp_path/"reports",registry=[])
    report=json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert report["record_statistics"]["extraction_modes"]=={"manual":1}
    assert report["record_statistics"]["public_time_unknown"]==1
    assert "<script>alert(1)</script>" not in Path(paths["html"]).read_text(encoding="utf-8")
