# Before going live — the checklist

The single pre-live list for this deployment: the edge switch that unshadows our
own files, the security decisions that are **deliberately still open** (each one
waits on the operator, not on a missing capability), and the verification commands
for everything already in place. Written 2026-11-08 while the site is still
private; `docs/` — not a second file — is where it stays, so the list cannot drift
against itself.

**The three open decisions, in the order they must be taken:**

1. **Caller identity, then a rate limit.** Today every public caller reaches the
   app as `127.0.0.1` (the tunnel), so the one limiter that exists collapses into a
   single shared bucket and the cheap read paths are unmetered. Identity first —
   trust `CF-Connecting-IP` from the tunnel (or `--proxy-headers` with
   `forwarded_allow_ips`) — *then* a per-IP bucket. A limiter built on the current
   identity would lock out honest users before it inconvenienced an abuser.
2. **Host firewall.** `ufw` is off and the ruleset is 23 `ACCEPT`s deep; `:22`,
   `:80` and `:8080` are reachable. This box also serves other projects, so a
   default-deny is a decision, not a chore: allow 22/80/443, confirm the others
   are meant to be closed.
3. **SSH passwords.** Effective config is `PasswordAuthentication yes` (the
   `00-hermes-password.conf` drop-in wins by being read first) with
   `PermitRootLogin no` and `fail2ban` active as mitigation. Install a key, prove
   it, then turn passwords off — in that order, or the operator locks themselves
   out.

**Already handled by the 2026-11-08 security pass** (do not re-open these):

- `engine.env` is mode 600 (it holds the upstream keys and shipped 664).
- Every response carries the policy: CSP (`script-src 'self'`), `nosniff`,
  `frame-ancestors 'none'`, referrer policy, permissions policy; HSTS over HTTPS
  only — `app/http_hardening.py`, proven in a real browser.
- Request bodies over 1 MiB are refused before they are read (house envelope
  `payload_too_large`, not `{"detail": ...}`).
- `rpcbind` is masked, so `0.0.0.0:111` no longer answers.
- Dependencies: `npm audit --omit=dev` reports 0 vulnerabilities and the four
  installed Python packages carry no known advisory (queried against OSV).
- Findings and the surfaces nobody has read yet: `.hermes/audit/2026-11-08_security.md`
  (gitignored — it maps holes that are not patched, so it stays on the box).

**Still optional, on purpose:** masking the raw echo in error messages (LOW; the
client escapes it and it aids diagnosis), and the second tier of systemd
sandboxing (`ProtectSystem=strict` + `ReadWritePaths`) — worth doing, needs a
careful test because the service writes to its data directories.

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
