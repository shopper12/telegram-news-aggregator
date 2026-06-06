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

## Recommended app approach

Use an Android notification-based KakaoTalk bot automation app first. Do not build a native Android app from scratch unless the script-app route fails.

Typical app requirements:

- Android phone with KakaoTalk installed and logged in.
- Notification access permission enabled for the bot app.
- KakaoTalk notification previews enabled so the bot app can read room/message text.
- Background/battery optimization disabled for the bot app and KakaoTalk.
- JavaScript-style script editor with a `response(room, msg, sender, isGroupChat, replier, ...)` callback or equivalent.

Common keywords to search in app stores or APK sources:

```text
메신저봇
메신저봇R
카카오톡 자동응답 봇
KakaoTalk bot
MessengerBot
```

Use a separate Kakao account/device for testing if possible. Notification-based automation can stop after KakaoTalk updates, Android permission changes, battery killing, or bot-app incompatibility.

## Android permissions concept

The bot app reads KakaoTalk notifications through Android notification-listener access and sends replies through the notification reply action when available. A custom native implementation would generally require Android `NotificationListenerService` to receive posted notifications and `RemoteInput` to send reply text through a notification action.

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

## Setup with an Android bot app

1. Install a KakaoTalk-compatible Android message bot automation app.
2. Open Android settings and enable notification access for the bot app.
3. In KakaoTalk, enable notifications for the target Open Chat room.
4. Make sure KakaoTalk notification previews show sender/message content.
5. Disable battery optimization for both KakaoTalk and the bot app.
6. Open the bot app and create a new script/bot.
7. Set the target messenger/package to KakaoTalk if the app asks for it.
8. Copy the full contents of `adapters/kakao_openchat_bot.js` into the script editor.
9. Save/reload/start the script.
10. Type `뉴스` in the Open Chat room.

## Basic setup inside the script

1. Keep this API URL:

```javascript
API_BASE_URL: "https://telegram-news-bot-api.onrender.com"
```

2. If Render has no `NEWS_BOT_API_KEY`, keep this empty:

```javascript
API_KEY: ""
```

3. If Render has `NEWS_BOT_API_KEY`, set the same value:

```javascript
API_KEY: "your-secret-key"
```

4. Restrict rooms if needed:

```javascript
TARGET_ROOMS: ["방 이름 1", "방 이름 2"]
```

All rooms:

```javascript
TARGET_ROOMS: []
```

The room name must exactly match the room name passed by the bot app.

## If the app callback signature differs

The adapter exposes this internal function:

```javascript
handleMessage(room, msg, replier)
```

If your bot app does not use this wrapper:

```javascript
function response(room, msg, sender, isGroupChat, replier, imageDB, packageName) {
  handleMessage(room, msg, replier);
}
```

then keep the full script and only change the bottom wrapper to call:

```javascript
handleMessage(room, msg, replier)
```

with your app's actual room/message/replier variables.

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
3. Send any normal message once in the Open Chat room so the script stores the room/replier.
4. Type `뉴스` in the Open Chat room.
5. Confirm the report is split into multiple messages if it is long.
6. Leave the bot app running and verify one scheduled time.

## Troubleshooting

### Bot does not respond at all

Check:

- The bot app has notification access.
- KakaoTalk notifications are enabled.
- Open Chat room notifications are enabled.
- Notification previews show message content.
- Battery optimization is disabled.
- The script is saved and running.
- The room name in `TARGET_ROOMS` matches exactly, or `TARGET_ROOMS` is empty.

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

## Native Android app fallback

If no script-based bot app works, build a native Android app with:

- `NotificationListenerService` for incoming KakaoTalk notifications.
- Filtering by package name `com.kakao.talk` and room title.
- `RemoteInput` reply action for sending messages.
- Foreground service or WorkManager for schedule reliability.
- HTTP client calling `https://telegram-news-bot-api.onrender.com/api/news.txt`.

This is more reliable to control but takes much longer and may still break if KakaoTalk changes its notification reply behavior.
