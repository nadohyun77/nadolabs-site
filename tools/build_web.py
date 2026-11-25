"""지금약국 웹판: 공개 데이터(pharmnow.json.gz)를 지역별 정적 HTML 로 만든다.

사용:
  py tool/web/build_web.py --fetch --out build/web/pharmnow
  py tool/web/build_web.py --data pharmnow.json.gz --meta meta.json --out <dir>

표준 라이브러리만 쓴다(Python 3.13). nadolabs-site 저장소에는 tools/build_web.py 로 복사해 쓴다
(CSS·JS·아이콘이 모두 이 파일 안에 있으므로 이 파일 하나만 있으면 된다).

출력(<out> 은 사이트의 /pharmnow/ 폴더):
  index.html                       소개 + 시도→시군구 목록 + '내 위치에서 가까운 순'
  <시도>/index.html                 시군구 목록 + 공공심야약국 (세종은 약국 전체)
  <시도>/<시군구>/index.html         약국 전체(이름·주소·전화·요일별 등록 운영시간)
  assets/pharmnow.css, assets/pharmnow.js, assets/icon.svg
  data/all.json                    '가까운 순'용 압축 목록
  sitemap.xml
  .generated.json                  이 생성기가 만든 파일 목록. 다음 실행 때 이 목록에 있던 파일만
                                   지운다 → 손으로 만든 privacy.html 등은 건드리지 않는다.

주소(slug) 규칙 — 한 번 정하면 바꾸지 않는다(검색 색인이 깨진다):
  시도   짧은 한글 이름: 서울 부산 대구 인천 광주 대전 울산 세종 경기 강원 충북 충남 전북 전남 경북 경남 제주
  시군구 주소의 둘째 낱말 그대로(종로구, 수원시, 홍성군). '수원시 장안구'처럼 구가 있는 시는 시 하나로 묶는다.
  세종은 시군구 페이지가 없다(시도 페이지에 약국 전체).
  URL 은 퍼센트 인코딩한 한글 경로: /pharmnow/%EC%84%9C%EC%9A%B8/%EC%A2%85%EB%A1%9C%EA%B5%AC/
  '전남광주통합특별시'로 적힌 주소는 시군구가 광주 5개 구이면 광주, 아니면 전남 페이지로 넣는다
  (데이터 대부분이 아직 옛 이름이라 검색어와 페이지를 맞춘다. 바꾸려면 SIDO 표와 _merge_sido 만 고친다).

루트 robots.txt 에 다음 한 줄을 더해 두면 좋다(이 생성기는 루트 파일을 쓰지 않는다):
  Sitemap: https://nadolabs.com/pharmnow/sitemap.xml
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

# ── 설정 ────────────────────────────────────────────────────────────────
PLAY_URL = ""  # Google Play 주소. 비어 있으면 앱 소개 블록을 내보내지 않는다.
ADSENSE_CLIENT = ""  # 예: "ca-pub-1234567890123456". 비어 있으면 광고 코드를 전혀 내보내지 않는다.
ADSENSE_SLOT = ""  # 본문 광고 단위 ID(data-ad-slot). ADSENSE_CLIENT 와 둘 다 있을 때만 본문 자리를 만든다.

SITE = "https://nadolabs.com"
BASE = "/pharmnow"
DATA_URL = "https://nadohyun77.github.io/pharmnow-data/data/pharmnow.json.gz"
META_URL = "https://nadohyun77.github.io/pharmnow-data/data/meta.json"
OG_IMAGE = SITE + "/assets/og_1200x630.png"

NOTICE = "공공데이터 기반 정보이며, 실제 영업 여부는 전화로 확인하세요."
SOURCES = "공공데이터포털(국립중앙의료원, 건강보험심사평가원, 행정안전부, 지자체 공공심야약국, 한국천문연구원)"
DAYS = ("월", "화", "수", "목", "금", "토", "일")

# (slug, 정식 이름, 별칭들)
SIDO = (
    ("서울", "서울특별시", ("서울", "서울시")),
    ("부산", "부산광역시", ("부산", "부산시")),
    ("대구", "대구광역시", ("대구", "대구시")),
    ("인천", "인천광역시", ("인천", "인천시")),
    ("광주", "광주광역시", ("광주", "광주시")),
    ("대전", "대전광역시", ("대전", "대전시")),
    ("울산", "울산광역시", ("울산", "울산시")),
    ("세종", "세종특별자치시", ("세종", "세종시")),
    ("경기", "경기도", ("경기",)),
    ("강원", "강원특별자치도", ("강원도", "강원")),
    ("충북", "충청북도", ("충북",)),
    ("충남", "충청남도", ("충남",)),
    ("전북", "전북특별자치도", ("전라북도", "전북")),
    ("전남", "전라남도", ("전남",)),
    ("경북", "경상북도", ("경북",)),
    ("경남", "경상남도", ("경남",)),
    ("제주", "제주특별자치도", ("제주도", "제주")),
)
SIDO_NAME = {s: n for s, n, _ in SIDO}
SIDO_ORDER = {s: i for i, (s, _, _) in enumerate(SIDO)}
_ALIAS = {}
for _s, _n, _al in SIDO:
    _ALIAS[_n] = _s
    for _a in _al:
        _ALIAS[_a] = _s
# lib/core/time/holidays_kr.dart 의 내장 달력(데이터에 공휴일 목록이 없을 때만 쓴다)
BUILTIN_HOLIDAYS = (
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-01", "2026-03-02", "2026-05-01", "2026-05-05",
    "2026-05-24", "2026-05-25", "2026-06-03", "2026-06-06", "2026-07-17", "2026-08-15", "2026-08-17", "2026-09-24",
    "2026-09-25", "2026-09-26", "2026-10-03", "2026-10-05", "2026-10-09", "2026-12-25",
    "2027-01-01", "2027-02-06", "2027-02-07", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-01", "2027-05-03",
    "2027-05-05", "2027-05-13", "2027-06-06", "2027-07-17", "2027-07-19", "2027-08-15", "2027-08-16", "2027-09-14",
    "2027-09-15", "2027-09-16", "2027-10-03", "2027-10-04", "2027-10-09", "2027-10-11", "2027-12-25", "2027-12-27",
)
BUILTIN_MAJOR = (
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-09-24", "2026-09-25", "2026-09-26",
    "2027-02-06", "2027-02-07", "2027-02-08", "2027-02-09", "2027-09-14", "2027-09-15", "2027-09-16",
)
GWANGJU_GU = {"동구", "서구", "남구", "북구", "광산구"}
NO_SIGUNGU = {"세종"}
_SGG_RE = re.compile(r"^[가-힣]{1,6}(시|군|구)$")


def _merge_sido(token: str, sgg: str | None) -> str | None:
    if token in _ALIAS:
        return _ALIAS[token]
    if token == "전남광주통합특별시":  # 2026 통합 명칭. 페이지는 광주/전남으로 나눠 둔다
        return "광주" if sgg in GWANGJU_GU else "전남"
    return None


def parse_region(addr: str) -> tuple[str | None, str | None]:
    """주소 → (시도 slug, 시군구). 알 수 없으면 None."""
    tokens = (addr or "").split()
    if not tokens:
        return None, None
    t1 = tokens[1].rstrip(",") if len(tokens) > 1 else None
    sgg = t1 if t1 and _SGG_RE.match(t1) else None
    sido = _merge_sido(tokens[0], sgg)
    if sido is None or sido in NO_SIGUNGU:
        return sido, None
    return sido, sgg


def page_path(sido: str, sgg: str | None = None) -> str:
    """사이트 안 경로(퍼센트 인코딩, 끝에 /)."""
    parts = [BASE, urllib.parse.quote(sido)]
    if sgg:
        parts.append(urllib.parse.quote(sgg))
    return "/".join(parts) + "/"


def page_dir(sido: str, sgg: str | None = None) -> str:
    return sido + ("/" + sgg if sgg else "")


# ── 시간 표시 ────────────────────────────────────────────────────────────
def fmt_min(m: int) -> str:
    if m == 1440:
        return "24:00"
    m %= 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def fmt_span(s) -> str:
    if not s:
        return "미등록"
    a, b = s
    if b - a >= 1440:
        return "24시간"
    if b > 1440:
        return f"{fmt_min(a)}~다음날 {fmt_min(b)}"
    return f"{fmt_min(a)}~{fmt_min(b)}"


def day_value(p: dict, i: int) -> str:
    h = p.get("h") or [None] * 8
    v = fmt_span(h[i])
    dh = p.get("dh")
    if dh and dh[i] and dh[i] != h[i]:
        v += f" (심야 지정 {fmt_span(dh[i])})"
    return v


def week_groups(p: dict) -> list[tuple[str, str]]:
    """월~일을 같은 시간끼리 묶고, 공휴일은 따로."""
    out = []
    i = 0
    while i < 7:
        v = day_value(p, i)
        j = i
        while j + 1 < 7 and day_value(p, j + 1) == v:
            j += 1
        if j == i:
            label = DAYS[i]
        elif j == i + 1:
            label = f"{DAYS[i]}·{DAYS[j]}"
        else:
            label = f"{DAYS[i]}~{DAYS[j]}"
        out.append((label, v))
        i = j + 1
    out.append(("공휴일", day_value(p, 7)))
    return out


def has_holiday_hours(p: dict, future_dd: list[str]) -> bool:
    h = p.get("h") or [None] * 8
    dh = p.get("dh") or [None] * 8
    return bool(h[6] or h[7] or dh[6] or dh[7] or future_dd)


def short_date(key: str) -> str:
    d = dt.date.fromisoformat(key)
    return f"{d.month}/{d.day}({DAYS[d.weekday()]})"


# ── 데이터 ───────────────────────────────────────────────────────────────
def load(data_path: Path, meta_path: Path | None) -> tuple[dict, dict]:
    with gzip.open(data_path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    meta = {}
    if meta_path and Path(meta_path).exists():
        meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    return data, meta


def fetch(dest: Path) -> tuple[Path, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for url in (DATA_URL, META_URL):
        req = urllib.request.Request(url, headers={"User-Agent": "pharmnow-web-builder"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
        p = dest / url.rsplit("/", 1)[1]
        p.write_bytes(body)
        out.append(p)
    return out[0], out[1]


def data_time(meta: dict) -> dt.datetime:
    kst = dt.timezone(dt.timedelta(hours=9))
    try:
        t = dt.datetime.fromisoformat(meta["fetched_at"])
        return t.astimezone(kst)
    except (KeyError, ValueError, TypeError):
        return dt.datetime.now(kst).replace(microsecond=0)


def group(pharmacies: list[dict]) -> tuple[dict, dict, list]:
    """→ ({sido: {sgg: [p]}}, {sido: [시군구 미상 p]}, [시도 미상 p])"""
    tree: dict[str, dict[str, list]] = {}
    loose: dict[str, list] = {}
    unknown = []
    for p in pharmacies:
        sido, sgg = parse_region(p.get("a", ""))
        if not sido:
            unknown.append(p)
        elif sgg:
            tree.setdefault(sido, {}).setdefault(sgg, []).append(p)
        else:
            loose.setdefault(sido, []).append(p)
            tree.setdefault(sido, {})
    return tree, loose, unknown


def sort_key(p: dict):
    return (0 if p.get("g") == 1 else 1, p.get("n", ""))


# ── HTML 조각 ────────────────────────────────────────────────────────────
E = html.escape
MARK = ('<svg class="mark" viewBox="0 0 676 350" aria-hidden="true"><path d="M0,18 Q0,0 18,0 L86,0 Q104,0 104,18 '
        'L104,246 L292,246 Q310,246 310,264 L310,332 Q310,350 292,350 L18,350 Q0,350 0,332 Z M366,18 Q366,0 384,0 '
        'L658,0 Q676,0 676,18 L676,86 Q676,104 658,104 L470,104 L470,246 L658,246 Q676,246 676,264 L676,332 '
        'Q676,350 658,350 L384,350 Q366,350 366,332 Z" fill="currentColor"/><circle cx="258" cy="52" r="52" '
        'fill="#F5521A"/></svg>')
SUN = ('<svg class="i-sun" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
       'stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5'
       'M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></svg>'
       '<svg class="i-moon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
       'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>')
ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><path d="M0,230 Q0,0 230,0 L794,0 '
            'Q1024,0 1024,230 L1024,794 Q1024,1024 794,1024 L230,1024 Q0,1024 0,794 Z" fill="#0E7C66"/><path '
            'd="M412.73,882.05 Q392,909 371.27,882.05 L193.85,651.44 A250,250 0 1 1 590.15,651.44 Z" fill="#FFFFFF"/>'
            '<path d="M348,406 Q348,380 374,380 L410,380 Q436,380 436,406 L436,445 Q436,455 446,455 L485,455 Q511,455 '
            '511,481 L511,517 Q511,543 485,543 L446,543 Q436,543 436,553 L436,592 Q436,618 410,618 L374,618 Q348,618 '
            '348,592 L348,553 Q348,543 338,543 L299,543 Q273,543 273,517 L273,481 Q273,455 299,455 L338,455 Q348,455 '
            '348,445 Z" fill="#0E7C66"/><path d="M878.92,294.5 A138,138 0 1 1 734.64,116.55 A116,116 0 0 0 878.92,294.5 Z" '
            'fill="#FFD666"/></svg>')


class Ctx:
    def __init__(self, data: dict, meta: dict):
        self.data = data
        self.when = data_time(meta)
        self.date_key = self.when.date().isoformat()
        self.date_text = f"{self.when.year}년 {self.when.month}월 {self.when.day}일 {self.when:%H:%M}"
        y = (self.when.date() - dt.timedelta(days=1)).isoformat()
        # 앱 HolidayCalendar 와 같이: 데이터에 목록이 없을 때만 내장 달력
        hol = data.get("holidays") or BUILTIN_HOLIDAYS
        major = data.get("major_holidays") or BUILTIN_MAJOR
        self.holidays = sorted(d for d in hol if d >= y)
        self.major = sorted(d for d in major if d >= y)
        self.asset_v = ""

    def future_dd(self, p: dict) -> list[str]:
        y = (self.when.date() - dt.timedelta(days=1)).isoformat()
        return [d for d in (p.get("dd") or []) if d >= y]


def head(ctx: Ctx, title: str, desc: str, path: str, crumbs: list[tuple[str, str]]) -> str:
    url = SITE + path
    ld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": SITE + u} for i, (n, u) in enumerate(crumbs)]}
    ads = ""
    if ADSENSE_CLIENT:
        ads = (f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client='
               f'{E(ADSENSE_CLIENT)}" crossorigin="anonymous"></script>\n')
    v = ctx.asset_v
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)}</title>
<meta name="description" content="{E(desc)}">
<link rel="canonical" href="{E(url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="지금약국">
<meta property="og:locale" content="ko_KR">
<meta property="og:title" content="{E(title)}">
<meta property="og:description" content="{E(desc)}">
<meta property="og:url" content="{E(url)}">
<meta property="og:image" content="{E(OG_IMAGE)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#F6F5F1" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#12161C" media="(prefers-color-scheme: dark)">
<link rel="icon" href="{BASE}/assets/icon.svg?v={v}" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{BASE}/assets/pharmnow.css?v={v}">
<script>try{{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}}catch(e){{}}</script>
{ads}<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False, separators=(",", ":"))}</script>
</head>
<body>
<a class="skip" href="#main">본문으로 건너뛰기</a>
<header class="top">
  <div class="wrap bar">
    <a class="nado" href="https://nadolabs.com/" aria-label="나도랩스 홈">{MARK}</a>
    <a class="app" href="{BASE}/">지금약국</a>
    <button class="tbtn" id="theme" type="button" aria-label="밝은 화면과 어두운 화면 바꾸기">{SUN}</button>
  </div>
</header>
"""


