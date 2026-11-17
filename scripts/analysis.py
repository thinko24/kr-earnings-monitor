"""규칙 기반 분석: 증감, 위험 신호, 회귀 추정, 직전 추정 적중 점검.

원칙: 숫자로 확인되는 것만 말하고, 해석이 필요한 부분은 '확인 필요'로 남긴다.
"""
from __future__ import annotations

import math
import statistics

import numpy as np

from common import (f1, pct, growth, ratio, parse_num, qparse, qnext, qprev_year,
                    period_text, iso_now)
import note_rules

HIGH, MID, LOW, GOOD = "high", "mid", "low", "good"


def qmap(quarters):
    return {q["q"]: q for q in quarters}


def ytd(quarters, label, key):
    """연초~해당 분기 누적."""
    y, qi = qparse(label)
    m = qmap(quarters)
    vals = [m.get(f"{y}Q{i}", {}).get(key) for i in range(1, qi + 1)]
    if any(v is None for v in vals):
        return None
    return round(sum(vals), 1)


# ─────────────────────────────────────────────────────────────
# 지표
# ─────────────────────────────────────────────────────────────
def metrics(quarters, label):
    m = qmap(quarters)
    c = m.get(label, {})
    y, qi = qparse(label)
    pq_label = f"{y}Q{qi-1}" if qi > 1 else f"{y-1}Q4"
    p = m.get(pq_label, {})
    ly = m.get(qprev_year(label), {})
    out = {
        "period": label,
        "rev": c.get("rev"), "op": c.get("op"), "pretax": c.get("pretax"), "net": c.get("net"),
        "rev_yoy": growth(c.get("rev"), ly.get("rev")),
        "rev_qoq": growth(c.get("rev"), p.get("rev")),
        "op_yoy": growth(c.get("op"), ly.get("op")),
        "opm": ratio(c.get("op"), c.get("rev")),
        "opm_ly": ratio(ly.get("op"), ly.get("rev")),
        "nonop": round(c["pretax"] - c["op"], 1) if c.get("pretax") is not None and c.get("op") is not None else None,
        "tax": round(c["pretax"] - c["net"], 1) if c.get("pretax") is not None and c.get("net") is not None else None,
        "cur_ratio": ratio(c.get("ca"), c.get("cl")),
        "cur_ratio_prev": ratio(p.get("ca"), p.get("cl")),
        "debt_ratio": ratio(c.get("tl"), c.get("eq")) if (c.get("eq") or 0) > 0 else None,
        "debt_ratio_prev": ratio(p.get("tl"), p.get("eq")) if (p.get("eq") or 0) > 0 else None,
        "cash": c.get("cash"), "cash_prev": p.get("cash"),
        "stdebt": c.get("stdebt"),
        "cl": c.get("cl"), "cl_prev": p.get("cl"),
        "eq": c.get("eq"), "eq_prev": p.get("eq"), "cap": c.get("cap"),
        "ytd_rev": ytd(quarters, label, "rev"),
        "ytd_rev_ly": ytd(quarters, qprev_year(label), "rev"),
        "ytd_op": ytd(quarters, label, "op"),
        "ytd_op_ly": ytd(quarters, qprev_year(label), "op"),
        "ytd_net": ytd(quarters, label, "net"),
        "ocf_ytd": c.get("ocf_ytd"),
        "ocf_ytd_ly": ly.get("ocf_ytd"),
        "prev_label": pq_label,
        "p_op": p.get("op"), "p_net": p.get("net"),
    }
    out["ytd_rev_yoy"] = growth(out["ytd_rev"], out["ytd_rev_ly"])
    return out


# ─────────────────────────────────────────────────────────────
# 회귀·추정
# ─────────────────────────────────────────────────────────────
def _ols(X, y):
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dof = max(len(y) - X.shape[1], 1)
    se = math.sqrt(ss_res / dof)
    return beta, r2, se


