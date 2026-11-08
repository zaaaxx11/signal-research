# Going public — the switches to flip

Written 2026-11-08, while the deployment is still private. Nothing here is urgent
today; it is the list to run through the day the site is meant to be found.

## 1. Turn off Cloudflare's managed `robots.txt`

**The problem.** Cloudflare answers `/robots.txt` at the edge with its own
content-signals policy (~2.6 kB), so the file the app serves from the origin is
correct and unreachable. A crawler therefore never sees our `Allow`/`Sitemap`
lines — and, more importantly today, never sees an explicit stance on AI use.

**What to do.**

1. Cloudflare dashboard → the zone → the setting that manages `robots.txt` /
   content signals (the "AI Crawl Control" / managed robots feature). Turn it off
   for this zone, or set the signals explicitly if some AI use *is* welcome
   (`search`, `ai-input`, `ai-train` each take yes/no).
2. Purge the cached copy: the origin's answer is cached 300 s
   (`CACHE_CONTROL` in `app/api/crawl.py`), and the edge copy can outlive it.
3. Verify — the first line must be **ours**, not `# As a condition of accessing…`:

   ```bash
   curl -s https://<public-host>/robots.txt | head -3
   curl -s https://<public-host>/sitemap.xml | head -3
   ```

   Expected: `# Every page is public; the API is not for crawlers.` then
   `User-agent: *`, and the sitemap starting with `<?xml version="1.0"`.

## 2. Then tighten our robots (it currently says `Allow: /`)

With Cloudflare out of the way, our file becomes what crawlers obey — and it
currently invites them into the API as well as the pages. The prefixes live in
`web/vite.prefixes.ts` (the same list the dev proxy uses).

Edit the body in `app/api/crawl.py::robots` to:

```python
    body = (
        "# Every page is public; the API is not for crawlers.\n"
        "User-agent: *\n"
        "Allow: /app/\n"
        "Disallow: /screens\n"
        "Disallow: /market\n"
        "Disallow: /engine\n"
        "Disallow: /symbols\n"
        "Disallow: /logos\n"
        "Disallow: /health\n"
        f"Sitemap: {origin_of(request)}/sitemap.xml\n"
    )
```

and extend `tests/test_api_crawl_documents.py` to assert each `Disallow:` is
present (keep the `Allow: /app/` shape, not a bare `Allow: /`).

## 3. Verify the rest of the public surface

Already in place and tested — re-check it the same day:

- **Sitemap follows the request host.** `curl -s https://<public-host>/sitemap.xml`
  must show `<loc>https://<public-host>/app/…`, and the same route on
  `http://localhost:8099` must show `localhost`. If both show one host, the host
  got hard-coded somewhere.
- **Preview tags.** `web/dist/index.html` must carry `og:url` + `og:image` with the
  public origin, and `web/index.html` must carry none —
  `VITE_PUBLIC_ORIGIN` comes from the box's `engine.env` via `scripts/deploy.sh`.
- **No third-party origin in the shell.** `test_shell_has_no_third_party_origins`
  allows only the self origin the preview tags declare.
- **Indexing is allowed.** `<meta name="robots" content="index, follow" />` in
  `web/index.html`.
- **After any change to these files, the deploy is manual:**

  ```bash
  sudo systemctl start ztockz-deploy.service   # pull → build → test gate → restart → smoke
  tail /home/ubuntu/ztockz-data/deploy.log     # one line per step
  ```

  The restart costs a few minutes of index rebuild on the box; that is why the
  timer is not enabled.

## 4. One thing that stays out of the repository

The public host lives in `/home/ubuntu/ztockz-data/engine.env` (`MEE_PUBLIC_ORIGIN`)
and in the box's tunnel config — never in the checkout. A test refuses that host
anywhere under `app/`, `web/` or `tests/`; if it ever fails, something got
hard-coded and the deployment lost the ability to move domains.
