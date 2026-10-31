import json, collections, statistics
from analyze import analyse, party, host

rows, page, ev = analyse("fv_g1")

# ---------- by type
print("== BY TYPE")
by = collections.defaultdict(lambda: [0, 0, 0])
for r in rows:
    by[r["type"]][0] += 1; by[r["type"]][1] += r["wire"]; by[r["type"]][2] += r["decoded"]
tot = [sum(v[i] for v in by.values()) for i in range(3)]
for k, v in sorted(by.items(), key=lambda x: -x[1][1]):
    print(f"{k:12s} {v[0]:3d} {v[1]:9d} {v[2]:9d}")
print(f"{'TOTAL':12s} {tot[0]:3d} {tot[1]:9d} {tot[2]:9d}")

# ---------- by host
print("\n== BY HOST")
bh = collections.defaultdict(lambda: [0, 0, 0])
for r in rows:
    h = host(r["url"]); bh[h][0] += 1; bh[h][1] += r["wire"]; bh[h][2] += r["decoded"]
for h, v in sorted(bh.items(), key=lambda x: -x[1][1])[:20]:
    print(f"{h[:44]:44s} {v[0]:3d} {v[1]:9d} {v[2]:9d}")

# ---------- first-party scripts
print("\n== FIRST-PARTY SCRIPTS (loaded)")
fps = [r for r in rows if r["type"] == "Script" and party(r["url"]) == "first"]
fpd = {}
for r in fps:
    n = r["url"].split("/assets/dist/")[-1].split("?")[0]
    fpd[n] = fpd.get(n, 0) + r["wire"]
for n, w in sorted(fpd.items(), key=lambda x: -x[1]):
    print(f"  {w:8d}  {n}")
print("  first-party script n=%d wire=%d dec=%d" % (
    len(fps), sum(r["wire"] for r in fps), sum(r["decoded"] for r in fps)))

print("\n== THIRD-PARTY SCRIPTS by host")
tps = collections.defaultdict(lambda: [0, 0, 0])
for r in rows:
    if r["type"] == "Script" and party(r["url"]) == "third":
        h = host(r["url"]); tps[h][0] += 1; tps[h][1] += r["wire"]; tps[h][2] += r["decoded"]
for h, v in sorted(tps.items(), key=lambda x: -x[1][1]):
    print(f"  {v[1]:8d} {v[2]:9d} n={v[0]:2d} {h}")
print("  third script total wire=%d dec=%d" % (sum(v[1] for v in tps.values()), sum(v[2] for v in tps.values())))

# ---------- css
print("\n== STYLESHEETS")
for r in rows:
    if r["type"] == "Stylesheet" or (r["mime"] or "").startswith("text/css"):
        hd = {k.lower(): v for k, v in r["headers"].items()}
        hd2 = {k.lower(): v for k, v in (r["extra"] or {}).items()}
        print(f"  wire={r['wire']:7d} dec={r['decoded']:8d} {r['url'][:110]}")
        print(f"     cc={hd2.get('cache-control') or hd.get('cache-control')} cf={hd2.get('cf-cache-status') or hd.get('cf-cache-status')} etag={(hd2.get('etag') or hd.get('etag'))}")

# ---------- images
print("\n== IMAGES")
imgs = [r for r in rows if r["type"] == "Image"]
kind = collections.Counter()
ext = collections.Counter()
for r in imgs:
    u = r["url"]
    if u.startswith("data:"):
        kind["data-uri"] += 1; ext[u.split(";")[0][5:]] += 1
    elif "logo.finviz.com" in u:
        kind["logo.finviz.com"] += 1; ext[u.rsplit(".", 1)[-1]] += 1
    elif u.startswith("blob:"):
        kind["blob"] += 1
    else:
        kind[host(u)] += 1; ext[u.rsplit(".", 1)[-1].split("?")[0]] += 1
print("  kinds:", dict(kind))
print("  formats:", dict(ext))
print("  image wire=%d dec=%d" % (sum(r["wire"] for r in imgs), sum(r["decoded"] for r in imgs)))
big = sorted([r for r in imgs if r["wire"] > 0], key=lambda x: -x["wire"])[:8]
for r in big:
    print(f"   {r['wire']:7d} {r['url'][:100]}")

# ---------- cache headers on doc / one js / one css
print("\n== CACHE HEADERS")
for want in ("Document", "Script", "Stylesheet"):
    r = next((x for x in rows if x["type"] == want and x["wire"] > 0), None)
    if not r:
        continue
    hd = {k.lower(): v for k, v in (r["headers"] or {}).items()}
    hd2 = {k.lower(): v for k, v in (r["extra"] or {}).items()}
    print(f"  {want} {r['url'][:90]}")
    for k in ("cache-control", "etag", "last-modified", "cf-cache-status", "age", "content-encoding", "expires", "vary"):
        v = hd2.get(k) or hd.get(k)
        if v:
            print(f"     {k}: {v}")

print("\n== SERVED FROM CACHE:", sum(1 for e in ev if e["method"] == "Network.requestServedFromCache"))
print("== FAILED:", sum(1 for e in ev if e["method"] == "Network.loadingFailed"))
