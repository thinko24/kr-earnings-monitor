"""fnlttSinglAcntAll 응답 → 분기 단독 실적.

분기 단독 복원 규칙
- 1~3분기: 손익계산서의 3개월 금액(thstrm_amount) 사용.
  3개월 금액이 누적과 같게 나오는 회사는 누적 차이로 계산.
- 4분기  : 사업보고서 연간 − 3분기 누적(thstrm_add_amount)
- 현금흐름표는 누적만 공시 → 누적 차이로 분기 단독 계산
- 재무상태표는 보고기간 말 잔액 그대로
"""
from __future__ import annotations

import re

from common import REPRT_BY_Q, to_eok, qlabel

IS_DIVS = ("IS", "CIS")

# key: (account_id 후보, 계정명 정규식 후보(공백 제거 후 비교))
IS_ACC = {
    "rev": (["ifrs-full_Revenue", "ifrs_Revenue"],
            [r"^매출액$", r"^영업수익$", r"^수익\(매출액\)$", r"^매출$", r"^매출액\(.*\)$", r"^[ⅠI]\.?매출액", r"^영업수익\(매출액\)$"]),
    "op": (["dart_OperatingIncomeLoss"],
           [r"^영업이익(\(손실\))?$", r"^영업손실(\(이익\))?$", r"^영업이익\(손실\)$", r"^[ⅤV]\.?영업이익"]),
    "pretax": (["ifrs-full_ProfitLossBeforeTax", "ifrs_ProfitLossBeforeTax"],
               [r"^법인세비용차감전", r"^법인세차감전", r"^법인세비용\(수익\)차감전"]),
    "net": (["ifrs-full_ProfitLoss", "ifrs_ProfitLoss"],
            [r"^(당기|분기|반기)순이익(\(손실\))?$", r"^(당기|분기|반기)순손익$", r"^(당기|분기|반기)순손실(\(이익\))?$"]),
    "sga": (["dart_TotalSellingGeneralAdministrativeExpenses"], [r"^판매비와관리비$", r"^판매비및관리비$"]),
}

BS_ACC = {
    "ca": (["ifrs-full_CurrentAssets", "ifrs_CurrentAssets"], [r"^유동자산$"]),
    "cl": (["ifrs-full_CurrentLiabilities", "ifrs_CurrentLiabilities"], [r"^유동부채$"]),
    "tl": (["ifrs-full_Liabilities", "ifrs_Liabilities"], [r"^부채총계$"]),
    "eq": (["ifrs-full_Equity", "ifrs_Equity"], [r"^자본총계$"]),
    "cash": (["ifrs-full_CashAndCashEquivalents", "ifrs_CashAndCashEquivalents"], [r"^현금및현금성자산$"]),
    "cap": (["ifrs-full_IssuedCapital", "ifrs_IssuedCapital"], [r"^자본금$"]),
}

# 1년 내 갚아야 할 차입성 부채(합산)
STDEBT_IDS = {
    "ifrs-full_ShorttermBorrowings", "ifrs-full_CurrentPortionOfLongtermBorrowings",
    "dart_ShortTermBorrowings", "dart_CurrentPortionOfLongTermBorrowings",
    "dart_CurrentPortionOfBonds", "ifrs-full_CurrentBondsIssued",
    "ifrs-full_CurrentPortionOfBondsIssued", "dart_ShortTermBondsIssued",
    "ifrs-full_CurrentBorrowings",
}
STDEBT_NAMES = [r"^단기차입금$", r"^유동성장기차입금$", r"^유동성장기부채$", r"^유동성사채$",
                r"^단기사채$", r"^유동성전환사채$", r"^전환사채\(유동\)$", r"^유동차입금$"]

CF_OCF = (["ifrs-full_CashFlowsFromUsedInOperatingActivities", "ifrs_CashFlowsFromUsedInOperatingActivities"],
          [r"^영업활동(으로인한|으로부터의|)현금흐름$", r"^영업활동현금흐름$"])


def amt(s) -> float | None:
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def _main_rows(rows, divs):
    return [r for r in rows if r.get("sj_div") in divs
            and (r.get("account_detail") in (None, "", "-"))]


def pick(rows, divs, ids, pats):
    """divs 순서대로(예: IS 먼저, 없으면 CIS) 계정을 찾는다."""
    for d in divs:
        cands = _main_rows(rows, (d,))
        for i in ids:
            for r in cands:
                if r.get("account_id") == i:
                    return r
        for p in pats:
            for r in cands:
                if re.search(p, _norm(r.get("account_nm"))):
                    return r
    return None


