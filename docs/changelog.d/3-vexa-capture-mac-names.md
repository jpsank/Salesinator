- **Vexa Capture: each paired Mac is listed once, by name.** Pairing the same Mac again replaces its earlier key instead of adding
  another "Mac"; the Settings card names each Mac, shows when it connected and was last used, and asks before disconnecting one.
  The app also notices within seconds that its bot has left a call, and treats removing an already-gone bot as done.
  A link left on the clipboard from an earlier call is no longer sent to the bot (only one copied in the last five minutes
  counts), and a bot that is still outside the call after a minute is taken off and the call's audio is captured on the Mac instead.
  The menu says "Vexa's bot is joining your call…" until Vexa reports the bot is in, and a bot that never got in is shown as
  such in Recent calls. `clients/capture-mac/make-local-signing-cert.sh` makes a local signing certificate that `build.sh` then
  uses, so rebuilds keep one identity and macOS stops re-asking for approvals.
