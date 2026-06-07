/*
 * MessengerBotR source.js for Kakao Open Chat.
 * Confirmed legacy signature: response(room, msg, sender, isGroupChat, replier, imageDB, packageName)
 * Commands: 뉴스, /뉴스, !뉴스, news, /news
 */

var API_URL = "https://telegram-news-bot-api.onrender.com/api/news-message";
var CHUNK_SIZE = 900;

function trimText(value) {
  return String(value || "").replace(/^\s+|\s+$/g, "");
}

function isNewsCommand(msg) {
  var q = trimText(msg).toLowerCase();
  return q === "뉴스" || q === "/뉴스" || q === "!뉴스" || q === "news" || q === "/news";
}

function fetchNewsMessage() {
  var body = org.jsoup.Jsoup.connect(API_URL)
    .ignoreContentType(true)
    .ignoreHttpErrors(true)
    .timeout(30000)
    .header("Accept", "application/json")
    .execute()
    .body();

  try {
    var data = JSON.parse(String(body || ""));
    if (data && typeof data.message === "string" && trimText(data.message)) {
      return trimText(data.message);
    }
  } catch (e) {
  }

  return "뉴스 없음";
}

function splitText(text) {
  var chunks = [];
  var remaining = trimText(text);
  var cut;

  if (!remaining) return ["뉴스 없음"];

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
  var out = String(text || "뉴스 없음");

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

  Api.showToast("response 실행됨: " + msg);

  if (!isNewsCommand(msg)) return;

  try {
    text = fetchNewsMessage();
  } catch (e) {
    Api.showToast("뉴스 조회 실패: " + e);
    text = "뉴스 없음";
  }

  sendLong(room, replier, text);
}
