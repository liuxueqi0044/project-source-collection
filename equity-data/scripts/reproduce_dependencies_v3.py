"""Capture the unchanged delayed-support reproduction before/after the fix."""
import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("dependency_probe", ROOT / "tests/test_sec_dependencies_v3.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    args = parser.parse_args()
    directory = ROOT / "reports/v3"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("dependency-" + args.phase + ".json")
    if target.exists():
        raise SystemExit("Evidence is immutable; choose a new evidence filename instead of overwriting " + str(target))
    result = probe.arrival_probe(directory / ("dependency-" + args.phase + "-data"))
    result.update(phase=args.phase, captured_at=datetime.now(timezone.utc).isoformat(),
                  production_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in ("equity_data/adapters/sec_source.py", "equity_data/adapters/base.py", "equity_data/engine.py", "equity_data/queue.py")})
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(target), "correct": result["correct"],
                      "facts": len(result["facts"]), "resumed_executed": result["resumed_run"]["executed"]}))


if __name__ == "__main__":
    main()
