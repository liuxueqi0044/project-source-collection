"""Source contracts are data; runtime connectivity is evidence, not documentation."""
from __future__ import annotations
import importlib
import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "configs" / "source_registry.json"


def get_registry(path=None):
    return json.loads(Path(path or REGISTRY_PATH).read_text(encoding="utf-8"))


def build_adapters(config, store=None):
    from .adapters.core_sources import DependencyAdapter
    from .queue import Queue
    registry = get_registry(config.get("source_registry"))
    entries = {item["source_id"]: item for item in registry["sources"]}
    entries.update({item["source_id"]: item for item in config.get("source_plugins", [])})
    selected = config.get("sources", list(entries))
    if isinstance(selected, dict):
        selected = [k for k,v in selected.items() if v is not False and (not isinstance(v,dict) or v.get("enabled",True))]
    adapters = {}
    for source_id in selected:
        entry = entries.get(source_id)
        if entry is None:
            continue
        if store is not None:
            store.register_source(source_id, **entry)
        if config.get("market") not in entry["markets"] and "GLOBAL" not in entry["markets"]:
            if store is not None:
                Queue(store).gap("MARKET_NOT_SUPPORTED", "Source does not support this market; no substitute market was used.", source_id=source_id,
                                 details={"requested":config.get("market"), "supported":entry["markets"]})
            continue
        if entry.get("adapter"):
            module, name = entry["adapter"].split(":",1)
            adapters[source_id] = getattr(importlib.import_module(module),name)()
        else:
            adapters[source_id] = DependencyAdapter(source_id, entry["data_types"], entry.get("dependency_reason", "Source adapter/permission not available"))
    if store is not None:
        supported = {d for sid in adapters for d in entries[sid]["data_types"] if entries[sid].get("adapter")}
        for dataset in set(config.get("data_types",[])) - supported:
            Queue(store).gap("REQUESTED_DATA_TYPE_UNAVAILABLE", "None of the selected implemented adapters provides this data type; check dependency list.", source_id="registry", data_type=dataset)
    return adapters


def capability_matrix(store=None):
    registry = get_registry()
    health = {}
    if store is not None:
        from .engine import status_report
        for row in status_report(store).get("source_health",[]):
            health[row["source_id"]]=row
    return {**registry,"runtime_health":health,"status_rule":"implemented/mock_tested/connected/verified are distinct; only runtime retrieval evidence supports connected; verified requires a documented independent audit"}
