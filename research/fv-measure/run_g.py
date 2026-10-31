import json, time, sys
sys.path.insert(0, "/home/ubuntu/research")
import harness

REAL_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
META = {"brands": [{"brand": "Chromium", "version": "145"}, {"brand": "Google Chrome", "version": "145"},
                   {"brand": "Not_A Brand", "version": "24"}],
        "fullVersionList": [{"brand": "Chromium", "version": "145.0.7632.0"},
                            {"brand": "Google Chrome", "version": "145.0.7632.0"}],
        "fullVersion": "145.0.7632.0", "platform": "Linux", "platformVersion": "6.17.0",
        "architecture": "x86", "model": "", "mobile": False, "bitness": "64", "wow64": False}


def run(tag, G, settle=4.0, url="https://finviz.com/screener/stocks?v=111"):
    cdp, js, drain_events, new_tab = G["cdp"], G["js"], G["drain_events"], G["new_tab"]
    tid = new_tab("about:blank"); time.sleep(0.4)
    S = cdp("Target.attachToTarget", targetId=tid, flatten=True)["sessionId"]

    def C(m, **kw): return cdp(m, session_id=S, **kw)

    C("Emulation.setUserAgentOverride", userAgent=REAL_UA, acceptLanguage="en-US,en;q=0.9",
      platform="Linux x86_64", userAgentMetadata=META)
    C("Network.enable", maxTotalBufferSize=200_000_000, maxResourceBufferSize=50_000_000)
    C("Page.enable"); C("Performance.enable")
    C("Network.setExtraHTTPHeaders", headers={"Accept-Language": "en-US,en;q=0.9"})
    C("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
    C("Page.bringToFront"); C("Emulation.setFocusEmulationEnabled", enabled=True)
    C("Network.setCacheDisabled", cacheDisabled=True); C("Network.clearBrowserCache")
    C("Page.addScriptToEvaluateOnNewDocument", source=harness.STEALTH)
    C("Page.addScriptToEvaluateOnNewDocument", source=harness.PROBE)
    time.sleep(0.3); drain_events()
    C("Page.navigate", url=url)
    ev = []; t0 = time.time()
    while time.time() - t0 < 30:
        while True:
            ch = [e for e in drain_events() if e.get("session_id") == S]
            ev.extend(ch)
            if len(ch) < 400:
                break
        try:
            if js("document.querySelectorAll('[data-ticker]').length") >= 20 and \
               js("document.readyState") in ("interactive", "complete"):
                break
        except Exception:
            pass
        time.sleep(0.05)
    t1 = time.time()
    while time.time() - t1 < settle:
        while True:
            ch = [e for e in drain_events() if e.get("session_id") == S]
            ev.extend(ch)
            if len(ch) < 400:
                break
        time.sleep(0.1)
    page = js(harness.SUMMARY_JS)
    json.dump(ev, open("/home/ubuntu/research/fv-measure/run_%s_events.json" % tag, "w"))
    open("/home/ubuntu/research/fv-measure/run_%s_page.json" % tag, "w").write(page)
    p = json.loads(page); pr = p.get("probe") or {}; n = p["nav"]
    return {"tag": tag, "title": p["title"], "ttfb": n["ttfb"], "fcp": (pr.get("paint") or {}).get("fcp"),
            "lcp": pr.get("lcp"), "dcl": n["dcl"], "load": n["load"], "row": pr.get("rowPainted"),
            "res": len(p["resources"]), "tr": sum(r["tr"] for r in p["resources"]),
            "events": len(ev), "rows": p["rowCount"]}
