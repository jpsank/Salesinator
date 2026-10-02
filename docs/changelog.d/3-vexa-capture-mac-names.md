- **Vexa Capture: each paired Mac is listed once, by name.** Pairing the same Mac again replaces its earlier key instead of adding
  another "Mac"; the Settings card names each Mac, shows when it connected and was last used, and asks before disconnecting one.
  The app also notices within seconds that its bot has left a call, and treats removing an already-gone bot as done.
  A link left on the clipboard from an earlier call is no longer sent to the bot (only one copied in the last five minutes
  counts), and a bot that has not reached the meeting after a minute (a wrong link) is taken off and the call's audio is captured on the Mac instead. A bot waiting to be admitted is left to wait, with one reminder to admit it.
  The menu says "Vexa's bot is joining your call…" until Vexa reports the bot is in, and a bot that never got in is shown as
  such in Recent calls. `clients/capture-mac/make-local-signing-cert.sh` makes a local signing certificate that `build.sh` then
  uses, so rebuilds keep one identity and macOS stops re-asking for approvals.
  Setup has a row for hearing calls on this Mac (Microphone, Screen & System Audio Recording) with an Allow button, and says what to do when
  macOS shows the permission as on but the app can't use it.
  When a call has no link to find, the app asks Vexa whether a bot is already on a call of that platform — Connect Zoom sends one the
  moment the rep starts a meeting — and, if so, takes it as the call's bot instead of capturing the audio a second time.
  The same goes when a link was found but Vexa answers that a bot is already on the call: the app now shows that bot, and notices when it leaves.