def cost_model(quarters, n=12):
    rows = [q for q in quarters if q.get("rev") is not None and q.get("opex") is not None][-n:]
    if len(rows) < 6:
        return None
    X = [[1, q["rev"]] for q in rows]
    y = [q["opex"] for q in rows]
    (a, b), r2, se = _ols(X, y)
    be = a / (1 - b) if 0 < b < 1 and a > 0 else None
    return {"a": float(a), "b": float(b), "r2": r2, "se": se, "n": len(rows), "be": be}


def trend_model(quarters, target_label, n=16):
    rows = [q for q in quarters if q.get("rev") is not None][-n:]
    if len(rows) < 8:
        return None
    t0 = qparse(rows[0]["q"])
    def tidx(lbl):
        y, q = qparse(lbl)
        return (y - t0[0]) * 4 + (q - t0[1])
    def xrow(lbl):
        _, q = qparse(lbl)
        return [1, tidx(lbl)] + [1 if q == k else 0 for k in (2, 3, 4)]
    X = [xrow(q["q"]) for q in rows]
    y = [q["rev"] for q in rows]
    beta, r2, se = _ols(X, y)
    pred = float(np.asarray(xrow(target_label)) @ beta)
    return {"pred": pred, "r2": r2, "se": se, "n": len(rows)}


def forecast(quarters, label):
    """label(최신 분기) 다음 분기를 추정."""
    m = qmap(quarters)
    target = qnext(label)
    cur = m.get(label, {})
    notes = []
    cm = cost_model(quarters)
    tm = trend_model(quarters, target)

    # 비율법: 최근 4분기 영업비용/매출
    last4 = [q for q in quarters if q.get("rev") and q.get("opex") is not None][-4:]
    cost_ratio = sum(q["opex"] for q in last4) / sum(q["rev"] for q in last4) if len(last4) == 4 else None

    # 전년동기법: 전년 같은 분기 × (1 + 최근 분기 YoY)
    ly_t = m.get(qprev_year(target), {}).get("rev")
    yoy_now = growth(cur.get("rev"), m.get(qprev_year(label), {}).get("rev"))
    yoy_est = ly_t * (1 + yoy_now / 100) if ly_t is not None and yoy_now is not None else None

    naive = cur.get("rev")
    cands = {"regression": tm["pred"] if tm else None, "yoy": yoy_est, "naive": naive}
    vals = [v for v in cands.values() if v is not None]
    if not vals:
        return None
    base_rev = statistics.median(vals)
    # 범위: 기본안 ± 회귀 표준오차(없으면 ±15%). 세 방법 값이 그보다 좁게 모이면 그 폭을 사용
    spread = tm["se"] if tm else base_rev * 0.15
    spread = max(min(spread, base_rev * 0.4), base_rev * 0.05)
    lo, hi = base_rev - spread, base_rev + spread

    def opex_of(rev):
        if cm and cm["r2"] >= 0.5:
            return cm["a"] + cm["b"] * rev
        if cost_ratio is not None:
            return rev * cost_ratio
        return None

    base_opex = opex_of(base_rev)
    rows = [{
        "item": "매출", "base": f1(base_rev), "range": f"{f1(lo)}~{f1(hi)}",
        "regression": f1(cands["regression"]), "ratio": "—", "yoy": f1(yoy_est),
    }]
    if base_opex is not None:
        op_b = base_rev - base_opex
        op_lo, op_hi = lo - opex_of(lo), hi - opex_of(hi)
        se = cm["se"] if cm and cm["r2"] >= 0.5 else 0
        op_lo, op_hi = min(op_lo, op_hi) - se, max(op_lo, op_hi) + se
        rows.append({"item": "영업비용", "base": f1(base_opex),
                     "range": f"{f1(opex_of(lo))}~{f1(opex_of(hi))}",
                     "regression": f1(cm["a"] + cm["b"] * base_rev) if cm else "—",
                     "ratio": f1(base_rev * cost_ratio) if cost_ratio else "—", "yoy": "—"})
        rows.append({"item": "영업이익", "base": f1(op_b, sign=True),
                     "range": f"{f1(op_lo, sign=True)}~{f1(op_hi, sign=True)}",
                     "regression": f1(base_rev - (cm["a"] + cm["b"] * base_rev), sign=True) if cm else "—",
                     "ratio": f1(base_rev * (1 - cost_ratio), sign=True) if cost_ratio else "—", "yoy": "—"})

    if tm:
        weak = " — 설명력이 낮아 참고만 함" if tm["r2"] < 0.3 else ""
        notes.append(f"매출 추세 회귀({tm['n']}개 분기, 추세+분기 더미): R² {tm['r2']:.2f}, 표준오차 {tm['se']:.1f}억{weak}")
    else:
        notes.append("매출 추세 회귀: 분기 데이터가 8개 미만이라 생략")
    if cm:
        be = f", 손익분기 매출 약 {cm['be']:.0f}억" if cm["be"] else ""
        use = "" if cm["r2"] >= 0.5 else " — R²가 0.5 미만이라 영업비용은 비율법으로 계산"
        notes.append(f"비용 회귀: 영업비용 = {cm['a']:.1f} + {cm['b']:.3f}×매출 (최근 {cm['n']}개 분기, R² {cm['r2']:.2f}){be}{use}")
    if cost_ratio:
        notes.append(f"비율법: 최근 4분기 영업비용/매출 {cost_ratio*100:.1f}%")
    if yoy_est is not None:
        notes.append(f"전년동기법: {period_text(qprev_year(target))} {f1(ly_t)}억 × (1 {'+' if yoy_now>=0 else '−'} {abs(yoy_now):.1f}%, 최근 분기 YoY)")
    notes.append(f"기본안 매출 = 세 방법(회귀·전년동기·직전 분기 {f1(naive)}억)의 중앙값. 신작·수주·일회성 이벤트는 반영하지 않은 숫자이므로 이벤트가 있으면 직접 조정해야 함")
    return {"period": period_text(target), "target": target, "rows": rows, "notes": notes,
            "base_rev": round(base_rev, 1), "base_op": round(base_rev - base_opex, 1) if base_opex is not None else None,
            "cost": cm, "method": "rules"}


