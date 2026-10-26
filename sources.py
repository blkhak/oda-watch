"""사이트별 게시판 수집. 모든 수집기는 같은 모양의 항목 목록을 돌려준다.

항목: {"key": 중복 판별용 고유값, "site", "board", "title", "date": "YYYY-MM-DD", "url", "info": 부가정보}
요청은 curl로 보낸다 (로컬 PC는 파이썬 HTTPS 인증서 검사가 보안 프로그램에 막히고, GitHub에서는 둘 다 된다).
"""
import html
import json
import os
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta

import overseas

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"


def get(url, timeout=60, retries=3):
    for attempt in range(retries):
        r = subprocess.run(["curl", "-s", "-L", "--max-time", str(timeout), "-A", UA, url], capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode("utf-8", "replace")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"접속 실패(curl {r.returncode}): {url.split('?')[0]}")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def ymd(s):
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", s or "")
    return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}" if m else ""


# ── EDCF·수출입은행: 같은 홈페이지 틀(notice-list-item) ──────────────────────────
def humanframe(site, board, base, path, title_field="제목", key_from="link", extra=()):
    """key_from: link(상세주소의 글번호) | goDetail(자바스크립트 글번호) | result(번호+수주업체, 입찰결과용)"""
    page = get(base + path)
    out = []
    for blk in page.split('class="notice-list-item"')[1:]:
        blk = re.sub(r'<div class="layer-file-list.*?</ul>', "", blk, flags=re.S)   # 첨부파일 목록 제거
        f = {t: clean(v) for t, v in re.findall(r'<span[^>]*title="([^"]+)"[^>]*>(.*?)</span>\s*(?=<span|</div>|$)', blk, re.S)}
        title = f.get(title_field) or ""
        if not title:
            continue
        link = re.search(r'href="(/[^"#]+?/\d+)(?:\?[^"]*)?"', blk) or re.search(r'href="([^"]*boardid=\d+)[^"]*"', blk)
        gd = re.search(r"goDetail\('(\d+)'\)", blk)
        if key_from == "result":
            key = f"{f.get('번호')}|{f.get('수주업체', '')}"
            url = base + path
        elif key_from == "goDetail" and gd:
            key, url = gd[1], base + path
        elif link:
            href = html.unescape(link[1])
            key = re.search(r"(\d+)$", href)[1]
            url = base + href
        else:
            continue
        date = ymd(f.get("등록일") or f.get("작성일") or f.get("공고일") or f.get("계약체결일자") or blk)
        info = " · ".join(f[k] for k in extra if f.get(k))
        out.append({"key": f"{site}|{board}|{key}", "site": site, "board": board, "title": title,
                    "date": date, "url": url, "info": info})
    return out


EDCF = "https://www.edcfkorea.go.kr"
EXIM = "https://www.koreaexim.go.kr"


def edcf():
    return (humanframe("EDCF", "입찰공고", EDCF, "/fe/HPHFFE065M01", "사업명", extra=("분류", "사업분야", "국가"))
            + humanframe("EDCF", "입찰예정사업", EDCF, "/fe/HPHFFE054M01", "국가", "goDetail", extra=("승인년도",))
            + humanframe("EDCF", "입찰결과", EDCF, "/fe/HPHFFE069M01", "사업명", "result",
                         extra=("분류", "국가", "수주업체", "수주기업"))
            + humanframe("EDCF", "공지사항", EDCF, "/HPHFFE122M01")
            + humanframe("EDCF", "보도자료", EDCF, "/HPHFFE124M01"))


def exim():
    return (humanframe("수출입은행", "공지사항", EXIM, "/HPHKBI039M01")
            + humanframe("수출입은행", "보도자료", EXIM, "/HPHKBI037M01"))


# ── KSP: JSON API ─────────────────────────────────────────────────────────────
def ksp():
    out = []
    for board, name in (("bidding", "입찰공고"), ("news", "소식")):
        d = json.loads(get(f"https://www.ksp.go.kr/api/post/{board}"))
        for a in d["articlePage"]["content"]:
            out.append({"key": f"KSP|{board}|{a['id']}", "site": "KSP", "board": name, "title": a["title"].strip(),
                        "date": ymd(a.get("userRegisterDate")), "url": f"https://www.ksp.go.kr/post/{board}/{a['id']}",
                        "info": a.get("categoryName") or ""})
    return out


# ── 해외건설협회: JSON API ──────────────────────────────────────────────────────
ICAK_BOARDS = [  # (bbsId, 게시판 이름, 목록 주소)
    (705, "입찰공고", "bidPblancList"), (12, "공지사항", "noticeList"), (11, "보도자료", "pressList"),
    (701, "국토교통 ODA", None), (700, "최신정보", "analysisList"), (141, "건설뉴스", "buildingNewsList"),
]


def icak():
    out = []
    for bbs, name, page in ICAK_BOARDS:
        q = urllib.parse.urlencode({"pageIndex": 1, "bbsId": bbs, "limit": 20})
        for p in json.loads(get(f"https://www.icak.or.kr/board/api/bbsList?{q}"))["data"]["list"]:
            view = page.replace("List", "View") if page and page != "buildingNewsList" else None
            url = (f"https://www.icak.or.kr/board/{view}?sn={p['sn']}&bbsId={bbs}" if view
                   else f"https://www.icak.or.kr/board/{page or 'bidPblancList'}")
            out.append({"key": f"ICAK|{bbs}|{p['sn']}", "site": "해외건설협회", "board": name,
                        "title": p["titl"].strip(), "date": ymd(p.get("regDt")), "url": url, "info": ""})
    return out


