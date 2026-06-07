/*
 * MessengerBotR source.js for Kakao Open Chat.
 * Commands:
 * - 뉴스 / 시황 / 브리핑
 * - 시세 삼성전자 / 시세 005930 / 시세 NVDA
 * - 생년월일 1987-12-28 08:30 여
 * - 사주 질문 / 타로 질문
 */

var API_URL = "https://telegram-news-bot-api.onrender.com/api/bot-command";
var CHUNK_SIZE = 900;

function trimText(value) {
  return String(value || "").replace(/^\s+|\s+$/g, "");
}

function shouldHandle(msg) {
  var q = trimText(msg).toLowerCase();
  if (!q) return false;
  if (q === "뉴스" || q === "/뉴스" || q === "!뉴스" || q === "news" || q === "/news") return true;
  if (q === "시황" || q === "브리핑" || q === "도움" || q === "도움말" || q === "help" || q === "/help") return true;
  if (q.indexOf("시세") === 0 || q.indexOf("quote") === 0) return true;
  if (q.indexOf("생년월일") === 0 || q.indexOf("생일") === 0 || q.indexOf("출생") === 0 || q.indexOf("사주등록") === 0) return true;
  if (q.indexOf("사주") === 0 || q.indexOf("운세") === 0 || q.indexOf("타로") === 0) return true;
  return false;
}

function escapeJson(value) {
  return String(value || "")
    .replace(/\\/g, "\\\\")
    .replace(/"/g, "\\\"")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r")
    .replace(/\t/g, "\\t");
}

function fetchBotMessage(msg, sender, room) {
  var userId = String(room || "") + ":" + String(sender || "");
  var payload = "{\"message\":\"" + escapeJson(msg) + "\",\"user_id\":\"" + escapeJson(userId) + "\"}";
  var body = org.jsoup.Jsoup.connect(API_URL)
    .ignoreContentType(true)
    .ignoreHttpErrors(true)
    .timeout(30000)
    .header("Accept", "application/json")
    .header("Content-Type", "application/json")
    .requestBody(payload)
    .method(org.jsoup.Connection.Method.POST)
    .execute()
    .body();

  try {
    var data = JSON.parse(String(body || ""));
    if (data && typeof data.message === "string" && trimText(data.message)) {
      return trimText(data.message);
    }
  } catch (e) {
    Api.showToast("bot json parse 실패: " + e);
  }

  return "응답 없음";
}

function splitText(text) {
  var chunks = [];
  var remaining = trimText(text);
  var cut;

  if (!remaining) return ["응답 없음"];

  while (remaining.length > CHUNK_SIZE) {
    cut = remaining.lastIndexOf("\n", CHUNK_SIZE);
    if (cut < 300) cut = CHUNK_SIZE;
    chunks.push(trimText(remaining.substring(0, cut)));
    remaining = trimText(remaining.substring(cut));
  }

  if (remaining.length > 0) chunks.push(remaining);
  return chunks;
}

function safeReply(room, replier, text) {
  var out = String(text || "응답 없음");

  try {
    if (replier && typeof replier.reply === "function") {
      replier.reply(out);
      return true;
    }
  } catch (e1) {
    Api.showToast("replier.reply 실패: " + e1);
  }

  try {
    if (typeof Api !== "undefined" && Api.replyRoom) {
      Api.replyRoom(String(room), out);
      return true;
    }
  } catch (e2) {
    Api.showToast("Api.replyRoom 실패: " + e2);
  }

  return false;
}

function sendLong(room, replier, text) {
  var chunks = splitText(text);
  var i;
  var prefix;

  for (i = 0; i < chunks.length; i++) {
    prefix = chunks.length > 1 ? "(" + (i + 1) + "/" + chunks.length + ")\n" : "";
    safeReply(room, replier, prefix + chunks[i]);
    if (i < chunks.length - 1) java.lang.Thread.sleep(700);
  }
}

function response(room, msg, sender, isGroupChat, replier, imageDB, packageName) {
  var text;

  if (!shouldHandle(msg)) return;

  try {
    text = fetchBotMessage(msg, sender, room);
  } catch (e) {
    Api.showToast("봇 API 실패: " + e);
    text = "서버 응답 실패";
  }

  sendLong(room, replier, text);
}