def crumbs_html(crumbs: list[tuple[str, str]]) -> str:
    items = []
    for i, (n, u) in enumerate(crumbs):
        if i == len(crumbs) - 1:
            items.append(f'<li aria-current="page">{E(n)}</li>')
        else:
            items.append(f'<li><a href="{E(u)}">{E(n)}</a></li>')
    return f'<nav class="crumbs" aria-label="위치"><ol>{"".join(items)}</ol></nav>'


def notice_html(ctx: Ctx) -> str:
    return (f'<div class="notice" role="note"><p><b>{E(NOTICE)}</b></p>'
            f'<p class="sub">데이터 기준 {E(ctx.date_text)} · 등록된 운영시간은 실제와 다를 수 있어요.</p></div>')


def ad_slot() -> str:
    if not (ADSENSE_CLIENT and ADSENSE_SLOT):
        return ""
    return (f'<aside class="ad" aria-label="광고"><p class="ad-l">광고</p><ins class="adsbygoogle" style="display:block" '
            f'data-ad-client="{E(ADSENSE_CLIENT)}" data-ad-slot="{E(ADSENSE_SLOT)}" data-ad-format="auto" '
            f'data-full-width-responsive="true"></ins><script>(adsbygoogle=window.adsbygoogle||[]).push({{}});</script></aside>')


