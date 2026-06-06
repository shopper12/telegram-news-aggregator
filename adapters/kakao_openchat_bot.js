/*
 * Kakao Open Chat adapter script for Android message-bot style apps.
 *
 * Features:
 * - Reply with the latest news report when a user types "뉴스".
 * - Auto-send the latest news report at configured KST times.
 *
 * Notes:
 * - Official Kakao Developers message APIs do not provide an Open Chat room bot API.
 * - This script is for an Android/PC Open Chat bot adapter that can read messages and reply to a room.
 * - Function signatures differ by bot app. If your app uses a different signature, keep the
 *   handleMessage(room, msg, replier) call and adapt only the wrapper function.
 */

const CONFIG = {
  API_BASE_URL: "https://telegram-news-bot-api.onrender.com",
  API_KEY: "", // Optional. Fill only when NEWS_BOT_API_KEY is set on Render.
  COMMANDS: ["뉴스", "/뉴스", "news", "/news"],
  TARGET_ROOMS: [], // Empty = all rooms. Example: ["내 오픈채팅방 이름"]
  CHUNK_SIZE: 900,
  REQUEST_TIMEOUT_MS: 30000,
  AUTO_SEND_ENABLED: true,
  AUTO_REFRESH_BEFORE_SEND: false, // true이면 /api/refresh 먼저 호출. Render free plan에서는 느릴 수 있음.
  SCHEDULES_KST: [
    { time: "08:35", label: "한국 장전" },
    { time: "15:50", label: "한국 장마감" },
    { time: "16:55", label: "미국 프리장" },
    { time: "05:20", label: "미국 장마감" }
  ]
};

let lastAutoSentKeys = {};
let latestReplierByRoom = {};
let schedulerStarted = false;

function isTargetRoom(room) {
  if (!CONFIG.TARGET_ROOMS || CONFIG.TARGET_ROOMS.length === 0) return true;
  return CONFIG.TARGET_ROOMS.indexOf(String(room)) >= 0;
}

function isNewsCommand(msg) {
  const normalized = String(msg || "").trim().toLowerCase();
  return CONFIG.COMMANDS.indexOf(normalized) >= 0;
}

function makeHeaders() {
  const headers = { "Accept": "text/plain" };
  if (CONFIG.API_KEY && CONFIG.API_KEY.trim().length > 0) {
    headers["x-api-key"] = CONFIG.API_KEY.trim();
  }
  return headers;
}

function httpGetText(url) {
  const conn = org.jsoup.Jsoup.connect(url)
    .ignoreContentType(true)
    .ignoreHttpErrors(true)
    .timeout(CONFIG.REQUEST_TIMEOUT_MS);
  const headers = makeHeaders();
  Object.keys(headers).forEach(function (key) { conn.header(key, headers[key]); });
  const response = conn.execute();
  const status = response.statusCode();
  const body = response.body();
  if (status < 200 || status >= 300) {
    throw new Error("HTTP " + status + ": " + body.substring(0, 300));
  }
  return body;
}

function httpPostJson(url, jsonText) {
  const conn = org.jsoup.Jsoup.connect(url)
    .ignoreContentType(true)
    .ignoreHttpErrors(true)
    .timeout(CONFIG.REQUEST_TIMEOUT_MS)
    .method(org.jsoup.Connection.Method.POST)
    .header("Content-Type", "application/json");
  const headers = makeHeaders();
  Object.keys(headers).forEach(function (key) { conn.header(key, headers[key]); });
  const response = conn.requestBody(jsonText).execute();
  const status = response.statusCode();
  const body = response.body();
  if (status < 200 || status >= 300) {
    throw new Error("HTTP " + status + ": " + body.substring(0, 300));
  }
  return body;
}

function refreshLatestReport() {
  return httpPostJson(
    CONFIG.API_BASE_URL + "/api/refresh",
    JSON.stringify({ hours: 6, limit: 15, briefing_kind: "regular" })
  );
}

function fetchLatestReportText() {
  return httpGetText(CONFIG.API_BASE_URL + "/api/news.txt");
}

