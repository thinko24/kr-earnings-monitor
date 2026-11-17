"""상세 보고서: HTML 생성 → headless Chrome으로 PDF."""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
from pathlib import Path

from common import REPORTS, f1, period_text, MINUS
from notes import table_html

LEVEL_KO = {"high": "위험", "mid": "주의", "low": "참고", "good": "개선"}

CSS = """
@page { size: A4; margin: 14mm 12mm; }
* { box-sizing: border-box; }
body { font-family: 'Noto Sans CJK KR','Noto Sans KR','Apple SD Gothic Neo','Malgun Gothic',sans-serif;
       color:#1b1f24; font-size: 10.5pt; line-height:1.55; margin:0; }
h1 { font-size: 18pt; margin: 0 0 2px; }
h2 { font-size: 12.5pt; margin: 18px 0 6px; padding-bottom:3px; border-bottom: 2px solid #1f4fd8; color:#123; }
h3 { font-size: 11pt; margin: 12px 0 4px; }
.meta { color:#5b6570; font-size:9pt; }
.badge { display:inline-block; font-size:8.5pt; padding:1px 7px; border-radius:9px; background:#eef2ff; color:#1f4fd8; margin-left:6px; }
.headline { background:#f4f7ff; border-left:4px solid #1f4fd8; padding:9px 12px; margin:10px 0; font-size:11pt; }
mark { background:#fff3b0; padding:0 2px; }
ul { margin: 4px 0 4px 18px; padding:0; } li { margin: 2px 0; }
table { border-collapse: collapse; width:100%; font-size:9pt; margin:6px 0; }
th, td { border:1px solid #d5dbe3; padding:3px 5px; text-align:right; }
th { background:#f1f4f8; text-align:center; }
td:first-child, th:first-child { text-align:left; }
table.note td, table.note th { text-align:left; font-size:8.3pt; }
.sig { padding:4px 8px; margin:3px 0; border-radius:5px; font-size:9.5pt; }
.sig.high { background:#fde8e8; } .sig.mid { background:#fff4e0; } .sig.low { background:#f1f4f8; } .sig.good { background:#e6f6ec; }
.sig b { margin-right:6px; }
.neg { color:#c62828; }
.small { font-size:8.5pt; color:#5b6570; }
.chart { width:100%; height:auto; }
.page-break { page-break-before: always; }
"""


def esc(s) -> str:
    t = html.escape(str(s)) if s is not None else ""
    return re.sub(r"==(.+?)==", r"<mark>\1</mark>", t)


def num_td(v, sign=False):
    cls = ' class="neg"' if isinstance(v, (int, float)) and v < 0 else ""
    return f"<td{cls}>{f1(v, sign)}</td>"


