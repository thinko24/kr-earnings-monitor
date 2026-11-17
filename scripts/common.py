"""공통 유틸: 경로, JSON 입출력, 숫자 형식, 날짜."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
RUNS = ROOT / "runs"
WATCHLIST = ROOT / "watchlist.yml"

KST = timezone(timedelta(hours=9))

# 보고서 코드 ↔ 분기
REPRT_BY_Q = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
Q_BY_REPRT = {v: k for k, v in REPRT_BY_Q.items()}
REPRT_NAME = {"11013": "1분기보고서", "11012": "반기보고서", "11014": "3분기보고서", "11011": "사업보고서"}


def now_kst() -> datetime:
    return datetime.now(KST)


def iso_now() -> str:
    return now_kst().isoformat(timespec="seconds")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def to_eok(won) -> float | None:
    """원 → 억 원(소수 첫째 자리)."""
    if won is None:
        return None
    return round(won / 1e8, 1)


MINUS = "−"  # 표시용 마이너스(U+2212)


def f1(x, sign: bool = False) -> str:
    """억 원 숫자를 '1,104.0' 형식으로. sign=True면 +/− 표시."""
    if x is None:
        return "—"
    s = f"{abs(x):,.1f}"
    if x < 0:
        return MINUS + s
    return ("+" + s) if sign and x > 0 else s


def pct(x, sign: bool = True) -> str:
    if x is None:
        return "—"
    s = f"{abs(x):.1f}%"
    if x < 0:
        return MINUS + s
    return ("+" + s) if sign and x > 0 else s


def growth(cur, prev):
    """증감률(%). 이전 값이 0 이하·없음이면 None (적자→흑자 같은 경우 비율이 의미 없음)."""
    if cur is None or prev is None or prev <= 0:
        return None
    return (cur / prev - 1) * 100


def ratio(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b * 100


def parse_num(s) -> float | None:
    """'−4', '+15', '1,104.0', '320' 같은 문자열 → float."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).replace(",", "").replace(MINUS, "-").replace("–", "-").strip()
    m = re.search(r"[-+]?\d+(\.\d+)?", t)
    return float(m.group(0)) if m else None


def qlabel(year: int, q: int) -> str:
    return f"{year}Q{q}"


def qparse(label: str) -> tuple[int, int]:
    y, q = label.split("Q")
    return int(y), int(q)


def qnext(label: str) -> str:
    y, q = qparse(label)
    return qlabel(y + 1, 1) if q == 4 else qlabel(y, q + 1)


def qprev_year(label: str) -> str:
    y, q = qparse(label)
    return qlabel(y - 1, q)


def period_text(label: str) -> str:
    """'2026Q3' → '2026 3Q'."""
    y, q = qparse(label)
    return f"{y} {q}Q"


def qsort_key(label: str):
    return qparse(label)
