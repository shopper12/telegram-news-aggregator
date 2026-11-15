# Telegram News Aggregator

텔레그램 채널/그룹에 올라오는 투자 뉴스를 수집하고, 중복 제거·키워드 추출·섹터 분류·요약 리포트를 생성하는 Python 프로젝트입니다.

## 목적

- 여러 텔레그램 채널의 뉴스를 한 번에 수집
- 같은 뉴스 반복 노출 제거
- 종목명/티커/섹터/핵심 키워드 추출
- 중요도 점수화
- OpenAI 요약 옵션 적용
- 결과를 콘솔, Markdown 파일, 텔레그램 봇 메시지로 출력
- 카카오/메신저 명령어 `봇 추천`으로 ChatGPT 브리핑 추천종목 표시

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
    chat_bridge.py      # ChatGPT 브리핑 추천종목 조회/저장 브리지
  config/
    channels.example.yaml
  scripts/
    run_once.py
    run_api_v7.py
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

## 7. 카카오/메신저 `봇 추천`

API 서버 모드에서 `봇 추천`, `봇 추천종목`, `오늘추천`을 입력하면 저장된 ChatGPT 브리핑 추천종목을 카카오 simpleText로 반환합니다.

추천 데이터 입력:

```bash
curl -X POST "$RENDER_API_BASE_URL/api/recommendations" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $CHAT_PICKS_API_KEY" \
  --data @app_recommendations.json
```

stock_scanner 앱과 연결하려면 아래처럼 설정합니다.

```env
CHAT_PICKS_SOURCE_URL=https://<stock-scanner-url>/api/recommendations
# 또는
STOCK_SCANNER_RECOMMENDATIONS_URL=https://<stock-scanner-url>/api/recommendations
```

로컬/Render 임시 저장 방식은 다음 변수를 사용합니다.

```env
CHAT_PICKS_API_KEY=
CHAT_PICKS_JSON=
CHAT_PICKS_PATH=/tmp/chat_picks.json
```

## 8. OpenAI 요약 사용

`.env`에 아래 값을 넣습니다.

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini
```

API 키가 없으면 로컬 규칙 기반 요약으로 동작합니다.

## 9. 보안 주의

`.env`, `*.session`, `data/*.db`는 Git에 올리면 안 됩니다.

이미 `.gitignore`에 포함되어 있습니다.

## 10. 출력 예시

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

## 11. 한계

이 프로젝트는 뉴스 수집·정리 도구입니다. 매수/매도 자동 실행 기능은 포함하지 않습니다. 가격·거래량·수급 검증은 별도 시세 API를 붙여 확장해야 합니다.

## 카카오톡 메모 발송 재인증 (invalid_grant / expired_or_invalid_refresh_token)

GitHub Actions 로그에서 이 오류가 나오면 기존 `KAKAO_REFRESH_TOKEN`을 재사용할 수 없습니다.
로컬 Windows PC에서 **카카오 로그인**을 다시 하여 발급받아야 합니다.
OAuth 코드·클라이언트 시크릿·리프레시 토큰을 채팅, GitHub Issue, 커밋, Actions 입력란에 올리지 마세요.

1. [Kakao Developers](https://developers.kakao.com/console/app)에서 해당 앱의 **Kakao Login ON**, `talk_message` 동의 항목,
   REST API 키와 클라이언트 시크릿을 확인합니다.
2. **앱 > 플랫폼 키 > REST API 키 > Redirect URI**에
   `http://localhost:4000/redirect`를 등록합니다. 다른 등록 URI를 쓰는 경우 스크립트 실행 시 정확히 입력하세요.
3. PC의 PowerShell에서 저장소를 열고 다음 명령을 실행합니다.

   ```powershell
   python -m pip install -r requirements.txt
   python scripts/renew_kakao_token.py
   ```

4. 스크립트가 여는 Kakao 로그인 화면에서 승인하고, 브라우저의 최종 리다이렉트 주소 전체를 **로컬 터미널에만** 붙여넣습니다.
   localhost 페이지가 열리지 않아도 주소에 `code`와 `state`가 있으면 됩니다.
5. GitHub CLI(`gh`)가 인증된 PC에서는 스크립트가
   `KAKAO_REFRESH_TOKEN`을 GitHub Actions Secret에 직접 저장합니다.
   `gh`가 없으면 로컬 터미널에서 토큰을 확인하여
   [GitHub Secrets](https://github.com/shopper12/telegram-news-aggregator/settings/secrets/actions)에 직접 저장합니다.
   `KAKAO_CLIENT_SECRET`도 저장소의 기존 앱 설정과 일치해야 합니다.

주의: Kakao가 기존 리프레시 토큰을 **회전**하면 새 토큰을 사용해야 합니다.
이 저장소의 GitHub Actions 토큰 자동 갱신/Secrets 자동 저장은 아직 구현되어 있지 않으며,
회전 시 재인증이 필요할 수 있습니다. Actions 로그에는 민감한 토큰을 출력하지 않습니다.

## 비공개 텔레그램 채널 접근

`config/channels.yaml`의 `https://t.me/c/1579665908`은 초대 링크가 아닌 비공개 채널 주소입니다.
수집용 `TELEGRAM_STRING_SESSION`을 생성한 **동일 계정**이 가입해야 합니다.
수집기는 이제 이 주소를 Telethon 대화 목록에서 채널 ID로 찾습니다.
미가입 또는 접근권한 없음 상태라면 해당 채널만 경고를 남기고 다른 뉴스는 계속 수집합니다.
