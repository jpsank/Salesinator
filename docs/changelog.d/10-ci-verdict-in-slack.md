- **The agent's progress is reported in the card's thread in plain language, with GitHub links.** "The agent has finished a first draft of …",
  then whether "the automatic checks passed", "did not pass" or "didn't run" — each
  saying what it means and that a developer must review the draft before anything is used — written for a salesperson, not a developer.
- **The agent's pull request now gets CI's verdict in the card's Slack thread.** The agent's output is only as good as its model (one pull
  request replaced a whole interface and could not compile), so the repository's own CI is the check: after the pull request opens, the
  sweep reads its checks from GitHub and says once "CI passed", "CI failed: `typecheck`, `gates`" (named, with a link), or "No CI has run"
  — the cue that GitHub Actions needs enabling on a fork. Public repositories need no token; an unreadable one is said once; GitHub being
  unreachable just waits. Pull requests already open are left alone. Upstream's contribution-paperwork checks (`merge-card` and the like) are not counted — they fail
  on every agent pull request — and a pass names any changed TypeScript/JavaScript that lies outside the pnpm workspace, which CI never
  typechecks or tests ("CI passed, but it does not cover `packages/transcript-rendering/…` — review by hand").
