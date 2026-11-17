"""주석(data/notes/*.json)을 읽고 분석 결과를 내는 규칙. Claude 없이 GitHub Actions에서 돈다.
주석 원문을 보고서에 옮기지 않고, 주석에서 확인한 숫자와 판단(근거 주석 번호 포함)만 남긴다.

note_findings(): 확인 항목 전부(경고가 아닌 '확인 결과'도 info로 포함) → 보고서 「주석 점검」
note_signals(): 그중 경고(high/mid/low)만 → 위험 신호 목록
answer_signals(): 숫자 규칙이 "주석 확인"으로 남긴 질문에 주석으로 답함(영업외 손익 → 환산·외환차 분해)

규칙(모두 [주석]으로 시작하는 신호)
1. 금융보증충당부채: 남의 빚에 대한 보증 손실 예상분. 자본 대비 10% 이상이면 높음
2. 특수관계자 채권·대여금 손실충당금: 금액(또는 '전액') 언급
3. 매출 상위 고객 1곳 비중(누적): 50% 이상 주의, 30% 이상 참고
4. 재고 평가충당률(평가충당금 ÷ 취득원가): 50% 이상 높음, 30% 이상 주의, 15% 이상 참고
5. 계속기업 가정에 대한 중요한 불확실성 언급: 높음
6. 외환손익 분해(기타·금융 손익 주석): 외화환산손익(비현금)과 외환차손익(실현)

원문 형식이 회사마다 달라 숫자를 못 찾으면 그 규칙은 조용히 건너뛴다(틀린 경고보다 경고 없음이 낫다).
"""
from __future__ import annotations

import re

from common import DATA, f1, pct

HIGH, MID, LOW, INFO = "high", "mid", "low", "info"

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


def _ref(n) -> str:
    return f"주석 {n['no']}. {n['title']}"


def _f(level, text, refs):
    refs = [r for r in dict.fromkeys(refs) if r]
    return {"level": level, "text": text, "ref": ", ".join(refs)}


# ── 규칙 ──────────────────────────────────────────────────────
def guarantee(info, eq):
    best, ref = None, None
    for n in info.get("all_notes", []):
        for t, div in _table_units(n):
            for r in t:
                if r and re.search(r"금융\s*보증\s*(충당)?부채", r[0]):
                    v = next((num(c) for c in r[1:] if num(c) is not None), None)
                    if v:
                        best, ref = max(best or 0, abs(v) / div), _ref(n)
        if best:
            break
    if not best:
        return _f(INFO, "금융보증충당부채: 주석 표에 해당 항목 없음", [])
    share = best / eq * 100 if eq else None
    lvl = HIGH if share is not None and share >= 10 else MID
    return _f(lvl, f"[주석] 금융보증충당부채 {f1(best)}억" + (f"(자본의 {share:.1f}%)" if share else "") +
              " — 다른 회사 빚에 선 보증·담보의 손실 예상분, 실제 유출 시점 확인", [ref])


def related_allowance(info, eq):
    amt, full, refs = None, False, []
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
                refs.append(_ref(n))
            elif re.search(r"전액", sent):
                full = True
                refs.append(_ref(n))
    if amt:
        share = amt / eq * 100 if eq else None
        lvl = MID if share is not None and share >= 10 else LOW
        return _f(lvl, f"[주석] 특수관계자 채권·대여금 손실충당금 {f1(amt)}억" +
                  (f"(자본의 {share:.1f}%)" if share else "") + " — 회수 가능성과 추가 자금 지원 여부 확인", refs)
    if full:
        return _f(LOW, "[주석] 특수관계자 채권·대여금에 전액 손실충당 — 대상·금액을 주석에서 확인", refs)
    return _f(INFO, "특수관계자 채권·대여금 손실충당: 주석에 언급 없음", [])


