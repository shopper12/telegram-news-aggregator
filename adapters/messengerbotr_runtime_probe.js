/*
 * MessengerBotR runtime probe.
 * Purpose: find which response signature your installed MessengerBotR uses.
 *
 * Paste this script alone.
 * Then send "테스트" from another Kakao account/device.
 */

function tryReply(room, replier, text) {
  text = String(text || "PROBE EMPTY");

  try {
    if (replier != null && typeof replier.reply === "function") {
      replier.reply(text);
      return true;
    }
  } catch (e1) {
  }

  try {
    if (typeof Api !== "undefined" && Api.replyRoom) {
      Api.replyRoom(String(room || ""), text);
      return true;
    }
  } catch (e2) {
  }

  return false;
}

function safeToString(x) {
  try {
    if (x === null) return "null";
    if (x === undefined) return "undefined";
    return String(x);
  } catch (e) {
    return "<toString error>";
  }
}

function response(a, b, c, d, e, f, g) {
  var text = "[PROBE]\n";
  text += "arguments.length=" + arguments.length + "\n";
  text += "a=" + safeToString(a) + "\n";
  text += "b=" + safeToString(b) + "\n";
  text += "c=" + safeToString(c) + "\n";
  text += "d=" + safeToString(d) + "\n";
  text += "e=" + safeToString(e) + "\n";
  text += "f=" + safeToString(f) + "\n";
  text += "g=" + safeToString(g) + "\n";

  // Legacy style likely: a=room, b=msg, e=replier.
  if (tryReply(a, e, text)) return;

  // Some MessengerBotR builds provide a single params object with reply method.
  if (tryReply("", a, text)) return;

  // Last resort: if Api.replyRoom exists and a looks like a room string.
  tryReply(a, null, text);
}
