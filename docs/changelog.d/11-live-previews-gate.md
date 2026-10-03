- **Live previews of proposed changes, served from your own machine.** `deploy/preview/preview.sh up <pr>` builds the terminal from a
  change and runs it at `preview-pr-<n>.<your domain>`, showing the viewer's own real data read-only: the preview holds no credentials,
  sits on a private network, and talks to the stack only through a gate that identifies the viewer (Cloudflare Access), lends it that
  person's short-lived read key, and refuses every write. See `deploy/preview/README.md`.
  The agent's pull request now gets one automatically: `preview.sh runner` builds a preview of every pull request that changes the terminal,
  and the card's Slack thread gets a plain "You can try this draft live" link (or a note that there is nothing to preview yet).
