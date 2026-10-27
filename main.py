"""6개 사이트 게시판의 새 글을 모아 PDF 보고서를 텔레그램으로 보낸다.

평일 오전 8:00·오후 5:50(KST)에 보낸다. GitHub 예약 실행은 10~30분씩 늦게 시작하므로 40분 일찍 깨워
보낼 시각까지 기다렸다가 수집한다(WAIT_FOR_SLOT=true). 주말·공휴일엔 건너뛰고 다음 보고에 몰아 담는다.
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
NOTHING_NEW = "해외사업 관련 추가 게시글 없습니다♥"


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
    """1시간 안에 보낼 시각이 있으면 그 시각 1분 전까지 기다린다 (수집에 1분쯤 걸림)"""
    now = datetime.now()
    for h, m in SEND_TIMES:
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        wait = (target - now).total_seconds() - 60
        if 0 < wait <= 3600:
            print(f"{target:%H:%M} 보고까지 {wait / 60:.0f}분 대기")
            time.sleep(wait)
            return


def main():
    test_days = int(os.environ.get("TEST_DAYS") or 0)
    now = datetime.now()
    if not test_days and not is_workday(now.date()) and os.environ.get("FORCE", "").lower() != "true":
        print("주말·공휴일이라 건너뜀")
        return
    if os.environ.get("WAIT_FOR_SLOT", "").lower() == "true" and not test_days:
        wait_for_slot()
        now = datetime.now()
    state = load_state()
    if test_days:
        since = now - timedelta(days=test_days)
    elif state:
        since = datetime.fromisoformat(state["last_run"]) - OVERLAP
    else:
        since = now - timedelta(days=FIRST_RUN_DAYS)
    items, errors = sources.collect_all(now, since)
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
    lines += [f"⚠ {s} 확인 실패: {e[:80]}" for s, e in errors.items()]
    print(title, *lines, sep="\n")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID") or (find_chat(token) if token else None)
    if token and chat and path:
        telegram.send_file(token, chat, path, caption=title + "\n" + "\n".join(lines))
    elif token and chat:
        telegram.send(token, chat, NOTHING_NEW, "\n".join(lines[1:]))   # 확인 실패한 사이트는 함께 알린다
    else:
        print("텔레그램 설정이 없어 보내지 않음:", path or NOTHING_NEW)
        if os.environ.get("GITHUB_ACTIONS"):
            sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID Secret을 등록하세요")
    if not test_days:
        save_state(update_seen(items, state, now, errors))


if __name__ == "__main__":
    main()
