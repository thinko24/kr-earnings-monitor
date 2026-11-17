"""진입점.

  python scripts/run.py check                # 모든 active 종목의 새 정기보고서 확인·분석
  python scripts/run.py sync                 # watchlist.yml 반영: 새 종목 과거 데이터 채우기 + 최신 보고서 분석
  python scripts/run.py backfill --corp 회사  # 과거 약 4년치 분기 데이터 다시 채우기
  python scripts/run.py analyze  --corp 회사  # 최신 정기보고서 다시 분석(이미 처리했어도)
  python scripts/run.py refresh              # API 호출 없이 신호·대시보드 색인·보고서만 다시 만들기

--corp 에는 회사명, 종목코드, 고유번호 중 하나를 쓴다.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback
from datetime import timedelta

import yaml

from common import (DATA, RUNS, WATCHLIST, load_json, save_json, iso_now, now_kst,
                    qlabel, qsort_key, REPRT_BY_Q)
import analysis
import financials
import notes as notes_mod
import notify
import report

PERIODIC = re.compile(r"(사업|반기|분기)보고서\s*\((\d{4})\.(\d{2})\)")
BACKFILL_YEARS = 4  # 올해 포함 이전 4년(약 4~5년치 분기)
HISTORY_KEEP = 8


# ─────────────────────────────────────────────────────────────
# watchlist
# ─────────────────────────────────────────────────────────────
def load_watchlist() -> list[dict]:
    y = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8")) or {}
    items = y.get("companies") or []
    out = []
    for it in items:
        if isinstance(it, str):
            it = {"name": it}
        it = {k: (str(v).strip() if v is not None and k in ("name", "stock_code", "corp_code", "status") else v)
              for k, v in it.items()}
        it.setdefault("status", "active")
        if it.get("stock_code"):
            it["stock_code"] = it["stock_code"].zfill(6)
        if it.get("corp_code"):
            it["corp_code"] = it["corp_code"].zfill(8)
        out.append(it)
    return out


def resolve(watch: list[dict], dart) -> list[dict]:
    """corp_code가 없는 항목을 이름·종목코드로 찾는다. 결과는 data/_resolved.json에 캐시."""
    cache = load_json(DATA / "_resolved.json", {})
    need = [w for w in watch if not w.get("corp_code") and cache.get(w.get("stock_code") or w.get("name")) is None]
    if need:
        if dart is None:
            raise RuntimeError("고유번호 조회에 DART_API_KEY가 필요합니다.")
        allc = dart.corp_codes()
        listed = [c for c in allc if c["stock_code"]]
        for w in need:
            hit = None
            if w.get("stock_code"):
                hit = next((c for c in listed if c["stock_code"] == w["stock_code"]), None)
            if not hit and w.get("name"):
                ex = [c for c in listed if c["corp_name"] == w["name"]]
                if not ex:
                    ex = [c for c in listed if w["name"] in c["corp_name"]]
                if len(ex) == 1:
                    hit = ex[0]
                elif len(ex) > 1:
                    raise RuntimeError(f"'{w['name']}'에 해당하는 상장사가 여러 개입니다: "
                                       + ", ".join(f"{c['corp_name']}({c['stock_code']})" for c in ex[:8])
                                       + " — watchlist.yml에 stock_code를 적어 주세요.")
            if not hit:
                raise RuntimeError(f"'{w.get('name') or w.get('stock_code')}'를 DART 상장사 목록에서 찾지 못했습니다.")
            cache[w.get("stock_code") or w.get("name")] = {
                "corp_code": hit["corp_code"], "name": hit["corp_name"], "stock_code": hit["stock_code"]}
        save_json(DATA / "_resolved.json", cache)
    out = []
    for w in watch:
        if not w.get("corp_code"):
            c = cache[w.get("stock_code") or w.get("name")]
            w = {**w, "corp_code": c["corp_code"], "name": w.get("name") or c["name"],
                 "stock_code": w.get("stock_code") or c["stock_code"]}
        out.append(w)
    return out


def find_corp(watch, key):
    key = str(key).strip()
    for w in watch:
        if key in (w.get("name"), w.get("stock_code"), w.get("corp_code")):
            return w
    raise SystemExit(f"watchlist.yml에서 '{key}'를 찾지 못했습니다.")


# ─────────────────────────────────────────────────────────────
# 회사 데이터
# ─────────────────────────────────────────────────────────────
def cpath(corp_code):
    return DATA / f"{corp_code}.json"


def load_company(w: dict) -> dict:
    c = load_json(cpath(w["corp_code"])) or {}
    c.setdefault("corp_code", w["corp_code"])
    c["name"] = w.get("name") or c.get("name")
    c["stock_code"] = w.get("stock_code") or c.get("stock_code")
    c.setdefault("quarters", [])
    c.setdefault("processed_rcept", [])
    c.setdefault("reports", [])
    c.setdefault("history", [])
    if w.get("fs_div"):
        c["fs_div"] = w["fs_div"].upper()
    return c


def save_company(c):
    c["updated_at"] = iso_now()
    save_json(cpath(c["corp_code"]), c)


def decide_fs_div(dart, c) -> str:
    if c.get("fs_div"):
        return c["fs_div"]
    y = now_kst().year
    for year in (y, y - 1):
        for code in ("11014", "11012", "11013", "11011"):
            if dart.full_fs(c["corp_code"], year, code, "CFS"):
                return "CFS"
            if dart.full_fs(c["corp_code"], year, code, "OFS"):
                return "OFS"
    return "CFS"


def backfill(dart, c, log):
    fs = decide_fs_div(dart, c)
    c["fs_div"] = fs
    y = now_kst().year
    total = []
    for year in range(y - BACKFILL_YEARS, y + 1):
        qs, used = financials.fetch_year(dart, c["corp_code"], year, fs)
        total += qs
    c["quarters"] = financials.merge_quarters(c["quarters"], total)
    c["backfilled_at"] = iso_now()
    log(f"{c['name']}: 과거 데이터 {len(total)}개 분기 저장({fs})")


def periodic_filings(dart, corp_code, days=150) -> list[dict]:
    end = now_kst()
    bgn = end - timedelta(days=days)
    rows = dart.disclosures(corp_code, bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d"), pblntf_ty="A")
    out = []
    for r in rows:
        m = PERIODIC.search(r.get("report_nm", ""))
        if not m:
            continue
        kind, yy, mm = m.group(1), int(m.group(2)), int(m.group(3))
        qi = {3: 1, 6: 2, 9: 3, 12: 4}.get(mm)
        if qi is None:
            continue  # 12월 결산이 아닌 회사는 현재 미지원
        out.append({**r, "year": yy, "qi": qi, "label": qlabel(yy, qi)})
    out.sort(key=lambda r: (r["rcept_dt"], r["rcept_no"]))
    return out


def recent_events(dart, corp_code, days=90) -> list[dict]:
    end = now_kst()
    bgn = end - timedelta(days=days)
    try:
        rows = dart.disclosures(corp_code, bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d"), max_pages=2)
    except Exception:
        return []
    rows = [r for r in rows if not PERIODIC.search(r.get("report_nm", ""))]
    rows.sort(key=lambda r: r["rcept_dt"], reverse=True)
    return rows


def process_filing(dart, c, f, log) -> dict | None:
    label = f["label"]
    qs, used = financials.fetch_year(dart, c["corp_code"], f["year"], c.get("fs_div") or "CFS", upto_q=f["qi"])
    if not any(q["q"] == label for q in qs):
        log(f"{c['name']}: {label} 재무제표가 아직 API에 없음(공시 직후일 수 있음) → 다음 실행 때 재시도")
        return None
    c["quarters"] = financials.merge_quarters(c["quarters"], qs)

    ninfo = None
    try:
        files = dart.document(f["rcept_no"])
        if files:
            ninfo = notes_mod.extract(files, prefer_consolidated=(c.get("fs_div") != "OFS"))
    except Exception as e:
        log(f"{c['name']}: 원문 주석 추출 실패({e}) — 재무제표 숫자만으로 분석")
    events = recent_events(dart, c["corp_code"])

    prev = c.get("latest")
    new = analysis.build_latest(c, label, f, ninfo, events, prev)
    keep_claude = prev and prev.get("source") == "claude" and prev.get("period") == new["period"]
    if keep_claude:
        # 같은 분기에 Claude 해설이 이미 있으면 덮지 않고 규칙 결과만 따로 둔다
        c["latest_rules"] = new
        prev.setdefault("notice", None)
        if prev.get("rcept_no") != f["rcept_no"]:
            prev["notice"] = f"정정 공시({f['report_nm']}, {f['rcept_dt']})가 접수되었습니다. 해설은 정정 전 보고서 기준입니다."
        shown = prev
    else:
        if prev and prev.get("period") != new["period"]:
            c["history"] = ([prev] + c.get("history", []))[:HISTORY_KEEP]
        c["latest"] = new
        c.pop("latest_rules", None)
        shown = new

    rep = report.write_report(c, new, ninfo)
    c["reports"] = [r for r in c["reports"] if r.get("html") != rep["html"]] + [rep]
    if f["rcept_no"] not in c["processed_rcept"]:
        c["processed_rcept"].append(f["rcept_no"])
    log(f"{c['name']}: {new['period']} 분석 완료(주석 {ninfo['status'] if ninfo else '없음'})")
    return {"name": c["name"], "corp_code": c["corp_code"], "period": new["period"],
            "report_name": f["report_nm"], "rcept_no": f["rcept_no"], "rcept_dt": f["rcept_dt"],
            "headline": shown["headline"] if keep_claude else new["headline"],
            "signals": new["signals"], "pdf": rep.get("pdf"), "html": rep.get("html")}


def refresh_company(c):
    """API 없이: 최신 분기 기준 신호·지표 재계산."""
    if not c.get("quarters"):
        return
    last = sorted(c["quarters"], key=lambda q: qsort_key(q["q"]))[-1]["q"]
    fc = analysis.forecast(c["quarters"], last)
    c["signals"] = analysis.signals(c["quarters"], last, fc["cost"] if fc else None)
    mt = analysis.metrics(c["quarters"], last)
    c["metrics"] = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in mt.items()}
    c["last_quarter"] = last


def build_index(watch):
    rows = []
    for w in watch:
        c = load_json(cpath(w["corp_code"]))
        if not c:
            rows.append({"corp_code": w["corp_code"], "name": w.get("name"), "stock_code": w.get("stock_code"),
                         "status": w.get("status", "active"), "empty": True})
            continue
        lt = c.get("latest") or {}
        mt = c.get("metrics") or {}
        sig = c.get("signals") or []
        rows.append({
            "corp_code": c["corp_code"], "name": c["name"], "stock_code": c.get("stock_code"),
            "status": w.get("status", "active"), "fs_div": c.get("fs_div"),
            "last_quarter": c.get("last_quarter"), "period": lt.get("period"),
            "source": lt.get("source"), "headline": lt.get("headline"),
            "rev": mt.get("rev"), "op": mt.get("op"), "rev_yoy": mt.get("rev_yoy"),
            "high": sum(1 for s in sig if s["level"] == "high"),
            "mid": sum(1 for s in sig if s["level"] == "mid"),
            "updated_at": c.get("updated_at"),
        })
    save_json(DATA / "index.json", {"updated_at": iso_now(), "companies": rows})


# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["check", "sync", "backfill", "analyze", "refresh"])
    ap.add_argument("--corp", default="")
    a = ap.parse_args()

    logs, errors, items = [], [], []
    log = lambda s: (print(s), logs.append(s))

    watch = load_watchlist()
    dart = None
    if a.mode != "refresh":
        from dart import Dart
        dart = Dart()
        watch = resolve(watch, dart)
    else:
        cache = load_json(DATA / "_resolved.json", {})
        watch = [w if w.get("corp_code") else {**w, **cache.get(w.get("stock_code") or w.get("name"), {})} for w in watch]
        watch = [w for w in watch if w.get("corp_code")]

    targets = [find_corp(watch, a.corp)] if a.corp.strip() else [w for w in watch if w.get("status") == "active"]

    for w in targets:
        try:
            c = load_company(w)
            if a.mode == "refresh":
                refresh_company(c)
                if c.get("latest"):
                    rep = report.write_report(c, c["latest"])
                    c["reports"] = [r for r in c["reports"] if r.get("html") != rep["html"]] + [rep]
                save_company(c)
                continue

            new_company = not c["quarters"]
            if a.mode == "backfill" or (a.mode == "sync" and (new_company or w.get("backfill"))):
                backfill(dart, c, log)

            if a.mode == "sync" and not (new_company or w.get("backfill")):
                pass  # 기존 종목은 sync에서 건드리지 않음
            elif a.mode in ("check", "sync", "analyze"):
                filings = periodic_filings(dart, c["corp_code"], days=150 if a.mode == "check" else 400)
                if a.mode == "check":
                    todo = [f for f in filings if f["rcept_no"] not in c["processed_rcept"]]
                else:
                    todo = filings[-1:]  # 최신 정기보고서 1건
                for f in todo:
                    it = process_filing(dart, c, f, log)
                    if it:
                        items.append(it)
            refresh_company(c)
            save_company(c)
        except Exception as e:
            traceback.print_exc()
            errors.append(f"{w.get('name')}: {e}")

    build_index(watch)
    calls = dart.calls if dart else 0
    save_json(RUNS / "latest.json", {"at": iso_now(), "mode": a.mode, "processed": len(items),
                                     "errors": errors, "log": logs, "api_calls": calls})
    title, md = notify.build_markdown(items, errors)
    save_json(RUNS / "notify.json", {"title": title, "markdown": md, "items": items, "errors": errors})
    (RUNS / "issue.md").write_text(md, encoding="utf-8")
    (RUNS / "issue_title.txt").write_text(title, encoding="utf-8")
    print(f"완료: 분석 {len(items)}건, 오류 {len(errors)}건, API 호출 {calls}회")
    # 알릴 것이 없으면 notify.json의 items가 비어 있음 → 워크플로가 알림 생략
    return 0


if __name__ == "__main__":
    sys.exit(main())
