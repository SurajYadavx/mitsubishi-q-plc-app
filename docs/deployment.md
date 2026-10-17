# GitHub Pages Deployment

## Repository setup

1. Create a new **public** GitHub repository (or use an existing one)
2. Push this project to `main`:

```powershell
cd "D:\Academics\TY - S1\MS\mitsubishi-q-plc-app_ANTIGRAVITY"
git init
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git add .
git commit -m "Initial project structure — Stage 1"
git push -u origin main
```

## Enable GitHub Pages

Because the static web dashboard is located in the `web/` subdirectory, GitHub's default "Deploy from a branch" dropdown (which only supports `/` or `/docs`) cannot directly select `web/`.

Instead, use the included GitHub Actions workflow ([.github/workflows/deploy-pages.yml](../.github/workflows/deploy-pages.yml)):

1. In your GitHub repository, navigate to:
   **Settings → Pages**
2. Under **Build and deployment → Source**, select:
   **GitHub Actions**
3. Once selected, every push to `main` touching `web/` will automatically build and publish the dashboard to GitHub Pages.

Alternatively, you can manually trigger the deployment from the **Actions** tab on GitHub using the *workflow_dispatch* button.

The published URL will be:
```
https://<YOUR_USERNAME>.github.io/<YOUR_REPO>/
```

Allow 1–2 minutes for the initial deployment to complete. Verify the live page in a browser before closing commissioning.

## MQTT broker CORS / WebSocket

HiveMQ Cloud allows WebSocket connections by default on port 8884.
No CORS configuration is required because MQTT.js uses WebSocket — not CORS-restricted fetch.

The browser connects directly to the broker using the read-only credentials.
The broker must allow WebSocket connections on its public hostname.

## Security checklist before deploying

- [ ] `.gitignore` excludes `gateway/config/secrets.ini`
- [ ] `.gitignore` excludes `firmware/plc_monitor/secrets.h`
- [ ] No PLC IP addresses or TCP ports appear in `web/` files
- [ ] Browser MQTT credentials are read-only (subscribe-only) accounts
- [ ] No admin or publisher credentials appear in `web/` files
- [ ] MQTT.js CDN URL uses HTTPS

## Updating the deployed site

Any push to `main` in the `web/` directory automatically redeploys.

```powershell
git add web/
git commit -m "Update dashboard"
git push
```

## Custom domain (optional)

Add a `CNAME` file inside `web/` with your domain, then configure DNS per GitHub's documentation.
