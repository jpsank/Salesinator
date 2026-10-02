# docker-hygiene.sh — sourced by redeploy.sh and restart-service.sh so the commands that build images also keep the
# Docker VM from filling up. Nothing else ever clears BuildKit's cache: it grew to 26 GB, filled the VM disk and took
# Postgres down with "No space left on device" (which also shows up as apt "invalid signature" in builds).
#
#   hygiene_preflight   before building: if the VM disk is low, drop the build cache; if it is still low, stop the
#                       build with a clear message instead of letting a full disk break running services.
#   hygiene_after       after building: drop build cache older than three days (recent layers keep rebuilds quick).
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
}
