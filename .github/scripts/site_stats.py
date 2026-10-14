#!/usr/bin/env python3
"""Pull visit stats from GoatCounter and write site-stats.json.

Feeds the "Site Snapshot" panel on sankar.work. Runs daily from
.github/workflows/site-stats.yml. The API token stays in repo secrets;
only aggregate counts are committed.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CODE = os.environ.get("GC_CODE", "sankarwork")
TOKEN = os.environ.get("GC_TOKEN", "")
SINCE = os.environ.get("GC_SINCE", "2026-10-14")  # day counting went live
WINDOW = 90
OUT = "site-stats.json"
BASE = f"https://{CODE}.goatcounter.com/api/v0"


def get(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/json",
        "User-Agent": "sankar.work-site-stats",
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise


def get_range(path, start, end, **extra):
    """GoatCounter accepts plain dates; fall back to RFC 3339 if it doesn't."""
    try:
        return get(path, {"start": start.isoformat(), "end": end.isoformat(), **extra})
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        return get(path, {"start": f"{start}T00:00:00Z", "end": f"{end}T23:59:59Z", **extra})


def main():
    if not TOKEN:
        sys.exit("GC_TOKEN is not set (add a GOATCOUNTER_TOKEN repo secret)")
    today = dt.datetime.now(dt.timezone.utc).date()
    since = dt.date.fromisoformat(SINCE)
    start = max(since, today - dt.timedelta(days=WINDOW - 1))

    tot = get_range("/stats/total", start, today)
    per_day = {}
    for s in tot.get("stats") or []:
        day = (s.get("day") or "")[:10]
        if not day:
            continue
        n = s.get("daily")
        if n is None:
            n = sum(s.get("hourly") or [])
        per_day[day] = per_day.get(day, 0) + int(n)
    days, d = [], start
    while d <= today:
        days.append([d.isoformat(), per_day.get(d.isoformat(), 0)])
        d += dt.timedelta(days=1)
    total = int(tot.get("total") or sum(n for _, n in days))

    loc = get_range("/stats/locations", start, today, limit=6)
    countries = []
    for c in (loc.get("stats") or loc.get("hits") or [])[:6]:
        cid = (c.get("id") or "").strip()
        name = c.get("name") or cid or "Unknown"
        countries.append([cid, name, int(c.get("count") or 0)])

    period = (f"since {start:%b} {start.day}, {start.year}" if start == since
              else f"last {WINDOW} days")
    out = {
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "since": SINCE,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "period": period,
        "total": total,
        "days": days,
        "countries": countries,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
        f.write("\n")
    print(f"{OUT}: {total} visits, {len(days)} days, {len(countries)} countries")


if __name__ == "__main__":
    main()
