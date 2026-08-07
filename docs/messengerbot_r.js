var API_BASE = "https://telegram-news-bot-api.onrender.com";
var API_PATH = "/reply";
var RAW_REPORT_URL = "https://raw.githubusercontent.com/shopper12/telegram-news-aggregator/main/reports/latest_report.json";
var API_TIMEOUT_MS = 180000;
var FALLBACK_TIMEOUT_MS = 15000;
var MAX_REPORT_AGE_MS = 2 * 60 * 60 * 1000;

function enc(value) {
    return java.net.URLEncoder.encode(String(value), "UTF-8");
}

function isBotCommand(message) {
    var text = String(message || "").trim();
    return text === "봇" ||
        text.indexOf("봇 ") === 0 ||
        text.indexOf("봇:") === 0 ||
        text.indexOf("봇아 ") === 0;
}

function commandBody(message) {
    var text = String(message || "").trim();
    if (text === "봇") return "도움말";
    if (text.indexOf("봇아 ") === 0) return text.substring(3).trim();
    if (text.indexOf("봇 ") === 0) return text.substring(2).trim();
    if (text.indexOf("봇:") === 0) return text.substring(2).trim();
    return text;
}

function isNewsCommand(message) {
    var body = commandBody(message).replace(/\s+/g, "").toLowerCase();
    return body === "뉴스" || body === "/뉴스" || body === "!뉴스" ||
        body === "news" || body === "/news" || body === "시황" ||
        body === "브리핑" || body === "뉴스갱신" ||
        body === "뉴스새로고침" || body === "뉴스업데이트";
}

function requestUrl(url, timeoutMs) {
    var response = org.jsoup.Jsoup
        .connect(url)
        .ignoreContentType(true)
        .ignoreHttpErrors(true)
        .timeout(timeoutMs)
        .header("User-Agent", "MessengerBotR/2.0")
        .method(org.jsoup.Connection.Method.GET)
        .execute();

    var statusCode = response.statusCode();
    var body = response.body();
    if (statusCode < 200 || statusCode >= 300) {
        throw new Error("HTTP " + statusCode + (body ? "\n" + body : ""));
    }
    if (!body || !String(body).trim()) {
        throw new Error("empty response");
    }
    return String(body);
}

function requestBot(message, userId) {
    var url = API_BASE + API_PATH +
        "?message=" + enc(message) +
        "&sender=" + enc(userId) +
        "&_t=" + String(new Date().getTime());
    return requestUrl(url, API_TIMEOUT_MS);
}

function parseGeneratedAt(value) {
    var text = String(value || "").trim();
    if (!text) return NaN;
    if (!/[zZ]|[+-]\d\d:\d\d$/.test(text)) {
        text += "Z";
    }
    return Date.parse(text);
}

function latestReportFromGithub() {
    var url = RAW_REPORT_URL + "?t=" + String(new Date().getTime());
    var raw = requestUrl(url, FALLBACK_TIMEOUT_MS);
    var payload = new org.json.JSONObject(raw);
    var report = payload.optString("report", "");
    var generatedAt = payload.optString("generated_at", "");

    if (!report || !String(report).trim()) {
        throw new Error("GitHub latest_report.json has no report");
    }

    var generatedMs = parseGeneratedAt(generatedAt);
    if (!isNaN(generatedMs)) {
        var age = new Date().getTime() - generatedMs;
        if (age > MAX_REPORT_AGE_MS) {
            return "⚠️ 최신 뉴스 리포트가 아직 갱신되지 않았습니다.\n" +
                "마지막 저장 시각: " + generatedAt + "\n" +
                "오래된 뉴스 본문은 표시하지 않습니다.";
        }
    }
    return String(report);
}

function response(
    room,
    msg,
    sender,
    isGroupChat,
    replier,
    imageDB,
    packageName
) {
    if (packageName && packageName !== "com.kakao.talk") {
        return;
    }
    if (!isBotCommand(msg)) {
        return;
    }

    var userId = String(room) + "::" + String(sender);
    try {
        replier.reply(requestBot(String(msg), userId));
        return;
    } catch (serverError) {
        if (isNewsCommand(msg)) {
            try {
                replier.reply(latestReportFromGithub());
                return;
            } catch (fallbackError) {
                replier.reply(
                    "뉴스봇 서버와 최신 리포트 연결이 모두 실패했습니다.\n" +
                    "Render: " + String(serverError) + "\n" +
                    "GitHub: " + String(fallbackError)
                );
                return;
            }
        }

        replier.reply(
            "뉴스봇 서버 연결 실패\n" +
            String(serverError) +
            "\n\n뉴스 명령은 GitHub 최신 리포트로 자동 우회하지만, " +
            "시세·사주 등 서버 명령은 Render가 깨어난 뒤 다시 입력하세요."
        );
    }
}