def check_accuracy(prev_latest: dict | None, quarters) -> dict | None:
    """직전 분석의 추정치(forecast)와 이번 실제치 비교."""
    if not prev_latest:
        return None
    fc = prev_latest.get("forecast") or {}
    period = fc.get("period")
    if not period:
        return None
    try:
        y, q = period.replace("Q", "").split()
        label = f"{int(y)}Q{int(q)}"
    except Exception:
        return None
    actual = qmap(quarters).get(label)
    if not actual:
        return None
    out = {"period": period, "items": []}
    for row in fc.get("rows", []):
        key = {"매출": "rev", "영업비용": "opex", "영업이익": "op"}.get(row.get("item"))
        if not key or actual.get(key) is None:
            continue
        base = parse_num(row.get("base"))
        a = actual[key]
        err = (a - base) / abs(base) * 100 if base not in (None, 0) else None
        lo_hi = [parse_num(x) for x in str(row.get("range", "")).replace("~", " ~ ").split("~")] if row.get("range") else []
        inside = None
        if len(lo_hi) == 2 and None not in lo_hi:
            inside = min(lo_hi) <= a <= max(lo_hi)
        out["items"].append({"item": row["item"], "base": base, "actual": a,
                             "err_pct": round(err, 1) if err is not None else None, "in_range": inside})
    if not out["items"]:
        return None
    parts = []
    for it in out["items"]:
        e = f" (오차 {pct(it['err_pct'])})" if it["err_pct"] is not None else ""
        r = "" if it["in_range"] is None else (", 범위 안" if it["in_range"] else ", 범위 밖")
        parts.append(f"{it['item']} 추정 {f1(it['base'])} → 실제 {f1(it['actual'])}{e}{r}")
    out["text"] = f"직전 분석의 {period} 추정: " + " / ".join(parts)
    return out


