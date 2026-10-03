- **CI now typechecks and tests `packages/transcript-rendering`.** It is a standalone npm project outside the pnpm workspace, so the `node` leg never looked
  at it — an agent pull request that broke its main interface passed CI. A new `packages` job runs its typecheck and tests (and is part of the required
  `gates` check), its lockfile was repaired so `npm ci` works, and the Slack verdict no longer warns that CI skipped it.
