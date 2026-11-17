"""공시 원문(document.xml zip)에서 주석과 「매출 및 수주상황」 표를 뽑는다.

DART 원문 XML 구조(요약)
  <SECTION-1><TITLE ...>III. 재무에 관한 사항</TITLE>
    <SECTION-2><TITLE>3. 연결재무제표 주석</TITLE>
      <P>1. 일반사항</P> ... <TABLE>...<TR><TD>..</TD></TR></TABLE> ...
      <P>17. 차입금</P> <P>1) 단기차입금</P> <TABLE>..</TABLE> <P>2) 장기차입금</P> ...

주석 제목 판별
- 주석 번호는 TITLE이 아니라 P 문단("17. 차입금")으로 나오는 경우가 많고, 회사에 따라
  SECTION-3의 TITLE이나 칸 하나짜리 표로 나오기도 한다. 세 경우 모두 제목 후보로 본다.
- "1) 단기차입금", "(1) …", "2.1 작성기준" 같은 하위 항목은 제목이 아니다.
  제목은 "숫자 + 마침표" 형식이어야 하고, 번호가 직전 주석보다 1~3 커야 한다
  (본문 속 "1. 회사의 개요" 같은 번호 문단이 새 주석으로 잘리는 것을 막음).
- "9-1. 차입금", "9-2. 차입금"처럼 한 주석이 나뉘어 있으면 같은 9번 주석으로 합친다.
- 주석 범위는 제목(TITLE)의 부모 요소가 아니라 "그 제목부터 같은 수준 이상의 다음 제목 전까지"를
  문서 순서대로 읽는다. 원문에 따라 주석 문단이 SECTION 밖에 놓이는 경우가 있기 때문(아나패스 2026Q3).
- 본문·표는 자르지 않고 전부 담는다. 보고서(HTML/PDF)에서만 줄여서 보여 준다.
XML이 깨진 경우가 있어 lxml recover 모드로 읽는다.
"""
from __future__ import annotations

import html
import re

from lxml import etree

# 찾을 주석 범주: (표시 이름, 주석 제목 정규식, 핵심 여부)
# 핵심 범주를 못 찾으면 「찾지 못한 주석」으로 보고한다. 비핵심은 없을 수도 있는 주석.
TARGETS = [
    ("영업부문·매출 구성", r"영업\s*부문|부문\s*정보|부문별|고객과의\s*계약|수익의\s*구분|매출액|영업수익|^수익(\s|$|\()", True),
    ("매출채권·대손", r"매출채권|수취채권|기타\s*채권|대손|손실\s*충당금|기대\s*신용\s*손실", True),
    ("재고자산", r"재고자산", True),
    ("차입금·사채", r"차입금|사채|차입\s*부채", True),
    ("특수관계자", r"특수\s*관계", True),
    ("우발부채·약정·담보", r"우발|약정|담보|지급\s*보증", True),
    ("기타손익·금융손익(외환)", r"기타\s*수익|기타\s*비용|기타\s*영업외|영업외|금융\s*수익|금융\s*비용|금융\s*원가|외환|외화", True),
    ("법인세", r"법인세", True),
    ("충당부채", r"충당\s*부채", False),
    ("파생상품·전환권·공정가치", r"파생|전환권|상환전환|우선주|공정\s*가치", False),
    ("유형·무형자산·손상", r"유형\s*자산|무형\s*자산|영업권|손상", False),
    ("리스", r"리스", False),
    ("자본·자기주식", r"자본금|자기\s*주식|자본\s*잉여금|기타\s*자본|주식\s*기준\s*보상|주식\s*선택권", False),
    ("비용의 성격별 분류·판관비", r"비용의\s*성격|판매비|관리비", False),
    ("종속·관계기업", r"종속\s*기업|관계\s*기업|공동\s*기업|지분법|연결\s*대상", False),
    ("금융위험(환율·이자율)", r"위험\s*관리|금융\s*위험|재무\s*위험|자본\s*위험", False),
    ("현금흐름", r"현금\s*흐름|영업에서\s*창출", False),
    ("주당이익", r"주당", False),
    ("계속기업", r"계속\s*기업", False),
    ("보고기간 후 사건", r"보고\s*기간\s*후|후속\s*사건", False),
]
CORE = [c for c, _, core in TARGETS if core]