def chart_svg(quarters, n=12) -> str:
    qs = [q for q in quarters if q.get("rev") is not None][-n:]
    if len(qs) < 2:
        return ""
    W, H, pad_l, pad_b, pad_t = 720, 220, 46, 28, 14
    vals = [q["rev"] for q in qs] + [q.get("op") or 0 for q in qs]
    vmax = max(vals) * 1.1
    vmin = min(0, min(vals) * 1.2)
    def y(v):
        return pad_t + (vmax - v) / (vmax - vmin) * (H - pad_t - pad_b)
    bw = (W - pad_l - 10) / len(qs)
    parts = [f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-size="10">']
    for t in range(5):
        v = vmin + (vmax - vmin) * t / 4
        parts.append(f'<line x1="{pad_l}" x2="{W-5}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e6e9ee"/>'
                     f'<text x="{pad_l-4}" y="{y(v)+3:.1f}" text-anchor="end" fill="#6b7480">{v:,.0f}</text>')
    parts.append(f'<line x1="{pad_l}" x2="{W-5}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#9aa3ad"/>')
    pts = []
    for i, q in enumerate(qs):
        x = pad_l + i * bw + bw * 0.18
        parts.append(f'<rect x="{x:.1f}" y="{y(q["rev"]):.1f}" width="{bw*0.64:.1f}" height="{y(0)-y(q["rev"]):.1f}" fill="#9db4f0"/>')
        parts.append(f'<text x="{x+bw*0.32:.1f}" y="{H-12}" text-anchor="middle" fill="#4a535e">{q["q"][2:4]}.{q["q"][-1]}Q</text>')
        if q.get("op") is not None:
            pts.append((x + bw * 0.32, y(q["op"]), q["op"]))
    if pts:
        parts.append('<polyline fill="none" stroke="#c62828" stroke-width="2" points="' +
                     " ".join(f"{a:.1f},{b:.1f}" for a, b, _ in pts) + '"/>')
        for a, b, v in pts:
            parts.append(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="2.6" fill="#c62828"/>')
    parts.append(f'<rect x="{W-190}" y="2" width="10" height="10" fill="#9db4f0"/><text x="{W-176}" y="11">매출</text>'
                 f'<line x1="{W-130}" x2="{W-116}" y1="7" y2="7" stroke="#c62828" stroke-width="2"/><text x="{W-112}" y="11">영업이익 (억 원)</text>')
    parts.append("</svg>")
    return "".join(parts)


def quarters_table(quarters, n=12) -> str:
    qs = quarters[-n:]
    cols = [("매출", "rev"), ("영업비용", "opex"), ("영업이익", "op"), ("세전이익", "pretax"), ("순이익", "net"),
            ("유동자산", "ca"), ("유동부채", "cl"), ("부채총계", "tl"), ("자본총계", "eq"), ("현금", "cash")]
    head = "<tr><th>항목(억 원)</th>" + "".join(f"<th>{q['q'][2:]}</th>" for q in qs) + "</tr>"
    body = []
    for name, k in cols:
        body.append(f"<tr><td>{name}</td>" + "".join(num_td(q.get(k)) for q in qs) + "</tr>")
    cr = "<tr><td>유동비율</td>" + "".join(
        f"<td>{q['ca']/q['cl']*100:.0f}%</td>" if q.get("ca") and q.get("cl") else "<td>—</td>" for q in qs) + "</tr>"
    body.append(cr)
    notes = [f"{q['q']}: {q['note']}" for q in qs if q.get("note") and q["note"] != "연간−3Q누적"]
    foot = f'<p class="small">4분기 = 사업보고서 연간 − 3분기 누적. {esc(" / ".join(notes))}</p>'
    return f"<table>{head}{''.join(body)}</table>{foot}"


def render_html(company: dict, latest: dict, notes_info: dict | None = None) -> str:
    name = company["name"]
    src = latest.get("source", "rules")
    badge = "규칙 기반 자동 보고서" if src == "rules" else "Claude 해설 포함"
    out = [f"<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'><title>{esc(name)} {esc(latest.get('period'))} 실적 분석</title>"
           f"<style>{CSS}</style></head><body>"]
    out.append(f"<h1>{esc(name)} <span class='meta'>{esc(company.get('stock_code',''))}</span><span class='badge'>{badge}</span></h1>")
    rc = latest.get("rcept_no")
    link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rc}" if rc else ""
    out.append(f"<div class='meta'>{esc(latest.get('report_name',''))} · 접수 {esc(latest.get('rcept_dt',''))} · "
               f"{'연결' if company.get('fs_div')=='CFS' else '별도'} 기준 · 분석 {esc(latest.get('analyzed_at','')[:16].replace('T',' '))}"
               + (f" · <a href='{link}'>DART 원문</a>" if link else "") + "</div>")
    out.append(f"<div class='headline'>{esc(latest.get('headline'))}</div>")

    sig = latest.get("signals") or company.get("signals") or []
    if sig:
        out.append("<h2>위험 신호 (규칙 점검)</h2>")
        for s in sig:
            out.append(f"<div class='sig {s['level']}'><b>{LEVEL_KO.get(s['level'], '')}</b>{esc(s['text'])}</div>")

    out.append("<h2>분기 추이</h2>" + chart_svg(company["quarters"]))

    for sec in latest.get("sections", []):
        out.append(f"<h2>{esc(sec['title'])}</h2><ul>" + "".join(f"<li>{esc(i)}</li>" for i in sec.get("items", [])) + "</ul>")

    fc = latest.get("forecast")
    if fc:
        out.append(f"<h2>Ⅶ. 다음 분기 추정 ({esc(fc.get('period'))})</h2>")
        out.append("<table><tr><th>항목(억 원)</th><th>기본안</th><th>범위</th><th>회귀</th><th>비율법</th><th>전년동기법</th></tr>")
        for r in fc.get("rows", []):
            out.append(f"<tr><td>{esc(r.get('item'))}</td><td><b>{esc(r.get('base'))}</b></td><td>{esc(r.get('range'))}</td>"
                       f"<td>{esc(r.get('regression'))}</td><td>{esc(r.get('ratio'))}</td><td>{esc(r.get('yoy'))}</td></tr>")
        out.append("</table><ul>" + "".join(f"<li>{esc(n)}</li>" for n in fc.get("notes", [])) + "</ul>")
        if fc.get("accuracy"):
            out.append(f"<p class='small'>추정 점검: {esc(fc['accuracy'])}</p>")

    prods = latest.get("products") or []
    out.append("<h2>Ⅷ. 품목별 매출</h2>")
    if prods:
        out.append("<table><tr><th>품목</th><th>출시·구분</th><th>누적</th><th>분기</th><th>신뢰도</th></tr>")
        for p in prods:
            out.append(f"<tr><td>{esc(p.get('name'))}</td><td>{esc(p.get('launch'))}</td><td>{esc(p.get('cum'))}</td>"
                       f"<td>{esc(p.get('q'))}</td><td>{esc(p.get('confidence'))}</td></tr>")
        out.append("</table>")
    if latest.get("products_note"):
        out.append(f"<p class='small'>{esc(latest['products_note'])}</p>")
    if notes_info and notes_info.get("sales_tables"):
        out.append("<h3>원문: 매출 및 수주상황</h3>")
        for t in notes_info["sales_tables"]:
            out.append(table_html(t))

    if latest.get("checklist"):
        out.append("<h2>다음에 확인할 것</h2><ul>" + "".join(f"<li>{esc(c)}</li>" for c in latest["checklist"]) + "</ul>")

    out.append("<h2 class='page-break'>분기 데이터</h2>" + quarters_table(company["quarters"]))

    if notes_info and notes_info.get("notes"):
        out.append(f"<h2>주석 발췌 <span class='meta'>({esc(notes_info.get('section') or '')})</span></h2>")
        nf = latest.get("notes_file")
        total = (notes_info.get("diag") or {}).get("note_count")
        if total:
            out.append(f"<p class='small'>원문 주석 {total}개 중 아래 {len(notes_info['notes'])}개를 발췌했습니다."
                       + (f" 본문·표 전체는 <code>{esc(nf)}</code>에 저장되어 있습니다." if nf else "") + "</p>")
        for n in notes_info["notes"]:
            out.append(f"<h3>[{esc(n['category'])}] {esc(n['title'])}</h3>")
            # 문서 순서대로(하위 항목 제목 바로 아래 그 표). 글자 2,000자·표 8개까지만 보여 줌
            blocks = n.get("blocks") or ([{"p": n["text"]}] if n.get("text") else []) + [{"table": t} for t in n.get("tables", [])]
            chars, tables, cut = 0, 0, False
            for b in blocks:
                if "p" in b:
                    if chars >= 2000:
                        cut = True
                        continue
                    t = b["p"][:2000 - chars]
                    chars += len(b["p"])
                    out.append(f"<p class='small'>{esc(t)}{'…' if chars > 2000 else ''}</p>")
                elif tables < 8:
                    out.append(table_html(b["table"], max_rows=30))
                    tables += 1
                else:
                    cut = True
            if cut:
                out.append("<p class='small'>… 일부 생략(전체는 data/notes 파일)</p>")
        if notes_info.get("missing"):
            out.append(f"<p class='small'>찾지 못한 핵심 주석: {esc(', '.join(notes_info['missing']))}</p>")
    elif latest.get("notes_status") in ("needs_pdf", "not_fetched"):
        out.append("<p class='small'>주석은 이 보고서에 포함되지 않았습니다.</p>")

    out.append("<p class='small' style='margin-top:18px'>이 보고서는 공시 숫자를 규칙에 따라 정리한 참고 자료이며 투자 권유가 아닙니다. "
               "추정치는 과거 분기 숫자만으로 계산했고 신작·수주·일회성 이벤트를 반영하지 않습니다.</p>")
    out.append("</body></html>")
    return "".join(out)


def find_chrome() -> str | None:
    for c in (os.environ.get("CHROME_BIN"), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if c and shutil.which(c):
            return shutil.which(c)
    return None


def to_pdf(html_path: Path, pdf_path: Path) -> bool:
    chrome = find_chrome()
    if not chrome:
        print("Chrome 없음: PDF 생략")
        return False
    cmd = [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return pdf_path.exists()
    except Exception as e:
        print("PDF 변환 실패:", e)
        return False


def write_report(company: dict, latest: dict, notes_info: dict | None = None) -> dict:
    label = latest.get("label") or latest["period"].replace(" ", "").replace("Q", "")
    slug = f"{company['corp_code']}_{latest['period'].replace(' ', '_')}"
    if latest.get("source", "rules") == "rules":
        slug += "_rules"
    REPORTS.mkdir(parents=True, exist_ok=True)
    h = REPORTS / f"{slug}.html"
    h.write_text(render_html(company, latest, notes_info), encoding="utf-8")
    p = REPORTS / f"{slug}.pdf"
    ok = to_pdf(h, p)
    return {"period": latest["period"], "rcept_no": latest.get("rcept_no"),
            "analyzed_at": latest.get("analyzed_at"), "source": latest.get("source", "rules"),
            "notes_included": bool(notes_info and notes_info.get("notes")),
            "html": f"reports/{h.name}", "pdf": f"reports/{p.name}" if ok else None}
