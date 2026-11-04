#!/bin/bash
# Ztockz auto-deploy — pull, build the shell, restart the API, smoke it.
#
# One command, run by ztockz-deploy.timer every five minutes.  It is a poll,
# not a webhook: the box pulls what GitHub already accepted, so a deploy needs
# no SSH key in GitHub's secrets and no public endpoint that could restart the
# service.  The cost is up to five minutes of latency, which the timer's
# OnUnitActiveSec sets.
#
# Every step is guarded.  A failed pull, a failed build or a failed smoke test
# leaves the previous release serving — the script exits non-zero and the
# timer logs it, but the service is never stopped on a broken tree.

set -uo pipefail

REPO=/home/ubuntu/ztockz
LOG=/home/ubuntu/ztockz-data/deploy.log
STAMP=/home/ubuntu/ztockz-data/.deployed-sha

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

cd "$REPO" || { log "FATAL: no repo at $REPO"; exit 1; }

# What GitHub has, and what is running.
remote_sha=$(git ls-remote origin refs/heads/main | cut -f1) || {
  log "WARN: cannot reach origin; keeping the current release"
  exit 0
}
local_sha=$(git rev-parse HEAD)

if [ "$remote_sha" = "$local_sha" ]; then
  # Already on the pushed commit.  Only the stamp is missing — either the
  # first run, or a deploy that was interrupted.  Rebuild the shell and
  # restart, so a half-finished release still gets finished.
  if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$remote_sha" ]; then
    exit 0  # nothing to do
  fi
  log "deploy: resuming $remote_sha (stamp missing)"
else
  log "deploy: remote=$remote_sha local=$local_sha"
fi

# 1. Pull.  A dirty tree is a developer's work in progress — never overwrite it.
if [ -n "$(git status --porcelain)" ]; then
  log "SKIP: working tree is dirty; leaving the checkout alone"
  exit 0
fi

git fetch --quiet origin main || { log "WARN: fetch failed"; exit 0; }
git checkout --quiet main || { log "WARN: checkout failed"; exit 0; }
git reset --hard --quiet "origin/main" || { log "WARN: reset failed"; exit 0; }

after=$(git rev-parse HEAD)
if [ "$after" != "$remote_sha" ]; then
  log "WARN: checked out $after but remote is $remote_sha"
  exit 0
fi

# 2. Build the shell.  The API serves web/dist from disk, so a failed build
#    must not restart the service onto a half-built bundle.
if ! (cd web && npm run build >/dev/null 2>&1); then
  log "FAIL: frontend build failed; the previous bundle is still in place"
  git checkout --quiet "$local_sha" 2>/dev/null
  (cd web && npm run build >/dev/null 2>&1)  # restore the last good bundle
  exit 1
fi
log "deploy: frontend built"

# 3. Restart the API.  systemd restarts it on failure; a failed restart leaves
#    the previous workers serving until they are killed.
if ! sudo -n systemctl restart ztockz; then
  log "FAIL: systemctl restart failed"
  exit 1
fi

# 4. Smoke it.  The service answers /health a moment after the restart.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 2
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8099/health 2>/dev/null)
  if [ "$code" = "200" ]; then
    printf '%s' "$after" >"$STAMP"
    log "deploy: OK sha=$after"
    exit 0
  fi
done

log "FAIL: /health did not answer 200 after the restart"
exit 1