"""OpenDART API 클라이언트 (https://opendart.fss.or.kr).

사용 API
- corpCode.xml           : 고유번호 전체 목록(zip)
- list.json              : 공시 검색
- fnlttSinglAcntAll.json : 단일회사 전체 재무제표
- document.xml           : 공시서류 원본파일(zip)
"""
from __future__ import annotations

import io
import os
import time
import zipfile
import xml.etree.ElementTree as ET

import requests

BASE = "https://opendart.fss.or.kr/api"

# status 코드: 000 정상, 013 조회된 데이터 없음, 020 요청 제한 초과, 010/011/012 키 문제
NO_DATA = "013"


class DartError(RuntimeError):
    pass


class Dart:
    def __init__(self, key: str | None = None, pause: float = 0.25):
        self.key = key or os.environ.get("DART_API_KEY", "").strip()
        if not self.key:
            raise DartError("DART_API_KEY가 없습니다. 저장소 Settings → Secrets에 등록하세요.")
        self.pause = pause
        self.s = requests.Session()
        self.calls = 0

    # ── 저수준 ──────────────────────────────────────────
    def _get(self, path: str, params: dict, binary: bool = False):
        params = {"crtfc_key": self.key, **params}
        last = None
        for attempt in range(4):
            try:
                r = self.s.get(f"{BASE}/{path}", params=params, timeout=60)
                self.calls += 1
                time.sleep(self.pause)
                if r.status_code >= 500:
                    raise DartError(f"HTTP {r.status_code}")
                r.raise_for_status()
                if binary:
                    # 오류 시 zip 대신 XML/JSON 메시지가 온다
                    if r.content[:2] != b"PK":
                        msg = r.content[:300].decode("utf-8", "replace")
                        if "<status>013</status>" in msg or '"013"' in msg:
                            return None
                        raise DartError(f"{path}: zip이 아닌 응답 {msg}")
                    return r.content
                j = r.json()
                st = j.get("status")
                if st == "000":
                    return j
                if st == NO_DATA:
                    return None
                if st == "020":  # 요청 제한 → 잠시 쉬고 재시도
                    time.sleep(5 * (attempt + 1))
                    last = DartError(f"{path}: 요청 제한(020)")
                    continue
                raise DartError(f"{path}: status {st} {j.get('message')}")
            except (requests.RequestException, DartError) as e:
                last = e
                if isinstance(e, DartError) and "status" in str(e) and "020" not in str(e):
                    raise
                time.sleep(2 * (attempt + 1))
        raise DartError(f"{path} 실패: {last}")

    # ── 고유번호 ────────────────────────────────────────
    def corp_codes(self) -> list[dict]:
        raw = self._get("corpCode.xml", {}, binary=True)
        z = zipfile.ZipFile(io.BytesIO(raw))
        xml = z.read(z.namelist()[0])
        root = ET.fromstring(xml)
        out = []
        for el in root.iter("list"):
            out.append({
                "corp_code": (el.findtext("corp_code") or "").strip(),
                "corp_name": (el.findtext("corp_name") or "").strip(),
                "stock_code": (el.findtext("stock_code") or "").strip(),
                "modify_date": (el.findtext("modify_date") or "").strip(),
            })
        return out

    # ── 공시 검색 ───────────────────────────────────────
    def disclosures(self, corp_code: str, bgn_de: str, end_de: str,
                    pblntf_ty: str | None = None, max_pages: int = 5) -> list[dict]:
        out = []
        for page in range(1, max_pages + 1):
            params = {"corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
                      "page_no": page, "page_count": 100}
            if pblntf_ty:
                params["pblntf_ty"] = pblntf_ty
            j = self._get("list.json", params)
            if not j:
                break
            out.extend(j.get("list", []))
            if page >= int(j.get("total_page", 1) or 1):
                break
        return out

    # ── 재무제표 ────────────────────────────────────────
    def full_fs(self, corp_code: str, year: int, reprt_code: str, fs_div: str) -> list[dict] | None:
        j = self._get("fnlttSinglAcntAll.json", {
            "corp_code": corp_code, "bsns_year": str(year),
            "reprt_code": reprt_code, "fs_div": fs_div})
        if not j:
            return None
        rows = j.get("list") or []
        return rows or None

    # ── 원문 ────────────────────────────────────────────
    def document(self, rcept_no: str) -> dict[str, bytes] | None:
        raw = self._get("document.xml", {"rcept_no": rcept_no}, binary=True)
        if not raw:
            return None
        z = zipfile.ZipFile(io.BytesIO(raw))
        return {n: z.read(n) for n in z.namelist()}