def emergency_html() -> str:
    return ('<p class="emg">위급하면 <a href="tel:119">119</a>에 전화하세요. '
            '<span>응급실 위치도 119에서 안내받을 수 있어요.</span></p>')


def promo_html() -> str:
    if not PLAY_URL:
        return ""
    return (f'<section class="promo" aria-labelledby="promo-h"><h2 id="promo-h">앱으로 쓰면 오프라인에서도 볼 수 있어요</h2>'
            f'<p>지금약국 앱은 약국 정보를 기기에 받아 두어 인터넷이 약할 때도 가까운 순으로 보여 줘요. 로그인 없이 바로 써요.</p>'
            f'<a class="btn play" href="{E(PLAY_URL)}" rel="noopener">Google Play에서 받기</a></section>')


def foot(ctx: Ctx) -> str:
    return f"""<footer class="foot">
  <div class="wrap">
    <p>{E(NOTICE)}</p>
    <p>데이터 기준 {E(ctx.date_text)} · 출처: {E(SOURCES)}</p>
    <p>이 페이지는 의료 상담이나 약 추천을 하지 않아요. 약에 관한 것은 약사·의사에게 물어보세요.</p>
    <p class="links"><a href="{BASE}/">지금약국 처음</a><a href="{BASE}/privacy.html">개인정보처리방침</a><a href="https://nadolabs.com/">NADO Labs</a><a href="https://blog.naver.com/nadolabs" rel="noopener">블로그</a></p>
    <p class="copy">{MARK}© {ctx.when.year} 나도랩스 · NADO Labs</p>
    <p class="biz">상호 <a href="https://nadolabs.com/">나도랩스 (NADO Labs)</a> · 대표 안도현 · 사업자등록번호 381-13-03245</p>
  </div>
</footer>
<script src="{BASE}/assets/pharmnow.js?v={ctx.asset_v}" defer></script>
</body>
</html>
"""


def tel_href(t: str) -> str:
    return "tel:" + re.sub(r"[^0-9+]", "", t)


def map_href(p: dict) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={p['la']:.6f},{p['lo']:.6f}"


def card_html(ctx: Ctx, p: dict, i: int) -> str:
    fdd = ctx.future_dd(p)
    hol = has_holiday_hours(p, fdd)
    badges = '<span class="badge night">공공심야약국</span>' if p.get("g") == 1 else ""
    phone = p.get("t")
    call = (f'<a class="btn call" href="{E(tel_href(phone))}">전화 {E(phone)}</a>' if phone
            else '<span class="btn call off">전화번호 미등록</span>')
    rows = "".join(f"<div><dt>{E(k)}</dt><dd>{E(v)}</dd></div>" for k, v in week_groups(p))
    hline = ""
    if hol:
        parts = [f"일요일 {day_value(p, 6)}", f"공휴일 {day_value(p, 7)}"]
        if fdd:
            parts.append("휴일지킴이 당번 " + ", ".join(short_date(d) for d in fdd[:4]))
        hline = f'<p class="holline">{E(" · ".join(parts))}</p>'
    hol_attr = ' data-hol="1"' if hol else ""
    return (f'<article class="ph" data-i="{i}"{hol_attr}>'
            f'<div class="ph-h"><h3>{E(p.get("n", ""))}</h3>{badges}</div>'
            f'<p class="st"></p><p class="today"></p>{hline}'
            f'<p class="addr">{E(p.get("a", ""))}</p>'
            f'<div class="acts">{call}<a class="btn map" href="{E(map_href(p))}" target="_blank" rel="noopener">'
            f'지도<span class="vh"> (새 창)</span></a></div>'
            f'<details><summary>요일별 등록 운영시간</summary><dl class="week">{rows}</dl></details></article>')


def page_json(ctx: Ctx, plist: list[dict]) -> str:
    items = []
    for p in plist:
        o = {"h": p.get("h"), "la": round(p["la"], 5), "lo": round(p["lo"], 5)}
        if p.get("dh"):
            o["dh"] = p["dh"]
        fdd = ctx.future_dd(p)
        if fdd:
            o["dd"] = fdd
        if p.get("g") == 1:
            o["g"] = 1
        items.append(o)
    payload = {"hol": ctx.holidays, "major": ctx.major, "p": items}
    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return s.replace("</", "<\\/")


def list_block(ctx: Ctx, plist: list[dict], heading: str) -> str:
    cards = "".join(card_html(ctx, p, i) for i, p in enumerate(plist))
    return f"""<section class="listing" aria-labelledby="list-h">
  <h2 id="list-h">{E(heading)}</h2>
  <div class="tools" id="tools" hidden>
    <div class="seg" role="group" aria-label="보기">
      <button type="button" data-mode="now" aria-pressed="true">지금 운영시간</button>
      <button type="button" data-mode="hol" aria-pressed="false">일요일·공휴일</button>
      <button type="button" data-mode="all" aria-pressed="false">전체</button>
    </div>
    <button type="button" class="btn near" id="near">내 위치에서 가까운 순</button>
    <p class="geo-note">위치는 이 기기 안에서 거리 계산에만 쓰고, 어디로도 보내지 않아요.</p>
    <p class="basis" id="basis" aria-live="polite"></p>
  </div>
  <noscript><p class="sub">자바스크립트를 켜면 지금 운영시간으로 등록된 약국만 골라 볼 수 있어요. 아래는 전체 목록이에요.</p></noscript>
  <p class="empty" id="empty" hidden></p>
  <div class="cards" id="cards">{cards}</div>
</section>
<script type="application/json" id="pn-data">{page_json(ctx, plist)}</script>
"""


def chips(links: list[tuple[str, str, int]]) -> str:
    return "<ul class=\"chips\">" + "".join(
        f'<li><a href="{E(u)}">{E(n)} <span>{c}</span></a></li>' for n, u, c in links) + "</ul>"


