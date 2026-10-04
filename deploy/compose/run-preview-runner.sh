#!/bin/sh
# run-preview-runner.sh — runs the live-preview runner as a native host process: it builds each
# terminal-touching pull request with the host's Docker and git, so it is not a compose service
# (that would hand a container the Docker socket). dev-up.sh and redeploy.sh start it; it needs
# ~/vexa-data/preview/gate.env, which `deploy/preview/preview.sh gate-up` also reads — see
# deploy/preview/README.md.
set -eu
exec "$(cd "$(dirname "$0")/../preview" && pwd)/preview.sh" runner
