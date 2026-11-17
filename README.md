# 관심종목 DART 실적 자동 분석 (kr-earnings-monitor)

정기보고서(분기·반기·사업)가 공시되면 GitHub Actions가 OpenDART에서 재무제표와 원문을 받아
**규칙 기반 분석 → PDF 보고서 → 대시보드 갱신 → 네이버 메일 알림**까지 자동으로 처리합니다.
Claude 구독 없이 돌아가며, 구독 중일 때는 Claude가 같은 JSON에 깊은 해설을 덧붙일 수 있습니다.

- 대시보드: `https://thinko24.github.io/kr-earnings-monitor/`
- 금액 단위: 억 원(소수 첫째 자리)

## 구조

```
watchlist.yml              관심종목 (이름만 적어도 됨)
index.html                 대시보드 (GitHub Pages)
data/index.json            대시보드 목록용 요약
data/{corp_code}.json      분기 데이터·분석
data/notes/{corp_code}_{분기}.json  원문 재무제표 주석 전체(번호·제목·본문·표, 문서 순서)
reports/*.html, *.pdf      상세 보고서
scripts/
  run.py                   진입점 (check / sync / backfill / analyze / refresh / notes)
  dart.py                  OpenDART API (list, fnlttSinglAcntAll, document.xml, corpCode.xml)
  financials.py            분기 단독 실적 복원
  analysis.py              위험 신호, 회귀 추정, 직전 추정 적중 점검
  notes.py                 원문에서 주석·「매출 및 수주상황」 표 추출
  note_rules.py            주석 숫자로 위험 신호([주석]) 만들기
  report.py                HTML → PDF(headless Chrome, Noto Sans CJK)
  notify.py                네이버 메일, Issue 본문
.github/workflows/monitor.yml
```

## 처음 설정 (한 번만)

### 1. OpenDART API 키
1. https://opendart.fss.or.kr → 인증키 신청·관리 → 인증키 신청 (무료, 하루 20,000회)
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - `DART_API_KEY` = 발급받은 40자리 키

### 2. 네이버 메일 알림
1. 네이버 메일 → 왼쪽 아래 **환경설정 → POP3/IMAP 설정 → IMAP/SMTP 설정 → 「사용함」** 저장
2. 2단계 인증을 쓰는 경우: 네이버 **내정보 → 보안설정 → 2단계 인증 → 관리 → 애플리케이션 비밀번호 관리**에서
   종류 「직접 입력: github」로 비밀번호 생성 (2단계 인증을 안 쓰면 일반 비밀번호도 되지만 권장하지 않음)
3. Secrets 등록
   - `NAVER_MAIL_USER` = 네이버 아이디(@ 앞부분)
   - `NAVER_MAIL_PASSWORD` = 위 애플리케이션 비밀번호
   - `MAIL_TO` = 받을 주소
   
   메일 주소·비밀번호는 Secrets에만 있고 공개 코드에는 들어가지 않습니다.