# ── 엔지니어링협회: 공지·소식은 ETIS 게시판 ──────────────────────────────────────
ETIS_BOARDS = [("TB150", "공지", 1, 1), ("TB160", "공고", 2, 1), ("TB180", "협회소식", 7, 1),
               ("TB115", "입법·행정예고", 3, 9), ("TB050", "임금실태·통계", 1, 3)]


def kenca():
    out = []
    for cat, name, left, top in ETIS_BOARDS:
        q = f"leftParam={left}&topParam={top}&boardId=TOTALBBS&categorygroup2={cat}"
        page = get(f"https://www.etis.or.kr/bbsList.do?{q}")
        body = page[page.find("<tbody"):page.find("</tbody>")]
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
            m = re.search(r"goDetail\('(\d+)'\);\">(.*?)</a>", row, re.S)
            if not m:
                continue
            out.append({"key": f"KENCA|{cat}|{m[1]}", "site": "엔지니어링협회", "board": name, "title": clean(m[2]),
                        "date": ymd(row), "url": f"https://www.etis.or.kr/bbsView.do?{q}&boardNo={m[1]}", "info": ""})
    return out


# ── 나라장터: 용역 입찰공고 중 해외 사업 ──────────────────────────────────────────
# 해외 지명이 들어가도 개발협력 사업이 아닌 것 (국외 연수·체험학습·해외취업 등)
NOISE = re.compile(r"연수|현장체험|체험학습|수학여행|해외취업|취업지원|봉사|탐방|벤치마킹|항공권|여행|관광객|유학생|"
                   r"어학|한국어|교재|청사관리|시설관리|박람회|전시회|홍보관|행사\s*대행|축제|공연|문화원|"
                   r"출장|상표|규격인증|인증획득|판로|바이어|시장개척|마케팅|수출\s*확대|만족도")
ODA_AGENCIES = re.compile(r"한국국제협력단|한국수출입은행|해외인프라도시개발지원공사|KIND|한국개발연구원|대외경제정책연구원")


def g2b(key, now):
    """최근 26시간 동안 등록된 용역 공고 (실행이 몇 분 늦어도 빠지지 않게 겹쳐 조회하고, 중복은 key로 거른다)"""
    if not key:
        raise RuntimeError("G2B_API_KEY가 없습니다")
    begin, end = (now - timedelta(hours=26)).strftime("%Y%m%d%H%M"), now.strftime("%Y%m%d%H%M")
    rows, page = [], 1
    while True:
        q = urllib.parse.urlencode({"serviceKey": key, "type": "json", "inqryDiv": 1, "inqryBgnDt": begin,
                                    "inqryEndDt": end, "numOfRows": 999, "pageNo": page})
        body = json.loads(get(f"https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc?{q}",
                              timeout=120))["response"]["body"]
        it = body.get("items") or []
        it = it if isinstance(it, list) else it.get("item", [])
        rows += it
        if not it or len(rows) >= int(body.get("totalCount", 0)):
            break
        page += 1
    out = []
    for r in rows:
        title, org = r["bidNtceNm"], r.get("ntceInsttNm") or ""
        if not (ODA_AGENCIES.search(org) or (overseas.is_overseas(title) and not NOISE.search(title))):
            continue
        amt = r.get("asignBdgtAmt") or r.get("presmptPrce")
        info = [org]
        if amt and str(amt).replace(".", "").isdigit() and float(amt) > 0:
            info.append(f"{float(amt) / 1e8:,.1f}억원")
        if r.get("bidClseDt"):
            info.append(f"마감 {r['bidClseDt'][:16]}")
        out.append({"key": f"G2B|{r['bidNtceNo']}|{r['bidNtceOrd']}", "site": "나라장터", "board": "용역 입찰공고(해외)",
                    "title": title, "date": ymd(r.get("bidNtceDt")),
                    "url": r.get("bidNtceDtlUrl") or "https://www.g2b.go.kr", "info": " · ".join(info)})
    return out


def collect_all(now=None):
    """(항목 목록, 실패한 사이트별 오류) — 한 사이트가 막혀도 나머지는 보고한다"""
    now = now or datetime.now()
    jobs = [("EDCF", edcf), ("수출입은행", exim), ("KSP", ksp), ("나라장터", lambda: g2b(os.environ.get("G2B_API_KEY"), now)),
            ("해외건설협회", icak), ("엔지니어링협회", kenca)]
    items, errors = [], {}
    for name, fn in jobs:
        try:
            got = fn()
            if not got and name != "나라장터":   # 나라장터는 해외 공고가 없는 날도 있다
                raise RuntimeError("게시글을 하나도 읽지 못했습니다 (홈페이지 구조 변경 가능성)")
            items += got
        except Exception as e:  # noqa: BLE001 — 사이트 하나의 실패로 보고 전체가 멈추면 안 됨
            errors[name] = str(e)[:200]
    return items, errors
