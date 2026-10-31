import json, collections, statistics, os
from analyze import analyse, party, host

OUT = "/home/ubuntu/research/fv-measure"

CATS = [
    ("Ad stack (prebid/header bidding)", ("investingchannel", "urbanlaurel", "amazon-adsystem",
                                          "doubleclick.net", "pubmatic", "adsrvr", "confiant",
                                          "adsafeprotected", "optable.co", "rlcdn.com",
                                          "ads.yahoo.com", "analytics.yahoo.com")),
    ("Analytics / pixels", ("googletagmanager", "clarity.ms", "quantserve", "quantcount",
                            "facebook.net", "googlesyndication", "google.com", "google-analytics")),
    ("Identity / sync", ("merequartz", "ay.delivery")),
    ("Cloudflare", ("cloudflareinsights",)),
]

def cat(u):
    h = host(u)
    for name, keys in CATS:
        if any(k in h for k in keys):
            return name
    return "First-party" if party(u) == "first" else "Other third-party"

# ---------- final numbers on the cleanest complete capture (g1) + confirm on g2
base = "fv_g1"
rows, page, ev = analyse(base)
print("== CATEGORY SPLIT (%s)" % base)
c = collections.defaultdict(lambda: [0, 0, 0])
for r in rows:
    k = cat(r["url"]); c[k][0] += 1; c[k][1] += r["wire"]; c[k][2] += r["decoded"]
tot = [sum(v[i] for v in c.values()) for i in range(3)]
for k, v in sorted(c.items(), key=lambda x: -x[1][1]):
    print("%-36s n=%3d wire=%9d (%5.1f%%) dec=%9d (%5.1f%%)" % (
        k, v[0], v[1], 100 * v[1] / tot[1], v[2], 100 * v[2] / tot[2]))
print("%-36s n=%3d wire=%9d dec=%9d" % ("TOTAL", tot[0], tot[1], tot[2]))

print("\n== JS DEEP DIVE")
scr = [r for r in rows if r["type"] == "Script"]
fp = [r for r in scr if party(r["url"]) == "first"]
tp = [r for r in scr if party(r["url"]) == "third"]
print("script requests: %d | first-party %d (wire %d, dec %d) | third %d (wire %d, dec %d)" % (
    len(scr), len(fp), sum(r["wire"] for r in fp), sum(r["decoded"] for r in fp),
    len(tp), sum(r["wire"] for r in tp), sum(r["decoded"] for r in tp)))
print("third-party share of script wire: %.1f%%" % (100 * sum(r["wire"] for r in tp) / sum(r["wire"] for r in scr)))

print("\n== TOP 10 BY WIRE (%s)" % base)
for i, r in enumerate(sorted(rows, key=lambda x: -x["wire"])[:10], 1):
    print("%2d. %8d B  dec=%8d  %-9s %-5s %s" % (i, r["wire"], r["decoded"], r["type"], party(r["url"]), r["url"][:88]))

# ---------- timing across all runs
runs = ["fv_c1", "fv_c2", "fv_c3", "fv_d1", "fv_d2", "fv_d3", "fv_g1", "fv_g2", "fv_g3"]
tab = []
for t in runs:
    p = json.load(open(os.path.join(OUT, "run_%s_page.json" % t)))
    pr = p.get("probe") or {}; n = p["nav"]
    tab.append({"run": t, "ttfb": n["ttfb"], "fcp": (pr.get("paint") or {}).get("fcp"),
                "lcp": pr.get("lcp"), "dcl": n["dcl"], "load": n["load"], "row": pr.get("rowPainted"),
                "res": len(p["resources"]), "docDec": n["decodedBodySize"]})
print("\n== TIMING TABLE (ms from navigationStart)")
print("%-7s %7s %7s %7s %8s %8s %7s %5s" % ("run", "TTFB", "FCP", "LCP", "DCL", "Load", "row1", "reqs"))
for r in tab:
    print("%-7s %7.0f %7s %7s %8.0f %8s %7s %5d" % (
        r["run"], r["ttfb"], "%.0f" % r["fcp"] if r["fcp"] else "-",
        "%.0f" % r["lcp"] if r["lcp"] else "-", r["dcl"],
        "%.0f" % r["load"] if r["load"] else "none", "%.0f" % r["row"] if r["row"] else "-", r["res"]))

print("\n== STATS n=%d" % len(tab))
for k in ("ttfb", "fcp", "lcp", "dcl", "row"):
    v = [r[k] for r in tab if r[k]]
    print("%-5s min=%6.0f med=%6.0f max=%6.0f mean=%6.0f spread=%.1fx" % (
        k, min(v), statistics.median(v), max(v), statistics.mean(v), max(v) / min(v)))

fast = [r for r in tab if r["fcp"] and r["fcp"] < 1000]
print("\nQUIET-BROWSER subset (FCP<1000ms): %s" % [r["run"] for r in fast])
for k in ("ttfb", "fcp", "dcl", "row"):
    v = [r[k] for r in fast if r[k]]
    if v:
        print("  %-5s min=%6.0f med=%6.0f max=%6.0f" % (k, min(v), statistics.median(v), max(v)))
print("\nrow1 < DCL in %d of %d runs" % (sum(1 for r in tab if r["row"] and r["row"] < r["dcl"]), len(tab)))
print("load event fired in %d of %d runs" % (sum(1 for r in tab if r["load"]), len(tab)))