def customer_share(info, ytd):
    if not ytd:
        return None
    for n in _notes(info, "영업부문·매출 구성"):
        for t, div in _table_units(n):
            vals = []
            # ① 행마다 고객: 앞쪽 절반 열이 당기(3개월·누적)
            for r in t:
                if r and CUSTOMER.match(r[0]):
                    # "-"(그 기간엔 해당 없음)도 자리로 세야 당기 칸 위치가 맞음(조이시티 고객 1)
                    slots = [c for c in r[1:] if num(c) is not None or c.strip() in ("-", "—", "–")]
                    vals += [x for x in (num(c) for c in slots[:max(1, (len(slots) + 1) // 2)]) if x is not None]
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
                    return _f(MID, f"[주석] 매출 상위 고객 1곳 비중 {s:.1f}%(누적) — 한 고객 수요에 실적이 좌우됨", [_ref(n)])
                if s >= 30:
                    return _f(LOW, f"[주석] 매출 상위 고객 1곳 비중 {s:.1f}%(누적)", [_ref(n)])
                return _f(INFO, f"매출 상위 고객 1곳 비중 {s:.1f}%(누적) — 30% 미만", [_ref(n)])
    return None


def inventory_reserve(info):
    triples, ref = [], None
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
                        ref = ref or _ref(n)
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
        return _f(INFO, f"재고 평가충당률 {rate:.1f}%{chg} — 15% 미만", [ref])
    return _f(lvl, f"[주석] 재고 평가충당률 {rate:.1f}%{chg} — 취득원가의 그만큼이 팔리기 어렵다고 본 금액, 추가 손실 여부 확인", [ref])


def going_concern(info):
    for n in info.get("all_notes", []):
        txt = n.get("text", "")
        if "계속기업" in txt and re.search(r"중요한\s*불확실성|유의적\s*의문", txt):
            return _f(HIGH, f"[주석] 계속기업 가정에 대한 불확실성 언급 — 주석 원문 확인", [_ref(n)])
    return _f(INFO, "계속기업 불확실성: 언급 없음", [])


FX = [("conv", +1, re.compile(r"외화\s*환산\s*이익")), ("conv", -1, re.compile(r"외화\s*환산\s*손실")),
      ("real", +1, re.compile(r"외환\s*차익")), ("real", -1, re.compile(r"외환\s*차손"))]
PERIOD_PREV = re.compile(r"^\s*(전분기|전반기|전기|전년)")
PERIOD_CUR = re.compile(r"^\s*(당분기|당반기|당기)")


def fx_breakdown(info):
    """기타·금융 손익 주석에서 외환손익을 외화환산(비현금)과 외환차(실현)로 나눈다(억 원).
    행 이름은 첫째 또는 둘째 칸(「기타수익 | 외환차익 | …」), 숫자는 첫 값(3개월 또는 당기).
    당분기·전분기 표가 따로 있으면 전분기 표는 건너뛴다."""
    tot = {"conv": 0.0, "real": 0.0}
    refs, quarterly, seen = [], False, set()
    for n in _notes(info, "기타손익·금융손익(외환)"):
        cur = True
        for t, div in _table_units(n):
            first = (t[0][0] if t and t[0] else "") or ""
            if PERIOD_PREV.match(first):
                cur = False
            elif PERIOD_CUR.match(first):
                cur = True
            if any("3개월" in c for r in t[:4] for c in r):
                quarterly = True
            if not cur:
                continue
            for r in t:
                for li in (0, 1):
                    if li >= len(r):
                        continue
                    hit = next(((k, sg) for k, sg, pat in FX if pat.search(r[li])), None)
                    if not hit:
                        continue
                    key = (n["no"], r[li])
                    v = next((num(c) for c in r[li + 1:] if num(c) is not None), None)
                    if v is None or key in seen:
                        break
                    seen.add(key)
                    tot[hit[0]] += hit[1] * v / div
                    refs.append(_ref(n))
                    break
    if not refs:
        return None
    return {"conv": tot["conv"], "real": tot["real"], "basis": "3개월" if quarterly else "누적", "refs": list(dict.fromkeys(refs))}


def fx_finding(info, nonop):
    fx = fx_breakdown(info)
    if not fx:
        return None
    tot = fx["conv"] + fx["real"]
    txt = (f"외환손익({fx['basis']}) {f1(tot, sign=True)}억 = 외화환산 {f1(fx['conv'], sign=True)}억(비현금) + "
           f"외환차 {f1(fx['real'], sign=True)}억(실현)")
    if nonop is not None and fx["basis"] == "3개월":
        txt += f" — 영업외 손익 {f1(nonop, sign=True)}억 = 외환 {f1(tot, sign=True)}억 + 그 밖 {f1(nonop - tot, sign=True)}억"
    return _f(INFO, txt, fx["refs"]) | {"fx": fx}


def _nonop(quarters, label):
    q = next((q for q in quarters if q["q"] == label), None)
    if not q or q.get("pretax") is None or q.get("op") is None:
        return None
    return q["pretax"] - q["op"]


def note_findings(info: dict | None, quarters: list, label: str) -> list[dict]:
    """주석에서 확인한 항목 전부. level: high/mid/low(경고) 또는 info(확인 결과)."""
    if not info or not info.get("all_notes"):
        return []
    eq, ytd = _eq(quarters, label), _ytd_rev(quarters, label)
    out = []
    for rule in (lambda: going_concern(info), lambda: guarantee(info, eq), lambda: related_allowance(info, eq),
                 lambda: customer_share(info, ytd), lambda: inventory_reserve(info),
                 lambda: fx_finding(info, _nonop(quarters, label))):
        try:
            s = rule()
        except Exception:
            s = None
        if s:
            out.append(s)
    return out


def public(findings: list) -> list:
    """저장·표시용(내부 계산값 제거)."""
    return [{k: v for k, v in x.items() if k in ("level", "text", "ref")} for x in findings]


def note_signals(info: dict | None, quarters: list, label: str, findings: list | None = None) -> list[dict]:
    fs = findings if findings is not None else note_findings(info, quarters, label)
    return [{"level": x["level"], "text": x["text"]} for x in fs if x["level"] != INFO]


def answer_signals(sig: list, findings: list) -> list:
    """숫자 규칙이 "주석 확인"으로 남긴 질문에 주석 확인 결과로 답한다."""
    fx = next((x["fx"] for x in findings if x.get("fx")), None)
    out = []
    for x in sig:
        t = x["text"]
        if fx and t.startswith("영업외 손익") and "주석 확인" in t:
            conv, real = fx["conv"], fx["real"]
            ans = f"주석: 외화환산 {f1(conv, sign=True)}억(비현금)·외환차 {f1(real, sign=True)}억(실현)"
            if abs(conv) >= abs(real) * 2 and abs(conv) >= 5:
                ans += " → 대부분 비현금 환산손익"
            elif abs(real) >= abs(conv) * 2 and abs(real) >= 5:
                ans += " → 실제 거래에서 난 손익이 큼"
            x = x | {"text": t.split(" — ")[0] + " — " + ans}
        out.append(x)
    return out


# ── 사업의 내용 「매출 및 수주상황」 표 → 품목별 계산 결과 ─────────────
PERIOD_HEAD = re.compile(r"제\s*\d+\s*기")
KIND = re.compile(r"^(내\s*수|수\s*출|합\s*계|소\s*계|계)$")


def _clean(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _name(s: str) -> str:
    """"기 타", "합   계"처럼 글자마다 띄운 이름은 붙이고, 나머지는 공백 하나로."""
    toks = s.split()
    return "".join(toks) if toks and all(len(t) == 1 for t in toks) else " ".join(toks)


def parse_sales(tables: list) -> dict | None:
    """「매출실적」 표를 품목별 {이름: {"total": [기간별], "export": [기간별]}}로. 기간 순서는 표 머리행 순서
    (보통 당기 누적, 전기 연간, 전전기 연간). 단위는 억 원."""
    recent_units = []
    for t in tables:
        txt = [" ".join(r) for r in t[:2]]
        if len(t) <= 2 and any("단위" in x for x in txt):
            recent_units = txt
            continue
        hi = next((i for i, r in enumerate(t) if sum(bool(PERIOD_HEAD.search(c)) for c in r) >= 2), None)
        if hi is None:
            continue
        periods = [c for c in t[hi] if PERIOD_HEAD.search(c)]
        k = len(periods)
        div = unit_div([" ".join(r) for r in t[:hi + 1]] + recent_units)
        items, order, cur = {}, [], None
        for r in t[hi + 1:]:
            if len(r) < k + 1:
                continue
            tail = r[-k:]
            if not all(num(c) is not None or c.strip() in ("-", "—", "") for c in tail):
                continue
            vals = [(num(c) or 0.0) / div for c in tail]
            text = [c for c in r[:-k] if c.strip()]
            kind = "합계"
            if text and KIND.match(_clean(text[-1])):
                kind = {"내수": "domestic", "수출": "export"}.get(_clean(text[-1]), "total")
                text = text[:-1]
            else:
                kind = "total"
            if text:
                name = text[-1].strip()
                cur = "합계" if KIND.match(_clean(name)) else _name(name)
            if cur is None:
                continue
            it = items.setdefault(cur, {})
            if cur not in order:
                order.append(cur)
            it[kind] = vals
        if not items:
            continue
        for it in items.values():
            if "total" not in it and ("domestic" in it or "export" in it):
                d, e = it.get("domestic", [0.0] * k), it.get("export", [0.0] * k)
                it["total"] = [a + b for a, b in zip(d, e)]
        return {"periods": periods, "items": {n: items[n] for n in order if "total" in items[n]}}
    return None


def load_prev_year_notes(corp_code: str, label: str) -> dict | None:
    """작년 같은 분기 주석 파일(있으면 품목별 진짜 전년 동기 비교에 씀)."""
    import json
    if not label or len(label) < 6:
        return None
    p = DATA / "notes" / f"{corp_code}_{int(label[:4]) - 1}{label[4:]}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:
        return None


def sales_mix(info: dict | None, quarters: list, label: str, prev_info: dict | None = None) -> dict | None:
    """품목별 매출 계산: 비중·수출 비중·연환산 vs 전년 연간·(있으면) 전년 동기 대비, 재무제표 매출과 대조."""
    if not info or not info.get("sales_tables"):
        return None
    ps = parse_sales(info["sales_tables"])
    if not ps:
        return None
    q = int(label[-1]) if label and label[-1].isdigit() else 4
    items = ps["items"]
    total_row = items.get("합계")
    names = [n for n in items if n != "합계"]
    cum_total = total_row["total"][0] if total_row else sum(items[n]["total"][0] for n in names)
    prev_ps = parse_sales(prev_info["sales_tables"]) if prev_info and prev_info.get("sales_tables") else None
    rows = []
    for n in names + (["합계"] if total_row else []):
        tv = items[n]["total"]
        if n != "합계" and all(abs(v) < 0.05 for v in tv[:2]):
            continue  # 당기·전년 모두 매출 없는 품목(예: 단종)
        cum = tv[0]
        prev_annual = tv[1] if len(tv) > 1 else None
        annualized = cum * 4 / q
        r = {"name": n, "cum": cum,
             "share": cum / cum_total * 100 if cum_total else None,
             "export_share": items[n]["export"][0] / cum * 100 if "export" in items[n] and cum else None,
             "prev_annual": prev_annual,
             "annualized": annualized,
             "pace_chg": (annualized / prev_annual - 1) * 100 if prev_annual else None,
             "yoy": None}
        if prev_ps and n in prev_ps["items"]:
            pv = prev_ps["items"][n]["total"][0]
            r["yoy"] = (cum / pv - 1) * 100 if pv else None
        rows.append(r)
    ytd = _ytd_rev(quarters, label)
    check = None
    if ytd and cum_total:
        diff = (cum_total / ytd - 1) * 100
        check = (f"품목 합계 {f1(cum_total)}억 = 재무제표 누적 매출 {f1(ytd)}억과 일치" if abs(diff) <= 2 else
                 f"품목 합계 {f1(cum_total)}억 vs 재무제표 누적 매출 {f1(ytd)}억({pct(diff)}) — 연결·별도 기준 또는 내부거래 차이 확인")
    return {"periods": ps["periods"], "q": q, "rows": rows, "check": check, "has_yoy": prev_ps is not None}


def sales_summary(mix: dict | None) -> list[str]:
    """품목별 계산 결과를 한 줄씩."""
    if not mix:
        return []
    rows = [r for r in mix["rows"] if r["name"] != "합계"]
    out = []
    if mix.get("check"):
        out.append(mix["check"])
    if rows:
        top = max(rows, key=lambda r: r["cum"])
        out.append(f"최대 품목 {top['name']} {f1(top['cum'])}억(비중 {top['share']:.1f}%)" +
                   (f", 수출 비중 {top['export_share']:.1f}%" if top.get("export_share") is not None else ""))
    key = "yoy" if mix.get("has_yoy") else "pace_chg"
    basis = "전년 동기 대비" if mix.get("has_yoy") else f"같은 속도 연간 환산(×4/{mix['q']}) vs 전년 연간"
    movers = sorted([r for r in rows if r.get(key) is not None and r["cum"] >= 1], key=lambda r: r[key])
    if movers:
        lo, hi = movers[0], movers[-1]
        out.append(f"{basis}: 가장 줄어든 품목 {lo['name']} {pct(lo[key])}" +
                   (f", 가장 늘어난 품목 {hi['name']} {pct(hi[key])}" if hi is not lo else ""))
    tot = next((r for r in mix["rows"] if r["name"] == "합계"), None)
    if tot and tot.get(key) is not None:
        out.append(f"전체 {basis} {pct(tot[key])}" + ("" if mix.get("has_yoy") else " — 계절성 무시한 단순 환산"))
    return out