# "17. 차입금", "17.차입금 (연결)", "9-1. 차입금". "1) …", "(1) …", "2.1 …"은 제외
NOTE_HEAD = re.compile(r"^\s*(\d{1,3})(?:\s*-\s*(\d{1,2}))?\s*\.\s*([^\d\s.][^\n]{0,60})$")
HEAD_MAX_LEN = 70
MAX_GAP = 3            # 제목 번호는 직전 번호 +1 ~ +3 까지만 인정
# 저장용 안전 상한(비정상 문서 대비). 일반적인 주석은 여기에 걸리지 않는다
SAFE_TEXT = 60000
SAFE_TABLES = 80
SAFE_ROWS = 500


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


def _tag(el) -> str:
    return el.tag.upper() if isinstance(el.tag, str) else ""


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _lines(el) -> list[str]:
    """문단을 <BR/> 단위 줄로. 제목과 본문 첫 줄이 <BR/>로만 나뉘어 한 문단에 붙은 원문이 있다
    (예: "6. 금융상품<BR/>6.1 당분기말과 …", 아나패스 2026Q3)."""
    parts = []

    def walk(e):
        if e.text:
            parts.append(e.text)
        for c in e:
            if _tag(c) == "BR":
                parts.append("\n")
            else:
                walk(c)
            if c.tail:
                parts.append(c.tail)
    walk(el)
    out = []
    for ln in "".join(parts).split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            out.append(ln)
    return out


def _in_table(el) -> bool:
    return any(_tag(a) == "TABLE" for a in el.iterancestors())


# 원문 XML에 닫히지 않은 표가 있으면 lxml recover 모드가 뒤쪽 내용 전체를 그 표 안에 넣는다(아나패스 2026Q3).
# 안에 주석 제목 문단이 들어 있는 표는 "깨진 표"로 보고, 표로 기록하지 않고 안의 문단을 일반 문단으로 읽는다.
_HEADLIKE = re.compile(r"^\s*\d{1,3}(?:\s*-\s*\d{1,2})?\s*\.\s*[^\d\s.]")


def _broken(tbl) -> bool:
    n = 0
    for p in tbl.iter("P", "p"):
        for t in _lines(p):
            if len(t) <= 70 and _HEADLIKE.match(t):
                n += 1
                if n >= 2:
                    return True
    return False


def _own_cells(tbl) -> list:
    """이 표 자신의 칸(중첩 표의 칸 제외)."""
    out = []
    for c in tbl.iter("TD", "TH", "TE", "TU", "td", "th"):
        owner = next((a for a in c.iterancestors() if _tag(a) == "TABLE"), None)
        if owner is tbl:
            out.append(c)
    return out


def _wrapper(tbl) -> bool:
    """칸 하나짜리 바깥 표 안에 문단·표를 넣어 주석 본문을 감싼 형식(조이시티 2026Q3).
    이런 표는 표로 기록하지 않고 안쪽 문단·표를 그대로 읽는다."""
    cells = _own_cells(tbl)
    if len(cells) != 1:
        return False
    c = cells[0]
    return any(_tag(x) == "TABLE" for x in c.iterdescendants()) or len(c.findall(".//P")) >= 2


def _in_real_table(el, broken: set) -> bool:
    return any(_tag(a) == "TABLE" and id(a) not in broken for a in el.iterancestors())


def _table_rows(tbl) -> list[list[str]]:
    """이 표의 행만(안쪽에 중첩된 표의 행은 제외)."""
    rows = []
    for tr in tbl.iter("TR", "tr"):
        owner = next((a for a in tr.iterancestors() if _tag(a) == "TABLE"), None)
        if owner is not tbl:
            continue
        cells = [_text(c) for c in tr if _tag(c) in ("TD", "TH", "TE", "TU")]
        if any(cells):
            rows.append(cells)
        if len(rows) >= SAFE_ROWS:
            break
    return rows


def _level(el) -> int:
    """TITLE이 몇 단계 SECTION 안에 있는지(SECTION-2 → 2). SECTION 밖이면 0."""
    for a in el.iterancestors():
        m = re.match(r"SECTION-(\d)", _tag(a))
        if m:
            return int(m.group(1))
    return 0