# ─────────────────────────────────────────────────────────────
# 위험 신호
# ─────────────────────────────────────────────────────────────
def signals(quarters, label, cm=None):
    mt = metrics(quarters, label)
    s = []
    add = lambda lvl, text: s.append({"level": lvl, "text": text})
    op, pop = mt["op"], mt["p_op"]
    if op is not None and pop is not None:
        if pop > 0 and op < 0:
            add(HIGH, f"영업 적자 전환: {mt['prev_label']} {f1(pop)}억 → {f1(op)}억")
        elif pop < 0 and op > 0:
            add(GOOD, f"영업 흑자 전환: {mt['prev_label']} {f1(pop)}억 → {f1(op)}억")
    n, pn = mt["net"], mt["p_net"]
    if n is not None and pn is not None and pn > 0 and n < 0:
        add(MID, f"순손실 전환: {f1(pn)}억 → {f1(n)}억")
    cr, crp = mt["cur_ratio"], mt["cur_ratio_prev"]
    if cr is not None:
        if cr < 100:
            add(HIGH, f"유동비율 {cr:.1f}%로 100% 미만 — 1년 내 갚을 부채가 1년 내 현금화 자산보다 많음")
        if crp is not None and crp - cr >= 20:
            add(HIGH if cr < 150 else MID, f"유동비율 급락: {crp:.1f}% → {cr:.1f}% ({crp-cr:.1f}%p 하락)")
    cl, clp = mt["cl"], mt["cl_prev"]
    if cl and clp and cl / clp >= 1.3 and cl - clp >= 30:
        add(HIGH, f"유동부채 {f1(clp)}억 → {f1(cl)}억(+{(cl/clp-1)*100:.0f}%) — 장기차입금의 유동성 대체(만기 1년 이내 진입) 가능성, 차입금 주석 확인 필요")
    if mt["stdebt"] and mt["cash"] is not None and mt["stdebt"] > max(mt["cash"] * 2, mt["cash"] + 50):
        add(HIGH, f"1년 내 만기 차입금 {f1(mt['stdebt'])}억 vs 현금 {f1(mt['cash'])}억 — 차환(만기 연장) 여부가 핵심")
    dr, drp = mt["debt_ratio"], mt["debt_ratio_prev"]
    if dr is not None:
        if dr > 200:
            add(HIGH, f"부채비율 {dr:.1f}%")
        elif drp is not None and dr - drp >= 30:
            add(MID, f"부채비율 상승: {drp:.1f}% → {dr:.1f}%")
    eq, cap = mt["eq"], mt["cap"]
    if eq is not None and eq <= 0:
        add(HIGH, f"완전 자본잠식: 자본총계 {f1(eq)}억")
    elif eq is not None and cap and eq < cap:
        add(HIGH, f"부분 자본잠식: 자본총계 {f1(eq)}억 < 자본금 {f1(cap)}억")
    c, cp = mt["cash"], mt["cash_prev"]
    if c is not None and cp and cp > 0 and c / cp <= 0.7 and cp - c >= 20:
        add(MID, f"현금 감소: {f1(cp)}억 → {f1(c)}억({pct((c/cp-1)*100)})")
    ocf, yop = mt["ocf_ytd"], mt["ytd_op"]
    if ocf is not None and yop is not None:
        if ocf < 0 and yop > 0:
            add(MID, f"이익의 질: 누적 영업이익 {f1(yop)}억인데 영업현금흐름 {f1(ocf)}억 — 매출채권·재고 증가 여부 확인")
        elif ocf < 0 and yop <= 0:
            add(MID, f"누적 영업현금흐름 {f1(ocf)}억으로 영업에서 현금이 빠져나감")
    no = mt["nonop"]
    if no is not None and op is not None and abs(no) >= max(abs(op) * 0.5, 10):
        add(MID if no < 0 else LOW,
            f"영업외 손익 {f1(no, sign=True)}억으로 세전이익({f1(mt['pretax'])}억)이 영업이익({f1(op)}억)과 크게 다름 — 환율·평가손익(비현금)인지 실현 손실인지 주석 확인")
    if mt["pretax"] is not None and mt["pretax"] < 0 and (mt["tax"] or 0) > 1:
        add(LOW, f"세전손실인데 법인세비용 {f1(mt['tax'])}억 — 원천세·이연법인세 처리 확인")
    if mt["rev_yoy"] is not None and mt["rev_yoy"] <= -20:
        add(MID, f"매출 전년 동기 대비 {pct(mt['rev_yoy'])}")
    revs = [q.get("rev") for q in quarters if q["q"] <= label][-5:]
    if len(revs) == 5 and all(r is not None for r in revs) and all(revs[i] > revs[i + 1] for i in range(4)):
        add(MID, "매출 4개 분기 연속 감소")
    if cm and cm.get("be") and mt["rev"] is not None:
        if mt["rev"] < cm["be"]:
            add(MID, f"매출 {f1(mt['rev'])}억이 비용 회귀로 구한 손익분기 매출(약 {cm['be']:.0f}억)보다 낮음")
    if mt["opm"] is not None and mt["opm_ly"] is not None and mt["opm_ly"] - mt["opm"] >= 5 and mt["opm"] > -100:
        add(LOW, f"영업이익률 하락: 전년 동기 {mt['opm_ly']:.1f}% → {mt['opm']:.1f}%")
    # 과거 4분기 결산에서 순손실이 반복되는지(손상 처리 패턴)
    q4s = [q for q in quarters if q["q"].endswith("Q4") and q.get("net") is not None and q.get("op") is not None]
    bad = [q["q"] for q in q4s if q["net"] < 0 and q["net"] < q["op"] - 20]
    if len(bad) >= 2:
        add(LOW, f"4분기 결산 때 영업이익보다 순이익이 크게 낮았던 해: {', '.join(b[:4] for b in bad)} — 연말 손상 처리 패턴 주의")
    return sort_signals(s)


