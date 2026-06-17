# ChatGPT briefing 자동 POST

## 목적

`reports/app_recommendations.json`이 갱신되면 GitHub Actions가 해당 JSON을 Render API의 `/api/recommendations`로 POST한다. 카카오/메신저에서 `봇 추천`을 입력하면 이 데이터가 표시된다.

## 동작 흐름

```text
ChatGPT 브리핑
  -> reports/app_recommendations.json 갱신
  -> GitHub Actions: Publish Chat Cards 실행
  -> Render /api/recommendations 로 POST
  -> 카카오/메신저 `봇 추천` 출력
```

## 필요한 GitHub Actions Secrets

Repository Settings -> Secrets and variables -> Actions -> New repository secret 에 아래 값을 넣는다.

```env
CHAT_PICKS_API_KEY=Render의 CHAT_PICKS_API_KEY와 같은 값
CHAT_PICKS_POST_URL=https://telegram-news-bot-api.onrender.com/api/recommendations
```

`CHAT_PICKS_POST_URL` 대신 `RENDER_API_BASE_URL=https://telegram-news-bot-api.onrender.com`만 넣어도 된다. 둘 다 있으면 `CHAT_PICKS_POST_URL`을 우선 사용한다.

## 필요한 Render 환경변수

telegram-news-aggregator Render 서비스에 아래 값을 넣는다.

```env
CHAT_PICKS_API_KEY=GitHub Actions secret과 같은 값
CHAT_PICKS_PATH=/tmp/chat_picks.json
```

`CHAT_PICKS_PATH`는 선택값이다. 생략하면 기본값 `/tmp/chat_picks.json`을 쓴다.

## 브리핑 JSON 파일 형식

```json
{
  "briefing_datetime_kst": "2026-06-17 09:05",
  "mode": "morning",
  "source": "chatgpt_market_briefing",
  "recommendations": [
    {
      "asset_name": "SK하이닉스",
      "ticker": "000660",
      "market": "KR",
      "direction": "long",
      "basis_price": 0,
      "basis_price_currency": "KRW",
      "basis_timestamp_kst": "2026-06-17 09:05",
      "basis_source": "verified source",
      "entry": "",
      "stop": "",
      "target1": "",
      "target2": "",
      "invalidation": "",
      "holding_period": "",
      "reason": "",
      "risk": "",
      "strategy_rule_applied": ""
    }
  ]
}
```

recommendations가 비어 있으면 workflow는 성공으로 종료하고 POST하지 않는다.

## 수동 실행

Actions 탭에서 `Publish Chat Cards` workflow를 선택하고 `Run workflow`를 누르면 현재 `reports/app_recommendations.json`을 다시 POST한다.
