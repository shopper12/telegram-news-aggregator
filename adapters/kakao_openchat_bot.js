/*
 * Kakao Open Chat adapter script for MessengerBotR / Android message-bot style apps.
 *
 * Stable mode:
 * - Reply with the latest news report when a user types "뉴스".
 * - No Java Thread scheduler. Some MessengerBotR/Rhino builds fail to compile Java Thread code.
 *
 * Auto-send:
 * - This file exposes checkAutoSendForRoom(room, replier).
 * - If your bot app has its own scheduler/timer feature, call that function there.
 */

var CONFIG = {
  API_BASE_URL: "https://telegram-news-bot-api.onrender.com",
  API_KEY: "", // Optional. Fill only when NEWS_BOT_API_KEY is set on Render.
  COMMANDS: ["뉴스", "/뉴스", "news", "/news"],
  TARGET_ROOMS: [], // Empty = all rooms. Example: ["내 오픈채팅방 이름"]
  CHUNK_SIZE: 900,
  REQUEST_TIMEOUT_MS: 30000,
  AUTO_REFRESH_BEFORE_SEND: false,
  SCHEDULES_KST: [
    { time: "08:35", label: "한국 장전" },
    { time: "15:50", label: "한국 장마감" },
    { time: "16:55", label: "미국 프리장" },
    { time: "05:20", label: "미국 장마감" }
  ]
};

var lastAutoSentKeys = {};

function isTargetRoom(room) {
  if (!CONFIG.TARGET_ROOMS || CONFIG.TARGET_ROOMS.length === 0) return true;
  return CONFIG.TARGET_ROOMS.indexOf(String(room)) >= 0;
}

function trimString(value) {
  return String(value || "").replace(/^\s+|\s+$/g, "");
}

function isNewsCommand(msg) {
  var normalized = trimString(msg).toLowerCase();
  return CONFIG.COMMANDS.indexOf(normalized) >= 0;
}

function addHeaders(conn) {
  conn.header("Accept", "text/plain");
  if (CONFIG.API_KEY && trimString(CONFIG.API_KEY).length > 0) {
    conn.header("x-api-key", trimString(CONFIG.API_KEY));
  }
  return conn;
}

function httpGetText(url) {
  var conn = org.jsoup.Jsoup.connect(url)
    .ignoreContentType(true)
    .ignoreHttpErrors(true)
    .timeout(CONFIG.REQUEST_TIMEOUT_MS);
  addHeaders(conn);
  var response = conn.execute();
  var status = response.statusCode();
  var body = response.body();
  if (status < 200 || status >= 300) {
    throw new Error("HTTP " + status + ": " + body.substring(0, 300));
  }
  return body;
}

function httpPostJson(url, jsonText) {
  var conn = org.jsoup.Jsoup.connect(url)
    .ignoreContentType(true)
    .ignoreHttpErrors(true)
    .timeout(CONFIG.REQUEST_TIMEOUT_MS)
    .method(org.jsoup.Connection.Method.POST)
    .header("Content-Type", "application/json");
  addHeaders(conn);
  var response = conn.requestBody(jsonText).execute();
  var status = response.statusCode();
  var body = response.body();
  if (status < 200 || status >= 300) {
    throw new Error("HTTP " + status + ": " + body.substring(0, 300));
  }
  return body;
}

function refreshLatestReport() {
  return httpPostJson(
    CONFIG.API_BASE_URL + "/api/refresh",
    '{"hours":6,"limit":15,"briefing_kind":"regular"}'
  );
}

function fetchLatestReportText() {
  return httpGetText(CONFIG.API_BASE_URL + "/api/news.txt");
}

function splitText(text, chunkSize) {
  var chunks = [];
  var remaining = trimString(text);
  var cut;
  if (!remaining) return ["뉴스 리포트가 비어 있습니다."];
  while (remaining.length > chunkSize) {
    cut = remaining.lastIndexOf("\n", chunkSize);
    if (cut < Math.floor(chunkSize * 0.5)) cut = chunkSize;
    chunks.push(trimString(remaining.substring(0, cut)));
    remaining = trimString(remaining.substring(cut));
  }
  if (remaining.length > 0) chunks.push(remaining);
  return chunks;
}

function replyLong(replier, text) {
  var chunks = splitText(text, CONFIG.CHUNK_SIZE);
  var i;
  var prefix;
  for (i = 0; i < chunks.length; i++) {
    prefix = chunks.length > 1 ? "(" + (i + 1) + "/" + chunks.length + ")\n" : "";
    replier.reply(prefix + chunks[i]);
    if (i < chunks.length - 1) java.lang.Thread.sleep(700);
  }
}

function sendNews(room, replier, reason) {
  var report;
  var header;
  if (!isTargetRoom(room)) return;
  try {
    if (CONFIG.AUTO_REFRESH_BEFORE_SEND) {
      refreshLatestReport();
    }
    report = fetchLatestReportText();
    header = reason ? "[" + reason + "]\n" : "";
    replyLong(replier, header + report);
  } catch (e) {
    replier.reply("뉴스 리포트 조회 실패: " + e.message);
  }
}

function getKstNowParts() {
  var tz = java.util.TimeZone.getTimeZone("Asia/Seoul");
  var cal = java.util.Calendar.getInstance(tz);
  var year = cal.get(java.util.Calendar.YEAR);
  var month = cal.get(java.util.Calendar.MONTH) + 1;
  var day = cal.get(java.util.Calendar.DAY_OF_MONTH);
  var hour = cal.get(java.util.Calendar.HOUR_OF_DAY);
  var minute = cal.get(java.util.Calendar.MINUTE);
  var dow = cal.get(java.util.Calendar.DAY_OF_WEEK); // 1=Sun, 2=Mon ... 7=Sat
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

function checkAutoSendForRoom(room, replier) {
  var now;
  var i;
  var item;
  var key;
  if (!isTargetRoom(room)) return;
  now = getKstNowParts();
  if (!isWeekdayKst(now.dayOfWeek)) return;

  for (i = 0; i < CONFIG.SCHEDULES_KST.length; i++) {
    item = CONFIG.SCHEDULES_KST[i];
    if (now.time !== item.time) continue;
    key = now.dateKey + "_" + String(room) + "_" + item.time + "_" + item.label;
    if (lastAutoSentKeys[key]) continue;
    lastAutoSentKeys[key] = true;
    sendNews(room, replier, "자동발송: " + item.label);
  }
}

function handleMessage(room, msg, replier) {
  if (!isTargetRoom(room)) return;

  if (isNewsCommand(msg)) {
    sendNews(room, replier, "수동 요청");
    return;
  }

  // This is not a real timer. It only checks schedule when any message arrives.
  // For exact auto-send, use the bot app's own scheduler/timer to call checkAutoSendForRoom(room, replier).
  checkAutoSendForRoom(room, replier);
}

function response(room, msg, sender, isGroupChat, replier, imageDB, packageName) {
  handleMessage(room, msg, replier);
}
