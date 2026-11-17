"""주석(data/notes/*.json)에서 숫자로 위험 신호를 뽑는 규칙. Claude 없이 GitHub Actions에서 돈다.

규칙(모두 [주석]으로 시작하는 신호)
1. 금융보증충당부채: 남의 빚에 대한 보증 손실 예상분. 자본 대비 10% 이상이면 높음
2. 특수관계자 채권·대여금 손실충당금: 금액(또는 '전액') 언급
3. 매출 상위 고객 1곳 비중(누적): 50% 이상 주의, 30% 이상 참고
4. 재고 평가충당률(평가충당금 ÷ 취득원가): 50% 이상 높음, 30% 이상 주의, 15% 이상 참고
5. 계속기업 가정에 대한 중요한 불확실성 언급: 높음

원문 형식이 회사마다 달라 숫자를 못 찾으면 그 규칙은 조용히 건너뛴다(틀린 경고보다 경고 없음이 낫다).
"""
from __future__ import annotations

import re

from common import f1

HIGH, MID, LOW = "high", "mid", "low"

NUM = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")
CUSTOMER = re.compile(r"^\s*(?:고객\s*[A-Z\d]+|[A-Z]\s*사)(?:\s*\(\*\d+\))?\s*$")
TOTAL_ROW = re.compile(r"합\s*계|총\s*재고|^\s*계\s*$|총\s*계")


def num(s: str):
    s = (s or "").strip().replace(" ", "")
    if not s or not NUM.match(s):
        return None
    neg = s.startswith("(") or s.startswith("-")
    v = float(s.strip("()").replace(",", "").lstrip("-"))
    return -v if neg else v


def unit_div(texts) -> float:
    """단위 문구 → 억 원으로 나눌 값. 기본은 천원."""
    for t in texts:
        if "단위" not in t:
            continue
        if "백만원" in t:
            return 100.0
        if "천원" in t:
            return 1e5
        if re.search(r"단위\s*[:：]?\s*원", t):
            return 1e8
    return 1e5


def _table_units(note) -> list[tuple[list, float]]:
    """주석의 표마다 (표, 억 환산 나눗수). 단위는 표 안 앞쪽 행이나 바로 앞 문단에서 찾는다."""
    out, recent = [], []
    for b in note.get("blocks") or [{"table": t} for t in note.get("tables", [])]:
        if "p" in b:
            recent = (recent + [b["p"]])[-2:]
            continue
        t = b["table"]
        head = [" ".join(r) for r in t[:3]]
        out.append((t, unit_div(head + recent[::-1])))
        recent = []
    return out


def _ytd_rev(quarters, label):
    y = label[:4]
    vals = [q.get("rev") for q in quarters if q["q"][:4] == y and q["q"] <= label]
    return sum(vals) if vals and all(v is not None for v in vals) else None


def _eq(quarters, label):
    q = next((q for q in quarters if q["q"] == label), None)
    return q.get("eq") if q else None


def _notes(info, cat):
    return [n for n in info.get("all_notes", []) if cat in n.get("categories", [])]


# ── 규칙 ──────────────────────────────────────────────────────
def guarantee(info, eq):
    best = None
    for n in info.get("all_notes", []):
        for t, div in _table_units(n):
            for r in t:
                if r and re.search(r"금융\s*보증\s*(충당)?부채", r[0]):
                    v = next((num(c) for c in r[1:] if num(c) is not None), None)
                    if v:
                        best = max(best or 0, abs(v) / div)
        if best:
            break
    if not best:
        return None
    share = best / eq * 100 if eq else None
    lvl = HIGH if share is not None and share >= 10 else MID
    return {"level": lvl, "text": f"[주석] 금융보증충당부채 {f1(best)}억" + (f"(자본의 {share:.1f}%)" if share else "") +
            " — 다른 회사 빚에 선 보증·담보의 손실 예상분, 실제 유출 시점 확인"}


