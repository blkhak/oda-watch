"""6개 사이트 게시판의 새 글을 모아 PDF 보고서를 텔레그램으로 보낸다.

평일 오전 8:00·오후 5:50(KST)에 보낸다. GitHub 예약 실행은 30~45분씩 늦게 시작하기도 하므로 80분 일찍 깨워
보낼 시각 12분 전까지 기다렸다가 수집하고(WAIT_FOR_SLOT=true), 보낼 시각 1분 전까지 접속이 안 되는 사이트는
더 기다리지 않고 '직접 확인하세요'로 표시해 제시간에 보낸다. 주말·공휴일엔 건너뛰고 다음 보고에 몰아 담는다.
조회는 직전 보고 10분 전부터 겹쳐 하고, 이미 보낸 글(state/seen.json)은 뺀다.

환경변수(GitHub Secrets): TELEGRAM_BOT_TOKEN, G2B_API_KEY (TELEGRAM_CHAT_ID는 없으면 자동으로 찾음)
FORCE=true 이면 주말·공휴일에도 실행 (수동 실행용)
TEST_DAYS=N 이면 시험 보고: 최근 N일 글을 이미 보냈는지와 상관없이 모두 담고, 기록(state)은 바꾸지 않는다
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import holidays

import report
import sources
import telegram

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state", "seen.json")
FIRST_RUN_DAYS = 3      # 첫 실행 땐 최근 며칠 안의 글만 보고 (나머지는 이미 본 것으로 기록)
STALE_DAYS = 30         # 처음 보는 글이라도 이보다 오래된 글은 목록 순서가 바뀐 것으로 보고 뺀다
FORGET_DAYS = 90        # 이 기간 동안 목록에서 안 보인 글은 기록에서 지운다
SEND_TIMES = [(8, 0), (17, 50)]   # 보고 시각 (KST)
OVERLAP = timedelta(minutes=10)   # 직전 보고와 겹쳐 조회하는 시간
COLLECT_LEAD = timedelta(minutes=12)   # 보낼 시각보다 이만큼 먼저 수집을 시작 (접속 재시도 여유)
LATE_BUDGET = timedelta(minutes=5)     # 보낼 시각을 이미 넘겨 시작했거나 수동 실행이면 수집에 쓰는 최대 시간
NOTHING_NEW = "해외사업 관련 추가 게시글 없습니다♥"
SITE_URLS = {"EDCF": "https://www.edcfkorea.go.kr/fe/HPHFFE065M01",
             "수출입은행": "https://www.koreaexim.go.kr/HPHKBI037M01",
             "KSP": "https://www.ksp.go.kr",
             "나라장터": "https://www.g2b.go.kr",
             "해외건설협회": "https://www.icak.or.kr/board/noticeList",
             "엔지니어링협회": "https://www.etis.or.kr/bbsList.do?leftParam=1&topParam=1&boardId=TOTALBBS&categorygroup2=TB150"}


def nothing_new(errors):
    """새 글 0건일 때 보낼 문구. 끝내 접속하지 못한 사이트가 있으면 직접 확인하라고 알린다"""
    if not errors:
        return NOTHING_NEW
    return f"해외사업 관련 추가 게시글 없으나, {'·'.join(errors)}의 경우 접속이 되지 않아 직접 확인하셔야 합니다"


def load_state():
    if not os.path.exists(STATE):
        return None
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)


def is_workday(d):
    return d.weekday() < 5 and d not in holidays.KR(years=d.year)


def pick_new(items, state, now):
    today = now.date()
    first = state is None
    seen = {} if first else state["seen"]
    new = []
    for it in items:
        if it["key"] in seen:
            continue
        d = date.fromisoformat(it["date"]) if it["date"] else None
        limit = FIRST_RUN_DAYS if first else STALE_DAYS
        if d is None and first:
            continue
        if d is None or (today - d).days <= limit:
            new.append(it)
    return new


def update_seen(items, state, now, errors):
    seen = {} if state is None else dict(state["seen"])
    stamp = now.date().isoformat()
    for it in items:
        seen[it["key"]] = stamp          # 마지막으로 목록에서 본 날
    cutoff = (now.date() - timedelta(days=FORGET_DAYS)).isoformat()
    failed = set(errors)
    seen = {k: v for k, v in seen.items() if v >= cutoff or site_of(k) in failed}
    return {"last_run": now.isoformat(timespec="minutes"), "seen": seen}


CHAT_FILE = os.path.join(HERE, "state", "chat_id.txt")


def find_chat(token):
    """TELEGRAM_CHAT_ID Secret이 없으면 봇에게 최근 말을 건 대화방을 찾아 state/chat_id.txt에 기억한다"""
    if os.path.exists(CHAT_FILE):
        with open(CHAT_FILE, encoding="utf-8") as f:
            return f.read().strip()
    ids = telegram.chat_ids(token)
    if len(ids) != 1:
        bot = telegram._call(token, "getMe", {}).get("username")
        sys.exit(f"대화방을 정할 수 없음(찾은 수 {len(ids)}): @{bot} 에게 메시지를 한 번 보내고 다시 실행하세요")
    os.makedirs(os.path.dirname(CHAT_FILE), exist_ok=True)
    with open(CHAT_FILE, "w", encoding="utf-8") as f:
        f.write(str(ids[0][0]))
    return str(ids[0][0])


def site_of(key):
    return {"EDCF": "EDCF", "수출입은행": "수출입은행", "KSP": "KSP", "G2B": "나라장터",
            "ICAK": "해외건설협회", "KENCA": "엔지니어링협회"}.get(key.split("|")[0], "")


def wait_for_slot():
    """가까운 보낼 시각(앞으로 100분 안, 또는 30분 안쪽으로 이미 지난 시각)을 찾아 수집 시작 시각까지 기다린다.
    보낼 시각을 돌려준다 (없으면 None)"""
    now = datetime.now()
    for h, m in SEND_TIMES:
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        until = (target - now).total_seconds()
        if -1800 < until <= 6000:
            wait = (target - COLLECT_LEAD - now).total_seconds()
            if wait > 0:
                print(f"{target:%H:%M} 보고 — 수집 시작까지 {wait / 60:.0f}분 대기")
                time.sleep(wait)
            else:
                print(f"{target:%H:%M} 보고 — 예약 실행이 늦게 시작해 바로 수집")
            return target
    return None


def set_deadline(slot):
    """접속 재시도를 멈출 시각: 보낼 시각 1분 전. 이미 늦었거나 수동 실행이면 지금부터 LATE_BUDGET"""
    now = datetime.now()
    end = slot - timedelta(minutes=1) if slot else now
    if slot is None or slot <= now:
        end = now + LATE_BUDGET
    end = max(end, now + timedelta(minutes=2))   # 막 늦게 시작했어도 한 번씩은 읽어 볼 시간
    sources.DEADLINE = end.timestamp()
    print(f"접속 재시도는 {end:%H:%M:%S}까지")


def main():
    test_days = int(os.environ.get("TEST_DAYS") or 0)
    now = datetime.now()
    if not test_days and not is_workday(now.date()) and os.environ.get("FORCE", "").lower() != "true":
        print("주말·공휴일이라 건너뜀")
        return
    slot = None
    if os.environ.get("WAIT_FOR_SLOT", "").lower() == "true" and not test_days:
        slot = wait_for_slot()
        now = datetime.now()
    set_deadline(slot)
    state = load_state()
    if test_days:
        since = now - timedelta(days=test_days)
    elif state:
        since = datetime.fromisoformat(state["last_run"]) - OVERLAP
    else:
        since = now - timedelta(days=FIRST_RUN_DAYS)
    empty_test = os.environ.get("EMPTY_TEST", "").lower() == "true"   # 새 글 0건 문구 시험 (수집·기록 안 함)
    items, errors = ([], {}) if empty_test else sources.collect_all(now, since)
    if test_days:
        new = [it for it in items if it["date"] and it["date"] >= since.date().isoformat()]
    else:
        new = pick_new(items, state, now)
    period = f"{since:%Y-%m-%d %H:%M} ~ {now:%Y-%m-%d %H:%M}"
    slot = f"시험 · 최근 {test_days}일" if test_days else ("오전" if now.hour < 12 else "오후")
    title = f"ODA·해외사업 게시판 보고 ({now:%Y-%m-%d} {slot})"
    path = None
    if new:     # 새 글이 하나도 없으면 보고서 없이 문구만 보낸다
        os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
        path = os.path.join(HERE, "out", f"게시판보고_{now:%Y%m%d_%H%M}.pdf")
        report.build(path, title, period, new, errors,
                     note=f"첫 보고라 최근 {FIRST_RUN_DAYS}일 안의 글만 담았습니다." if state is None else "")

    counts = {}
    for it in new:
        counts[it["site"]] = counts.get(it["site"], 0) + 1
    lines = [f"새 글 {len(new)}건" + (" — " + ", ".join(f"{s} {n}" for s, n in counts.items()) if counts else "")]
    picks = [it for it in new if report.is_opportunity(it)]
    if picks:
        lines += [f"★ 사업참여 기회 {len(picks)}건"] + [f"· {it['title'][:60]}" for it in picks[:5]]
    lines += [f"⚠ {s} 접속이 되지 않아 확인 못 함 — 직접 확인하세요 {SITE_URLS.get(s, '')}".rstrip() for s in errors]
    print(title, *lines, sep="\n")
    for s, e in errors.items():
        print(f"  {s}: {e}")
    if slot and datetime.now() < slot:   # 일찍 끝났으면 보낼 시각까지 기다렸다 보낸다
        wait = (slot - datetime.now()).total_seconds()
        print(f"{slot:%H:%M}까지 {wait:.0f}초 기다렸다 전송")
        time.sleep(wait)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID") or (find_chat(token) if token else None)
    if token and chat and path:
        telegram.send_file(token, chat, path, caption=title + "\n" + "\n".join(lines))
    elif token and chat:
        telegram.send(token, chat, nothing_new(errors), "(시험 발송)" if empty_test else "",
                      links=[(f"{s} 열기", SITE_URLS[s]) for s in errors if s in SITE_URLS])
    else:
        print("텔레그램 설정이 없어 보내지 않음:", path or nothing_new(errors))
        if os.environ.get("GITHUB_ACTIONS"):
            sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID Secret을 등록하세요")
    if not test_days and not empty_test:
        save_state(update_seen(items, state, now, errors))


if __name__ == "__main__":
    main()
