"""Repeatable synthetic reproductions; never overwrite saved pre-fix evidence."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from equity_data.parsers import PARSER_VERSION, parse_document

CASES = {
    "negative_ascii": ("<p>For fiscal 2031 EPS is expected to be USD -2.50.</p>", ["-2.50"]),
    "negative_parentheses": ("<p>For fiscal 2031 EPS is expected to be (USD 2.50).</p>", ["-2.50"]),
    "negative_currency_parentheses": ("<p>For fiscal 2031 EPS is expected to be USD (2.50).</p>", ["-2.50"]),
    "mixed_dom": ("<p>For fiscal 2031 revenue is expected to be USD 10 million.</p><div>For fiscal 2031 EPS is expected to be USD 2.50.</div><table><tr><td>Unrelated table</td></tr></table><div><span>For fiscal 2031 operating margin is expected to be 12 percent.</span></div>", ["10000000", "2.50", "12"]),
}

ADDITIONAL_CASES = {
    "negative_before_currency": ("<p>For fiscal 2031 EPS is expected to be -USD 2.50.</p>", ["-2.50"]),
    "negative_before_dollar": ("<p>For fiscal 2031 EPS is expected to be -$2.50.</p>", ["-2.50"]),
}
EDGE_CASES = {
    "hidden_minus": ('<h2>Outlook fiscal 2025</h2><table><tr><th>Metric</th><th>USD millions</th></tr><tr><td>Revenue</td><td><span hidden>-</span>5</td></tr></table>', []),
    "hidden_row": ('<h2>Outlook fiscal 2025</h2><table><tr><th>Metric</th><th>USD millions</th></tr><tr hidden><td>Revenue</td><td>-5</td></tr><tr><td>Revenue</td><td>5</td></tr></table>', []),
    "percentage_monetary_scale": ('<h2>Outlook fiscal 2025</h2><table><tr><th>Metric</th><th>percent millions</th></tr><tr><td>Operating margin</td><td>5</td></tr></table>', []),
    "semantic_loss": ('<p>We expect net income for fiscal 2025 to be a loss of USD 5 million.</p>', []),
    "stale_heading": ('<h2>Outlook fiscal 2025</h2><p>Actual results for fiscal 2024 are shown below.</p><table><tr><th>Metric</th><th>USD millions</th></tr><tr><td>Revenue</td><td>5</td></tr></table>', []),
}

def run(output, parse_fn=parse_document, parser_version=PARSER_VERSION, cases=CASES):
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder / "results.json"
    if report_path.exists():
        raise SystemExit(f"Refusing to overwrite evidence: {report_path}")
    results = []
    for name, (body, expected) in cases.items():
        raw = ("<html><body>" + body + "</body></html>").encode()
        (folder / f"{name}.html").write_bytes(raw)
        result = parse_fn(raw, source_url=f"https://synthetic.invalid/{name}")
        actual = [row["data"]["point"] for row in result["observations"] if row["dataset"] == "guidance"]
        results.append({"case": name, "expected_points": expected, "actual_points": actual,
                      "passed": actual == expected, "raw_sha256": hashlib.sha256(raw).hexdigest(), "result": result})
    report = {"evidence_type": "synthetic_regression_reproduction", "parser_version": parser_version,
              "cases": results, "passed": sum(c["passed"] for c in results), "total": len(results)}
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"path": str(report_path), "parser_version": parser_version, "passed": report["passed"], "total": report["total"]}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--additional", action="store_true")
    parser.add_argument("--edge-cases", action="store_true")
    parser.add_argument("--baseline-file", help="Saved original parser source, for reproducible pre-fix failures")
    args = parser.parse_args()
    fn, version = parse_document, PARSER_VERSION
    if args.baseline_file:
        import importlib.util
        spec = importlib.util.spec_from_file_location("equity_data.preserved_baseline", args.baseline_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn, version = module.parse_document, module.PARSER_VERSION
    run(args.output, fn, version, EDGE_CASES if args.edge_cases else (ADDITIONAL_CASES if args.additional else CASES))
