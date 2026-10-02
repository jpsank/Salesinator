# bot-image.sh — sourced by redeploy.sh: keep the locally built meeting-bot image current.
#
# The runtime spawns every bot from BROWSER_IMAGE. When that is a locally built tag (not a published vexaai/ one) it is
# rebuilt here when the sources it is built from changed — the bot Dockerfile copies in all of core/ and the workspace
# manifests. The image carries a label with a fingerprint of those sources (the working tree, uncommitted edits included),
# so an unchanged tree costs a few seconds and a changed one is rebuilt. Needs CD (this directory) from the caller.

REPO_ROOT="$(cd "$CD/../.." && pwd)"

bot_source_stamp() {
  ( cd "$REPO_ROOT" && git ls-files -co --exclude-standard -z -- core pnpm-lock.yaml pnpm-workspace.yaml package.json tsconfig.base.json turbo.json \
      | xargs -0 shasum -a 256 2>/dev/null | shasum -a 256 | cut -c1-16 )
}

# $1: the BROWSER_IMAGE the runtime spawns bots from.
ensure_bot_image() {
  _img="$1"
  case "$_img" in
    "") echo "  bot image: none configured — skipped"; return 0 ;;
    vexaai/*) echo "  bot image: $_img is a published image, not built here — skipped"; return 0 ;;
  esac
  _want="$(bot_source_stamp)"
  _have="$(docker image inspect "$_img" --format '{{ index .Config.Labels "vexa.bot-source" }}' 2>/dev/null || true)"
  if [ "$_have" = "$_want" ]; then
    echo "  bot image $_img: unchanged — left as is"
    return 0
  fi
  echo "  bot image $_img: sources changed (or never stamped) — rebuilding (bots already running keep the old image)"
  make -C "$CD" bot BOT_IMAGE="$_img" BOT_SOURCE_STAMP="$_want"
}