### 3. GitHub Pages
**Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main` / `(root)` → Save**

### 4. Actions 권한 확인
**Settings → Actions → General → Workflow permissions → Read and write permissions** (데이터 저장용)

### 5. 첫 실행
**Actions → 실적 점검 → Run workflow → mode: `backfill`** 
→ API 키가 맞는지 확인하면서 세 종목의 과거 데이터에 새 필드(자본금, 1년 내 만기 차입금, 영업현금흐름)를 채웁니다.

## 사용법

| 하고 싶은 것 | 방법 |
|---|---|
| 종목 추가 | `watchlist.yml`에 `- name: 회사명` 추가 후 저장(commit). 휴대폰 GitHub 앱이나 대시보드의 「종목 추가·편집」 버튼으로도 됨. 몇 분 뒤 과거 데이터·최신 분석·메일이 옴 |
| 종목 보관 | `status: archived` (점검 중지, 대시보드에 흐리게 표시) |
| 종목 삭제 | 목록에서 줄 삭제 (데이터 파일은 남음) |
| 지금 점검 | Actions → 실적 점검 → Run workflow → `check` |
| 한 종목 다시 분석 | Run workflow → `analyze`, corp에 회사명 |

### 자동 실행 일정 (한국 시간)
- 5·8·11월 16일·18일 07:46 — 분기·반기보고서 마감(15일) 다음 날, 주말·공휴일 연장 대비 이틀 뒤
- 4월 1일·3일 07:46 — 사업보고서 마감(3월 31일) 다음 날
- 매월 1일 10:17 — 정정·지연 공시 확인. 공개 저장소는 60일간 활동이 없으면 예약 실행이 멈추므로 이 실행이 기록을 남겨 막아 줍니다.

GitHub 예약 실행은 수 분~수십 분 늦을 수 있습니다.

## 분석 내용 (규칙 기반)

- **분기 단독 복원**: 1~3분기는 3개월 금액, 4분기 = 사업보고서 연간 − 3분기 누적. 현금흐름은 누적 차이
- **위험 신호**: 영업 적자 전환, 유동비율 100% 미만·급락, 유동부채 급증(유동성 대체 의심), 1년 내 만기 차입금 > 현금, 부채비율, 자본잠식,
  현금 급감, 이익 대비 영업현금흐름, 영업외 손익 괴리, 세전손실인데 법인세, 매출 급감·연속 감소, 손익분기 매출 미달, 4분기 손상 패턴
- **다음 분기 추정**: 매출 추세 회귀(추세+분기 더미), 비용 회귀(영업비용 = a + b×매출 → 손익분기 매출), 비율법, 전년동기법.
  기본안은 세 방법의 중앙값이며 **신작·수주 같은 이벤트는 반영하지 않습니다**
- **적중 점검**: 직전 분석의 추정치와 이번 실제치 비교
- **주석 점검**: 원문(document.xml) 주석 전체를 읽고, 주석 원문을 옮기는 대신 확인 결과와 근거 주석 번호만 보고서에 표시
  (보증·특수관계자 충당·상위 고객 비중·재고 평가충당률·계속기업·외환손익 분해). 숫자 규칙이 "주석 확인"으로 남긴
  질문(예: 영업외 손익이 환산손익인지)은 주석으로 답을 붙임. 주석 원문은 `data/notes/`에 저장(Claude 해설용)
- 품목별 다음 분기 추정, 이벤트 해석은 하지 않음 → 필요하면 Claude 해설로 보완

## Claude로 해설 덧붙이기 (구독 중일 때)

Claude에게 이렇게 요청하면 됩니다.

> thinko24/kr-earnings-monitor 저장소의 `data/{corp_code}.json`을 읽고, kr-disclosure-report-analysis 스킬로 최신 보고서를 분석해 줘.
> 결과를 `latest`에 `source: "claude"`로 넣고(기존 규칙 결과는 `latest_rules`로 옮김), PDF를 `reports/{corp_code}_{연도}_{분기}Q.pdf`로 만들어
> `reports` 목록에 추가한 뒤 commit·push 해 줘.

`latest.source`가 `claude`이면 같은 분기의 자동 분석이 해설을 덮어쓰지 않습니다(정정 공시가 오면 안내 문구만 붙음).
다음 분기 보고서가 오면 Claude 해설은 `history`로 옮겨져 대시보드 탭에서 계속 볼 수 있습니다.

## data/{corp_code}.json 구조

```jsonc
{
  "corp_code", "name", "stock_code", "fs_div": "CFS|OFS",
  "quarters": [{"q":"2026Q3","rev","opex","op","pretax","net","sga",
                "ca","cl","tl","eq","cash","cap","stdebt","ocf","ocf_ytd","note","src"}],
  "latest": {"source":"rules|claude","period":"2026 3Q","label":"2026Q3","headline","signals":[{"level","text"}],
             "sections":[{"title","items"}],"forecast":{"period","rows","notes","accuracy"},
             "products","products_note","checklist","notes_status","notes_missing",
             "rcept_no","rcept_dt","report_name","analyzed_at","notice"},
  "latest_rules": { ... },            // Claude 해설이 있을 때 같은 분기의 규칙 결과
  "history": [ ...이전 latest... ],
  "signals": [...], "metrics": {...}, // 최신 분기 기준 자동 계산
  "reports": [{"period","source","html","pdf","notes_included","rcept_no"}],
  "processed_rcept": ["접수번호", ...],
  "notes_files": {"2026Q3": "data/notes/00580065_2026Q3.json"}
}
```

## data/notes/{corp_code}_{분기}.json 구조

```jsonc
{
  "corp_code", "name", "label", "rcept_no", "report_nm", "fs_div", "extracted_at",
  "status": "extracted|no_notes_section|no_matching_notes|parse_failed",
  "section": "3. 연결재무제표 주석",
  "all_notes": [{"no":17, "title":"차입금", "categories":["차입금·사채"],
                 "text":"본문 전체", "tables":[[["구분","금액"],...]],
                 "blocks":[{"p":"1) 단기차입금"},{"table":[...]}, ...]}],  // 문서 순서
  "notes": [...],          // all_notes 중 분석 대상 범주에 걸린 것
  "missing": [...],        // 못 찾은 핵심 범주
  "sales_tables": [...],   // 사업의 내용 「매출 및 수주상황」 표
  "diag": {"note_count", "numbers", "number_gaps", "sections_tried"}
}
```

## 주석 점검·주석 기반 위험 신호([주석])

Claude 없이 GitHub Actions에서 저장된 주석의 숫자로 판단합니다. 대시보드·PDF·메일의 위험 신호 목록에 `[주석]`으로 표시됩니다.

| 규칙 | 경고 기준 |
|---|---|
| 금융보증충당부채(다른 회사 빚에 선 보증·담보의 손실 예상분) | 있으면 주의, 자본의 10% 이상이면 높음 |
| 특수관계자 채권·대여금 손실충당금 | 금액이 자본의 10% 이상이면 주의, 그 밖에 '전액 충당' 언급은 참고 |
| 매출 상위 고객 1곳 비중(누적) | 50% 이상 주의, 30% 이상 참고 |
| 재고 평가충당률(평가충당금 ÷ 취득원가) | 50% 이상 높음, 30% 이상 주의, 15% 이상 참고(직전 공시보다 10%p 이상 오르면 한 단계 올림) |
| 계속기업 가정의 중요한 불확실성 언급 | 높음 |
| 외환손익 분해(기타·금융 손익 주석) | 경고 아님. 영업외 손익을 외화환산(비현금)·외환차(실현)·그 밖으로 나눠 표시하고, 영업외 신호에 답으로 붙임 |

숫자를 못 찾으면 그 규칙은 건너뜁니다(틀린 경고보다 경고 없음). 주석 내용의 해석은 Claude 해설에서 합니다.

## 한계
- 12월 결산 법인만 지원
- 원문 XML 형식이 회사마다 조금씩 달라 일부 주석을 못 찾을 수 있음(보고서에 「찾지 못한 핵심 주석」, Actions 로그에 주석 개수·번호 건너뜀으로 표시)
- 공시 직후에는 재무제표 API 반영이 늦을 수 있음 → 이틀 뒤 실행에서 다시 시도
- 공개 저장소이므로 관심종목과 분석 내용이 누구에게나 보입니다
- 투자 권유가 아닌 참고 자료입니다
