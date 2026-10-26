"""새 게시글 목록을 한글 문서(.hwpx)로 만든다."""
from collections import OrderedDict

from hwpx import HwpxDocument

SITE_ORDER = ["EDCF", "수출입은행", "KSP", "나라장터", "해외건설협회", "엔지니어링협회"]
HEAD_FILL = "#DCE6F2"


def build(path, title, period, items, errors, note=""):
    doc = HwpxDocument.new()
    doc.set_page_margins(left=4252, right=4252, top=4252, bottom=4252)   # 약 15mm (1mm ≈ 283.5)
    bold = doc.ensure_run_style(bold=True)
    big = doc.ensure_run_style(bold=True, size=16)
    small = doc.ensure_run_style(size=9, color="#555555")

    doc.add_paragraph(title, char_pr_id_ref=big)
    doc.add_paragraph(f"대상 기간: {period}", char_pr_id_ref=small)
    doc.add_paragraph("")

    by_site = OrderedDict((s, []) for s in SITE_ORDER)
    for it in items:
        by_site.setdefault(it["site"], []).append(it)

    # 요약표: 사이트별 새 글 수
    doc.add_paragraph("■ 요약", char_pr_id_ref=bold)
    summary = doc.add_table(len(by_site) + 1, 3)
    summary.set_column_widths([3, 2, 7])
    for c, h in enumerate(("사이트", "새 글", "게시판별")):
        summary.set_cell_text(0, c, h)
        summary.set_cell_shading(0, c, HEAD_FILL)
    for r, (site, its) in enumerate(by_site.items(), 1):
        boards = OrderedDict()
        for it in its:
            boards[it["board"]] = boards.get(it["board"], 0) + 1
        detail = "접속 실패" if site in errors else (", ".join(f"{b} {n}" for b, n in boards.items()) or "-")
        summary.set_cell_text(r, 0, site)
        summary.set_cell_text(r, 1, f"{len(its)}건")
        summary.set_cell_text(r, 2, detail)
    doc.add_paragraph("")

    for site, its in by_site.items():
        if not its:
            continue
        doc.add_paragraph(f"■ {site} ({len(its)}건)", char_pr_id_ref=bold)
        t = doc.add_table(len(its) + 1, 4)
        t.set_column_widths([2.2, 7, 1.8, 3.5])
        for c, h in enumerate(("게시판", "제목", "등록일", "비고")):
            t.set_cell_text(0, c, h)
            t.set_cell_shading(0, c, HEAD_FILL)
        for r, it in enumerate(sorted(its, key=lambda x: (x["board"], x["date"])), 1):
            t.set_cell_text(r, 0, it["board"])
            cell = t.cell(r, 1)
            doc.add_hyperlink(it["url"], it["title"], paragraph=cell.paragraphs[0])
            t.set_cell_text(r, 2, it["date"][5:].replace("-", ".") if it["date"] else "")
            t.set_cell_text(r, 3, it["info"])
        doc.add_paragraph("")

    if errors:
        doc.add_paragraph("■ 확인하지 못한 사이트", char_pr_id_ref=bold)
        for site, err in errors.items():
            doc.add_paragraph(f"- {site}: {err}")
        doc.add_paragraph("")

    doc.add_paragraph("제목을 누르면 원문으로 이동합니다." + (f" {note}" if note else ""), char_pr_id_ref=small)
    doc.save_to_path(path)
    return path
