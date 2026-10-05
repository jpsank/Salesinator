# docker-hygiene.sh — sourced by redeploy.sh and restart-service.sh so the commands that build images also keep the
# Docker VM from filling up. Nothing else ever clears BuildKit's cache: it grew to 26 GB, filled the VM disk and took
# Postgres down with "No space left on device" (which also shows up as apt "invalid signature" in builds).
#
#   hygiene_preflight   before building: if the VM disk is low, drop the build cache; if it is still low, stop the
#                       build with a clear message instead of letting a full disk break running services.
#   hygiene_after       after building: drop build cache older than three days (recent layers keep rebuilds quick),
#                       and remove the stray ':dev' copy of this stack's images that a build without IMAGE_TAG leaves
#                       beside the running set (images a container still uses are never touched).
#
# Both are best-effort: an unreadable disk or a failed prune never fails a deploy. Knobs (GB / hours):
#   HYGIENE_MIN_FREE_GB=8   free space below which the cache is dropped before building
#   HYGIENE_ABORT_FREE_GB=3 free space below which the build is refused
#   HYGIENE_KEEP_HOURS=72   how much recent cache to keep

# Free GB on the Docker VM disk, read through any running container (they all see the VM's disk); empty if unknown.
hygiene_free_gb() {
  _c="$(docker ps -q 2>/dev/null | head -n 1)"
  [ -n "$_c" ] || return 0
  docker exec "$_c" df -Pk / 2>/dev/null | awk 'NR==2 { printf "%d", $4 / 1048576 }'
}

hygiene_preflight() {
  _min="${HYGIENE_MIN_FREE_GB:-8}"; _abort="${HYGIENE_ABORT_FREE_GB:-3}"
  _free="$(hygiene_free_gb)"
  [ -n "$_free" ] || return 0
  [ "$_free" -ge "$_min" ] && return 0
  echo "== docker VM disk is low (${_free} GB free) — dropping the build cache first =="
  docker builder prune -af 2>&1 | tail -n 1 || true
  _free="$(hygiene_free_gb)"
  if [ -n "$_free" ] && [ "$_free" -lt "$_abort" ]; then
    echo "refusing to build: only ${_free} GB free on the Docker VM disk. A full disk takes Postgres down." >&2
    echo "free space first (docker system df; remove images nothing uses), then run this again." >&2
    return 1
  fi
}

hygiene_after() {
  echo
  echo "== trimming build cache older than ${HYGIENE_KEEP_HOURS:-72}h =="
  docker builder prune -f --filter "until=${HYGIENE_KEEP_HOURS:-72}h" 2>&1 | tail -n 1 || true
  hygiene_stray_images
}

# The images this stack builds are tagged IMAGE_TAG (from .env). A compose build that runs without it — `make dev`, or
# compose started from a directory with no .env — builds a second ':dev' copy of every one of them. Nothing runs from
# that copy, so it is removed here. `docker rmi` refuses an image a container uses, so a stack that really is on ':dev'
# keeps its images. transcription-cpu:dev belongs to the separate transcription stack and is left alone.
hygiene_stray_images() {
  _tag="$(sed -n 's/^IMAGE_TAG=//p' "$CD/.env" 2>/dev/null | head -n 1 | tr -d '[:space:]')"
  _tag="${_tag:-dev}"
  [ "$_tag" = "dev" ] && return 0
  _stray="$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -E '^vexaai/v012-.*:dev$' | grep -v 'transcription-cpu' || true)"
  [ -n "$_stray" ] || return 0
  echo
  echo "== removing the unused ':dev' copy of this stack's images =="
  # shellcheck disable=SC2086
  docker rmi $_stray 2>&1 | grep -c '^Untagged' | sed 's/^/  removed: /' || true
}
