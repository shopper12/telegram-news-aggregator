/*
 * Minimal MessengerBotR debug script.
 * Purpose: verify that MessengerBotR is receiving KakaoTalk/Open Chat notifications.
 *
 * Test:
 * 1. Paste this file into MessengerBotR instead of the real news bot script.
 * 2. Send a message from ANOTHER Kakao account/device into the Open Chat room.
 * 3. If MessengerBotR is connected correctly, it replies with room/msg/sender.
 */

function response(room, msg, sender, isGroupChat, replier, imageDB, packageName) {
  replier.reply(
    "[DEBUG]\n" +
    "room=" + room + "\n" +
    "sender=" + sender + "\n" +
    "msg=" + msg + "\n" +
    "isGroupChat=" + isGroupChat + "\n" +
    "packageName=" + packageName
  );
}