# 주석 섹션 다음에 오는 같은 수준의 목차 제목(여기서 범위를 끝낸다)
NEXT_SECTION = re.compile(r"^\s*(?:[IVX]+\.|\d+\.\s*(?:재무제표\s*$|배당|증권의\s*발행|기타\s*재무|연결\s*재무제표\s*$))")


def _is_section_title(el) -> bool:
    """목차에 나오는 진짜 섹션 제목인지(TABLE-GROUP 등 안의 표 제목은 아님)."""
    par = el.getparent()
    return par is not None and _tag(par).startswith("SECTION")


def _range(title_el, stop_log: list | None = None):
    """title_el 다음부터 다음 섹션 제목 전까지의 요소(문서 순서).
    끝: 부모가 SECTION-k(k ≤ 현재 수준)인 TITLE, 또는 「4. 재무제표」「6. 배당…」「IV.」 같은 다음 목차 제목."""
    root = title_el.getroottree().getroot()
    lv = _level(title_el) or 99
    started = False
    for el in root.iter():
        if not started:
            if el == title_el:
                started = True
            continue
        if _tag(el) == "TITLE":
            t = _text(el)
            if (_is_section_title(el) and _level(el) <= lv) or NEXT_SECTION.match(t):
                if stop_log is not None:
                    stop_log.append({"stop": t[:40], "parent": _tag(el.getparent()), "level": _level(el)})
                break
        yield el


def _blocks(section, stop_log: list | None = None, tolerant: bool = False):
    """섹션(요소 또는 TITLE 요소)의 내용을 문서 순서대로 (kind, value) 목록으로. kind: p / table / title
    TITLE 요소를 넘기면 그 제목의 범위(_range)를 읽는다."""
    out = []
    els = list(_range(section, stop_log) if _tag(section) == "TITLE" else section.iter())
    broken: set = set()
    keep = []  # 깨진 표 객체를 살려 두어 id 재사용을 막음
    if tolerant:
        seen = set()
        for el in els:
            for a in [el, *el.iterancestors()]:
                if _tag(a) == "TABLE" and id(a) not in seen:
                    seen.add(id(a))
                    keep.append(a)
                    if _broken(a):
                        broken.add(id(a))
    # 감싸기 표(칸 하나에 문단·표가 든 바깥 표)는 투명하게 취급
    checked = set()
    for el in els:
        if _tag(el) != "TABLE" or id(el) in checked:
            continue
        checked.add(id(el))
        keep.append(el)
        if id(el) not in broken and _wrapper(el):
            broken.add(id(el))
    for el in els[:1]:  # 범위 시작보다 바깥에 있는 감싸기 표(깨진 구조 대비)
        for a in el.iterancestors():
            if _tag(a) == "TABLE" and id(a) not in checked:
                checked.add(id(a))
                keep.append(a)
                if _wrapper(a):
                    broken.add(id(a))
    in_tbl = lambda e: _in_real_table(e, broken)
    for el in els:
        tag = _tag(el)
        if tag in ("TD", "TH", "TE") and not in_tbl(el):
            owner = next((a for a in el.iterancestors() if _tag(a) == "TABLE"), None)
            if owner is not None and id(owner) in broken and el.text and el.text.strip():
                out.append(("p", re.sub(r"\s+", " ", el.text).strip()))  # 감싸기 표 칸에 바로 쓴 글
            continue
        if tag == "TABLE":
            if id(el) in broken:
                continue
            if in_tbl(el):
                continue  # 중첩 표는 바깥 표 하나로만 기록
            rows = _table_rows(el)
            # 칸 하나짜리 표에 주석 제목만 있는 경우 → 제목 후보
            if len(rows) == 1 and len(rows[0]) == 1 and NOTE_HEAD.match(rows[0][0]):
                out.append(("title", rows[0][0]))
            elif rows:
                out.append(("table", rows))
        elif tag in ("P", "TITLE"):
            if el is section or in_tbl(el):
                continue
            if tag == "TITLE" and el.getparent() is section:
                continue  # 섹션 자신의 제목
            if tag == "TITLE":
                t = _text(el)
                if t:
                    out.append(("title", t))
            else:
                for t in _lines(el):
                    out.append(("p", t))
    return out


