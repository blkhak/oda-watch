"""사이트별 게시판 수집. 모든 수집기는 같은 모양의 항목 목록을 돌려준다.

항목: {"key": 중복 판별용 고유값, "site", "board", "title", "date": "YYYY-MM-DD", "url", "info": 부가정보}
요청은 curl로 보낸다 (로컬 PC는 파이썬 HTTPS 인증서 검사가 보안 프로그램에 막히고, GitHub에서는 둘 다 된다).
"""
import html
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
from datetime import timedelta

import overseas

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"


SYSTEM_CA = "/etc/ssl/certs/ca-certificates.crt"
# 수출입은행 서버는 해외 접속 때 중간 인증서를 빼고 보내 리눅스 curl이 검증하지 못한다.
# 공식 배포본(cacerts.thawte.com, 2027-11 만료)을 시스템 인증서 묶음에 더해 쓴다 — 검증을 끄는 게 아님.
EXTRA_CA = {"www.koreaexim.go.kr": os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs",
                                                "thawte-tls-rsa-ca-g1.pem")}


def ca_bundle(url):
    host = urllib.parse.urlsplit(url).hostname
    if host not in EXTRA_CA or not os.path.exists(SYSTEM_CA):   # 윈도우 curl은 시스템 인증서 저장소가 알아서 채운다
        return []
    path = os.path.join(tempfile.gettempdir(), f"ca-{host}.pem")
    if not os.path.exists(path):
        with open(path, "wb") as out:
            for src in (SYSTEM_CA, EXTRA_CA[host]):
                with open(src, "rb") as f:
                    out.write(f.read() + b"\n")
    return ["--cacert", path]