def sort_signals(s):
    order = {HIGH: 0, MID: 1, LOW: 2, GOOD: 3}
    return sorted(s, key=lambda x: order.get(x["level"], 9))


# ─────────────────────────────────────────────────────────────
# 보고서 본문(대시보드 latest와 같은 구조)
# ─────────────────────────────────────────────────────────────
def build_latest(company: dict, label: str, filing: dict, notes_info: dict | None,
                 disclosures: list[dict] | None, prev_latest: dict | None) -> dict:
    quarters = company["quarters"]
    mt = metrics(quarters, label)
    fc = forecast(quarters, label)
    cm = fc["cost"] if fc else None
    # 주석을 읽어 확인한 결과: 숫자 규칙의 "주석 확인" 질문에 답하고, 주석 경고를 신호에 더함
    findings = note_rules.note_findings(notes_info, quarters, label)
    sig = sort_signals(note_rules.answer_signals(signals(quarters, label, cm), findings)
                       + note_rules.note_signals(notes_info, quarters, label, findings))
    acc = check_accuracy(prev_latest, quarters)
    y, qi = qparse(label)
    m = qmap(quarters)

    def hl(text, cond):
        return f"=={text}==" if cond else text

    big_yoy = mt["rev_yoy"] is not None and abs(mt["rev_yoy"]) >= 20
    op_neg = (mt["op"] or 0) < 0
    cr_bad = mt["cur_ratio"] is not None and (mt["cur_ratio"] < 100 or (mt["cur_ratio_prev"] or 0) - mt["cur_ratio"] >= 20)
    st_bad = bool(mt["stdebt"] and mt["cash"] is not None and mt["stdebt"] > max(mt["cash"] * 2, mt["cash"] + 50))
    ocf_bad = mt["ocf_ytd"] is not None and mt["ocf_ytd"] < 0
    nonop_big = mt["nonop"] is not None and mt["op"] is not None and abs(mt["nonop"]) >= max(abs(mt["op"]) * 0.5, 10)

    # Ⅰ 손익
    s1 = [
        f"{period_text(label)} 매출 {f1(mt['rev'])}억: " + hl(f"전년 동기 대비 {pct(mt['rev_yoy'])}", big_yoy) + f", 직전 분기 대비 {pct(mt['rev_qoq'])}",
        hl(f"영업이익 {f1(mt['op'])}억", op_neg) + f"(영업이익률 {pct(mt['opm'], sign=False)}, 전년 동기 {pct(mt['opm_ly'], sign=False)}), 순이익 {f1(mt['net'])}억",
    ]
    if qi > 1 and mt["ytd_rev"] is not None:
        s1.append(f"1~{qi*3}월 누적 매출 {f1(mt['ytd_rev'])}억({pct(mt['ytd_rev_yoy'])}), 누적 영업이익 {f1(mt['ytd_op'])}억(전년 {f1(mt['ytd_op_ly'])}억)")
    c = m.get(label, {})
    if c.get("sga") is not None and c.get("rev"):
        s1.append(f"판관비 {f1(c['sga'])}억(매출 대비 {c['sga']/c['rev']*100:.1f}%)")

    # Ⅱ 영업외·세금
    s2 = []
    if mt["nonop"] is not None:
        s2.append(hl(f"영업외 손익(세전 − 영업이익) {f1(mt['nonop'], sign=True)}억", nonop_big) + f": 영업이익 {f1(mt['op'])}억 → 세전 {f1(mt['pretax'])}억")
    if mt["tax"] is not None:
        eff = ratio(mt["tax"], mt["pretax"]) if (mt["pretax"] or 0) > 0 else None
        s2.append(f"법인세 등 {f1(mt['tax'])}억" + (f"(유효세율 {eff:.1f}%)" if eff is not None else ""))
    s2.append("환율·금융자산 평가손익이 실현 손실인지 장부상 평가인지는 주석(금융수익·비용)에서 확인")

    # Ⅲ 재무상태
    s3 = []
    if mt["cur_ratio"] is not None:
        s3.append(hl(f"유동비율 {mt['cur_ratio']:.1f}%(직전 분기 {pct(mt['cur_ratio_prev'], sign=False)})", cr_bad) + f", 부채비율 {pct(mt['debt_ratio'], sign=False)}(직전 {pct(mt['debt_ratio_prev'], sign=False)})")
    if mt["cash"] is not None:
        line = f"현금 {f1(mt['cash'])}억(직전 분기 {f1(mt['cash_prev'])}억)"
        if mt["stdebt"]:
            line += ", " + hl(f"1년 내 만기 차입금 {f1(mt['stdebt'])}억", st_bad)
        s3.append(line)
    if mt["eq"] is not None:
        s3.append(f"자본총계 {f1(mt['eq'])}억(직전 분기 {f1(mt['eq_prev'])}억)")

    # Ⅳ 현금흐름
    s4 = []
    if mt["ocf_ytd"] is not None:
        s4.append(hl(f"1~{qi*3}월 영업활동현금흐름 {f1(mt['ocf_ytd'])}억", ocf_bad) + f"(전년 동기 {f1(mt['ocf_ytd_ly'])}억) vs 누적 영업이익 {f1(mt['ytd_op'])}억")
    else:
        s4.append("현금흐름표 영업활동 항목을 찾지 못함")

    # Ⅴ 이벤트(공시 목록·주석)
    s5 = []
    for d in (disclosures or [])[:10]:
        s5.append(f"{d['rcept_dt'][:4]}.{d['rcept_dt'][4:6]}.{d['rcept_dt'][6:]} {d['report_nm'].strip()}")
    if not s5:
        s5.append("최근 90일 주요 공시 없음(정기보고서 제외)")

    # Ⅵ 분기 비교
    seq = [q for q in quarters if q["q"] <= label][-5:]
    s6 = ["매출 " + " → ".join(f"{q['q'][2:]} {f1(q.get('rev'))}" for q in seq),
          "영업이익 " + " → ".join(f"{q['q'][2:]} {f1(q.get('op'))}" for q in seq)]
    if cm:
        s6.append(f"영업비용 = {cm['a']:.1f}억 + 매출×{cm['b']:.3f} (R² {cm['r2']:.2f})" +
                  (f" → 손익분기 매출 약 {cm['be']:.0f}억" if cm["be"] else ""))
    if acc:
        s6.append(acc["text"])

    sections = [
        {"title": "Ⅰ. 손익", "items": s1},
        {"title": "Ⅱ. 영업외·세금", "items": s2},
        {"title": "Ⅲ. 재무상태", "items": s3},
        {"title": "Ⅳ. 현금흐름", "items": s4},
        {"title": "Ⅴ. 주요 공시(최근 90일)", "items": s5},
        {"title": "Ⅵ. 분기 비교", "items": s6},
    ]

    # 헤드라인: 실적 한 줄 + 가장 중요한 신호 1~2개
    head = f"{period_text(label)} 매출 {f1(mt['rev'])}억({pct(mt['rev_yoy'])})·영업이익 {f1(mt['op'])}억"
    if mt["op_yoy"] is not None:
        head += f"({pct(mt['op_yoy'])})"
    top = [x["text"].split(" — ")[0].replace("[주석] ", "주석: ") for x in sig if x["level"] in (HIGH, MID)][:2]
    head += ". " + ("; ".join(f"=={t}==" for t in top) + "." if top else "숫자상 큰 위험 신호는 없음.")

    checklist = []
    for x in sig:
        if x["level"] in (HIGH, MID) and "확인" in x["text"]:
            what = x["text"].split(" — ")[0].replace("[주석] ", "")
            checklist.append(x["text"].split(" — ")[-1] + (f"({what})" if x["text"].startswith("[주석]") else ""))
    if notes_info and notes_info.get("missing"):
        checklist.append("원문에서 찾지 못한 주석: " + ", ".join(notes_info["missing"]))
    if fc:
        checklist.append(f"{fc['period']} 실제 매출이 기본안 {f1(fc['base_rev'])}억 범위에 드는지")

    products = []
    if notes_info and notes_info.get("sales_tables"):
        products_note = "사업의 내용 「매출 및 수주상황」 표를 원문에서 옮겼습니다(아래 표). 규칙 기반 보고서는 품목별 다음 분기 추정을 하지 않습니다."
    else:
        products_note = "원문에서 품목별 매출 표를 찾지 못했습니다."

    return {
        "source": "rules",
        "analyzed_at": iso_now(),
        "period": period_text(label),
        "label": label,
        "rcept_no": filing.get("rcept_no"),
        "rcept_dt": filing.get("rcept_dt"),
        "report_name": filing.get("report_nm"),
        "headline": head,
        "signals": sig,
        "sections": sections,
        "forecast": ({k: v for k, v in fc.items() if k not in ("cost",)} | {"accuracy": acc["text"] if acc else "직전 추정 없음"}) if fc else None,
        "products": products,
        "products_note": products_note,
        "checklist": checklist,
        "note_findings": note_rules.public(findings),
        "notes_status": notes_info.get("status") if notes_info else "not_fetched",
        "notes_missing": notes_info.get("missing", []) if notes_info else [],
    }