def related_allowance(info, eq):
    amt, full = None, False
    for n in info.get("all_notes", []):
        if "특수관계자" not in n.get("categories", []) and "특수관계" not in n.get("text", ""):
            continue
        cells = [c for t in n.get("tables", []) for r in t for c in r if len(c) > 15]  # 표 칸에 쓴 설명문(XBRL 형식)
        for sent in re.split(r"(?<=다)\.|\n", "\n".join([n.get("text", "")] + cells)):
            if not re.search(r"충당금", sent) or not re.search(r"설정", sent):
                continue
            if "특수관계자" not in n.get("categories", []) and "특수관계" not in sent:
                continue
            m = re.search(r"([\d,]{5,})\s*천원(?:\s*\([^)]*\))?\s*에\s*대하여", sent)
            if m:
                amt = max(amt or 0, float(m.group(1).replace(",", "")) / 1e5)
            elif re.search(r"전액", sent):
                full = True
    if amt:
        share = amt / eq * 100 if eq else None
        lvl = MID if share is not None and share >= 10 else LOW
        return {"level": lvl, "text": f"[주석] 특수관계자 채권·대여금 손실충당금 {f1(amt)}억" +
                (f"(자본의 {share:.1f}%)" if share else "") + " — 회수 가능성과 추가 자금 지원 여부 확인"}
    if full:
        return {"level": LOW, "text": "[주석] 특수관계자 채권·대여금에 전액 손실충당 — 대상·금액을 주석에서 확인"}
    return None


def customer_share(info, ytd):
    if not ytd:
        return None
    for n in _notes(info, "영업부문·매출 구성"):
        for t, div in _table_units(n):
            vals = []
            # ① 행마다 고객: 앞쪽 절반 열이 당기(3개월·누적)
            for r in t:
                if r and CUSTOMER.match(r[0]):
                    ns = [num(c) for c in r[1:]]
                    ns = [x for x in ns if x is not None]
                    vals += ns[:max(1, (len(ns) + 1) // 2)]
            # ② 열마다 고객(머리행에 A사·B사…): 바로 다음 숫자 행
            if not vals:
                for i, r in enumerate(t):
                    if sum(bool(CUSTOMER.match(c)) for c in r) >= 2 and i + 1 < len(t):
                        vals = [x for x in (num(c) for c in t[i + 1][1:]) if x is not None]
                        break
            shares = [v / div / ytd for v in vals if v and v / div / ytd <= 1.02]
            if shares:
                s = max(shares) * 100
                if s >= 50:
                    return {"level": MID, "text": f"[주석] 매출 상위 고객 1곳 비중 {s:.1f}%(누적) — 한 고객 수요에 실적이 좌우됨"}
                if s >= 30:
                    return {"level": LOW, "text": f"[주석] 매출 상위 고객 1곳 비중 {s:.1f}%(누적)"}
                return None
    return None


def inventory_reserve(info):
    triples = []
    for n in _notes(info, "재고자산"):
        for t, _ in _table_units(n):
            for r in t:
                if not r or not TOTAL_ROW.search(r[0]):
                    continue
                ns = [num(c) for c in r[1:]]
                for i in range(len(ns) - 2):
                    a, b, c = ns[i:i + 3]
                    if a and b and c is not None and a > 0 and abs(a - abs(b) - c) <= max(1, a * 0.005):
                        triples.append((a, abs(b)))
    if not triples:
        return None
    a, b = triples[0]
    rate = b / a * 100
    prev = triples[1][1] / triples[1][0] * 100 if len(triples) > 1 else None
    chg = f"(직전 공시 {prev:.1f}%)" if prev is not None else ""
    lvl = HIGH if rate >= 50 else MID if rate >= 30 else LOW if rate >= 15 else None
    if prev is not None and rate - prev >= 10 and lvl in (LOW, MID):
        lvl = {LOW: MID, MID: HIGH}[lvl]
    if not lvl:
        return None
    return {"level": lvl, "text": f"[주석] 재고 평가충당률 {rate:.1f}%{chg} — 취득원가의 그만큼이 팔리기 어렵다고 본 금액, 추가 손실 여부 확인"}


def going_concern(info):
    for n in info.get("all_notes", []):
        txt = n.get("text", "")
        if "계속기업" in txt and re.search(r"중요한\s*불확실성|유의적\s*의문", txt):
            return {"level": HIGH, "text": f"[주석] 계속기업 가정에 대한 불확실성 언급({n['no']}. {n['title']}) — 주석 원문 확인"}
    return None


def note_signals(info: dict | None, quarters: list, label: str) -> list[dict]:
    if not info or not info.get("all_notes"):
        return []
    eq, ytd = _eq(quarters, label), _ytd_rev(quarters, label)
    out = []
    for rule in (lambda: going_concern(info), lambda: guarantee(info, eq), lambda: related_allowance(info, eq),
                 lambda: customer_share(info, ytd), lambda: inventory_reserve(info)):
        try:
            s = rule()
        except Exception:
            s = None
        if s:
            out.append(s)
    return out
