# Pulse Diagnostics Website

Static, responsive product landing page for Pulse Diagnostics. It is **not** the Windows desktop app: the desktop app's React bundle remains in `ui/index.html`.

## Local preview

From the repository root:

```powershell
python -m http.server 8000
```

Open <http://localhost:8000/website/>. The page uses plain HTML, CSS and JavaScript: **no Node.js, npm or framework build is necessary**.

## Publish through GitHub Pages

A GitHub Actions workflow is included at `.github/workflows/deploy-website.yml`.

1. Push the repository to GitHub on the `main` branch.
2. Open **Settings → Pages** in the repository.
3. Set **Build and deployment → Source** to **GitHub Actions**.
4. Open **Actions** and run **Deploy website to GitHub Pages**, or push a change to `website/`.
5. Find the published site at `https://YOUR_USERNAME.github.io/YOUR_REPOSITORY/`.

The workflow only uploads `website/`; no Python source code, Windows executables, cache or drivers are published as site assets. The page derives its GitHub links automatically when served from standard `github.io` project Pages URLs.

## Images and metrics

Images in `assets/*.webp` are UI previews from the Pulse Diagnostics project. Their metric values are **illustrative test data**, not live sensor readings or a benchmark result. The landing page itself does not connect to the user's hardware.

Change copy in `index.html`, appearance in `styles.css`, and preview switching behavior in `script.js`.
