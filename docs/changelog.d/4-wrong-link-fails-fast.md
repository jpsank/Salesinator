- **A bot sent to a call that is already live now gives up on a wrong link within seconds.** `POST /bots` accepts
  `meeting_in_progress: true` (carried to the bot as `meetingInProgress` on `invocation.v1`, a back-compatible optional
  field). Zoom shows the same "Error" page for a wrong link and for a host who hasn't started the meeting; a bot whose caller
  says the call is live now ends the join as `validation_error` after three looks, which the join-retry never re-spawns.
  Scheduled and calendar joins don't set it and keep waiting for the host. Vexa Capture sets it, and captures the call's audio
  on the Mac when the bot ends without ever getting in.
  Seen live: a bot sent to a dead Zoom link failed as `validation_error` 14 s after it was requested, nothing re-spawned it, and
  Vexa Capture started capturing the call's audio a second later.
