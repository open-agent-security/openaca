# Deployment runbook — openaca.dev on Cloudflare Pages

The static export is published to Cloudflare Pages on every push to
`main`. This document is the one-time setup runbook plus the model
for migrating later (e.g., to GitHub Pages once the repo is public).

## Why Cloudflare Pages

The repo is private until V0 launch. GitHub Pages requires a public
repo on the free tier, so it isn't an option yet. Cloudflare Pages:

- Free for private-source projects in direct-upload mode (no
  Cloudflare GitHub app installed on the source repo).
- Free SSL on custom domains.
- DNS management lives at the same registrar (`openaca.dev` is bought
  from Cloudflare), so domain pointing is one-pane.
- Migration to GitHub Pages later is straightforward: change the
  deploy step in `.github/workflows/publish.yml`, add a `dist/CNAME`
  file, switch DNS records once.

## One-time setup

1. **Buy the domain** at Cloudflare Registrar (or, if elsewhere,
   move the nameservers to Cloudflare so DNS is in one place).
2. **Create the Pages project**:
   - Cloudflare dashboard → Workers & Pages → Create → Pages →
     "Create with Direct Upload".
   - Project name: `openaca` (matches `CLOUDFLARE_PAGES_PROJECT`).
   - Skip the Git integration — direct upload means GitHub Actions
     pushes builds; no GitHub-app permissions on this repo.
3. **Generate an API token**:
   - Cloudflare dashboard → My Profile → API Tokens → Create Token.
   - Use the "Edit Cloudflare Workers" template (covers Pages).
   - Restrict to the account containing the Pages project.
4. **Find your account ID**: Cloudflare dashboard sidebar (right
   side of any zone overview).
5. **Set GitHub secrets**:

   ```bash
   gh secret set CLOUDFLARE_API_TOKEN -R open-agent-security/openaca --body "<token>"
   gh secret set CLOUDFLARE_ACCOUNT_ID -R open-agent-security/openaca --body "<account-id>"
   gh secret set CLOUDFLARE_PAGES_PROJECT -R open-agent-security/openaca --body "openaca"
   ```

6. **Bind the custom domain**:
   - Pages project → Custom domains → Set up a custom domain.
   - Enter `openaca.dev`.
   - If DNS is on Cloudflare for the same account, the records get
     created automatically. If DNS is elsewhere, add the CNAME the
     UI suggests.

## Verify

After the next push to `main` (or a manual `workflow_dispatch`):

- `gh run watch -R open-agent-security/openaca` on the Publish workflow
  should show `Deploy to Cloudflare Pages` succeeding.
- `https://openaca.dev/` serves the index page.
- `https://openaca.dev/overlays/GHSA-3q26-f695-pp76.json` returns the
  JSON overlay.
- `https://openaca.dev/index.json`, `/modified_id.csv`, `/all.zip`,
  `/schema/openaca.schema.json` all 200.

If the deploy step is skipped (workflow warning), one of the three
secrets is missing — re-run step 5.

## Migration to GitHub Pages (when the repo goes public)

Out of V0 scope; this is the V1 plan if we consolidate on GitHub:

1. Repo → Settings → Pages → Source = "GitHub Actions".
2. Edit `tools/export.py` to also write a `dist/CNAME` file
   containing `openaca.dev`.
3. In `.github/workflows/publish.yml`, replace the
   `cloudflare/wrangler-action` step with the GitHub Pages deploy
   steps (`actions/configure-pages` → `upload-pages-artifact` →
   `deploy-pages`).
4. Update DNS: replace the Cloudflare Pages CNAME with the GitHub
   Pages apex IPs.
5. Remove the `CLOUDFLARE_*` secrets.

The static URL pattern (`/overlays/<id>.json`, `/index.json`, etc.)
does not change, so any tooling consuming
`https://openaca.dev/...` is unaffected by the migration.