# ── 페이지 ───────────────────────────────────────────────────────────────
def render_region(ctx: Ctx, sido: str, sgg: str | None, plist: list[dict], sub_links=None, night=None) -> str:
    sname = SIDO_NAME[sido]
    name = sgg or sido
    full = f"{sido} {sgg}" if sgg else sname
    path = page_path(sido, sgg)
    crumbs = [("지금약국", BASE + "/"), (sido, page_path(sido))]
    if sgg:
        crumbs.append((sgg, path))
    n_all = len(plist) if plist else sum(c for _, _, c in (sub_links or []))
    n_night = sum(1 for p in (plist or night or []) if p.get("g") == 1)
    title = f"{full} 휴일·심야 약국 · 일요일·공휴일 운영시간 | 지금약국"
    desc = (f"{full} 약국 {n_all:,}곳의 요일별·공휴일 등록 운영시간"
            + (f", 공공심야약국 {n_night}곳" if n_night else "")
            + f". 일요일·공휴일·밤에 운영시간이 등록된 약국을 찾고, 가기 전에 전화로 확인하세요. 데이터 기준 {ctx.date_key}.")
    out = [head(ctx, title, desc, path, crumbs), '<main id="main" class="wrap">', crumbs_html(crumbs)]
    out.append(f'<p class="eyebrow">{E(sname)}</p><h1>{E(name)} 휴일·심야 약국</h1>')
    lead = f"{full}에 등록된 약국 {n_all:,}곳"
    if n_night:
        lead += f"(공공심야약국 {n_night}곳)"
    lead += "의 요일별·공휴일 운영시간을 모았어요. 지금 운영시간으로 등록된 약국을 먼저 보여 드려요."
    out.append(f'<p class="lead">{E(lead)}</p>')
    out.append(notice_html(ctx))
    out.append(emergency_html())
    out.append(ad_slot())
    if sub_links:
        out.append(f'<section aria-labelledby="sgg-h"><h2 id="sgg-h">{E(sido)} 시군구별 약국</h2>{chips(sub_links)}</section>')
    if plist:
        heading = f"{name} 약국 {len(plist):,}곳"
        out.append(list_block(ctx, plist, heading))
    elif night:
        out.append(list_block(ctx, night, f"{sido} 공공심야약국 {len(night)}곳"))
    out.append(promo_html())
    out.append("</main>")
    out.append(foot(ctx))
    return "\n".join(x for x in out if x)


def render_index(ctx: Ctx, tree: dict, loose: dict, total: int, n_night: int) -> str:
    path = BASE + "/"
    crumbs = [("지금약국", path)]
    title = "지금약국 · 휴일·심야 약국 운영시간 찾기 (일요일·공휴일·밤)"
    desc = (f"전국 약국 {total:,}곳의 요일별·공휴일 등록 운영시간을 지역별로 모았어요. 일요일·공휴일·밤에 지금 운영시간으로 "
            f"등록된 약국을 찾고, 가기 전에 전화로 확인하세요. 데이터 기준 {ctx.date_key}.")
    out = [head(ctx, title, desc, path, crumbs), '<main id="main" class="wrap">']
    out.append('<p class="eyebrow">휴일·심야 약국</p><h1>지금 운영시간으로 등록된 약국, 가까운 순으로</h1>')
    out.append(f'<p class="lead">일요일·공휴일·밤에 약이 급할 때 쓰는 페이지예요. 전국 약국 {total:,}곳(공공심야약국 {n_night}곳)의 '
               f'등록 운영시간을 공공데이터로 모았어요. 등록된 시간 기준이라 실제와 다를 수 있으니, 가기 전에 꼭 전화해 보세요.</p>')
    out.append(notice_html(ctx))
    out.append(emergency_html())
    out.append(ad_slot())
    out.append(f"""<section class="near-box" aria-labelledby="near-h">
  <h2 id="near-h">내 위치에서 가까운 순</h2>
  <p>지금 운영시간으로 등록된 약국을 10km 안에서 가까운 순으로 보여 드려요.</p>
  <button type="button" class="btn call big" id="near-index" data-base="{BASE}">내 위치에서 가까운 순으로 보기</button>
  <p class="geo-note">위치는 브라우저 안에서 거리 계산에만 쓰고, 서버나 다른 곳으로 보내지 않아요. 저장하지도 않아요.</p>
  <noscript><p class="sub">이 기능은 자바스크립트가 필요해요. 아래 지역 목록에서 찾아 주세요.</p></noscript>
  <p class="basis" id="basis" aria-live="polite"></p>
  <div class="cards" id="near-list"></div>
</section>""")
    out.append('<section aria-labelledby="reg-h"><h2 id="reg-h">지역별 약국</h2>')
    for sido in sorted(tree, key=lambda s: SIDO_ORDER[s]):
        subs = tree[sido]
        cnt = sum(len(v) for v in subs.values()) + len(loose.get(sido, []))
        if sido in NO_SIGUNGU:
            links = [(f"{sido} 전체", page_path(sido), cnt)]
        else:
            links = [(k, page_path(sido, k), len(v)) for k, v in sorted(subs.items())]
        out.append(f'<div class="region"><h3><a href="{E(page_path(sido))}">{E(SIDO_NAME[sido])}</a> '
                   f'<span>{cnt:,}곳</span></h3>{chips(links)}</div>')
    out.append("</section>")
    out.append(f"""<section aria-labelledby="how-h"><h2 id="how-h">이렇게 보여 드려요</h2>
<ul class="how">
<li>약국마다 국립중앙의료원에 등록된 요일별·공휴일 운영시간을 한국 시간 기준으로 계산해 "등록 운영시간 기준 ~22:00까지"처럼 적어요.</li>
<li>공휴일 운영시간이 등록되지 않은 약국은 평일 시간으로 대신 채우지 않고 "미등록"으로 둬요. 쉬는지 여는지 알 수 없다는 뜻이에요.</li>
<li>지자체가 지정한 공공심야약국과 휴일지킴이 당번 날짜는 따로 표시해요.</li>
<li>데이터는 하루 한 번 새로 받아요. 지금 기준은 {E(ctx.date_text)}예요.</li>
</ul></section>""")
    out.append(promo_html())
    out.append("</main>")
    out.append(foot(ctx))
    return "\n".join(x for x in out if x)


def all_json(ctx: Ctx, tree: dict, loose: dict) -> str:
    """'가까운 순'용 압축 목록 (data/all.json).
    {v:2, date, hol, major,
     iv: [[시작분, 종료분], ...]                 서로 다른 운영 구간
     w:  ["a.b..."]                              주간 패턴 8칸(월..일, 공휴일) = iv 번호(16진), 빈칸은 미등록
     r:  ["서울/종로구", ...]                     지역 경로
     p:  [[id, 이름, 위도Δ, 경도Δ, 지역, 시간패턴, 전화, 지정시간패턴+1, 심야(1), 당번일[]]]}
    이름은 끝의 '약국'을 떼고 저장한다('약국'으로 끝나지 않으면 앞에 '='). 위경도는 1e5 배 정수를
    바로 앞 줄과의 차이로 저장한다(첫 줄은 그대로). 뒤쪽 기본값(0, "", [])은 생략한다."""
    iv_idx: dict[tuple, int] = {}
    iv: list = []
    pat_idx: dict[str, int] = {}
    w: list[str] = []
    regions: list[str] = []
    rows = []

    def ivi(span) -> str:
        if not span:
            return ""
        k = tuple(span)
        if k not in iv_idx:
            iv_idx[k] = len(iv)
            iv.append(list(span))
        return format(iv_idx[k], "x")

    def pat(h) -> int:
        k = ".".join(ivi(x) for x in (h or [None] * 8))
        if k not in pat_idx:
            pat_idx[k] = len(w)
            w.append(k)
        return pat_idx[k]

    entries = []
    for sido in sorted(tree, key=lambda s: SIDO_ORDER[s]):
        for sgg, plist in sorted(tree[sido].items()):
            entries.append((page_dir(sido, sgg), plist))
        if loose.get(sido):
            entries.append((page_dir(sido), loose[sido]))
    plat = plng = 0
    for rpath, plist in entries:
        ri = len(regions)
        regions.append(rpath)
        for p in sorted(plist, key=lambda p: (p["la"], p["lo"])):
            n = p.get("n", "")
            n = n[:-2] if n.endswith("약국") and len(n) > 2 else "=" + n
            la, lo = round(p["la"] * 1e5), round(p["lo"] * 1e5)
            row = [p["id"], n, la - plat, lo - plng, ri, pat(p.get("h")), p.get("t") or "",
                   pat(p["dh"]) + 1 if p.get("dh") else 0, 1 if p.get("g") == 1 else 0, ctx.future_dd(p)]
            plat, plng = la, lo
            while len(row) > 6 and row[-1] in (0, "", []):
                row.pop()
            rows.append(row)
    payload = {"v": 2, "date": ctx.date_key, "hol": ctx.holidays, "major": ctx.major, "iv": iv, "w": w,
               "r": regions, "p": rows}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def decode_all(s: str) -> list[dict]:
    """all.json → 약국 목록(검증·테스트용, 브라우저 JS 와 같은 해석)."""
    j = json.loads(s)
    weeks = [[None if x == "" else j["iv"][int(x, 16)] for x in k.split(".")] for k in j["w"]]
    out, la, lo = [], 0, 0
    for r in j["p"]:
        r = r + [0] * (10 - len(r))
        la += r[2]
        lo += r[3]
        n = r[1][1:] if r[1].startswith("=") else r[1] + "약국"
        out.append({"id": r[0], "n": n, "la": la / 1e5, "lo": lo / 1e5, "r": j["r"][r[4]], "h": weeks[r[5]],
                    "t": r[6] or None, "dh": weeks[r[7] - 1] if r[7] else None, "g1": r[8] == 1, "dd": r[9] or []})
    return out


