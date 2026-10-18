"""Create a V4.1 delivery with the verified V4 snapshot/locking/ZIP engine.

python scripts/package_v41.py --output ../equity-data-system-v4-1.zip

No packaging runs on import. Previous manifests and archives remain historical.
Only this module's isolated V4 engine instance receives the new manifest name,
manifest metadata, and new operational-table privacy policy.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

MANIFEST = "DELIVERY_MANIFEST_V41.json"
DEFAULT_ARCHIVE = "equity-data-system-v4-1.zip"


class _ManifestJson:
    """Customize manifest metadata before the original engine hashes/validates it.

    V4 exposes the filename as a constant, but its version/description are fixed
    literals. Delegate all JSON operations; adapt only its manifest envelope.
    This never edits JSON after ZIP validation or alters the shared json module.
    """
    def dumps(self, value, *args, **kwargs):
        if isinstance(value, dict) and value.get("source_manifest_written") == MANIFEST:
            value = {**value,
                "manifest_version": "delivery-v4.1-sha256-v1",
                "historical_manifests": "V3 and V4 manifests retained as historical evidence; DELIVERY_MANIFEST_V41.json is authoritative for this delivery.",
                "packaging_engine": "Verified V4 engine; isolated V4.1 filename, metadata, and operational-column policy adapter.",
                "new_operational_table_policy": "Initialization diagnostics/configuration and raw_import_events JSON only; original import source paths, retrieval provenance, business facts, and raw bytes are unchanged."}
        return json.dumps(value, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(json, name)


def _load_engine():
    spec = importlib.util.spec_from_file_location("_isolated_v41_delivery_engine", Path(__file__).with_name("package_v4.py"))
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.MANIFEST = MANIFEST
    engine.json = _ManifestJson()
    previous_columns = engine.operational_columns

    def operational_columns(connection):
        yield from previous_columns(connection)
        # Initialization records are scheduling/configuration diagnostics, not
        # extracted research facts. Imports retain immutable source identity and
        # timing; only their operational result envelopes are subject to scrub.
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if table not in {"initialization_batches", "initialization_events", "raw_import_events"}:
                continue
            for info in connection.execute("PRAGMA table_info(" + engine._quote(table) + ")"):
                if "TEXT" not in info[2].upper():
                    continue
                column = info[1]
                if table == "initialization_batches" and column in {"specification_json", "result_json", "last_error"}:
                    yield table, column
                elif table == "initialization_events" and column == "details_json":
                    yield table, column
                elif table == "raw_import_events" and column in {"sources_json", "result_json"}:
                    yield table, column

    engine.operational_columns = operational_columns
    previous_exclusion = engine.exclusion

    def exclusion(path, root):
        if path.relative_to(root).as_posix() == MANIFEST:
            return "v41_manifest_regenerated"
        return previous_exclusion(path, root)

    engine.exclusion = exclusion
    return engine


def package(project_root, output=None):
    root = Path(project_root).resolve()
    destination = Path(output).resolve() if output is not None else root.parent / DEFAULT_ARCHIVE
    # Delegation retains existing-file rejection, source change detection,
    # collector locks, SQLite backup/quick_check, raw hash checks and ZIP hash
    # verification. No post-processing of a published archive is performed.
    return _load_engine().package(root, destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(package(args.project_root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
