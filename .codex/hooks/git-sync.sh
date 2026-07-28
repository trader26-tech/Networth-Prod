#!/usr/bin/env bash
# SCOPED, concurrency-safe "commit + push" for MANY parallel agents sharing one
# working tree and one branch. Used by the Stop hook (auto-commit-push.sh) and by
# any agent committing mid-turn (e.g. the deploy skill).
#
# The guardrail that makes simultaneous agents safe:
#   • Each agent commits ONLY the files IT edited this session (tracked live by
#     track-edit.sh into <git-dir>/agent-manifests/<session>.list). We stage just
#     that scope — never `git add -A` — so a sibling agent's half-finished /
#     non-building files are NEVER swept into our commit, and never lost (they
#     stay in the working tree until their own session commits them).
#   • A staged-Python syntax gate: if any staged .py file doesn't parse, we HOLD
#     (don't commit a broken state) and retry next turn.
#   • Atomic mkdir mutex serialises all git writes → no index corruption.
#   • "Nothing to commit" is SUCCESS (a sibling already handled our files).
#   • Push races reconciled with pull --rebase --autostash + retry, so no commit
#     is lost. Autostash preserves other agents' uncommitted work across rebase.
#   • NEVER exits non-zero for a concurrency reason; never blocks the turn.
#
# Usage:  git-sync.sh ["commit message"] ["short-tag"] ["session-id"] [extra path…]
#   $1  commit message   (default: "auto: Claude Code changes (<stamp>)")
#   $2  short tag         appended as " [tag]"  (e.g. 8-char session id)
#   $3  session id        (default: $CLAUDE_CODE_SESSION_ID) → selects the manifest
#   $4+ extra paths       force-include these files in the commit scope
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || { echo "git-sync: not in a project dir"; exit 0; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "git-sync: not a git repo"; exit 0; }

stamp="$(date '+%Y-%m-%d %H:%M:%S')"
msg="${1:-auto: Claude Code changes ($stamp)}"
tag="${2:-}"
sid="${3:-${CLAUDE_CODE_SESSION_ID:-}}"
[ "$#" -ge 3 ] && shift 3 || shift "$#"
extra=( "$@" )
[ -n "$tag" ] && msg="$msg [$tag]"

branch="$(git symbolic-ref --short HEAD 2>/dev/null || echo main)"
GITDIR="$(git rev-parse --git-dir)"
LOCKDIR="$GITDIR/.auto-commit.lock.d"
MANIFEST="$GITDIR/agent-manifests/${sid}.list"

# --- atomic mutex: only one git writer at a time (up to ~60s wait) ------------
acquired=""
for _ in $(seq 1 120); do
  if mkdir "$LOCKDIR" 2>/dev/null; then acquired=1; break; fi
  now="$(date +%s)"
  lm="$(stat -f %m "$LOCKDIR" 2>/dev/null || stat -c %Y "$LOCKDIR" 2>/dev/null || echo "$now")"
  [ $(( now - lm )) -gt 120 ] && rmdir "$LOCKDIR" 2>/dev/null || true
  sleep 0.5
done
[ -n "$acquired" ] || { echo "git-sync: another agent is syncing (lock busy) — skipped; will reconcile next turn"; exit 0; }
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

# --- build the commit scope: this session's manifest + any explicit extras ----
scope=()
if [ -n "$sid" ] && [ -s "$MANIFEST" ]; then
  while IFS= read -r f; do [ -n "$f" ] && scope+=( "$f" ); done < "$MANIFEST"
fi
for f in "${extra[@]:-}"; do [ -n "$f" ] && scope+=( "$f" ); done

if [ "${#scope[@]}" -gt 0 ]; then
  git add -A -- "${scope[@]}" 2>/dev/null || true          # stage ONLY our files (incl. our deletes)
  mode="scoped:${#scope[@]}"
else
  # Nothing tracked for this session. Only fall back to whole-tree if NO other
  # agent is active — otherwise we'd sweep a sibling's in-flight files.
  others=""
  for m in "$GITDIR/agent-manifests"/*.list; do
    [ -e "$m" ] || continue
    [ "$m" = "$MANIFEST" ] && continue
    [ -s "$m" ] && { others=1; break; }
  done
  if [ -n "$others" ]; then
    echo "git-sync: nothing tracked for this session & other agents are active — skipping (protecting their WIP)"
    exit 0
  fi
  git add -A 2>/dev/null || true                            # safe: single agent / legacy
  mode="whole-tree"
fi

# --- syntax gate: never commit un-parseable Python we've staged ---------------
bad=""
while IFS= read -r f; do
  case "$f" in
    *.py) { [ -f "$f" ] && ! python3 -m py_compile "$f" 2>/dev/null; } && bad="$bad $f" ;;
  esac
done < <(git diff --cached --name-only 2>/dev/null)
if [ -n "$bad" ]; then
  if [ "${#scope[@]}" -gt 0 ]; then git reset -q -- "${scope[@]}" 2>/dev/null || true; else git reset -q 2>/dev/null || true; fi
  echo "git-sync: HELD — staged Python doesn't parse ($bad ) — not committing a broken state; retry next turn"
  exit 0
fi

# --- nothing of ours to commit → a sibling agent already committed it ---------
if git diff --cached --quiet 2>/dev/null; then
  git pull --rebase --autostash -q origin "$branch" >/dev/null 2>&1 || true
  echo "git-sync: nothing to commit ($mode); synced with origin/$branch"
  exit 0
fi

git commit -q -m "$msg" >/dev/null 2>&1 || { echo "git-sync: commit skipped (nothing new after staging)"; exit 0; }

# our scope is now committed → clear this session's manifest so we start fresh
[ -n "$sid" ] && [ -f "$MANIFEST" ] && : > "$MANIFEST" 2>/dev/null || true

# --- push, replaying our commit on top if origin advanced --------------------
pushed=""
for _ in 1 2 3 4 5; do
  if git push -q origin "$branch" >/dev/null 2>&1; then pushed=1; break; fi
  if ! git pull --rebase --autostash -q origin "$branch" >/dev/null 2>&1; then
    git rebase --abort >/dev/null 2>&1 || true
    break
  fi
done

if [ -n "$pushed" ]; then
  echo "git-sync: committed & pushed to origin/$branch ($mode)"
else
  echo "git-sync: committed locally to $branch ($mode); push deferred (concurrent push or conflict) — reconciles next turn"
fi
exit 0