def sitemap(ctx: Ctx, paths: list[str]) -> str:
    lm = ctx.when.isoformat()
    urls = "".join(f"<url><loc>{E(SITE + p)}</loc><lastmod>{lm}</lastmod></url>\n" for p in paths)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + urls + "</urlset>\n")


# ── 빌드 ─────────────────────────────────────────────────────────────────
MANIFEST = ".generated.json"


def build(data: dict, meta: dict, out: Path, log=print) -> dict:
    out = Path(out)
    ctx = Ctx(data, meta)
    ctx.asset_v = hashlib.sha256((CSS + JS + ICON_SVG).encode()).hexdigest()[:10]
    pharmacies = [p for p in data.get("pharmacies", []) if p.get("la") and p.get("lo")]
    tree, loose, unknown = group(pharmacies)
    files: dict[str, str] = {}
    files["assets/pharmnow.css"] = CSS
    files["assets/pharmnow.js"] = JS
    files["assets/icon.svg"] = ICON_SVG
    paths = [BASE + "/"]
    n_night = sum(1 for p in pharmacies if p.get("g") == 1)
    total = len(pharmacies) - len(unknown)
    files["index.html"] = render_index(ctx, tree, loose, total, n_night)
    for sido in sorted(tree, key=lambda s: SIDO_ORDER[s]):
        subs = tree[sido]
        if sido in NO_SIGUNGU:
            plist = sorted(loose.get(sido, []), key=sort_key)
            files[f"{sido}/index.html"] = render_region(ctx, sido, None, plist)
        else:
            links = [(k, page_path(sido, k), len(v)) for k, v in sorted(subs.items())]
            extra = sorted(loose.get(sido, []), key=sort_key)
            night = sorted([p for v in subs.values() for p in v if p.get("g") == 1], key=sort_key)
            if extra:  # 시군구를 읽지 못한 주소: 시도 페이지에 전체 표시
                plist = sorted(night + [p for p in extra if p.get("g") != 1], key=sort_key)
                files[f"{sido}/index.html"] = render_region(ctx, sido, None, [], links, plist)
            else:
                files[f"{sido}/index.html"] = render_region(ctx, sido, None, [], links, night)
            for sgg, plist in sorted(subs.items()):
                files[f"{sido}/{sgg}/index.html"] = render_region(ctx, sido, sgg, sorted(plist, key=sort_key))
                paths.append(page_path(sido, sgg))
        paths.append(page_path(sido))
    files["data/all.json"] = all_json(ctx, tree, loose)
    files["sitemap.xml"] = sitemap(ctx, sorted(set(paths), key=lambda p: (p.count("/"), p)))

    old = []
    mf = out / MANIFEST
    if mf.exists():
        try:
            old = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError:
            old = []
    out.mkdir(parents=True, exist_ok=True)
    changed = 0
    for rel, body in files.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        b = body.encode("utf-8")
        if not p.exists() or p.read_bytes() != b:
            p.write_bytes(b)
            changed += 1
    removed = 0
    for rel in set(old) - set(files):
        p = out / rel
        if p.is_file():
            p.unlink()
            removed += 1
            d = p.parent
            while d != out and d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                d = d.parent
    mf.write_text(json.dumps(sorted(files), ensure_ascii=False, indent=0), encoding="utf-8")
    html_files = [k for k in files if k.endswith(".html")]
    sizes = {k: len(v.encode("utf-8")) for k, v in files.items()}
    stats = {
        "pages": len(html_files),
        "bytes_total": sum(sizes.values()),
        "bytes_all_json": sizes["data/all.json"],
        "largest_page": max(((sizes[k], k) for k in html_files)),
        "unknown_region": len(unknown),
        "loose": {k: len(v) for k, v in loose.items()},
        "changed": changed,
        "removed": removed,
        "files": sorted(files),
    }
    log(f"pages={stats['pages']} total={stats['bytes_total']:,}B all.json={stats['bytes_all_json']:,}B "
        f"largest={stats['largest_page'][0]:,}B ({stats['largest_page'][1]}) unknown_region={len(unknown)} "
        f"changed={changed} removed={removed}")
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="지금약국 지역별 정적 페이지 생성")
    ap.add_argument("--data", type=Path)
    ap.add_argument("--meta", type=Path)
    ap.add_argument("--fetch", action="store_true", help="공개 데이터 두 파일을 직접 받는다")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.fetch:
        a.data, a.meta = fetch(Path(tempfile.mkdtemp(prefix="pharmnow-web-")))
    if not a.data:
        ap.error("--data 또는 --fetch 가 필요합니다")
    data, meta = load(a.data, a.meta)
    if not data.get("pharmacies"):
        print("약국 데이터가 비어 있어 중단합니다", file=sys.stderr)
        return 1
    build(data, meta, a.out)
    return 0


