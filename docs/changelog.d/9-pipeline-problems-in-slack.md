- **A failed push of the agent's branch is no longer silent.** After the agent finished, the pipeline retried pushing the branch (and
  opening the pull request) every few seconds, and a failure left only a stack trace in a log — a card could sit "building" forever.
  The first time it fails for a given reason, the bot now says so in the card's Slack thread with GitHub's own reason; for the common
  cause, an expired or revoked token for the product repo's identity (a copy of yours made when you click *Use this repo*), it says how
  to refresh it. A different reason is said again; the same one is not. When the pull request opens, its link is posted in the thread.
