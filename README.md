# Telegram News Aggregator

텔레그램 채널/그룹에 올라오는 투자 뉴스를 수집하고, 중복 제거·키워드 추출·섹터 분류·요약 리포트를 생성하는 Python 프로젝트입니다.

## 목적

- 여러 텔레그램 채널의 뉴스를 한 번에 수집
- 같은 뉴스 반복 노출 제거
- 종목명/티커/섹터/핵심 키워드 추출
- 중요도 점수화
- OpenAI 요약 옵션 적용
- 결과를 콘솔, Markdown 파일, 텔레그램 봇 메시지로 출력

## 구조

```text
telegram_news_aggregator/
  src/telegram_news/
    app.py              # CLI 진입점
    settings.py         # 환경변수/설정 로더
    telegram_client.py  # Telethon 수집
    store.py            # SQLite 저장소
    normalizer.py       # 텍스트 정규화/중복 제거
    extractor.py        # 키워드/섹터/티커 추출
    summarizer.py       # OpenAI 요약 또는 로컬 요약
    report.py           # Markdown 리포트 생성
    notifier.py         # 텔레그램 봇 발송
  config/
    channels.example.yaml
  scripts/
    run_once.py
    init_db.py
  tests/
```

## 1. 설치

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

패키지 설치:

```bash
pip install -r requirements.txt
```

## 2. 텔레그램 API 키 발급

1. https://my.telegram.org 접속
2. API development tools 진입
3. `api_id`, `api_hash` 발급
4. `.env.example`을 `.env`로 복사
5. `.env`에 값 입력

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
copy .env.example .env
```

## 3. 채널 설정

`config/channels.example.yaml`을 복사합니다.

```bash
cp config/channels.example.yaml config/channels.yaml
```

Windows PowerShell:

```powershell
copy config\channels.example.yaml config\channels.yaml
```

예시:

```yaml
channels:
  - name: "국내증시뉴스"
    username: "some_public_channel"
    category: "korea_stock"
  - name: "크립토뉴스"
    username: "some_crypto_channel"
    category: "crypto"
```

`username`에는 텔레그램 채널의 공개 username을 넣습니다. 비공개 채널은 사용자가 해당 계정으로 가입되어 있어야 하고, Telethon에서 접근 가능한 entity 값을 확인해야 합니다.

## 4. DB 초기화

```bash
python scripts/init_db.py
```

## 5. 1회 실행

```bash
python scripts/run_once.py run --hours 6
```

또는 패키지 CLI:

```bash
python -m telegram_news.app collect --hours 6
python -m telegram_news.app report --hours 6
python -m telegram_news.app run --hours 6
```

## 6. 텔레그램으로 요약 발송

`.env`에 아래 값을 넣습니다.

```env
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_TARGET_CHAT_ID=123456789
```

그 다음:

```bash
python -m telegram_news.app run --hours 6 --send
```

## 7. OpenAI 요약 사용

`.env`에 아래 값을 넣습니다.

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini
```

API 키가 없으면 로컬 규칙 기반 요약으로 동작합니다.

## 8. 보안 주의

`.env`, `*.session`, `data/*.db`는 Git에 올리면 안 됩니다.

이미 `.gitignore`에 포함되어 있습니다.

## 9. 출력 예시

```text
[텔레그램 뉴스 종합] 2026-05-16 17:30 KST / 최근 6시간

1. 반복 키워드
- 원전/SMR: 8건
- 전력기기: 5건
- 로봇: 4건
- 비트코인 ETF: 3건

2. 중요 뉴스
- 복수 채널에서 원전 기자재 수주 기대 뉴스 반복
- 특정 종목명 동반 출현
- 다만 급등 후 추격 구간이면 신규 진입 부적합

3. 매매 관점
- 뉴스 강도 높음 + 거래대금 동반 종목만 후보
- 뉴스만 있고 거래대금 없는 종목 제외
- 장대양봉 추격 금지, 눌림/재돌파 확인
```

## 10. 한계

이 프로젝트는 뉴스 수집·정리 도구입니다. 매수/매도 자동 실행 기능은 포함하지 않습니다. 가격·거래량·수급 검증은 별도 시세 API를 붙여 확장해야 합니다.