def get(url, timeout=60, retries=5):
    for attempt in range(retries):
        r = subprocess.run(["curl", "-s", "-L", "-b", "", "--max-time", str(timeout), "-A", UA, *ca_bundle(url), url],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode("utf-8", "replace")
        time.sleep(10 * (attempt + 1))   # 수출입은행 서버가 가끔 연결을 끊는다
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


EDCF_BID_KINDS = ["", "개도국발주사업", "EDCF발주사업"]   # 첫 쪽이 10건뿐이라 분류별로도 읽어 밀려난 공고가 없게 한다


def edcf():
    bids = {}
    for kind in EDCF_BID_KINDS:
        path = "/fe/HPHFFE065M01" + (f"?{urllib.parse.urlencode({'boardtext8': kind})}" if kind else "")
        try:
            got = humanframe("EDCF", "입찰공고", EDCF, path, "사업명", extra=("분류", "사업분야", "국가"))
        except RuntimeError:
            if not kind:        # 전체 목록은 꼭 읽어야 하고, 분류별 목록은 보충용이라 실패해도 넘어간다
                raise
            continue
        for it in got:
            bids.setdefault(it["key"], it)
    return (list(bids.values())
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
    (701, "국토교통 ODA", "edcfList"), (700, "최신정보", "analysisList"), (141, "건설뉴스", "buildingNewsList"),
]


ICAK_ODA_FIELDS = ["발주처", "EDCF 지원", "컨설팅비", "사업기간"]   # 국토교통 ODA 본문 표에서 비고에 옮길 항목


def icak_oda_info(cn):
    """국토교통 ODA 글은 상세 화면이 회원 로그인 전용이라, 목록 API 본문(cnTxt)의 표에서 핵심 항목을 뽑아 비고에 담는다"""
    text = lambda s: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<(br|/p|/div)[^>]*>", " ", s)))).strip()
    rows = {text(k): text(v).split("*")[0].strip() for k, v in
            re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", cn or "", re.S)}
    return " / ".join(f"{k}: {rows[k]}" for k in ICAK_ODA_FIELDS if rows.get(k) and rows[k] != "-")


def icak():
    out = []
    for bbs, name, page in ICAK_BOARDS:
        q = urllib.parse.urlencode({"pageIndex": 1, "bbsId": bbs, "limit": 20})
        for p in json.loads(get(f"https://www.icak.or.kr/board/api/bbsList?{q}"))["data"]["list"]:
            info = ""
            if page in ("buildingNewsList", "edcfList"):   # 상세 화면을 바로 열 수 없어 목록으로 연결
                url = f"https://www.icak.or.kr/board/{page}"
            else:
                url = f"https://www.icak.or.kr/board/{page.replace('List', 'View')}?sn={p['sn']}&bbsId={bbs}"
            if page == "edcfList":
                info = icak_oda_info(p.get("cnTxt")) + " (상세는 해건협 로그인 필요)"
            out.append({"key": f"ICAK|{bbs}|{p['sn']}", "site": "해외건설협회", "board": name,
                        "title": p["titl"].strip(), "date": ymd(p.get("regDt")), "url": url, "info": info})
    return out


# ── 엔지니어링협회: 공지·소식은 ETIS 게시판, 해외사업 관련 글만 (국가명·해외·ODA 등 — overseas.py) ──
ETIS_BOARDS = [("TB150", "공지", 1, 1), ("TB160", "공고", 2, 1), ("TB180", "협회소식", 7, 1),
               ("TB115", "입법·행정예고", 3, 9), ("TB050", "임금실태·통계", 1, 3)]


def kenca():
    out, raw = [], 0
    for cat, name, left, top in ETIS_BOARDS:
        q = f"leftParam={left}&topParam={top}&boardId=TOTALBBS&categorygroup2={cat}"
        page = get(f"https://www.etis.or.kr/bbsList.do?{q}")
        body = page[page.find("<tbody"):page.find("</tbody>")]
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
            m = re.search(r"goDetail\('(\d+)'\);\">(.*?)</a>", row, re.S)
            if not m:
                continue
            raw += 1
            if not overseas.is_overseas(clean(m[2])):
                continue
            out.append({"key": f"KENCA|{cat}|{m[1]}", "site": "엔지니어링협회", "board": name, "title": clean(m[2]),
                        "date": ymd(row), "url": f"https://www.etis.or.kr/bbsView.do?{q}&boardNo={m[1]}", "info": ""})
    if not raw:
        raise RuntimeError("게시글을 하나도 읽지 못했습니다 (홈페이지 구조 변경 가능성)")
    return out


# ── 나라장터: 수요기관이 한국수출입은행인 입찰공고 (용역·물품·공사) 중 해외사업만 ─────────────
G2B_DEMAND = "한국수출입은행"
# 해외사업: 국가명·해외·ODA·EDCF 등(overseas.py) 또는 아래 낱말이 들어간 공고
G2B_KEEP = re.compile(r"대외경제협력기금|PMA|Project Management|원조|개도국|협력국")
# 은행 내부 구매·운영 공고 (EDCF 외화금고 시스템처럼 해외 낱말이 들어가도 내부 업무면 뺀다)
G2B_INTERNAL = re.compile(r"시스템\s*(구축|개발|고도화)|감리|외화금고|라이선스|소프트웨어|\bSW\b|서버|노후장비|소모품|복합기|"
                          r"보험|세무|회계|4대보험|급여|긴급의료|보안\s*지원|정보보호|디자인업무|아웃소싱|소액수의|"
                          r"청소|경비|시설관리|임차|인쇄|홍보물|행사\s*대행|교육\s*위탁|채용")
G2B_KINDS = [("Servc", "용역"), ("Thng", "물품"), ("Cnstwk", "공사")]
G2B_MAX_DAYS = 28   # API 조회 기간 한도(약 한 달)


def g2b(key, since, now):
    """since부터 지금까지 등록된 공고 (since는 main이 직전 보고보다 10분 앞당겨 넘긴다)"""
    if not key:
        raise RuntimeError("G2B_API_KEY가 없습니다")
    since = max(since, now - timedelta(days=G2B_MAX_DAYS))
    begin, end = since.strftime("%Y%m%d%H%M"), now.strftime("%Y%m%d%H%M")
    out = []
    for op, kind in G2B_KINDS:
        rows, page = [], 1
        while True:
            q = urllib.parse.urlencode({"serviceKey": key, "type": "json", "inqryDiv": 1, "inqryBgnDt": begin,
                                        "inqryEndDt": end, "dminsttNm": G2B_DEMAND, "numOfRows": 999, "pageNo": page})
            d = json.loads(get(f"https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfo{op}PPSSrch?{q}",
                               timeout=120))
            if "response" not in d:
                raise RuntimeError(f"나라장터 API 오류: {json.dumps(d, ensure_ascii=False)[:150]}")
            body = d["response"]["body"]
            it = body.get("items") or []
            it = it if isinstance(it, list) else it.get("item", [])
            rows += it
            if not it or len(rows) >= int(body.get("totalCount", 0)):
                break
            page += 1
        for r in rows:
            title = r["bidNtceNm"]
            if G2B_DEMAND not in (r.get("dminsttNm") or ""):
                continue
            if G2B_INTERNAL.search(title) or not (overseas.is_overseas(title) or G2B_KEEP.search(title)):
                continue
            amt = r.get("asignBdgtAmt") or r.get("presmptPrce")
            info = []
            if amt and str(amt).replace(".", "").isdigit() and float(amt) > 0:
                info.append(f"{float(amt) / 1e8:,.1f}억원")
            if r.get("bidClseDt"):
                info.append(f"마감 {r['bidClseDt'][:16]}")
            out.append({"key": f"G2B|{r['bidNtceNo']}|{r['bidNtceOrd']}", "site": "나라장터", "board": f"{kind} 입찰공고",
                        "title": r["bidNtceNm"], "date": ymd(r.get("bidNtceDt")),
                        "url": r.get("bidNtceDtlUrl") or "https://www.g2b.go.kr", "info": " · ".join(info)})
    return out


def collect_all(now, since):
    """(항목 목록, 실패한 사이트별 오류) — 한 사이트가 막혀도 나머지는 보고한다"""
    jobs = [("EDCF", edcf), ("수출입은행", exim), ("KSP", ksp),
            ("나라장터", lambda: g2b(os.environ.get("G2B_API_KEY"), since, now)),
            ("해외건설협회", icak), ("엔지니어링협회", kenca)]
    items, errors = [], {}
    for name, fn in jobs:
        try:
            got = fn()
            if not got and name not in ("나라장터", "엔지니어링협회"):   # 둘은 걸러서 0건인 날도 있다
                raise RuntimeError("게시글을 하나도 읽지 못했습니다 (홈페이지 구조 변경 가능성)")
            items += got
        except Exception as e:  # noqa: BLE001 — 사이트 하나의 실패로 보고 전체가 멈추면 안 됨
            errors[name] = str(e)[:200]
    return items, errors
