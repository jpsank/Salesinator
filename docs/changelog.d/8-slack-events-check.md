- **Settings can now tell you whether Slack is actually sending events.** Reactions (votes, ✅) only work while Slack delivers events to
  Vexa, and when it stopped — Socket Mode had been switched on in the Slack app — nothing said so; reactions just did nothing. Settings →
  Integrations → Slack → **Slack events** shows when the last event arrived and has **Check that events arrive**, which proves delivery end
  to end: the bot reacts 👀 to your latest card, waits for Slack to report it back, and takes it off. A failure names the cause — nothing
  arrived (Event Subscriptions off, a different Request URL, Socket Mode on), or it arrived and was refused (a mismatched Signing Secret) —
  and a missing `reactions:write` scope is explained instead of failing silently.
