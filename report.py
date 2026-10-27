"""새 게시글 목록을 PDF 보고서로 만든다. 한글 글꼴: GitHub(우분투)는 나눔고딕(fonts-nanum), 윈도우는 맑은 고딕."""
import os
import re
from collections import OrderedDict
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SITE_ORDER = ["EDCF", "수출입은행", "KSP", "나라장터", "해외건설협회", "엔지니어링협회"]
FONTS = [("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
         ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf")]
# 사업 참여 기회(컨설턴트 참여희망 신청·입찰공고 등)는 맨 위 '★ 사업참여 기회'에 한 번 더 모아 보인다
OPPORTUNITY = re.compile(r"컨설턴트|참여\s*(희망|의향)|참여\s*기업|참가\s*신청|Long\s*-?list|롱리스트|Short\s*-?list|숏리스트|"
                         r"입찰|본구매|PMA|F/S|타당성\s*조사|EOI|관심\s*표명|사전\s*자격|추천\s*(요청|신청|안내)", re.I)
HEAD_FILL = colors.HexColor("#DCE6F2")
STAR_FILL = colors.HexColor("#FFF4D6")
LINK = colors.HexColor("#1F4E99")


def _fonts():
    for regular, bold in FONTS:
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("KR", regular))
            pdfmetrics.registerFont(TTFont("KR-B", bold))
            return
    raise RuntimeError("한글 글꼴을 찾지 못했습니다 (우분투: sudo apt-get install fonts-nanum)")


def _styles():
    s = lambda name, **kw: ParagraphStyle(name, **{"fontName": "KR", "fontSize": 8.5, "leading": 11,
                                                   "wordWrap": "CJK", **kw})
    return {"title": s("title", fontSize=15, leading=20, fontName="KR-B"),
            "small": s("small", fontSize=8, textColor=colors.HexColor("#555555")),
            "h": s("h", fontSize=11, leading=15, fontName="KR-B", spaceBefore=6, spaceAfter=3),
            "cell": s("cell"), "head": s("head", fontName="KR-B"), "body": s("body", fontSize=9.5, leading=13)}


def is_opportunity(it):
    if "결과" in it["board"]:      # 입찰결과는 이미 끝난 사업
        return False
    return it["board"] in ("입찰공고", "국토교통 ODA") or bool(OPPORTUNITY.search(it["title"]))


def _link(it):
    return f'<link href="{escape(it["url"], {chr(34): "&quot;"})}" color="#{LINK.hexval()[2:]}">{escape(it["title"])}</link>'


def _table(rows, widths, st, fill=HEAD_FILL):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9AA5B1")),
                           ("BACKGROUND", (0, 0), (-1, 0), fill),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
    return t


def build(path, title, period, items, errors, note=""):
    _fonts()
    st = _styles()
    P = lambda text, style="cell": Paragraph(text, st[style])
    by_site = OrderedDict((s, []) for s in SITE_ORDER)
    for it in items:
        by_site.setdefault(it["site"], []).append(it)

    story = [P(escape(title), "title"), P(escape(f"대상 기간: {period}"), "small"), Spacer(1, 4 * mm)]
    picks = [it for it in items if is_opportunity(it)]
    if picks:
        story.append(P(escape(f"★ 사업참여 기회 ({len(picks)}건) — 컨설턴트 참여희망 신청·입찰공고 등"), "h"))
        rows = [[P("사이트·게시판", "head"), P("제목", "head"), P("등록일", "head"), P("비고", "head")]]
        for it in sorted(picks, key=lambda x: x["date"], reverse=True):
            rows.append([P(escape(f'{it["site"]} {it["board"]}')), P(_link(it)),
                         P(it["date"][5:].replace("-", ".") if it["date"] else ""), P(escape(it["info"]))])
        story.append(_table(rows, [30 * mm, 88 * mm, 14 * mm, 48 * mm], st, STAR_FILL))
    story.append(P("■ 요약", "h"))
    rows = [[P("사이트", "head"), P("새 글", "head"), P("게시판별", "head")]]
    for site, its in by_site.items():
        boards = OrderedDict()
        for it in its:
            boards[it["board"]] = boards.get(it["board"], 0) + 1
        detail = "접속 실패" if site in errors else (", ".join(f"{b} {n}" for b, n in boards.items()) or "-")
        rows.append([P(site), P(f"{len(its)}건"), P(escape(detail))])
    story.append(_table(rows, [35 * mm, 18 * mm, 127 * mm], st))

    for site, its in by_site.items():
        if not its:
            continue
        story.append(P(escape(f"■ {site} ({len(its)}건)"), "h"))
        rows = [[P("게시판", "head"), P("제목", "head"), P("등록일", "head"), P("비고", "head")]]
        for it in sorted(its, key=lambda x: (x["board"], x["date"])):
            rows.append([P(escape(it["board"])), P(_link(it)), P(it["date"][5:].replace("-", ".") if it["date"] else ""),
                         P(escape(it["info"]))])
        story.append(_table(rows, [26 * mm, 92 * mm, 14 * mm, 48 * mm], st))

    if errors:
        story.append(P("■ 확인하지 못한 사이트", "h"))
        story += [P(escape(f"- {site}: {err}"), "body") for site, err in errors.items()]

    story += [Spacer(1, 4 * mm), P(escape("제목을 누르면 원문으로 이동합니다." + (f" {note}" if note else "")), "small")]
    SimpleDocTemplate(path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                      bottomMargin=15 * mm, title=title).build(story)
    return path
