- **Reconnecting GitHub now refreshes the agent's copy of your token.** "Use this repo" copies your GitHub token to the shared `product-repo` identity the agent pushes as,
  but the copy was a one-time snapshot: reconnecting GitHub, or pasting a new token, left it stale and pushes failed with "the saved connection to GitHub has expired"
  until you ran "Use this repo" again. The copy now remembers whose token it is and is refreshed whenever that person saves a new one. (Clearing your token still leaves
  the copy in place, and a token saved directly under `product-repo` stops it following anyone.)
