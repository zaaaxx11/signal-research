import re, json, subprocess, collections, concurrent.futures as cf

HTML = "/home/ubuntu/research/stocks.html"
h = open(HTML, encoding="utf-8", errors="replace").read()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"

# --- script tags with attributes
tags = re.findall(r"<script([^>]*)>", h)
scripts = []
for a in tags:
    m = re.search(r'\ssrc="([^"]+)"', a)
    if m:
        scripts.append({"src": m.group(1),
                        "module": 'type="module"' in a or "type='module'" in a,
                        "defer": " defer" in a, "async": "async" in a or "async=true" in a})
uniq = {}
for s in scripts:
    uniq.setdefault(s["src"], s)
print("script tags with src:", len(scripts), "| unique src:", len(uniq))

css = re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"', h)
print("stylesheets:", len(css), css)

inline = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", h, re.S)
inline_bytes = sum(len(b) for _, b in inline)
json_islands = [(a, len(b)) for a, b in inline if "application/json" in a]


def head(url):
    r = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-D", "-", "--compressed",
                        "-A", UA, "-H", "Referer: https://finviz.com/screener/stocks?v=111",
                        "-w", "%{size_download}", url], capture_output=True, text=True, timeout=60)
    hdrs, _, size = r.stdout.partition("\n\n")
    d = {}
    for line in hdrs.splitlines():
        if ":" in line and not line.startswith("HTTP"):
            k, v = line.split(":", 1)
            d[k.strip().lower()] = v.strip()
    return {"url": url, "br_bytes": int(size or 0), "headers": d}


def gz(url):
    """gzip size of the decoded body"""
    r = subprocess.run(["bash", "-c",
                        f'curl -sS --compressed -A "{UA}" "{url}" | gzip -9 -c | wc -c'],
                       capture_output=True, text=True, timeout=90)
    try:
        return int(r.stdout.strip())
    except Exception:
        return 0


res = {}
with cf.ThreadPoolExecutor(6) as ex:
    futs = {ex.submit(head, "https://finviz.com" + u): u for u in uniq}
    for f in cf.as_completed(futs):
        res[futs[f]] = f.result()

for u, s in uniq.items():
    s.update({k: res[u]["headers"].get(k) for k in
              ("content-encoding", "cache-control", "cf-cache-status", "etag", "content-type")})
    s["br_bytes"] = res[u]["br_bytes"]

json.dump({"scripts": list(uniq.values()), "css": css, "inline_bytes": inline_bytes,
           "json_islands": json_islands, "script_tag_count": len(scripts),
           "inline_count": len(inline)},
          open("/home/ubuntu/research/fv-measure/assets.json", "w"), indent=1)

print("\n%-46s %8s %s %s %s %s" % ("bundle", "br B", "mod", "def", "asy", "cf"))
for s in sorted(uniq.values(), key=lambda x: -x["br_bytes"]):
    print("%-46s %8d %-3s %-3s %-3s %s" % (s["src"].replace("/assets/dist/", ""), s["br_bytes"],
                                           "Y" if s["module"] else "", "Y" if s["defer"] else "",
                                           "Y" if s["async"] else "", s["cf-cache-status"]))
print("\ninline <script> blocks:", len(inline), "bytes:", inline_bytes)
print("json islands:", json_islands)
