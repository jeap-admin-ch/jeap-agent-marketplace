#!/bin/sh
# jeap-run.sh — run a long Maven build/test detached, then poll it.
#
# The migrate-spring-boot-4 skill copies this file into the project at .jeap-migration/jeap-run.sh
# and runs it from there: it writes its scratch (run.log / run.exit / run.start / run.pid) into the
# current directory, so it must be invoked with the project root as the working directory. The
# canonical copy lives in this skill's scripts/ directory — edit it there, never the per-project copy.
#
# It detaches the build into a new session (setsid) so the agent's per-tool-call teardown can't
# kill it, and records the exit code in a sentinel file so a later `wait` can read it. Every
# invocation is the same command, so one approval ("Yes, approve for the session") covers all
# runs and polls.
#
# GUARDRAIL: this wrapper only ever execs an allowlisted build tool (mvn / mvnw) — never an
# arbitrary command. So approving it once "for the session" does NOT grant arbitrary shell
# execution: the worst a fumbled or injected command can do through it is run Maven, not `rm -rf`
# or `curl | sh`. The check runs in __exec, not only in `run`, because __exec is independently
# callable and is covered by the very same session approval. This is defense-in-depth, not a
# sandbox — a Maven build runs plugins, the lifecycle, and the project's own tests, which is
# arbitrary code execution by design, so still only run this against a project you trust.
#
#   jeap-run.sh run <mvn|./mvnw> <args...>   # launch detached (returns immediately)
#   jeap-run.sh wait                         # block ~20s; re-run the SAME line (no extra sleep) until maven_exit=
#
# `run` refuses to start a second build only while one is genuinely in flight (live run.pid + no exit
# recorded yet), so a stray double-launch can't interleave run.log or race run.exit; the pidfile is
# cleared on normal exit so a finished build never blocks a new one. `wait` before any `run` says so
# and exits cleanly instead of erroring, and reports a build that vanished without an exit code (e.g.
# killed across a session restart) instead of looping "still running" forever.
set -u
DIR=.jeap-migration; LOG="$DIR/run.log"; EXIT_FILE="$DIR/run.exit"; START="$DIR/run.start"; PIDFILE="$DIR/run.pid"

# The only programs this wrapper may launch. Keep this list tight — it is the guardrail.
allowed() {
  case "${1:-}" in
    mvn|mvnw|./mvnw) return 0 ;;
    *) return 1 ;;
  esac
}

case "${1:-}" in
  run)
    shift
    allowed "${1:-}" || { echo "refused: jeap-run.sh runs only mvn/mvnw, not '${1:-}'" >&2; exit 3; }
    mkdir -p "$DIR"
    # Refuse a second build only while one is genuinely in flight — concurrent runs would interleave
    # run.log and race run.exit. "In flight" = launched (pidfile present) AND not yet finished
    # (run.exit still empty) AND the process is still alive (kill -0). Requiring run.exit empty means a
    # finished build never blocks a new one even if its pidfile somehow lingered; a dead pid never blocks
    # either, since kill -0 fails for it.
    if [ ! -s "$EXIT_FILE" ] && [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
      echo "refused: a build is already running (pid $(cat "$PIDFILE")). Poll it with '$0 wait', or stop it before starting another." >&2; exit 4
    fi
    rm -f "$EXIT_FILE"; date +%s > "$START"
    if command -v setsid >/dev/null 2>&1; then
      setsid sh "$0" __exec "$@" </dev/null >/dev/null 2>&1 &
    else
      nohup  sh "$0" __exec "$@" </dev/null >/dev/null 2>&1 &   # fallback (e.g. stock macOS, no setsid)
    fi
    echo "$!" > "$PIDFILE"
    echo "started: $* (pid $!) — follow live with: tail -f $LOG" ;;
  __exec)                       # internal: the detached command body (re-checks the allowlist)
    shift
    allowed "${1:-}" || { mkdir -p "$DIR"; echo "refused: jeap-run.sh runs only mvn/mvnw, not '${1:-}'" > "$LOG"; echo 126 > "$EXIT_FILE"; rm -f "$PIDFILE"; exit 126; }
    # Clear the pidfile right after recording the exit code, so a normally-finished build leaves no
    # stale pid that a later PID reuse could turn into a false "already running" refusal. A build that
    # is killed never reaches this line, so its pidfile persists — which is what lets `wait` report it
    # as gone and what (correctly) still does not block a new `run`, since its pid is dead.
    "$@" > "$LOG" 2>&1; rc=$?; echo "$rc" > "$EXIT_FILE"; rm -f "$PIDFILE" ;;
  wait)
    if [ ! -s "$EXIT_FILE" ] && [ ! -f "$START" ]; then
      echo "no build has been started yet — launch one first, e.g. '$0 run ./mvnw clean verify'." >&2; exit 0
    fi
    end=$(( $(date +%s) + 20 ))
    while [ ! -s "$EXIT_FILE" ] && [ "$(date +%s)" -lt "$end" ]; do sleep 3; done
    if [ -s "$EXIT_FILE" ]; then
      echo "maven_exit=$(cat "$EXIT_FILE")"
    elif [ -f "$PIDFILE" ] && ! kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
      echo "build process is gone but no exit code was recorded — it was most likely killed (e.g. a session restart). Re-launch it: '$0 run ./mvnw …'." >&2
    else
      start=$(cat "$START" 2>/dev/null); [ -n "$start" ] || start=$(date +%s)
      echo "still running (~$(( ($(date +%s) - start) / 60 ))m elapsed) — re-run the same wait now, no extra sleep (live: tail -f $LOG)"
    fi ;;
  *) echo "usage: $0 run <mvn|./mvnw> <args...> | wait" >&2; exit 2 ;;
esac