function splitText(text, chunkSize) {
  const chunks = [];
  let remaining = String(text || "").trim();
  if (!remaining) return ["뉴스 리포트가 비어 있습니다."];
  while (remaining.length > chunkSize) {
    let cut = remaining.lastIndexOf("\n", chunkSize);
    if (cut < Math.floor(chunkSize * 0.5)) cut = chunkSize;
    chunks.push(remaining.substring(0, cut).trim());
    remaining = remaining.substring(cut).trim();
  }
  if (remaining.length > 0) chunks.push(remaining);
  return chunks;
}

function replyLong(replier, text) {
  const chunks = splitText(text, CONFIG.CHUNK_SIZE);
  for (let i = 0; i < chunks.length; i++) {
    const prefix = chunks.length > 1 ? "(" + (i + 1) + "/" + chunks.length + ")\n" : "";
    replier.reply(prefix + chunks[i]);
    if (i < chunks.length - 1) java.lang.Thread.sleep(700);
  }
}

function sendNews(room, replier, reason) {
  if (!isTargetRoom(room)) return;
  try {
    if (CONFIG.AUTO_REFRESH_BEFORE_SEND) {
      refreshLatestReport();
    }
    const report = fetchLatestReportText();
    const header = reason ? "[" + reason + "]\n" : "";
    replyLong(replier, header + report);
  } catch (e) {
    replier.reply("뉴스 리포트 조회 실패: " + e.message);
  }
}

function handleMessage(room, msg, replier) {
  if (!isTargetRoom(room)) return;
  latestReplierByRoom[String(room)] = replier;
  startSchedulerIfNeeded();
  if (isNewsCommand(msg)) {
    sendNews(room, replier, "수동 요청");
  }
}

function getKstNowParts() {
  const tz = java.util.TimeZone.getTimeZone("Asia/Seoul");
  const cal = java.util.Calendar.getInstance(tz);
  const year = cal.get(java.util.Calendar.YEAR);
  const month = cal.get(java.util.Calendar.MONTH) + 1;
  const day = cal.get(java.util.Calendar.DAY_OF_MONTH);
  const hour = cal.get(java.util.Calendar.HOUR_OF_DAY);
  const minute = cal.get(java.util.Calendar.MINUTE);
  const dow = cal.get(java.util.Calendar.DAY_OF_WEEK); // 1=Sun, 2=Mon ... 7=Sat
  return {
    dateKey: year + "-" + pad2(month) + "-" + pad2(day),
    time: pad2(hour) + ":" + pad2(minute),
    dayOfWeek: dow
  };
}

function pad2(n) {
  return n < 10 ? "0" + n : String(n);
}

function isWeekdayKst(dayOfWeek) {
  return dayOfWeek >= 2 && dayOfWeek <= 6;
}

function checkAutoSend() {
  if (!CONFIG.AUTO_SEND_ENABLED) return;
  const now = getKstNowParts();
  if (!isWeekdayKst(now.dayOfWeek)) return;

  for (let i = 0; i < CONFIG.SCHEDULES_KST.length; i++) {
    const item = CONFIG.SCHEDULES_KST[i];
    if (now.time !== item.time) continue;
    const key = now.dateKey + "_" + item.time + "_" + item.label;
    if (lastAutoSentKeys[key]) continue;
    lastAutoSentKeys[key] = true;

    Object.keys(latestReplierByRoom).forEach(function (room) {
      const replier = latestReplierByRoom[room];
      if (replier) sendNews(room, replier, "자동발송: " + item.label);
    });
  }
}

function startSchedulerIfNeeded() {
  if (schedulerStarted) return;
  schedulerStarted = true;
  try {
    java.lang.Thread(function () {
      while (true) {
        try {
          checkAutoSend();
        } catch (e) {
          // Ignore scheduler loop errors to keep the bot alive.
        }
        java.lang.Thread.sleep(30000);
      }
    }).start();
  } catch (e) {
    // Some bot apps block background threads. In that case command replies still work,
    // and auto-send must be configured in the bot app's own scheduler if available.
  }
}

/*
 * Common wrapper for MessengerBot-style apps.
 * If your app already provides a response(...) function, paste this whole file as-is.
 */
function response(room, msg, sender, isGroupChat, replier, imageDB, packageName) {
  handleMessage(room, msg, replier);
}
