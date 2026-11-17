"""주석 추출 확인: python tests/test_notes.py

DART 원문에서 실제로 보이는 형식을 흉내 낸 문서로 확인한다.
- 하위 항목 "1) 단기차입금"을 새 주석으로 자르지 않는지
- 긴 본문·많은 표·긴 표가 잘리지 않는지
- 같은 범주 주석이 여러 개(우발부채 + 담보)일 때 모두 담는지
- 제목이 SECTION-3 TITLE / 칸 하나짜리 표 / "(연결)" 접미사로 나와도 나뉘는지
- 본문 속 "1. 회사의 개요" 같은 번호 문단을 주석 제목으로 보지 않는지
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import notes

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def tbl(name, n):
    rows = "".join(f"<TR><TD>{name}{i}</TD><TE>{i * 10:,}</TE></TR>" for i in range(n))
    return f"<TABLE><THEAD><TR><TH>구분</TH><TH>금액</TH></TR></THEAD><TBODY>{rows}</TBODY></TABLE>"


def doc(inner, title="3. 연결재무제표 주석"):
    return (f'<?xml version="1.0" encoding="utf-8"?><DOCUMENT><BODY><SECTION-1><TITLE>III. 재무에 관한 사항</TITLE>'
            f'<SECTION-2><TITLE>{title}</TITLE>{inner}</SECTION-2>'
            f'<SECTION-2><TITLE>4. 재무제표</TITLE><P>1. 이건 다른 섹션</P></SECTION-2>'
            f'</SECTION-1></BODY></DOCUMENT>').encode("utf-8")


# ── A. P 문단 제목 + 하위 항목 + 긴 본문 ────────────────────────
long_cb = "전환가액 8,120원, 시가 하락 시 70%까지 조정, 발행 2년 후 조기상환 청구 가능. " * 40
A = doc(
    "<P>1. 일반사항</P><P>1. 회사의 개요</P><P>회사는 1990년 설립.</P>"
    "<P>2. 중요한 회계정책</P><P>2.1 작성기준</P>"
    f"<P>3. 매출채권</P>{tbl('대손', 5)}"
    f"<P>4. 재고자산</P>{tbl('재고', 4)}<P>재고자산평가손실 7,230백만원.</P>"
    f"<P>5. 차입금</P><P>1) 단기차입금</P>{tbl('단기', 3)}<P>2) 장기차입금</P>{tbl('장기', 3)}<P>3) 유동성 대체</P>{tbl('유동', 2)}"
    f"<P>6. 전환사채</P><P>{long_cb}</P>" + "".join(tbl(f'CB{i}_', 2) for i in range(5)) +
    f"<P>7. 우발부채와 약정사항</P>{tbl('약정', 3)}"
    f"<P>8. 담보제공자산</P>{tbl('담보', 3)}"
    f"<P>9. 특수관계자 거래</P><P>(1) 특수관계자 현황</P>{tbl('현황', 3)}<P>2) 주요 거래내역</P>{tbl('거래', 60)}"
    f"<P>3) 채권·채무 잔액</P>{tbl('잔액', 4)}"
    f"<P>10. 금융수익과 금융비용</P>{tbl('외화환산손실', 4)}"
    "<P>11. 법인세비용</P><P>유효세율 26.4%</P>"
)
r = notes.extract({"main.xml": A})
by_no = {n["no"]: n for n in r["all_notes"]}
check(r["status"] == "extracted", f"A status {r['status']}")
check([n["no"] for n in r["all_notes"]] == list(range(1, 12)), f"A 번호 {[n['no'] for n in r['all_notes']]}")
check(len(by_no[5]["tables"]) == 3, "A 차입금 하위 항목 표 3개가 한 주석에 모여야 함")
check(len(by_no[9]["tables"]) == 3 and len(by_no[9]["tables"][1]) == 61, "A 특수관계자 표 3개·거래 표 61행 전부")
check(len(by_no[6]["text"]) >= len(long_cb) - 5 and len(by_no[6]["tables"]) == 5, "A 전환사채 본문·표 잘림")
check("회사의 개요" in by_no[1]["text"], "A 본문 속 '1. 회사의 개요'는 주석 1의 본문이어야 함")
cats = {n["category"] for n in r["notes"]}
check(sum("우발" in n["category"] for n in r["notes"]) == 2, "A 우발부채·담보 두 주석 모두")
for c in ("매출채권·대손", "기타손익·금융손익(외환)", "특수관계자", "재고자산", "법인세"):
    check(any(c in x for x in cats), f"A 범주 누락: {c}")
check(r["missing"] == ["영업부문·매출 구성"], f"A missing {r['missing']}")
check(r["diag"]["number_gaps"] == [], f"A 번호 건너뜀 {r['diag']['number_gaps']}")

# ── B. SECTION-3 TITLE 제목 + 칸 하나짜리 표 제목 + (연결) 접미사 ──
B = doc(
    "<SECTION-3><TITLE>1. 일반사항 (연결)</TITLE><P>지배기업 개요</P></SECTION-3>"
    f"<SECTION-3><TITLE>2. 영업부문 (연결)</TITLE>{tbl('부문', 4)}</SECTION-3>"
    "<TABLE><TR><TD>3. 차입금 (연결)</TD></TR></TABLE>" + tbl('차입', 3) +
    f"<P>4.특수관계자 (연결)</P>{tbl('특수', 2)}"
    "<P>4. 이건 본문 속 번호 문단</P><P>9. 건너뛴 번호도 본문</P>"
    f"<P>5. 보고기간 후 사건</P><P>11월 사채 발행</P>",
    title="5. 재무제표 주석",
)
r = notes.extract({"main.xml": B}, prefer_consolidated=False)
check([n["no"] for n in r["all_notes"]] == [1, 2, 3, 4, 5], f"B 번호 {[n['no'] for n in r['all_notes']]}")
if len(r["all_notes"]) == 5:
    check(r["all_notes"][2]["title"] == "차입금 (연결)" and len(r["all_notes"][2]["tables"]) == 1, "B 표 제목 주석")
    check("본문 속 번호 문단" in r["all_notes"][3]["text"] and "건너뛴 번호" in r["all_notes"][3]["text"], "B 본문 번호 문단 처리")
    check("영업부문·매출 구성" in r["all_notes"][1]["categories"], "B 영업부문 범주")

# ── C. 연결 주석이 없으면 별도 주석으로 ──────────────────────────
r = notes.extract({"main.xml": doc("<P>1. 일반사항</P><P>2. 차입금</P>", title="5. 재무제표 주석")})
check(r["status"] == "extracted" and len(r["all_notes"]) == 2, f"C 별도 주석 대체 {r['status']}")

# ── D. 주석 섹션이 없을 때 진단 정보 ────────────────────────────
r = notes.extract({"main.xml": doc("<P>1. 일반사항</P>", title="2. 재무제표")})
check(r["status"] == "no_notes_section", f"D status {r['status']}")

# ── E. 주석 문단이 SECTION 밖에 놓인 원문(아나패스 2026Q3 형식) ─────
E = ('<?xml version="1.0" encoding="utf-8"?><DOCUMENT><BODY><SECTION-1><TITLE>III. 재무에 관한 사항</TITLE>'
     '<SECTION-2><TITLE>3. 연결재무제표 주석</TITLE><P>해당사항 없음</P></SECTION-2>'
     '<SECTION-2><TITLE>5. 재무제표 주석</TITLE></SECTION-2>'
     '<P>1. 회사의 개요</P><P>설립</P><P>2. 중요한 회계정책</P><P>2.1 작성기준</P>'
     f'<P>3. 고객과의 계약에서 생기는 수익</P><P>4.1 수익 구분</P>{tbl("수익", 3)}<P>4. 차입금</P>{tbl("차입", 2)}'
     '<SECTION-2><TITLE>6. 배당에 관한 사항</TITLE><P>1. 배당 없음</P></SECTION-2>'
     '</SECTION-1></BODY></DOCUMENT>').encode("utf-8")
r = notes.extract({"main.xml": E}, prefer_consolidated=False)
check([n["no"] for n in r["all_notes"]] == [1, 2, 3, 4], f"E SECTION 밖 주석 {[n['no'] for n in r['all_notes']]}")
check(r["section"] == "5. 재무제표 주석", f"E 섹션 {r['section']}")

# ── F. "9-1. 차입금", "9-2. 차입금" 나뉜 제목(월덱스 2026Q3 형식, SECTION-3) ──
F = doc(
    "".join(f"<SECTION-3><TITLE>{t}</TITLE>{tbl(t[:2], 2)}</SECTION-3>" for t in
            ["1. 일반사항 (연결)", "8. 무형자산 (연결)"][:1]) +
    "".join(f"<SECTION-3><TITLE>{i}. 주석{i} (연결)</TITLE></SECTION-3>" for i in range(2, 9)) +
    f"<SECTION-3><TITLE>9-1. 차입금 (연결)</TITLE>{tbl('단기', 2)}</SECTION-3>"
    f"<SECTION-3><TITLE>9-2. 차입금 (연결)</TITLE>{tbl('장기', 3)}</SECTION-3>"
    f"<SECTION-3><TITLE>10. 순확정급여자산 (연결)</TITLE></SECTION-3>"
)
r = notes.extract({"main.xml": F})
by_no = {n["no"]: n for n in r["all_notes"]}
check([n["no"] for n in r["all_notes"]] == list(range(1, 11)), f"F 번호 {[n['no'] for n in r['all_notes']]}")
check(9 in by_no and by_no[9]["title"] == "차입금 (연결)" and len(by_no[9]["tables"]) == 2, "F 9-1·9-2 차입금 합치기")
check("차입금·사채" not in r["missing"], "F 차입금 범주")

# ── G. 앞쪽 표가 닫히지 않아 주석 전체가 그 표 안에 들어간 원문(recover 모드) ──
G = ('<?xml version="1.0" encoding="utf-8"?><DOCUMENT><BODY><SECTION-1><TITLE>III. 재무에 관한 사항</TITLE>'
     '<SECTION-2><TITLE>4. 재무제표</TITLE><TABLE><TBODY><TR><TD>자산</TD><TD>100</TD></TR>'   # </TABLE> 없음
     '<SECTION-2><TITLE>5. 재무제표 주석</TITLE>'
     f'<P>1. 회사의 개요</P><P>설립</P><P>2. 매출채권</P>{tbl("대손", 3)}<P>3. 차입금</P>{tbl("차입", 2)}'
     '</SECTION-2><SECTION-2><TITLE>6. 배당에 관한 사항</TITLE></SECTION-2>'
     '</SECTION-1></BODY></DOCUMENT>').encode("utf-8")
r = notes.extract({"main.xml": G}, prefer_consolidated=False)
check([n["no"] for n in r["all_notes"]] == [1, 2, 3], f"G 깨진 표 안 주석 {[n['no'] for n in r['all_notes']]} {r['diag'].get('sections_tried')}")
if len(r["all_notes"]) == 3:
    check(len(r["all_notes"][1]["tables"]) == 1 and len(r["all_notes"][1]["tables"][0]) == 4, "G 주석 안 진짜 표")

# ── H. 제목과 본문 첫 줄이 <BR/>로만 나뉜 한 문단(아나패스 2026Q3) ──
H = doc("<P><BR/>1. 회사의 개요<BR/><BR/></P><P>설립</P>"
        "<P>2. 금융상품<BR/>2.1 당분기말과 전기말 현재 금융상품의 범주별 분류내역은 다음과 같습니다.<BR/>2.1.1 금융자산</P>" + tbl("금융", 2) +
        "<P>3. 특수관계자<BR/>3.1 당분기말 현재 당사의 특수관계자 현황은 다음과 같으며 거래 내역은 아래와 같습니다.</P>" + tbl("특수", 3),
        title="5. 재무제표 주석")
r = notes.extract({"main.xml": H}, prefer_consolidated=False)
check([(n["no"], n["title"]) for n in r["all_notes"]] == [(1, "회사의 개요"), (2, "금융상품"), (3, "특수관계자")],
      f"H BR 문단 {[(n['no'], n['title']) for n in r['all_notes']]}")

# ── I. 주석마다 칸 하나짜리 바깥 표로 감싼 원문(조이시티 2026Q3) ──
def wrap(inner):
    return f"<TABLE><TBODY><TR><TD>{inner}</TD></TR></TBODY></TABLE>"
I = doc("<P>1. 일반사항 (연결)</P>" + wrap("<P>지배기업 개요</P><P>설립 2000년</P>") +
        "<P>2. 차입금 (연결)</P>" + wrap("<P>(1) 단기차입금 내역</P>" + tbl("차입처", 4) + "<P>(2) 장기차입금</P>" + tbl("장기", 2)))
r = notes.extract({"main.xml": I})
by_no = {n["no"]: n for n in r["all_notes"]}
check(len(r["all_notes"]) == 2, f"I 감싸기 표 {len(r['all_notes'])}")
if 2 in by_no:
    check(len(by_no[2]["tables"]) == 2 and len(by_no[2]["tables"][0]) == 5, f"I 안쪽 표 {[len(t) for t in by_no[2]['tables']]}")
    check("단기차입금 내역" in by_no[2]["text"], "I 안쪽 문단")

# ── J. 「매출 및 수주상황」 표가 깨진 표 안에 있는 원문 ──
J = ('<?xml version="1.0" encoding="utf-8"?><DOCUMENT><BODY><SECTION-1><TITLE>II. 사업의 내용</TITLE>'
     '<SECTION-2><TITLE>1. 사업의 개요</TITLE><TABLE><TBODY><TR><TD>x</TD></TR>'   # </TABLE> 없음
     '<SECTION-2><TITLE>4. 매출 및 수주상황</TITLE><P>가. 매출실적</P>' + tbl("OLED", 3) + '</SECTION-2>'
     '<SECTION-2><TITLE>5. 재무제표 주석</TITLE><P>1. 회사의 개요</P><P>2. 매출채권</P></SECTION-2>'
     '</SECTION-1></BODY></DOCUMENT>').encode("utf-8")
r = notes.extract({"main.xml": J}, prefer_consolidated=False)
check(len(r["sales_tables"]) >= 1 and any("OLED0" in c for row in r["sales_tables"][0] for c in row), f"J 매출 표 {r['sales_tables'][:1]}")

print("OK" if not fails else "실패:\n- " + "\n- ".join(fails))
sys.exit(1 if fails else 0)
