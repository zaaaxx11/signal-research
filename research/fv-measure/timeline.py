import json, collections, statistics, os
from analyze import analyse, party, host

OUT = "/home/ubuntu/research/fv-measure"

# ---------------- timing table across all valid runs
runs = ["fv_c1", "fv_c2", "fv_c3", "fv_d1", "fv_d2", "fv_d3", "fv_g1"]
rows_out = []
for t in runs:
    p = json.load(open(os.path.join(OUT, "run_%s_page.json" % t)))
    pr = p.get("probe") or {}
    n = p["nav"]
    rows_out.append({
        "run": t, "ttfb": n["ttfb"], "fcp": (pr.get("paint") or {}).get("fcp"),
        "lcp": pr.get("lcp"), "dcl": n["dcl"], "load": n["load"],
        "row": pr.get("rowPainted"), "rowDom": pr.get("rowInDom"), "all": pr.get("allRows"),
        "docDec": n["decodedBodySize"], "docEnc": n["encodedBodySize"],
        "res": len(p["resources"]), "tr": sum(r["tr"] for r in p["resources"]),
        "frames": pr.get("frames"), "rows": p["rowCount"]})

cols = ["ttfb", "fcp", "lcp", "dcl", "load", "row"]
print("== TIMING (ms from navigationStart)")
print("%-8s %8s %8s %8s %9s %9s %8s" % ("run", "TTFB", "FCP", "LCP", "DCL", "Load", "row1"))
for r in rows_out:
    print("%-8s %8s %8s %8s %9s %9s %8s" % (r["run"], fmt(r["ttfb"]) if False else
          "%.0f" % r["ttfb"], "%.0f" % r["fcp"] if r["fcp"] else "-",
          "%.0f" % r["lcp"] if r["lcp"] else "-",
          "%.0f" % r["dcl"] if r["dcl"] else "-",
          "%.0f" % r["load"] if r["load"] else "none",
          "%.0f" % r["row"] if r["row"] else "-"))

print("\n== STATS (min / median / max, 7 runs)")
for c in cols:
    v = [r[c] for r in rows_out if r[c]]
    if not v:
        continue
    print("%-6s n=%d  min=%7.0f  med=%7.0f  max=%7.0f  spread=%.1fx" % (
        c, len(v), min(v), statistics.median(v), max(v), max(v) / min(v)))

# ---------------- critical path: what lands before first row paint
rows, page, ev = analyse("fv_g1")
doc_ts = None
for e in ev:
    if e["method"] == "Network.requestWillBeSent" and e["params"].get("type") == "Document":
        doc_ts = e["params"]["timestamp"]; break
end = {}
for e in ev:
    if e["method"] == "Network.loadingFinished":
        end[e["params"]["requestId"]] = e["params"]["timestamp"]
    if e["method"] == "Network.loadingFailed":
        end[e["params"]["requestId"]] = e["params"]["timestamp"]

p = json.load(open(os.path.join(OUT, "run_fv_g1_page.json")))
row_ms = (p["probe"] or {}).get("rowPainted")
timeline = []
for r in rows:
    t = end.get(r["id"])
    if not t:
        continue
    timeline.append((round((t - doc_ts) * 1000), r["wire"], r["type"], r["url"], party(r["url"])))

before = [x for x in timeline if x[0] <= row_ms]
after = [x for x in timeline if x[0] > row_ms]
print("\n== CRITICAL PATH (first row painted at %.0f ms)" % row_ms)
print("requests done before row: %d  (wire %d B) | after: %d (wire %d B)" % (
    len(before), sum(x[1] for x in before), len(after), sum(x[1] for x in after)))
print("first-party wire before row: %d of %d total first-party" % (
    sum(x[1] for x in before if x[4] == "first"),
    sum(x[1] for x in timeline if x[4] == "first")))
print("\ntop requests completing before the row:")
for x in sorted(before, key=lambda y: -y[1])[:10]:
    print("  %6d ms %8d %-10s %s" % (x[0], x[1], x[2], x[3][:88]))
print("\nfirst 12 first-party items (by completion):")
fp = sorted([x for x in timeline if x[4] == "first"], key=lambda y: y[0])
for x in fp[:14]:
    print("  %6d ms %8d %-10s %s" % (x[0], x[1], x[2], x[3].replace("https://finviz.com/assets/dist/", "")[:70]))

# ---------------- JS execution cost (Performance.getMetrics deltas)
print("\n== MAIN-THREAD COST (Performance.getMetrics deltas)")
for t in ("fv_d1", "fv_d2", "fv_d3"):
    m = json.load(open(os.path.join(OUT, "run_%s_metrics.json" % t)))
    b = {x["name"]: x["value"] for x in m["before"]}
    a = {x["name"]: x["value"] for x in m["after"]}
    d = {k: a.get(k, 0) - b.get(k, 0) for k in a}
    print("%-8s ScriptDuration=%.2fs TaskDuration=%.2fs LayoutDuration=%.2fs StyleRecalc=%.2fs "
          "Layouts=%d Recalcs=%d Nodes_delta=%d JSHeap=%.1fMB" % (
              t, d.get("ScriptDuration", 0), d.get("TaskDuration", 0), d.get("LayoutDuration", 0),
              d.get("RecalcStyleDuration", 0), int(d.get("LayoutCount", 0)), int(d.get("RecalcStyleCount", 0)),
              int(d.get("Nodes", 0)), a.get("JSHeapUsedSize", 0) / 1e6))
