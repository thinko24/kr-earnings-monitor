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
reports/*.html, *.pdf      상세 보고서
scripts/
  run.py                   진입점 (check / sync / backfill / analyze / refresh)
  dart.py                  OpenDART API (list, fnlttSinglAcntAll, document.xml, corpCode.xml)
  financials.py            분기 단독 실적 복원
  analysis.py              위험 신호, 회귀 추정, 직전 추정 적중 점검
  notes.py                 원문에서 주석·「매출 및 수주상황」 표 추출
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
- **주석 발췌**: 원문(document.xml)에서 차입금·특수관계자·우발부채·부문/매출·재고·충당부채·금융위험·법인세·후속사건 주석 표와
  사업의 내용 「매출 및 수주상황」 표를 옮김 (PDF 첨부 없이 주석 확인 가능)
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
  "processed_rcept": ["접수번호", ...]
}
```

## 한계
- 12월 결산 법인만 지원
- 원문 XML 형식이 회사마다 조금씩 달라 일부 주석을 못 찾을 수 있음(보고서에 「찾지 못한 주석」으로 표시)
- 공시 직후에는 재무제표 API 반영이 늦을 수 있음 → 이틀 뒤 실행에서 다시 시도
- 공개 저장소이므로 관심종목과 분석 내용이 누구에게나 보입니다
- 투자 권유가 아닌 참고 자료입니다
