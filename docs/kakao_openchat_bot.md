# Kakao Open Chat Bot Adapter

## Purpose

This adapter connects a Kakao Open Chat automation app to the Render news API.

Supported behavior:

1. When a user types `뉴스`, the bot fetches `https://telegram-news-bot-api.onrender.com/api/news.txt` and replies in the room.
2. At configured KST times, the bot automatically sends the latest news report to rooms where it has already seen activity.

## Important limitation

Kakao Developers' official KakaoTalk Message REST API is not an Open Chat room bot API. It supports user-consented KakaoTalk message sending flows, but it does not provide a normal official API for reading Open Chat room messages and posting replies as a room bot.

Therefore this adapter is intended for Android/PC message-bot automation apps that can:

- read Open Chat messages, and
- call a JavaScript `response(...)` function with a `replier` object.

## Files

```text
adapters/kakao_openchat_bot.js
```

## Render API URL

```text
https://telegram-news-bot-api.onrender.com
```

Manual command endpoint:

```text
GET https://telegram-news-bot-api.onrender.com/api/news.txt
```

Optional refresh endpoint:

```text
POST https://telegram-news-bot-api.onrender.com/api/refresh
```

## Basic setup

1. Install or open your Android KakaoTalk bot automation app.
2. Create a script for the target Open Chat room.
3. Copy the full contents of `adapters/kakao_openchat_bot.js` into the script editor.
4. Set `TARGET_ROOMS` if you want to restrict sending to specific rooms.
5. Save/reload the script.
6. Type `뉴스` in the Open Chat room.

## Security key

If Render has no `NEWS_BOT_API_KEY`, leave this empty:

```javascript
API_KEY: ""
```

If Render has `NEWS_BOT_API_KEY`, set the same value:

```javascript
API_KEY: "your-secret-key"
```

## Room restriction

All rooms:

```javascript
TARGET_ROOMS: []
```

Specific rooms only:

```javascript
TARGET_ROOMS: ["방 이름 1", "방 이름 2"]
```

The room name must exactly match the room name passed by the bot app.

## Auto-send schedule

Default KST schedule:

```javascript
SCHEDULES_KST: [
  { time: "08:35", label: "한국 장전" },
  { time: "15:50", label: "한국 장마감" },
  { time: "16:55", label: "미국 프리장" },
  { time: "05:20", label: "미국 장마감" }
]
```

Auto-send only works after the bot has seen at least one message in the room because the script stores the room's latest `replier` object.

If your bot app blocks background JavaScript threads, manual `뉴스` replies will still work, but auto-send should be configured using the app's own scheduler if it has one.

## Freshness mode

Default:

```javascript
AUTO_REFRESH_BEFORE_SEND: false
```

This means the bot sends the latest cached report. The cache is updated by:

- GitHub Actions scheduled workflow, and/or
- Render `/api/refresh` calls.

If you want the bot to force-refresh before every send:

```javascript
AUTO_REFRESH_BEFORE_SEND: true
```

Caution: Render free plan may be slow, and `/api/refresh` can take a long time because it collects Telegram messages and generates a report.

## Test checklist

1. Test Render API directly:

```bash
curl https://telegram-news-bot-api.onrender.com/health
curl https://telegram-news-bot-api.onrender.com/api/news.txt
```

2. Paste the script into the bot app.
3. Type `뉴스` in the Open Chat room.
4. Confirm the report is split into multiple messages if it is long.
5. Leave the bot app running and verify one scheduled time.

## Troubleshooting

### `뉴스 리포트 조회 실패: HTTP 401`

Render has `NEWS_BOT_API_KEY` set, but the script's `API_KEY` is empty or different.

### `뉴스 리포트 조회 실패: timeout`

Render free service may be asleep or `/api/refresh` is too slow. First open:

```text
https://telegram-news-bot-api.onrender.com/health
```

Then retry.

### Manual command works, auto-send does not

The bot app likely does not allow background JS threads, or the script has not yet stored a `replier` for that room. Send any message in the room once, then type `뉴스`. If automatic schedule still does not work, use the app's own scheduling feature to call the same `sendNews(...)` behavior.
