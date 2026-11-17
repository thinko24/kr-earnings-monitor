"""실제 DART 응답(조이시티 2026 3분기) 일부로 파서 확인: python tests/test_parser.py"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from financials import parse_report, build_year

rows = json.loads((Path(__file__).parent / "fixtures/joycity_2026_11014.json").read_text(encoding="utf-8"))
q = build_year(2026, {"11014": parse_report(rows, "11014")})[0]
expect = {"rev": 295.7, "op": -32.1, "pretax": -61.9, "net": -70.2, "ca": 408.9, "cl": 1160.9,
          "tl": 1229.6, "eq": 783.2, "cash": 119.8, "stdebt": 985.0, "ocf_ytd": -40.2}
bad = {k: (q.get(k), v) for k, v in expect.items() if q.get(k) != v}
print("OK" if not bad else f"불일치: {bad}")
sys.exit(1 if bad else 0)
