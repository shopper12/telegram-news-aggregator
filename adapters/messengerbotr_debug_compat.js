/*
 * MessengerBotR compatibility debug script.
 *
 * Paste this entire file into MessengerBotR first.
 * If this does not reply, the problem is MessengerBotR/KakaoTalk notification connection,
 * not the news API.
 */

function safeReply(room, replier, text) {
  try {
    if (replier && replier.reply) {
      replier.reply(text);
      return;
    }
  } catch (e1) {
    // fallback below
  }

  try {
    if (typeof Api !== "undefined" && Api.replyRoom) {
      Api.replyRoom(room, text);
      return;
    }
  } catch (e2) {
    // no fallback
  }
}

function response(room, msg, sender, isGroupChat, replier, imageDB, packageName) {
  safeReply(
    room,
    replier,
    "[DEBUG OK]\n" +
    "room=" + room + "\n" +
    "sender=" + sender + "\n" +
    "msg=" + msg + "\n" +
    "isGroupChat=" + isGroupChat + "\n" +
    "packageName=" + packageName
  );
}
