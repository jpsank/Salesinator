- **Feature requests can be approved by the team's votes and a leader's go-ahead.** In Settings → Integrations → Slack, **Who can approve**
  takes a list of Slack members, workspace admins/owners, and/or Slack user groups; any one makes a leader. Each feature-request card is
  posted with 👍 and 👎 already on it, and a **leader's ✅ approves it only once 👍 outnumber 👎** (the bot's own seeds don't count,
  someone who reacted both ways has voted neither, a non-leader's ✅ counts as a 👍). The bot replies in the card's thread with who
  approved and the tally; a leader's ✅ before the votes are there is approved on its own when they tip. The tally and approver are
  recorded. With nobody chosen, the original rule (anyone's ✅) stands. The Slack app needs `reactions:write` (and `users:read` /
  `usergroups:read` for admins or a group) and a `reaction_removed` subscription; reconnect Slack after adding them.
