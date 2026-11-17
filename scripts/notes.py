"""공시 원문(document.xml zip)에서 주석과 「매출 및 수주상황」 표를 뽑는다.

DART 원문 XML 구조(요약)
  <SECTION-1><TITLE ...>III. 재무에 관한 사항</TITLE>
    <SECTION-2><TITLE>3. 연결재무제표 주석</TITLE>
      <P>1. 일반사항</P> ... <TABLE>...<TR><TD>..</TD></TR></TABLE> ...
주석 번호는 TITLE이 아니라 P 문단("17. 차입금")으로 나오는 경우가 많아 문단 패턴으로 나눈다.
XML이 깨진 경우가 있어 lxml recover 모드로 읽는다.
"""
from __future__ import annotations

import html
import re

from lxml import etree

# 찾을 주석 범주: (표시 이름, 주석 제목 정규식)
TARGETS = [
    ("차입금·사채", r"차입금|사채"),
    ("특수관계자", r"특수관계"),
    ("우발부채·약정·담보", r"우발|약정|담보|지급보증"),
    ("영업부문·매출 구성", r"영업부문|부문정보|부문별|고객과의\s*계약|수익의\s*구분|매출액"),
    ("재고자산", r"재고자산"),
    ("충당부채", r"충당부채"),
    ("금융위험(환율·이자율)", r"위험\s*관리|금융위험|재무위험"),
    ("법인세", r"법인세"),
    ("보고기간 후 사건", r"보고기간\s*후|후속\s*사건"),
]

NOTE_HEAD = re.compile(r"^\s*(\d{1,2})\s*[.\)]\s*([^\d\s][^\n]{0,40})$")
MAX_TABLES_PER_NOTE = 3
MAX_ROWS = 40


def _decode(b: bytes) -> bytes:
    head = b[:200].decode("ascii", "ignore").lower()
    if "euc-kr" in head or "ks_c_5601" in head:
        txt = b.decode("cp949", "replace")
        txt = re.sub(r'encoding="[^"]+"', 'encoding="utf-8"', txt, count=1)
        return txt.encode("utf-8")
    return b


def _parse(b: bytes):
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    try:
        return etree.fromstring(_decode(b), parser)
    except Exception:
        return None


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _table_rows(tbl) -> list[list[str]]:
    rows = []
    for tr in tbl.iter("TR", "tr"):
        cells = [_text(c) for c in tr if isinstance(c.tag, str) and c.tag.upper() in ("TD", "TH", "TE", "TU")]
        if any(cells):
            rows.append(cells)
        if len(rows) >= MAX_ROWS:
            break
    return rows


def _blocks(section):
    """섹션 안을 문서 순서대로 (kind, value) 목록으로."""
    out = []
    for el in section.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.upper()
        if tag == "TABLE":
            # 표 안의 P는 건너뛰도록 표 자체만 기록
            out.append(("table", _table_rows(el)))
        elif tag == "P":
            if any((a.tag.upper() if isinstance(a.tag, str) else "") == "TABLE" for a in el.iterancestors()):
                continue
            t = _text(el)
            if t:
                out.append(("p", t))
    return out


def _find_section(root, pattern):
    for t in root.iter("TITLE"):
        if re.search(pattern, _text(t)):
            return t.getparent(), _text(t)
    return None, None


def extract(files: dict[str, bytes], prefer_consolidated: bool = True) -> dict:
    """반환: {status, notes:[{category,title,text,tables}], sales_tables, missing}"""
    roots = [r for r in (_parse(b) for n, b in sorted(files.items()) if n.lower().endswith(".xml")) if r is not None]
    if not roots:
        return {"status": "parse_failed", "notes": [], "sales_tables": [], "missing": [t[0] for t in TARGETS]}
    main = roots[0]

    # 주석 섹션: 연결 우선
    pats = [r"연결\s*재무제표\s*주석", r"^\s*\d*\.?\s*재무제표\s*주석"] if prefer_consolidated else \
           [r"^\s*\d*\.?\s*재무제표\s*주석", r"연결\s*재무제표\s*주석"]
    sec, sec_title = None, None
    for r in roots:
        for p in pats:
            sec, sec_title = _find_section(r, p)
            if sec is not None:
                break
        if sec is not None:
            break

    notes = []
    if sec is not None:
        cur = None
        chunks = []
        for kind, val in _blocks(sec):
            if kind == "p":
                m = NOTE_HEAD.match(val)
                if m and len(val) <= 45:
                    cur = {"no": int(m.group(1)), "title": m.group(2).strip(), "text": [], "tables": []}
                    chunks.append(cur)
                    continue
            if cur is None:
                continue
            if kind == "p" and sum(len(x) for x in cur["text"]) < 1200:
                cur["text"].append(val)
            elif kind == "table" and len(cur["tables"]) < MAX_TABLES_PER_NOTE and val:
                cur["tables"].append(val)
        used = set()
        for cat, pat in TARGETS:
            for ch in chunks:
                if id(ch) in used:
                    continue
                if re.search(pat, ch["title"]):
                    notes.append({"category": cat, "title": f"{ch['no']}. {ch['title']}",
                                  "text": " ".join(ch["text"])[:1200], "tables": ch["tables"]})
                    used.add(id(ch))
                    if cat != "차입금·사채":
                        break

    # 사업의 내용 → 매출 및 수주상황
    sales_tables = []
    for r in roots:
        s, _ = _find_section(r, r"매출\s*(및|과)\s*수주")
        if s is None:
            s, _ = _find_section(r, r"^\s*\d*\.?\s*매출\s*$|주요\s*제품.*매출")
        if s is not None:
            for kind, val in _blocks(s):
                if kind == "table" and val and len(sales_tables) < 4:
                    sales_tables.append(val)
            break

    found = {n["category"] for n in notes}
    missing = [c for c, _ in TARGETS if c not in found]
    status = "extracted" if notes else ("no_notes_section" if sec is None else "no_matching_notes")
    return {"status": status, "section": sec_title, "notes": notes,
            "sales_tables": sales_tables, "missing": missing}


def table_html(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    out = ['<table class="note">']
    for i, r in enumerate(rows):
        tag = "th" if i == 0 else "td"
        cells = "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in r + [""] * (w - len(r)))
        out.append(f"<tr>{cells}</tr>")
    out.append("</table>")
    return "".join(out)
