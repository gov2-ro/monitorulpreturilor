#!/usr/bin/env bash
# hc_run.sh — wrap a command with healthchecks.io start/success/fail pings.
#
# Usage: hc_run.sh <healthcheck-uuid> <command...>
#
# Pings <uuid>/start before the command, <uuid> on exit 0, <uuid>/fail otherwise.
# All pings are best-effort (curl failures don't change the wrapped command's exit code).
# Set HC_RUN_DRYRUN=1 to skip the curl calls (for local testing).
set -u

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <healthcheck-uuid> <command...>" >&2
    exit 2
fi

UUID="$1"; shift
PING="https://hc-ping.com/${UUID}"

_ping() {
    [ "${HC_RUN_DRYRUN:-0}" = "1" ] && return 0
    curl -fsS -m 10 --retry 3 -o /dev/null "$@" || true
}

_ping "${PING}/start"
"$@"
status=$?
if [ "$status" -eq 0 ]; then
    _ping "${PING}"
else
    _ping --data-raw "exit=$status" "${PING}/fail"
fi
exit "$status"
