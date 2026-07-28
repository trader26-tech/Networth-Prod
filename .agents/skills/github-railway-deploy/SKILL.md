---
name: github-railway-deploy
description: >
  Auto-deploy to production after code changes. Triggers whenever Codex finishes
  writing, editing, or fixing code and the user wants changes deployed. This skill must
  activate whenever: the user mentions "deploy", "push to main", "push to GitHub",
  "Railway", "production", "go live", or when any code task is completed in a project
  that has a Railway deployment. Also trigger when the user says things like "make it
  live", "deploy the changes", "ship it", "push the changes". The skill commits all
  changed files, pushes to the main branch on GitHub, and confirms Railway has picked
  up the deployment — all without leaving Codex.
---

# GitHub → Railway Auto-Deploy Skill

After finishing code changes, this skill:
1. Stages and commits all modified files with a descriptive commit message
2. Pushes to the `main` branch on GitHub
3. Optionally verifies Railway picked up the deployment via its CLI or API
4. Reports the live production URL so you can confirm the changes

---

> **⚠️ MULTIPLE AGENTS SHARE THIS REPO.** Several Codex sessions edit the
> same working tree at once, and a Stop hook auto-commits on every turn. So by
> the time you deploy, another agent may have **already committed your files**,
> or **pushed ahead of you**. That is normal — do **not** panic, do **not**
> report failure, and do **not** force-push. Always commit/push through the
> shared concurrency-safe script below, which handles every race for you.

## Steps 1–3 — Commit & push (concurrency-safe, one command)

Do **not** run raw `git add` / `git commit` / `git push` yourself. Run the shared
script — it takes a lock, stages everything, commits (or no-ops if a sibling
agent already committed), and pushes with automatic rebase-and-retry if origin
advanced:

```bash
"$CLAUDE_PROJECT_DIR/.Codex/hooks/git-sync.sh" "<type>: <short summary under 72 chars>"
```

Where `<type>` is one of `feat`, `fix`, `refactor`, `chore`, `docs`, `style`,
`test`. Example:

```bash
"$CLAUDE_PROJECT_DIR/.Codex/hooks/git-sync.sh" "feat: add JWT auth endpoints"
```

Interpret its single status line — all of these are **success, keep going to
Step 4**:

| Output contains | Meaning | What to tell the user |
|---|---|---|
| `committed & pushed` | your commit is on origin | "Pushed — Railway deploy triggered." |
| `nothing to commit (already committed by another agent)` | a sibling agent already committed & pushed your files | "Already committed by a parallel agent — it's on origin." |
| `push deferred` | committed locally; a concurrent push won the race | "Committed; it'll reconcile & deploy on the next turn." — then re-run the script once to try the push again. |
| `lock busy` | another agent is mid-sync right now | wait a few seconds and re-run the script once. |

**Never** force-push, and **never** treat "nothing to commit" as an error — with
parallel agents it usually just means your work is already safely on origin.
Verify what's actually live in Step 4 rather than trusting local state.

To confirm your changes reached origin regardless of who committed them:
```bash
git fetch -q origin && git log -1 --stat origin/main   # your files should be in a recent commit
```

---

## Step 4 — Confirm Railway deployment triggered

Railway auto-deploys on push to the connected branch. To verify:

### Option A — Railway CLI (preferred if installed)
```bash
# Show recent deployments for the linked project
railway status

# Stream live deployment logs (Ctrl+C after ~30 seconds once status is "SUCCESS")
railway logs --tail
```

### Option B — Railway API (if CLI not available and RAILWAY_TOKEN is set)
```bash
# List recent deployments via REST API
curl -s -H "Authorization: Bearer $RAILWAY_TOKEN" \
  "https://backboard.railway.app/graphql/v2" \
  -H "Content-Type: application/json" \
  --data '{"query":"{ me { projects { edges { node { name deployments(first:1) { edges { node { status createdAt } } } } } } } }"}' \
  | python3 -m json.tool
```

### Option C — No CLI, no token
Tell the user: "I've pushed to GitHub. Railway should auto-deploy within 1–2 minutes. Check your Railway dashboard at https://railway.app/dashboard to watch the build."

---

## Step 5 — Report outcome

After confirming the push succeeded, output a clean summary:

```
✅ Deployed to production

  Commit : abc1234 — feat: add user authentication
  Branch : main
  Remote : https://github.com/<org>/<repo>
  Railway: Deployment triggered — status: BUILDING
  URL    : https://<your-app>.up.railway.app
```

If Railway status is unavailable, omit those lines and tell the user where to check.

---

## Error handling

| Situation | Action |
|---|---|
| `git push` rejected (non-fast-forward) | Pull --rebase, then push; surface conflicts if any |
| Merge conflicts on rebase | Stop, show conflict files, ask user to resolve |
| Railway CLI not installed | Fall back to API or dashboard link |
| No `RAILWAY_TOKEN` env var | Skip status check; tell user to verify in dashboard |
| Repo has no `origin` remote | Ask user for GitHub URL; run `git remote add origin <url>` |
| Not on `main` branch | Ask user to confirm before switching or merging |

---

## Setup guide (first time)

See `references/setup.md` for:
- Linking an existing repo to Railway
- Installing the Railway CLI and logging in
- Setting `RAILWAY_TOKEN` for programmatic status checks
- Configuring Railway to deploy from the `main` branch only

---

## Important constraints

- **Never force-push** (`git push --force`) without explicit user approval — this can destroy history.
- **Never auto-resolve merge conflicts** — always surface them to the user.
- **Only push to `main`** — do not create or push to other branches unless the user asks.
- **Do not expose tokens** in commit messages, logs, or any output.
- If the project has a `.env` or secrets file, confirm it's in `.gitignore` before staging.