def _find_sections(root, pattern):
    """제목이 pattern에 맞는 TITLE 요소들. 내용은 _blocks(TITLE)로 읽는다."""
    for t in root.iter("TITLE"):
        if re.search(pattern, _text(t)):
            yield t, _text(t)


def _head(val: str, last_no: int):
    """제목이면 (번호, 하위번호, 제목), 아니면 None. "9-2." 처럼 현재 주석의 이어지는 부분이면 하위번호 2."""
    if len(val) > HEAD_MAX_LEN:
        return None
    m = NOTE_HEAD.match(val)
    if not m:
        return None
    no, sub = int(m.group(1)), int(m.group(2) or 0)
    title = m.group(3).strip().rstrip(":：").strip()
    if sub >= 2 and no == last_no:
        return no, sub, title          # 같은 주석의 이어지는 부분
    if last_no == 0:
        ok = no <= MAX_GAP          # 첫 주석은 1번 근처
    else:
        ok = last_no < no <= last_no + MAX_GAP
    if not ok:
        return None
    return no, sub, title


def split_notes(section, stop_log: list | None = None, tolerant: bool = False) -> list[dict]:
    """주석 섹션을 번호별 덩어리로 나눈다. 반환: [{no,title,text:[..],tables:[..],blocks:[..]}]
    blocks는 문서 순서 그대로의 [{"p": 문단} | {"table": 행 목록}] — 어느 하위 항목의 표인지 알 수 있게"""
    chunks, cur, last_no = [], None, 0
    for kind, val in _blocks(section, stop_log, tolerant):
        if kind in ("p", "title"):
            h = _head(val, last_no)
            if h and h[1] >= 2 and cur is not None and h[0] == cur["no"]:
                cur["blocks"].append({"p": val})   # "9-2. 차입금": 같은 주석에 이어 붙임
                cur["text"].append(val)
                continue
            if h:
                cur = {"no": h[0], "title": h[2], "text": [], "tables": [], "blocks": []}
                chunks.append(cur)
                last_no = h[0]
                continue
        if cur is None:
            continue
        if kind in ("p", "title"):
            if sum(len(x) for x in cur["text"]) < SAFE_TEXT:
                cur["text"].append(val)
                cur["blocks"].append({"p": val})
        elif kind == "table" and len(cur["tables"]) < SAFE_TABLES:
            cur["tables"].append(val)
            cur["blocks"].append({"table": val})
    return chunks


def categorize(title: str) -> list[str]:
    return [c for c, pat, _ in TARGETS if re.search(pat, title)]


