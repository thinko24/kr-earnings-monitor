# kr-earnings-monitor 작업 안내 (Claude용)

관심종목의 DART 정기보고서를 GitHub Actions로 자동 분석하는 저장소. 사용자는 한국 개인투자자이며 GitHub 계정은 thinko24.

## 사용자 선호
- 결과는 한국어, 금액 단위는 억 원(소수 첫째 자리), 음수는 `−`(U+2212)
- 분석은 숫자를 근거로 회의적으로(주장 검증형)
- 핵심 문구는 `==강조==`로 표시 → 대시보드·PDF·메일에서 형광펜으로 표시됨
- 공개 저장소: 메일 주소·비밀번호·API 키는 코드에 넣지 말고 Secrets만 사용

## 구조
- `index.html`: 대시보드(GitHub Pages, main / root). 빌드 없음, 순수 HTML+JS. `data/index.json`과 `data/{corp_code}.json`을 읽음
- `scripts/run.py`: 진입점(check / sync / backfill / analyze / refresh)
- `scripts/financials.py`: 분기 단독 복원(4Q = 연간 − 3Q 누적)
- `scripts/analysis.py`: 위험 신호, 회귀 추정, 적중 점검, 보고서 본문(latest)
- `scripts/notes.py`: document.xml에서 주석·「매출 및 수주상황」 표 추출. 결과 전체는 `data/notes/{corp_code}_{분기}.json`에 저장
- `scripts/report.py`: HTML → PDF(headless Chrome, Noto Sans CJK)
- `scripts/notify.py`: 네이버 SMTP 메일, Issue 본문
- `.github/workflows/monitor.yml`: 일정·수동·watchlist 변경 시 실행. mode에 `mailtest` 있음
- 데이터 구조는 README.md의 「data/{corp_code}.json 구조」 참고
- Secrets: `DART_API_KEY`, `NAVER_MAIL_USER`, `NAVER_MAIL_PASSWORD`, `MAIL_TO`

## 수정할 때 규칙
- 해설을 쓸 때는 `data/notes/{corp_code}_{분기}.json`의 `all_notes`(본문·표 전체, `blocks`는 문서 순서)를 먼저 읽는다. 파일이 없으면 Actions에서 mode `notes`(해설은 그대로 두고 주석만 저장)를 실행해 달라고 요청
- `latest.source == "claude"`인 해설은 자동 분석이 덮어쓰지 않음. Claude가 해설을 쓰면 기존 규칙 결과는 `latest_rules`로 옮기고 `source: "claude"`로 저장
- 대시보드를 고친 뒤에는 로컬에서 `python3 -m http.server`로 띄우고 headless Chrome 스크린샷(PC·휴대폰 폭)으로 확인
- 분석 코드를 고친 뒤에는 `python3 tests/test_parser.py`, `python3 tests/test_notes.py`와 `python3 scripts/run.py refresh`(API 호출 없음)로 확인
- 작업 공간에서 opendart.fss.or.kr 접속이 막혀 있으면 실제 API 동작은 Actions에서 확인(`gh run list/view`)
- 커밋 메시지는 한국어
