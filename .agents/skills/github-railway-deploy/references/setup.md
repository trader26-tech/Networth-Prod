# Setup Guide — GitHub + Railway Auto-Deploy

Follow these steps once per project to enable the skill.

---

## 1. Connect your GitHub repo to Railway

1. Go to https://railway.app → **New Project** → **Deploy from GitHub repo**
2. Select the repo and choose the **`main`** branch as the deployment source
3. Railway will auto-deploy on every push to `main` from this point forward

If the project already exists in Railway:
- Go to **Project Settings → Source** and confirm it's linked to the correct GitHub repo and the `main` branch

---

## 2. Install the Railway CLI (recommended)

The CLI lets Claude check deployment status without leaving the terminal.

```bash
# macOS / Linux
npm install -g @railway/cli

# Or via Homebrew
brew install railway

# Log in
railway login
```

After login, link your local project folder:

```bash
cd /path/to/your/project
railway link
# Select your Railway project from the list
```

---

## 3. Set RAILWAY_TOKEN for API access (alternative to CLI)

If you prefer not to use the CLI, you can use the Railway API directly.

1. Go to https://railway.app/account/tokens
2. Generate a new token and copy it
3. Add to your shell profile (e.g. `~/.zshrc` or `~/.bashrc`):

```bash
export RAILWAY_TOKEN="your_token_here"
```

4. Reload: `source ~/.zshrc`

---

## 4. Ensure main branch exists and is the default

```bash
# Rename current branch to main if it's called master
git branch -m master main
git push -u origin main

# Set main as default on GitHub:
# GitHub repo → Settings → Branches → Default branch → main
```

---

## 5. Protect .env and secrets from being committed

Make sure secrets never get pushed:

```bash
# Check .gitignore includes these
cat .gitignore | grep -E "\.env|secrets|\.key"

# If not, add them
echo ".env" >> .gitignore
echo ".env.local" >> .gitignore
echo "*.key" >> .gitignore
```

For Railway environment variables, set them in:
**Railway Dashboard → Project → Variables**

These are injected at runtime and never stored in your repo.

---

## 6. Verify the full pipeline works

```bash
# Make a small test change
echo "# test" >> README.md
git add README.md
git commit -m "chore: test deploy pipeline"
git push origin main

# Check Railway dashboard or:
railway status
```

You should see a new deployment appear in Railway within ~30 seconds.

---

## Troubleshooting

**Push rejected with "non-fast-forward"**
```bash
git pull --rebase origin main
git push origin main
```

**Railway not deploying after push**
- Confirm the repo branch in Railway Settings → Source is `main`
- Check Railway has GitHub permissions: railway.app/account/integrations

**`railway: command not found`**
- Re-install: `npm install -g @railway/cli`
- Or use the API method with `RAILWAY_TOKEN`

**Deployment fails in Railway (build error)**
- Run `railway logs` to see build output
- Fix the error in code, then the skill will push again
