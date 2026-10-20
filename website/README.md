# AgenticFan website

Public product site: https://agenticfan.timmyagentic.si

Buildless HTML, CSS and JavaScript. All assets are local; product illustrations use explicitly labeled sample data. The site never connects to a visitor's hardware, router or ledger.

## Local verification

```sh
python3 -m http.server 8787 --directory website/public
node --check website/public/site.js
git diff --check
wrangler deploy --config website/wrangler.jsonc --dry-run
```

Check desktop and 390px mobile layouts, section links, copy feedback, keyboard focus, FAQ expansion and missing-page behavior. Verify the documented release/install links.

## Authorized production deployment

After the repository review and merge gates, run from the verified commit:

```sh
wrangler deploy --config website/wrangler.jsonc
```

This config deploys only the website and its exact custom domain. It does not build, publish or install the application. Read back the active deployment and independently verify HTTPS, CSS/JS, robots, sitemap, 404 and primary links.
