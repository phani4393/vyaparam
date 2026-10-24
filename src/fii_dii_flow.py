"""
FII/DII net cash-flow context -- the distinctively-Indian signal with no US
analogue.

Foreign Institutional Investors (FII/FPI) and Domestic Institutional Investors
(DII) publish their daily net buy/sell in the cash segment. The *direction and
divergence* of these two flows is one of the most-watched sentiment reads on
Dalal Street: sustained FII selling absorbed by DII buying (or vice versa) is
a very different tape than both pulling the same way.

This module is BEST-EFFORT and degrades gracefully. NSE's public JSON endpoint
(nseindia.com/api/fiidiiTradeReact) requires a browser-like session (a cookie
primed by first hitting the homepage) and rate-limits aggressively from
datacenter IPs, so a scheduled CI run may well get blocked. On ANY failure
this returns a result with status="unavailable" and the scanner simply omits
the flow line rather than failing the whole scan -- same "degrade visibly,
never fake data" rule as the price layer.

Numbers are in Rs crore (NSE's native unit); positive = net buy.

Usage (standalone): python fii_dii_flow.py
"""

import json
import urllib.error
import urllib.request

NSE_HOME = "https://www.nseindia.com"
NSE_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}


def _unavailable(reason):
    return {"status": "unavailable", "reason": reason, "fii_net": None,
            "dii_net": None, "date": None}


def fetch_fii_dii():
    """Returns {status, date, fii_net, dii_net, reason?}. fii_net/dii_net are
    floats in Rs crore (positive = net buy). status is 'ok' or 'unavailable'."""
    try:
        # NSE's API requires a cookie primed by first hitting a page. Build an
        # opener with a cookie jar so the homepage sets the cookie and the API
        # call carries it.
        import http.cookiejar
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        home_req = urllib.request.Request(NSE_HOME, headers=_HEADERS)
        opener.open(home_req, timeout=20).read()  # prime the cookie

        api_req = urllib.request.Request(NSE_FII_DII_URL, headers=_HEADERS)
        with opener.open(api_req, timeout=20) as resp:
            data = json.load(resp)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            ValueError, OSError) as e:
        return _unavailable(f"fetch failed: {e.__class__.__name__}")

    # Response is a list of {category, date, buyValue, sellValue, netValue}.
    if not isinstance(data, list) or not data:
        return _unavailable("unexpected response shape")

    fii_net = dii_net = None
    date = None
    for row in data:
        try:
            cat = str(row.get("category", "")).upper()
            net = float(str(row.get("netValue")).replace(",", ""))
            date = row.get("date") or date
        except (TypeError, ValueError):
            continue
        if "FII" in cat or "FPI" in cat:
            fii_net = net
        elif "DII" in cat:
            dii_net = net

    if fii_net is None and dii_net is None:
        return _unavailable("could not parse FII/DII rows")

    return {"status": "ok", "date": date, "fii_net": fii_net, "dii_net": dii_net}


def _crore(v):
    if v is None:
        return "n/a"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.0f} cr"


def flow_bias(flow):
    """Reduce the two flows to a coarse bias label for alert context. This is
    sentiment colour, NOT a trade trigger -- stated as such in the output."""
    if flow.get("status") != "ok":
        return None
    fii, dii = flow.get("fii_net"), flow.get("dii_net")
    if fii is None or dii is None:
        return None
    if fii > 0 and dii > 0:
        return "both FII+DII net buyers (risk-on)"
    if fii < 0 and dii < 0:
        return "both FII+DII net sellers (risk-off)"
    if fii < 0 and dii > 0:
        return "FII selling absorbed by DII buying (divergence)"
    if fii > 0 and dii < 0:
        return "FII buying into DII selling (divergence)"
    return "mixed/flat flows"


def flow_context_line(flow):
    """One-line FII/DII summary for an alert, or None if unavailable."""
    if flow.get("status") != "ok":
        return None
    bias = flow_bias(flow)
    datestr = f" ({flow['date']})" if flow.get("date") else ""
    return (f"FII/DII{datestr}: FII {_crore(flow['fii_net'])}, "
            f"DII {_crore(flow['dii_net'])} -- {bias} [context only, not a trigger]")


def main():
    flow = fetch_fii_dii()
    if flow["status"] != "ok":
        print(f"FII/DII flow unavailable: {flow.get('reason')}")
        print("(NSE blocks datacenter IPs aggressively; this is expected in CI. "
              "Run locally for live figures.)")
        return
    print("=" * 60)
    print("FII/DII NET CASH FLOW")
    print("=" * 60)
    print(f"Date:     {flow['date']}")
    print(f"FII net:  Rs {_crore(flow['fii_net'])}")
    print(f"DII net:  Rs {_crore(flow['dii_net'])}")
    print(f"Bias:     {flow_bias(flow)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
