- **A feature request raised again is noted on its card, not posted as a second one.** The copilot words the same ask differently each time
  ("CSV Export of Results", then "Export results to CSV"), so exact-title matching missed it and a second card — and a second agent run
  building the same thing — followed. The watcher now compares the requests' content words, and replies in the original card's thread
  ("Raised again — the 2nd time", with where the original stands: waiting, being built, or done with its pull request) and records the
  mention. It matches the same customer's requests from the last 14 days, only within one call for the shared "unmapped" workspace, and
  never against a request whose build failed. It leans toward not merging: different requests such as "export to PDF" and "export to CSV"
  stay separate.
