# oda-watch

평일 오전 8시·오후 4시(KST)에 아래 게시판의 새 글을 모아 한글 보고서(.hwpx)로 텔레그램에 보낸다.

| 사이트 | 확인하는 게시판 |
|---|---|
| EDCF | 입찰공고, 입찰예정사업, 입찰결과(수주업체 발표 포함), 공지사항, 보도자료 |
| 수출입은행 | 공지사항, 보도자료 |
| KSP | 입찰공고, 소식 |
| 나라장터 | 용역 입찰공고 중 해외 사업(국가명·ODA 등 낱말, KOICA·수은·KIND 등 발주) |
| 해외건설협회 | 입찰공고, 공지사항, 보도자료, 국토교통 ODA, 최신정보, 건설뉴스 |
| 엔지니어링협회(ETIS) | 공지, 공고, 협회소식, 입법·행정예고, 임금실태·통계 |

- 이미 보낸 글은 `state/seen.json`에 기록해 다시 보내지 않는다. 주말·공휴일엔 건너뛰고 다음 보고에 몰아 담는다.
- 사이트 하나가 막혀도 나머지는 보고하고, 실패한 사이트는 보고서와 메시지에 표시한다.
- 나라장터 해외 사업 판별 규칙: `overseas.py`(국가·낱말), `sources.py`의 `NOISE`(국외 연수 등 제외)·`ODA_AGENCIES`

## 설정 (GitHub Secrets)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — stock-watch와 같은 봇 (`python telegram.py <토큰>`으로 chat_id 확인)
- `G2B_API_KEY` — 공공데이터포털 조달청 입찰공고정보서비스 인증키

## 수동 실행
Actions → oda-watch → Run workflow (주말에도 보냄). 로컬: `FORCE=true python main.py` (텔레그램 설정이 없으면 out/에 파일만 만든다)