# ── 공용 CSS ─────────────────────────────────────────────────────────────
CSS = r"""
:root{--ink:#12161C;--paper:#F6F5F1;--dot:#F5521A;--bg:var(--paper);--fg:var(--ink);--sub:#666D7A;--line:rgba(18,22,28,.13);
--card:#FFFFFF;--card2:#ECEAE4;--acc:#0E7C66;--acc-strong:#094F41;--acc-bg:#E2F0EA;--call:#0E7C66;--on-call:#FFFFFF;
--warn:#8A4B00;--warn-bg:#FFEFD9;--emg:#B3261E;--emg-bg:#FDECEA;--shadow:0 1px 2px rgba(18,22,28,.06),0 10px 28px -14px rgba(18,22,28,.22);--r:20px}
:root[data-theme="dark"]{--bg:var(--ink);--fg:var(--paper);--sub:#A3AAB6;--line:rgba(246,245,241,.15);--card:#1A2029;--card2:#232B36;
--acc:#6FD1B7;--acc-strong:#9BE3CF;--acc-bg:#15332C;--call:#0B6B58;--on-call:#FFFFFF;--warn:#FFCC80;--warn-bg:#3A2F1B;--emg:#FFB4AB;--emg-bg:#3A1D1B;
--shadow:0 1px 2px rgba(0,0,0,.3),0 12px 32px -16px rgba(0,0,0,.6)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:var(--ink);--fg:var(--paper);--sub:#A3AAB6;--line:rgba(246,245,241,.15);--card:#1A2029;--card2:#232B36;
--acc:#6FD1B7;--acc-strong:#9BE3CF;--acc-bg:#15332C;--call:#0B6B58;--on-call:#FFFFFF;--warn:#FFCC80;--warn-bg:#3A2F1B;--emg:#FFB4AB;--emg-bg:#3A1D1B;
--shadow:0 1px 2px rgba(0,0,0,.3),0 12px 32px -16px rgba(0,0,0,.6)}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font-family:"Noto Sans KR",system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;
line-height:1.65;word-break:keep-all;overflow-wrap:anywhere;font-size:16px}
a{color:inherit}
.wrap{max-width:820px;margin:0 auto;padding:0 16px}
h1,h2,h3{line-height:1.3;letter-spacing:-.02em;margin:0;text-wrap:balance}
h1{font-size:clamp(1.7rem,6vw,2.4rem);font-weight:800;margin:4px 0 10px}
h2{font-size:1.3rem;font-weight:800;margin:36px 0 12px}
p{margin:0}
.sub{color:var(--sub);font-size:.92rem}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.skip{position:absolute;left:-999px;top:8px;background:var(--fg);color:var(--bg);padding:10px 14px;border-radius:8px;z-index:50}
.skip:focus{left:8px}
:focus-visible{outline:3px solid var(--acc);outline-offset:2px;border-radius:8px}
.top{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.bar{display:flex;align-items:center;gap:6px;height:56px}
.nado{display:grid;place-items:center;min-width:44px;height:44px;color:var(--fg);margin-left:-8px;border-radius:10px}
.nado .mark{height:14px;width:auto}
.app{font-weight:800;font-size:1.05rem;text-decoration:none;color:var(--acc-strong);padding:10px 6px;min-height:44px;display:flex;align-items:center;border-left:1px solid var(--line);padding-left:12px}
.tbtn{margin-left:auto;border:1px solid var(--line);background:none;color:var(--fg);width:44px;height:44px;border-radius:50%;cursor:pointer;display:grid;place-items:center}
.tbtn:hover{background:var(--card2)}
.i-moon{display:none}
:root[data-theme="dark"] .i-sun{display:none}:root[data-theme="dark"] .i-moon{display:block}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .i-sun{display:none}:root:not([data-theme="light"]) .i-moon{display:block}}
.crumbs ol{list-style:none;margin:14px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:0 6px;font-size:.9rem;color:var(--sub)}
.crumbs li+li::before{content:"›";margin-right:6px}
.crumbs a{display:inline-block;padding:10px 0;text-decoration:underline;text-underline-offset:3px}
.crumbs li[aria-current]{padding:10px 0}
.eyebrow{font-size:.82rem;font-weight:700;letter-spacing:.08em;color:var(--sub);display:flex;align-items:center;gap:.55em;margin:20px 0 0}
.eyebrow::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--dot)}
.crumbs+.eyebrow{margin-top:6px}
.lead{color:var(--sub);font-size:1.02rem;max-width:62ch}
.notice{margin:18px 0 0;background:var(--warn-bg);color:var(--fg);border-radius:var(--r);padding:14px 16px}
.notice b{color:var(--warn);font-weight:700}
.notice .sub{color:var(--fg);opacity:.85;margin-top:2px}
.emg{margin:10px 0 0;font-size:.95rem;color:var(--sub)}
.emg a{display:inline-flex;align-items:center;justify-content:center;min-width:56px;min-height:44px;padding:0 12px;margin:0 2px;border-radius:999px;
background:var(--emg-bg);color:var(--emg);font-weight:800;text-decoration:none}
.emg span{display:inline}
.ad{margin:20px 0 0}.ad-l{font-size:.75rem;color:var(--sub)}
.chips{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:8px}
.chips a{display:inline-flex;align-items:center;gap:6px;min-height:44px;padding:8px 14px;border:1px solid var(--line);border-radius:999px;background:var(--card);text-decoration:none;font-weight:500}
.chips a span{color:var(--sub);font-size:.85rem;font-variant-numeric:tabular-nums}
.chips a:hover{border-color:var(--acc);color:var(--acc-strong)}
.region{padding:16px 0;border-top:1px solid var(--line)}
.region h3{font-size:1.1rem;margin:0 0 10px;display:flex;align-items:baseline;gap:8px}
.region h3 a{text-decoration:none;display:inline-flex;align-items:center;min-height:44px}
.region h3 a:hover{text-decoration:underline}
.region h3 span{color:var(--sub);font-size:.88rem;font-weight:500}
.how{padding-left:1.2em;color:var(--fg)}.how li{margin:6px 0}
.tools{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:0 0 14px}
.tools[hidden]{display:none}
.seg{display:flex;background:var(--card2);border-radius:999px;padding:4px;gap:2px;width:100%;max-width:460px}
.seg button{flex:1;border:0;background:none;color:var(--fg);font:inherit;font-weight:700;font-size:.92rem;padding:8px 6px;border-radius:999px;cursor:pointer;min-height:44px}
.seg button[aria-pressed="true"]{background:var(--acc);color:#fff}
:root[data-theme="dark"] .seg button[aria-pressed="true"]{background:var(--call)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .seg button[aria-pressed="true"]{background:var(--call)}}
.geo-note{width:100%;font-size:.85rem;color:var(--sub)}
.basis{width:100%;font-size:.92rem;color:var(--fg);font-weight:500}
.basis:empty{display:none}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:var(--r);padding:16px;margin:0 0 12px}
.cards{display:grid;gap:12px}
.ph{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:16px;box-shadow:var(--shadow)}
.ph[hidden]{display:none}
.ph-h{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px}
.ph h3{font-size:1.15rem;font-weight:800}
.badge{font-size:.78rem;font-weight:700;border-radius:999px;padding:2px 9px;background:var(--acc-bg);color:var(--acc-strong)}
.badge.duty{background:var(--warn-bg);color:var(--warn)}
.dist{font-size:.85rem;color:var(--sub);font-variant-numeric:tabular-nums}
.st{margin-top:6px;font-weight:700;font-size:1rem}
.st:empty,.today:empty{display:none}
.st.open{color:var(--acc-strong)}
.st.open::before{content:"";display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--acc);margin-right:7px;vertical-align:1px}
.st.closed{color:var(--sub)}
.st.unknown{color:var(--warn)}
.st.soon{color:var(--warn)}.st.soon::before{background:var(--warn)}
.today{font-size:.92rem;color:var(--sub);font-variant-numeric:tabular-nums}
.holline{display:none;font-size:.92rem;margin-top:4px;color:var(--fg)}
.mode-hol .holline{display:block}
.addr{margin-top:6px;font-size:.95rem}
.acts{display:flex;gap:8px;margin-top:12px}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:48px;padding:0 16px;border-radius:12px;font:inherit;font-weight:700;text-decoration:none;cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.btn.call{flex:1;background:var(--call);color:var(--on-call);border-color:transparent;font-variant-numeric:tabular-nums}
.btn.call.off{background:var(--card2);color:var(--fg);cursor:default}
.btn.call.big{width:100%;max-width:460px;flex:none}
.btn.map{min-width:76px}
.btn.near{min-height:44px;border-radius:999px;font-size:.92rem}
.btn:hover{filter:brightness(1.05)}
details{margin-top:10px;border-top:1px solid var(--line);padding-top:4px}
summary{cursor:pointer;min-height:44px;display:flex;align-items:center;font-weight:500;color:var(--sub);font-size:.92rem}
.week{margin:0 0 4px;display:grid;gap:2px}
.week div{display:grid;grid-template-columns:5.2em 1fr;gap:8px;font-size:.92rem;font-variant-numeric:tabular-nums}
.week dt{color:var(--sub)}.week dd{margin:0}
.near-box{margin-top:28px;background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:18px 16px}
.near-box h2{margin-top:0}
.near-box>p{margin-bottom:12px}
.near-box .geo-note{margin-top:8px}
.near-box .cards{margin-top:14px}
.near-box .ph{box-shadow:none}
.near-box .basis{margin-top:10px}
.region-link{font-size:.9rem;margin-top:4px}
.region-link a{display:inline-flex;align-items:center;min-height:44px;color:var(--acc-strong)}
.promo{margin-top:36px;padding:18px 16px;border-radius:var(--r);background:var(--acc-bg)}
.promo h2{margin:0 0 6px;font-size:1.15rem}
.btn.play{margin-top:12px;background:var(--fg);color:var(--bg);border-color:transparent}
.foot{margin-top:48px;border-top:1px solid var(--line);padding:24px 0 40px;color:var(--sub);font-size:.88rem}
.foot p{margin:4px 0}
.foot .links{display:flex;flex-wrap:wrap;gap:0 16px}
.foot .links a{display:inline-flex;align-items:center;min-height:44px}
.foot .copy{display:flex;align-items:center;gap:8px;margin-top:8px}
.foot .mark{height:12px;width:auto;color:var(--fg)}
.foot .biz a{color:inherit;text-underline-offset:3px}
@media (min-width:900px){.cards{grid-template-columns:1fr 1fr}.near-box .cards{grid-template-columns:1fr}h1{margin-top:8px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

# ── 공용 JS (앱 lib/core/time/open_status.dart · nearby.dart 를 옮김) ─────────
JS = r"""
(function(){
'use strict';
var root=document.documentElement;
var tb=document.getElementById('theme');
if(tb) tb.addEventListener('click',function(){
  var dark=root.dataset.theme?root.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme=dark?'light':'dark';
  try{localStorage.setItem('theme',root.dataset.theme);}catch(e){}
});
/*core*/
var DAY=['월','화','수','목','금','토','일'];
function pad(n){return (n<10?'0':'')+n;}
function fmt(m){if(m===1440)return '24:00';m=((m%1440)+1440)%1440;return pad(Math.floor(m/60))+':'+pad(m%60);}
function span(s){if(!s)return '미등록';if(s[1]-s[0]>=1440)return '24시간';return fmt(s[0])+'~'+(s[1]>1440?'다음날 '+fmt(s[1]):fmt(s[1]));}
// 한국 시간(UTC+9, 서머타임 없음) 벽시계. 방문자 기기 시간대와 무관하다.
function kstFrom(ms){
  var t=new Date(ms+9*3600000);
  return {y:t.getUTCFullYear(),mo:t.getUTCMonth()+1,d:t.getUTCDate(),m:t.getUTCHours()*60+t.getUTCMinutes()};
}
function day(now,off,cal){
  var t=new Date(Date.UTC(now.y,now.mo-1,now.d+off));
  var k=t.getUTCFullYear()+'-'+pad(t.getUTCMonth()+1)+'-'+pad(t.getUTCDate());
  var wd=(t.getUTCDay()+6)%7; // 월=0 … 일=6
  return {k:k,wd:wd,slot:cal.hol[k]?7:wd,mo:t.getUTCMonth()+1,d:t.getUTCDate()};
}
function calOf(D){var c={hol:{},major:{}};(D.hol||[]).forEach(function(k){c.hol[k]=1;});(D.major||[]).forEach(function(k){c.major[k]=1;});return c;}
// open_status.dart openStatus 와 같은 규칙: 전날·오늘·다음 날 구간을 오늘 0시 기준 분으로 펼쳐
// 맞닿거나 겹치는 구간을 이어 마감 시각을 구한다. 공휴일 칸이 비면 평일 칸으로 채우지 않는다(unknown).
function status(p,now,cal){
  var Y=day(now,-1,cal),T=day(now,0,cal),N=day(now,1,cal),m=now.m;
  var src=[p.h||[]];if(p.dh)src.push(p.dh);
  var spans=[],any=false;
  src.forEach(function(w){var y=w[Y.slot],t=w[T.slot],n=w[N.slot];
    if(y)spans.push([y[0]-1440,y[1]-1440]);
    if(t){any=true;spans.push([t[0],t[1]]);}
    if(n)spans.push([n[0]+1440,n[1]+1440]);});
  var c=null;
  spans.forEach(function(s){if(s[0]<=m&&m<s[1])c=(c===null||s[1]>c)?s[1]:c;});
  var hol=T.slot===7;
  if(c!==null){
    var ext=true;while(ext){ext=false;spans.forEach(function(s){if(s[0]<=c&&s[1]>c){c=s[1];ext=true;}});}
    var allDay=c-m>=1440;
    return {st:'open',closes:c,allDay:allDay,soon:!allDay&&c-m<=30,left:c-m,hol:hol};
  }
  if(!any)return {st:'unknown',hol:hol};
  var later=null;spans.forEach(function(s){if(s[0]>m&&s[0]<1440)later=(later===null||s[0]<later)?s[0]:later;});
  return {st:'closed',opens:later,hol:hol};
}
function label(s){
  if(s.st==='open'){
    var t;
    if(s.allDay)t='24시간 (등록 기준)';
    else if(s.closes===1440)t='등록 운영시간 기준 자정까지';
    else if(s.closes>1440)t='등록 운영시간 기준 내일 '+fmt(s.closes)+'까지';
    else t='등록 운영시간 기준 ~'+fmt(s.closes)+'까지';
    if(s.soon)t+=' · 마감까지 약 '+s.left+'분, 전화로 확인해 주세요';
    return t;
  }
  if(s.st==='closed')return s.opens!=null?'지금은 운영시간 전 · 등록 기준 '+fmt(s.opens)+'부터':'등록 기준 지금은 운영시간 아님';
  return s.hol?'공휴일 운영시간 미등록 — 전화로 확인':'오늘 운영시간 미등록 · 휴무인지 알 수 없어요';
}
function todayLine(p,T){
  var name=T.slot===7?'공휴일':DAY[T.wd];
  var h=(p.h||[])[T.slot],t='오늘('+name+') 등록 '+(h?span(h):'시간 없음');
  var d=p.dh&&p.dh[T.slot];
  if(d&&JSON.stringify(d)!==JSON.stringify(h))t+=' · 심야 지정 '+span(d);
  return t;
}
function kindOf(T,cal){if(cal.major[T.k])return 'major';if(cal.hol[T.k])return 'holiday';if(T.wd===6)return 'sunday';return 'normal';}
/*endcore*/
// 미리보기용 ?at=YYYY-MM-DDTHH:MM (KST)
function kstNow(){
  var q=/[?&]at=(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)/.exec(location.search);
  if(q) return {y:+q[1],mo:+q[2],d:+q[3],m:(+q[4])*60+(+q[5])};
  return kstFrom(Date.now());
}
function basisText(now,T,kind){
  var t=T.mo+'월 '+T.d+'일('+DAY[T.wd]+') '+fmt(now.m)+' 한국 시간 기준';
  if(kind==='sunday')t+=' · 일요일엔 쉬는 약국이 많아요. 꼭 전화로 확인하세요.';
  else if(kind==='holiday'||kind==='major')t+=' · 오늘은 공휴일이에요. 쉬는 약국이 많으니 꼭 전화로 확인하세요.';
  return t;
}
function dist(a,b,c,d){var R=6371000,r=Math.PI/180,x=(c-a)*r,y=(d-b)*r;var h=Math.sin(x/2)*Math.sin(x/2)+Math.cos(a*r)*Math.cos(c*r)*Math.sin(y/2)*Math.sin(y/2);return 2*R*Math.asin(Math.min(1,Math.sqrt(h)));}
function km(m){return m<1000?Math.round(m/10)*10+'m':(m/1000).toFixed(1)+'km';}
function locate(btn,ok){
  if(!navigator.geolocation){btn.textContent='이 브라우저는 위치를 지원하지 않아요';return;}
  var orig=btn.textContent;btn.disabled=true;btn.textContent='위치 확인 중…';
  navigator.geolocation.getCurrentPosition(function(pos){btn.disabled=false;btn.textContent=orig;ok(pos.coords.latitude,pos.coords.longitude);},
    function(e){btn.disabled=false;btn.textContent=orig;var b=document.getElementById('basis');
      if(b)b.textContent=e.code===1?'위치 권한이 꺼져 있어요. 브라우저 설정에서 허용하거나 지역 목록에서 찾아 주세요.':'위치를 확인하지 못했어요. 잠시 뒤 다시 시도해 주세요.';},
    {enableHighAccuracy:false,timeout:15000,maximumAge:120000});
}
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}

// ── 지역 페이지 ──
var dataEl=document.getElementById('pn-data');
if(dataEl){
  var D=JSON.parse(dataEl.textContent),cal=calOf(D),now=kstNow(),T=day(now,0,cal),kind=kindOf(T,cal);
  var box=document.getElementById('cards'),cards=[].slice.call(box.querySelectorAll('.ph'));
  var items=cards.map(function(c,i){
    var p=D.p[+c.getAttribute('data-i')],s=status(p,now,cal),duty=!!(p.dd&&p.dd.indexOf(T.k)>=0);
    var st=c.querySelector('.st');st.textContent=label(s);st.className='st '+s.st+(s.soon?' soon':'');
    c.querySelector('.today').textContent=todayLine(p,T);
    if(duty){var hd=c.querySelector('.ph-h');hd.appendChild(el('span','badge duty','오늘 휴일지킴이'));}
    return {c:c,i:i,p:p,s:s,first:(p.g===1||duty),hol7:!!((p.h&&p.h[7])||(p.dh&&p.dh[7])),d:null};
  });
  var mode='now',here=null;
  var tools=document.getElementById('tools');tools.hidden=false;
  var basis=document.getElementById('basis'),empty=document.getElementById('empty');
  var btns=[].slice.call(tools.querySelectorAll('[data-mode]'));
  function apply(){
    var list=items.slice();
    var nonNormal=kind!=='normal';
    list.sort(function(a,b){
      if(mode==='now'){
        if(here){
          var fa=nonNormal&&a.first&&a.d<=3000,fb=nonNormal&&b.first&&b.d<=3000;
          if(fa!==fb)return fa?-1:1;return a.d-b.d;
        }
        if(nonNormal&&a.first!==b.first)return a.first?-1:1;
        var ca=a.s.closes||0,cb=b.s.closes||0;if(ca!==cb)return cb-ca;return a.i-b.i;
      }
      if(mode==='hol'&&a.hol7!==b.hol7)return a.hol7?-1:1;
      if(here)return a.d-b.d;
      return a.i-b.i;
    });
    var shown=0,open=0,unk=0;
    list.forEach(function(it){
      var show=mode==='all'||(mode==='now'&&it.s.st==='open')||(mode==='hol'&&it.c.getAttribute('data-hol')==='1');
      it.c.hidden=!show;if(show)shown++;if(it.s.st==='open')open++;if(it.s.st==='unknown')unk++;
      box.appendChild(it.c);
    });
    document.body.classList.toggle('mode-hol',mode==='hol');
    var t=basisText(now,T,kind)+' · ';
    if(mode==='now')t+='지금 운영시간으로 등록된 약국 '+open+'곳 / 전체 '+items.length+'곳'+(here?' · 가까운 순':'');
    else if(mode==='hol')t+='일요일·공휴일 운영시간이 등록된 약국 '+shown+'곳'+(here?' · 가까운 순':'');
    else t+='전체 '+items.length+'곳'+(here?' · 가까운 순':'');
    basis.textContent=t;
    empty.hidden=true;
    if(mode==='now'&&open===0){empty.hidden=false;empty.textContent='지금 운영시간으로 등록된 약국이 없어요. "전체"에서 오늘 운영시간 미등록 약국'+(unk?' '+unk+'곳':'')+'을 보고 전화로 확인해 보세요. 위급하면 119에 전화하세요.';}
    else if(mode==='now'&&unk>0){empty.hidden=false;empty.textContent='오늘 운영시간이 등록되지 않은 약국 '+unk+'곳은 "전체"에서 볼 수 있어요. 쉬는지 여는지 알 수 없어 전화 확인이 필요해요.';}
    else if(mode==='hol'&&shown===0){empty.hidden=false;empty.textContent='일요일·공휴일 운영시간이 등록된 약국이 없어요.';}
  }
  btns.forEach(function(b){b.addEventListener('click',function(){mode=b.getAttribute('data-mode');btns.forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false');});apply();});});
  var nb=document.getElementById('near');
  nb.addEventListener('click',function(){locate(nb,function(la,lo){
    here=[la,lo];
    items.forEach(function(it){it.d=dist(la,lo,it.p.la,it.p.lo);var x=it.c.querySelector('.dist');if(!x){x=el('span','dist');it.c.querySelector('.ph-h').appendChild(x);}x.textContent=km(it.d);});
    nb.textContent='가까운 순으로 정렬했어요';apply();
  });});
  apply();
}

// ── 처음 페이지: 내 위치에서 가까운 순 ──
var ni=document.getElementById('near-index');
if(ni){
  var BASE=ni.getAttribute('data-base')||'/pharmnow';
  var cache=null;
  function load(cb){
    if(cache)return cb(cache);
    fetch(BASE+'/data/all.json',{cache:'no-cache'}).then(function(r){if(!r.ok)throw 0;return r.json();}).then(function(j){cache=j;cb(j);})
      .catch(function(){document.getElementById('basis').textContent='약국 목록을 받지 못했어요. 아래 지역 목록에서 찾아 주세요.';});
  }
  ni.addEventListener('click',function(){
    var b=document.getElementById('basis');b.textContent='약국 목록을 받는 중…';
    load(function(A){b.textContent='';locate(ni,function(la,lo){render(A,la,lo);});});
  });
  function render(A,la,lo){
    var cal=calOf(A),now=kstNow(),T=day(now,0,cal),kind=kindOf(T,cal),R=10000;
    var dLa=R/111000,dLo=R/85000,res=[];
    if(!A.W){A.W=A.w.map(function(k){return k.split('.').map(function(x){return x===''?null:A.iv[parseInt(x,16)];});});
      var cla=0,clo=0;A.p.forEach(function(r){cla+=r[2];clo+=r[3];r.la=cla/1e5;r.lo=clo/1e5;r.n=r[1].charAt(0)==='='?r[1].slice(1):r[1]+'약국';});}
    A.p.forEach(function(r){
      var pla=r.la,plo=r.lo;
      if(Math.abs(pla-la)>dLa||Math.abs(plo-lo)>dLo)return;
      var d=dist(la,lo,pla,plo);if(d>R)return;
      var p={h:A.W[r[5]],dh:r[7]?A.W[r[7]-1]:null,dd:r[9]||[]};
      var s=status(p,now,cal);if(s.st!=='open')return;
      var duty=p.dd.indexOf(T.k)>=0;
      res.push({r:r,d:d,s:s,p:p,duty:duty,first:(r[8]===1||duty)});
    });
    res.sort(function(a,b){return a.d-b.d;});
    if(kind!=='normal')res.sort(function(a,b){var fa=a.first&&a.d<=3000,fb=b.first&&b.d<=3000;if(fa!==fb)return fa?-1:1;return a.d-b.d;});
    res=res.slice(0,20);
    var list=document.getElementById('near-list');list.textContent='';
    var b=document.getElementById('basis');
    b.textContent=basisText(now,T,kind)+' · 10km 안 지금 운영시간으로 등록된 약국 '+(res.length===20?'가까운 20곳':res.length+'곳');
    if(!res.length){list.appendChild(el('p','empty','10km 안에 지금 운영시간으로 등록된 약국이 없어요. 지역 목록에서 오늘 운영시간 미등록 약국을 보고 전화로 확인해 보세요. 위급하면 119에 전화하세요.'));return;}
    res.forEach(function(x){
      var r=x.r,a=el('article','ph'),h=el('div','ph-h');
      h.appendChild(el('h3',null,r.n));
      if(r[8]===1)h.appendChild(el('span','badge','공공심야약국'));
      if(x.duty)h.appendChild(el('span','badge duty','오늘 휴일지킴이'));
      h.appendChild(el('span','dist',km(x.d)));a.appendChild(h);
      a.appendChild(el('p','st open'+(x.s.soon?' soon':''),label(x.s)));
      a.appendChild(el('p','today',todayLine(x.p,T)));
      var rg=A.r[r[4]],rl=el('p','region-link'),ra=el('a',null,rg.replace('/',' ')+' 약국 목록 보기');
      ra.href=BASE+'/'+rg.split('/').map(encodeURIComponent).join('/')+'/';rl.appendChild(ra);
      var acts=el('div','acts');
      if(r[6]){var c=el('a','btn call','전화 '+r[6]);c.href='tel:'+r[6].replace(/[^0-9+]/g,'');acts.appendChild(c);}
      else acts.appendChild(el('span','btn call off','전화번호 미등록'));
      var m=el('a','btn map','지도');m.href='https://www.google.com/maps/search/?api=1&query='+r.la.toFixed(5)+','+r.lo.toFixed(5);m.target='_blank';m.rel='noopener';
      acts.appendChild(m);a.appendChild(acts);a.appendChild(rl);
      list.appendChild(a);
    });
  }
}
})();
"""

if __name__ == "__main__":
    sys.exit(main())
