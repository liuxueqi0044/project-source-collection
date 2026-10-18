"""Immutable evidence for form-typed original XML discovery, before/after."""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_sec_v4 import original_xml_probe

parser = argparse.ArgumentParser()
parser.add_argument("--phase", required=True, choices=("before", "after"))
parser.add_argument("--label", default="")
args = parser.parse_args()
if args.label and not re.fullmatch(r"[a-z0-9-]+", args.label):
    parser.error("label must contain lowercase letters, digits or hyphens")
folder = ROOT / "reports/v4"
folder.mkdir(parents=True, exist_ok=True)
stem = "sec-original-xml-" + args.phase + ("-" + args.label if args.label else "")
target = folder / (stem + ".json")
if target.exists():
    raise SystemExit("Refusing to overwrite evidence")
source = ROOT / "equity_data/adapters/sec_source.py"
if args.phase == "before":
    (folder / "sec-source-before-v4.py").write_bytes(source.read_bytes())
result = original_xml_probe(folder / (stem + "-data"))
result.update(captured_at=datetime.now(timezone.utc).isoformat(), production_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"phase": args.phase, "original_saved": result["original_saved"], "evidence": str(target)}))