def parse_report(rows: list[dict], reprt_code: str) -> dict:
    """한 보고서 → {'q': 3개월, 'cum': 누적, 'bs': 잔액, 'cf': 누적, 'rcept_no'}."""
    out = {"q": {}, "cum": {}, "bs": {}, "cf": {}, "rcept_no": rows[0].get("rcept_no") if rows else None}
    for k, (ids, pats) in IS_ACC.items():
        r = pick(rows, IS_DIVS, ids, pats)
        if not r:
            out["q"][k] = out["cum"][k] = None
            continue
        q = amt(r.get("thstrm_amount"))
        add = amt(r.get("thstrm_add_amount"))
        if reprt_code == "11011":
            out["q"][k], out["cum"][k] = None, q
        elif reprt_code == "11013":
            out["q"][k], out["cum"][k] = q, add if add is not None else q
        else:
            out["q"][k], out["cum"][k] = q, add
    for k, (ids, pats) in BS_ACC.items():
        r = pick(rows, ("BS",), ids, pats)
        out["bs"][k] = amt(r.get("thstrm_amount")) if r else None
    st = 0.0
    found = False
    for r in _main_rows(rows, ("BS",)):
        if r.get("account_id") in STDEBT_IDS or any(re.search(p, _norm(r.get("account_nm"))) for p in STDEBT_NAMES):
            v = amt(r.get("thstrm_amount"))
            if v is not None:
                st += v
                found = True
    out["bs"]["stdebt"] = st if found else None
    r = pick(rows, ("CF",), *CF_OCF)
    out["cf"]["ocf"] = amt(r.get("thstrm_amount")) if r else None
    return out


def build_year(year: int, reps: dict[str, dict]) -> list[dict]:
    """한 해의 보고서들(reprt_code → parse_report 결과) → 분기 단독 레코드 목록."""
    quarters = []
    standalone: dict[int, dict] = {}
    for qi in (1, 2, 3, 4):
        rep = reps.get(REPRT_BY_Q[qi])
        if not rep:
            continue
        prev = reps.get(REPRT_BY_Q[qi - 1]) if qi > 1 else None
        vals = {}
        for k in IS_ACC:
            q3m = rep["q"].get(k)
            cum = rep["cum"].get(k)
            prev_cum = prev["cum"].get(k) if prev else None
            if prev_cum is None and qi > 1:
                # 직전 누적이 없으면 앞 분기 단독 합으로 대신
                parts = [standalone.get(i, {}).get(k) for i in range(1, qi)]
                prev_cum = sum(parts) if parts and all(p is not None for p in parts) else None
            v = None
            if qi == 1:
                v = q3m if q3m is not None else cum
            elif qi in (2, 3):
                if q3m is not None and not (cum is not None and prev_cum and abs(q3m - cum) < 1 and abs(prev_cum) > 1):
                    v = q3m
                elif cum is not None and prev_cum is not None:
                    v = cum - prev_cum
            else:  # 4분기 = 연간 − 3분기 누적
                if cum is not None and prev_cum is not None:
                    v = cum - prev_cum
            vals[k] = v
        standalone[qi] = vals
        # 현금흐름(누적)
        ocf_cum = rep["cf"].get("ocf")
        prev_ocf = prev["cf"].get("ocf") if prev else None
        if qi == 1:
            ocf_q = ocf_cum
        else:
            ocf_q = ocf_cum - prev_ocf if (ocf_cum is not None and prev_ocf is not None) else None

        rec = {"q": qlabel(year, qi)}
        for k in ("rev", "op", "pretax", "net"):
            rec[k] = to_eok(vals.get(k))
        rec["opex"] = round(rec["rev"] - rec["op"], 1) if rec["rev"] is not None and rec["op"] is not None else None
        if vals.get("sga") is not None:
            rec["sga"] = to_eok(vals["sga"])
        for k in ("ca", "cl", "tl", "eq", "cash", "cap", "stdebt"):
            v = to_eok(rep["bs"].get(k))
            if v is not None:
                rec[k] = v
        if ocf_q is not None:
            rec["ocf"] = to_eok(ocf_q)
        if ocf_cum is not None:
            rec["ocf_ytd"] = to_eok(ocf_cum)
        if qi == 4:
            rec["note"] = "연간−3Q누적"
        rec["src"] = rep.get("rcept_no")
        quarters.append(rec)
    return quarters


def merge_quarters(old: list[dict], new: list[dict]) -> list[dict]:
    """새 값으로 덮되, 기존에만 있는 필드(사람·Claude가 쓴 note 등)는 유지."""
    by = {q["q"]: dict(q) for q in old or []}
    for n in new:
        o = by.get(n["q"], {})
        merged = {**o, **{k: v for k, v in n.items() if v is not None}}
        # 사람이 쓴 note가 있으면 자동 note보다 우선
        if o.get("note") and o.get("note") != n.get("note"):
            merged["note"] = o["note"]
        by[n["q"]] = merged
    from common import qsort_key
    return [by[k] for k in sorted(by, key=qsort_key)]


def fetch_year(dart, corp_code: str, year: int, fs_div: str, upto_q: int = 4) -> tuple[list[dict], str]:
    """한 해 보고서를 받아 분기 레코드 반환. 지정 fs_div에 데이터가 없으면 다른 쪽으로 대체."""
    reps = {}
    used = fs_div
    for qi in range(1, upto_q + 1):
        code = REPRT_BY_Q[qi]
        rows = dart.full_fs(corp_code, year, code, fs_div)
        if rows is None:
            alt = "OFS" if fs_div == "CFS" else "CFS"
            rows = dart.full_fs(corp_code, year, code, alt)
            if rows is not None:
                used = alt
        if rows:
            reps[code] = parse_report(rows, code)
    return build_year(year, reps), used