def extract(files: dict[str, bytes], prefer_consolidated: bool = True) -> dict:
    """반환: {status, section, notes, all_notes, sales_tables, missing, diag}
    - all_notes: 주석 섹션의 모든 주석(번호·제목·본문 전체·표 전체·해당 범주)
    - notes: all_notes 중 TARGETS 범주에 걸린 것(보고서 발췌용, 원본 그대로)
    """
    roots = [r for r in (_parse(b) for n, b in sorted(files.items()) if n.lower().endswith(".xml")) if r is not None]
    if not roots:
        return {"status": "parse_failed", "section": None, "notes": [], "all_notes": [],
                "sales_tables": [], "missing": list(CORE), "diag": {}}

    # 주석 섹션: 연결 우선. 찾은 섹션에서 주석이 하나도 안 나뉘면 다음 후보로
    cons, sep = r"연결\s*재무제표\s*주석", r"^\s*[\dIVX]*\.?\s*재무제표\s*주석"
    pats = [cons, sep] if prefer_consolidated else [sep, cons]
    sec_title, chunks, tried = None, [], []
    for p in pats:
        for r in roots:
            for s, t in _find_sections(r, p):
                # 두 방식 중 더 많이 나뉜 쪽: ① 제목의 부모 요소 안 ② 제목부터 다음 섹션 제목 전까지(문서 순서)
                # ③ ②에서 깨진 표(안에 주석 제목이 든 표)를 풀어 읽기
                stop = []
                by_parent = split_notes(s.getparent()) if s.getparent() is not None else []
                by_range = split_notes(s, stop)
                by_tol = split_notes(s, None, tolerant=True) if len(by_range) < 3 else []
                ch = max((by_parent, by_range, by_tol), key=len)
                tried.append({"section": t, "parent": _tag(s.getparent()), "level": _level(s),
                              "by_parent": len(by_parent), "by_range": len(by_range), "by_tolerant": len(by_tol),
                              "range_stop": stop[:1]})
                if len(ch) > len(chunks):
                    sec_title, chunks = t, ch
            if chunks:
                break
        if chunks:
            break

    all_notes = []
    for ch in chunks:
        all_notes.append({"no": ch["no"], "title": ch["title"], "categories": categorize(ch["title"]),
                          "text": "\n".join(ch["text"]), "tables": ch["tables"], "blocks": ch["blocks"]})
    notes = [{"category": " / ".join(n["categories"]), "title": f"{n['no']}. {n['title']}",
              "text": n["text"], "tables": n["tables"], "blocks": n["blocks"]} for n in all_notes if n["categories"]]

    # 사업의 내용 → 매출 및 수주상황
    sales_tables = []
    for r in roots:
        secs = list(_find_sections(r, r"매출\s*(및|과)\s*수주")) or \
               list(_find_sections(r, r"^\s*\d*\.?\s*매출\s*$|주요\s*제품.*매출"))
        if secs:
            s0 = secs[0][0]
            blocks = _blocks(s0) or (_blocks(s0.getparent()) if s0.getparent() is not None else [])
            for kind, val in blocks:
                if kind == "table" and len(sales_tables) < 12:
                    sales_tables.append(val)
            break

    found = {c for n in all_notes for c in n["categories"]}
    missing = [c for c in CORE if c not in found]
    diag = {"sections_tried": tried, "note_count": len(all_notes),
            "numbers": [n["no"] for n in all_notes]}
    nums = diag["numbers"]
    diag["number_gaps"] = [b for a, b in zip(nums, nums[1:]) if b != a + 1]
    if roots and not all_notes:
        # 실패 원인 확인용: 주석 섹션 근처의 짧은 문단 샘플
        short = []
        for r in roots[:1]:
            for el in r.iter("P", "TITLE"):
                t = _text(el)
                if t and len(t) <= HEAD_MAX_LEN and re.match(r"^\s*\d", t):
                    short.append(t)
                if len(short) >= 30:
                    break
        diag["short_numbered_paragraphs"] = short
        for r in roots[:1]:
            for el in r.iter("P"):
                t = _text(el)
                if t and len(t) <= HEAD_MAX_LEN and re.match(r"^\s*1\s*\.\s*(회사의\s*개요|일반\s*사항)", t):
                    diag.setdefault("head_paths", []).append(
                        ">".join(_tag(a) for a in reversed(list(el.iterancestors())))[-200:] + f" | {t}")
            diag["head_paths"] = diag.get("head_paths", [])[:6]
    status = "extracted" if all_notes else ("no_notes_section" if not tried else "no_matching_notes")
    return {"status": status, "section": sec_title, "notes": notes, "all_notes": all_notes,
            "sales_tables": sales_tables, "missing": missing, "diag": diag}


def summary(info: dict | None) -> str:
    """로그용 한 줄 요약."""
    if not info:
        return "주석 없음"
    d = info.get("diag") or {}
    s = f"주석 {info['status']}: {d.get('note_count', 0)}개 중 대상 {len(info.get('notes', []))}개"
    if d.get("number_gaps"):
        s += f", 번호 건너뜀 {d['number_gaps'][:5]}"
    if info.get("missing"):
        s += f", 못 찾음 {', '.join(info['missing'])}"
    return s


def table_html(rows: list[list[str]], max_rows: int | None = None) -> str:
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    shown = rows if not max_rows or len(rows) <= max_rows else rows[:max_rows]
    out = ['<table class="note">']
    for i, r in enumerate(shown):
        tag = "th" if i == 0 else "td"
        cells = "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in r + [""] * (w - len(r)))
        out.append(f"<tr>{cells}</tr>")
    out.append("</table>")
    if len(shown) < len(rows):
        out.append(f'<p class="small">… 이하 {len(rows) - len(shown)}행 생략(전체는 data/notes 파일)</p>')
    return "".join(out)
