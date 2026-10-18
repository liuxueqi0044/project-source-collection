"""Synthetic declaration-scan counts against preserved V3 and V4 code."""
import hashlib
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from equity_data.engine import run_cycle
from equity_data.store import Store
import equity_data.adapters.sec_source as current
from test_sec_dependencies_v3 import Client, fixture, facts, DECLARATIONS, CONTEXTS, FACT

baseline = ROOT / "reports/v4/sec-source-before-v4.py"
spec = importlib.util.spec_from_file_location("equity_data.adapters.sec_before_v4", baseline)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
output = ROOT / "reports/v4/sec-support-scan-benchmark.json"
if output.exists():
    raise SystemExit("Refusing to overwrite evidence")
results = {}
for label, module, path in (("before", previous, baseline), ("after", current, Path(current.__file__))):
    with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
        fixtures, config, base = fixture()
        fixtures[base + "report.htm"] = DECLARATIONS + CONTEXTS + b"</body></html>"
        manifest = b"<ACCEPTANCE-DATETIME>20240801163028\n<DOCUMENT>\n<TYPE>10-Q\n<FILENAME>report.htm\n</DOCUMENT>\n"
        for index in range(24):
            filename = "arbitrary-rendered-" + str(index) + ".htm"
            fixtures[base + filename] = DECLARATIONS + FACT + ("<p>Part %s</p>" % index).encode() + b"</body></html>"
            manifest += f"<DOCUMENT>\n<TYPE>EX-99.1\n<FILENAME>{filename}\n</DOCUMENT>\n".encode()
        fixtures[base + "0000000789-24-000001-index-headers.html"] = manifest
        started = time.monotonic()
        with patch.object(module, "_document_ids", wraps=module._document_ids) as scan:
            result = run_cycle(store, Client(store, fixtures), config, {"sec_edgar": module.SecAdapter()})
        results[label] = {"production_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "wall_seconds": round(time.monotonic() - started, 4),
                          "declaration_scan_calls": scan.call_count, "facts": len(facts(store)), "run": result}
report = {"synthetic": True, "scope": "one accession: 1 declaration original + 24 distinct rendered report originals",
          "limitation": "Demonstrates avoided declaration rescans; wall time is local, not a full-market throughput benchmark.", "results": results}
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({key: {k: v for k, v in value.items() if k != "run"} for key, value in results.items()}))
