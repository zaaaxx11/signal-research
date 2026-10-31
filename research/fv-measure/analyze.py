import json, collections, statistics, sys, os
from urllib.parse import urlparse

OUT = "/home/ubuntu/research/fv-measure"


def load(tag):
    ev = json.load(open(os.path.join(OUT, "run_%s_events.json" % tag)))
    page = json.load(open(os.path.join(OUT, "run_%s_page.json" % tag)))
    return ev, page


def analyse(tag):
    ev, page = load(tag)
    req, resp, data, fin, fails, extra = {}, {}, collections.Counter(), {}, {}, {}
    for e in ev:
        m = e["method"]; p = e["params"]
        if m == "Network.requestWillBeSent":
            rid = p["requestId"]
            req[rid] = {"url": p["request"]["url"], "type": p.get("type"),
                        "initiator": (p.get("initiator") or {}).get("type"),
                        "initUrl": (p.get("initiator") or {}).get("url", ""),
                        "ts": p.get("timestamp"), "frameId": p.get("frameId")}
        elif m == "Network.responseReceived":
            r = p["response"]
            resp[p["requestId"]] = {"status": r.get("status"), "mime": r.get("mimeType"),
                                    "type": p.get("type"), "headers": r.get("headers", {}),
                                    "fromDisk": r.get("fromDiskCache"), "fromSW": r.get("fromServiceWorker"),
                                    "remoteIP": r.get("remoteIP"), "protocol": r.get("protocol")}
        elif m == "Network.dataReceived":
            data[p["requestId"]] += p.get("dataLength", 0)
        elif m == "Network.loadingFinished":
            fin[p["requestId"]] = p.get("encodedDataLength", 0)
        elif m == "Network.loadingFailed":
            fails[p["requestId"]] = p.get("errorText")
        elif m == "Network.responseReceivedExtraInfo":
            extra[p["requestId"]] = p.get("headers", {})
    rows = []
    for rid, q in req.items():
        r = resp.get(rid, {})
        rows.append({"id": rid, "url": q["url"], "type": (r.get("type") or q["type"] or "Other"),
                     "status": r.get("status"), "wire": fin.get(rid, 0), "decoded": data[rid],
                     "initiator": q["initiator"], "initer": q.get("initUrl", ""),
                     "mime": r.get("mime"), "headers": r.get("headers", {}),
                     "extra": extra.get(rid, {}), "failed": fails.get(rid),
                     "frameId": q.get("frameId"), "fromDisk": r.get("fromDisk")})
    return rows, page, ev


def host(u):
    try:
        return urlparse(u).hostname or ""
    except Exception:
        return ""


def party(u):
    h = host(u)
    return "first" if (h == "finviz.com" or h.endswith(".finviz.com")) else "third"


def norm(u):
    """strip volatile query for dedupe"""
    return u.split("&cb=")[0]


def table(tag, topn=10, show=True):
    rows, page, ev = analyse(tag)
    # dedupe by requestId, ignore preflight noise
    by = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        k = r["type"]
        by[k][0] += 1; by[k][1] += r["wire"]; by[k][2] += r["decoded"]
    tot = [sum(v[i] for v in by.values()) for i in range(3)]
    fp = [sum(r["wire"] for r in rows if party(r["url"]) == "first"),
          sum(r["decoded"] for r in rows if party(r["url"]) == "first"),
          sum(1 for r in rows if party(r["url"]) == "first")]
    tp = [sum(r["wire"] for r in rows if party(r["url"]) == "third"),
          sum(r["decoded"] for r in rows if party(r["url"]) == "third"),
          sum(1 for r in rows if party(r["url"]) == "third")]
    out = {"tag": tag, "requests": len(rows), "by_type": dict(by), "total": tot,
           "first": fp, "third": tp, "rows": rows, "page": page,
           "doc": page["nav"]["decodedBodySize"]}
    if show:
        print("=" * 70)
        print(tag, "requests:", len(rows), "| doc decoded:", page["nav"]["decodedBodySize"])
        for k, v in sorted(by.items(), key=lambda x: -x[1][1]):
            print(f"  {k:14s} n={v[0]:3d}  wire={v[1]:9,d}  decoded={v[2]:9,d}")
        print(f"  TOTAL          n={tot[0]:3d}  wire={tot[1]:9,d}  decoded={tot[2]:9,d}")
        print(f"  first-party    n={fp[2]:3d}  wire={fp[0]:9,d}  decoded={fp[1]:9,d}  ({100*fp[0]/max(tot[1],1):.1f}% wire)")
        print(f"  third-party    n={tp[2]:3d}  wire={tp[0]:9,d}  decoded={tp[1]:9,d}  ({100*tp[0]/max(tot[1],1):.1f}% wire)")
        print("  TOP%d by wire:" % topn)
        for r in sorted(rows, key=lambda x: -x["wire"])[:topn]:
            print(f"   {r['wire']:8,d} {r['decoded']:8,d} {r['type']:10s} {party(r['url']):5s} {r['url'][:105]}")
    return out


if __name__ == "__main__":
    tags = sys.argv[1:] or ["fv_d1", "fv_d2", "fv_d3"]
    for t in tags:
        table(t)
