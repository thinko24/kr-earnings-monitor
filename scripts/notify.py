"""알림: 네이버 메일(SMTP) + GitHub Issue 본문 파일.

필요한 환경변수(저장소 Secrets)
  NAVER_MAIL_USER     네이버 아이디(예: yenbyoung) 또는 전체 주소
  NAVER_MAIL_PASSWORD 네이버 애플리케이션 비밀번호
  MAIL_TO             받는 주소(없으면 보내는 주소로)
"""
from __future__ import annotations

import html
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from common import RUNS, load_json

LEVEL = {"high": "🔴", "mid": "🟠", "low": "⚪", "good": "🟢"}


def pages_url() -> str:
    u = os.environ.get("PAGES_URL", "").strip()
    return u if not u or u.endswith("/") else u + "/"


def build_markdown(items: list[dict], errors: list[str]) -> tuple[str, str]:
    base = pages_url()
    names = ", ".join(f"{i['name']} {i['period']}" for i in items)
    title = f"[실적 알림] {names}" if items else "[실적 알림] 오류 발생"
    lines = []
    for i in items:
        lines.append(f"### {i['name']} — {i['period']} ({i['report_name']})")
        lines.append(i["headline"])
        lines.append("")
        for s in i.get("signals", [])[:5]:
            lines.append(f"- {LEVEL.get(s['level'], '')} {s['text']}")
        links = []
        if base:
            links.append(f"[대시보드]({base}#{i['corp_code']})")
            if i.get("pdf"):
                links.append(f"[PDF 보고서]({base}{i['pdf']})")
            elif i.get("html"):
                links.append(f"[보고서]({base}{i['html']})")
        links.append(f"[DART 원문](https://dart.fss.or.kr/dsaf001/main.do?rcpNo={i['rcept_no']})")
        lines.append("")
        lines.append(" · ".join(links))
        lines.append("")
    if errors:
        lines.append("### 처리 중 오류")
        lines += [f"- {e}" for e in errors]
    return title, "\n".join(lines)


def build_html(items: list[dict], errors: list[str]) -> str:
    base = pages_url()
    color = {"high": "#fde8e8", "mid": "#fff4e0", "low": "#f1f4f8", "good": "#e6f6ec"}
    parts = ["<div style=\"font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;font-size:14px;line-height:1.6;color:#1b1f24;max-width:680px\">"]
    for i in items:
        parts.append(f"<h3 style='margin:18px 0 4px'>{html.escape(i['name'])} — {html.escape(i['period'])}</h3>")
        parts.append(f"<div style='color:#5b6570;font-size:12px'>{html.escape(i['report_name'])} · 접수 {html.escape(i.get('rcept_dt',''))}</div>")
        parts.append(f"<p style='background:#f4f7ff;border-left:4px solid #1f4fd8;padding:8px 10px'>{html.escape(i['headline'])}</p>")
        for s in i.get("signals", [])[:6]:
            parts.append(f"<div style='background:{color.get(s['level'],'#fff')};padding:4px 8px;margin:3px 0;border-radius:4px;font-size:13px'>{html.escape(s['text'])}</div>")
        links = []
        if base:
            links.append(f"<a href='{base}#{i['corp_code']}'>대시보드</a>")
            if i.get("pdf"):
                links.append(f"<a href='{base}{i['pdf']}'>PDF 보고서</a>")
        links.append(f"<a href='https://dart.fss.or.kr/dsaf001/main.do?rcpNo={i['rcept_no']}'>DART 원문</a>")
        parts.append("<p>" + " · ".join(links) + "</p>")
    if errors:
        parts.append("<h3>처리 중 오류</h3><ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in errors) + "</ul>")
    parts.append("<p style='color:#8a939c;font-size:11px'>kr-earnings-monitor 자동 알림 · 규칙 기반 참고 자료이며 투자 권유가 아닙니다.</p></div>")
    return "".join(parts)


def send_mail(subject: str, html_body: str, text_body: str) -> bool:
    user = os.environ.get("NAVER_MAIL_USER", "").strip()
    pw = os.environ.get("NAVER_MAIL_PASSWORD", "").strip()
    if not user or not pw:
        print("메일 설정(NAVER_MAIL_USER/PASSWORD)이 없어 메일 알림을 건너뜁니다.")
        return False
    sender = user if "@" in user else f"{user}@naver.com"
    to = os.environ.get("MAIL_TO", "").strip() or sender
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("실적 알림", sender))
    msg["To"] = to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.naver.com", 465, timeout=30) as s:
            s.login(sender.split("@")[0], pw)
            s.sendmail(sender, [to], msg.as_string())
        print("메일 발송 완료")
        return True
    except Exception as e:
        print("메일 발송 실패:", e)
        return False


def main():
    """워크플로에서 호출: runs/notify.json을 읽어 메일 발송.  `python notify.py failure`면 실패 알림."""
    if len(sys.argv) > 1 and sys.argv[1] == "failure":
        url = os.environ.get("RUN_URL", "")
        send_mail("[실적 알림] 자동 점검 실패",
                  f"<p>GitHub Actions 실행이 실패했습니다.</p><p><a href='{url}'>실행 기록 보기</a></p>",
                  f"GitHub Actions 실행이 실패했습니다.\n{url}")
        return
    data = load_json(RUNS / "notify.json")
    if not data or not (data.get("items") or data.get("errors")):
        print("알릴 내용 없음")
        return
    title, md = data["title"], data["markdown"]
    send_mail(title, build_html(data["items"], data["errors"]), md)


if __name__ == "__main__":
    main()
