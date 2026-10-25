#!/usr/bin/env python3
"""Refresh site-stats.json for the "Site Snapshot" panel on sankar.work.

Daily visitors = Google Analytics history (imported once, kept in the
file's "history" block) + GoatCounter from the day it went live. The
panel shows the most recent 90 days. Runs from
.github/workflows/site-stats.yml; the API token stays in repo secrets
and only aggregate counts are committed.
"""
import collections
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
SINCE = os.environ.get("GC_SINCE", "2026-10-14")  # day GoatCounter went live
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
            body = e.read().decode("utf-8", "replace")[:500]
            sys.exit(f"GoatCounter API {path} failed: HTTP {e.code}: {body}")


def get_range(path, start, end, **extra):
    """GoatCounter wants RFC 3339 times rounded to the hour, not in the future."""
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    stop = min(now, dt.datetime.combine(end, dt.time(23), tzinfo=dt.timezone.utc))
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    begin = dt.datetime.combine(start, dt.time(0), tzinfo=dt.timezone.utc)
    return get(path, {"start": begin.strftime(fmt), "end": stop.strftime(fmt), **extra})


def main():
    if not TOKEN:
        sys.exit("GC_TOKEN is not set (add a GOATCOUNTER_TOKEN repo secret)")
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = {}
    hist = prev.get("history") or {}

    today = dt.datetime.now(dt.timezone.utc).date()
    since = dt.date.fromisoformat(SINCE)
    win_start = today - dt.timedelta(days=WINDOW - 1)

    # Daily counts: imported Google Analytics history is authoritative up to its
    # last day (hist "end"); GoatCounter fills every day after that.
    hist_days = hist.get("days") or []
    hist_end = hist.get("end") or (hist_days[-1][0] if hist_days else (since - dt.timedelta(days=1)).isoformat())
    per_day = {d: int(n) for d, n in hist_days if d <= hist_end}
    gc_start = max(since, win_start)
    gc_count_from = max(gc_start, dt.date.fromisoformat(hist_end) + dt.timedelta(days=1))
    events = int(hist.get("events") or 0) if (hist.get("days") or [["9999"]])[-1][0] >= win_start.isoformat() else 0
    top_events = []
    if gc_start <= today:
        # Per-path stats: page paths give daily visitors; event paths
        # (clicks sent by the site, e.g. "project-ctm-agentic") give events.
        res = get_range("/stats/hits", gc_start, today, limit=100)
        hits = res.get("hits") or []
        if res.get("more"):
            print("warning: more than 100 paths; extra event paths not counted")
        page_visits = clicks = 0
        for h in hits:
            if h.get("event"):
                top_events.append([h.get("path") or "", int(h.get("count") or 0)])
            for st in h.get("stats") or []:
                day = (st.get("day") or "")[:10]
                if not day or day < gc_count_from.isoformat():
                    continue
                n = st.get("daily")
                if n is None:
                    n = sum(st.get("hourly") or [])
                if h.get("event"):
                    clicks += int(n)
                else:
                    per_day[day] = per_day.get(day, 0) + int(n)
                    page_visits += int(n)
        # Same meaning as Google Analytics' event count: page views + interactions.
        events += page_visits + clicks
        top_events.sort(key=lambda x: -x[1])

    first = min([d for d in per_day] + [gc_start.isoformat()])
    start = max(win_start, dt.date.fromisoformat(first))
    days, d = [], start
    while d <= today:
        days.append([d.isoformat(), per_day.get(d.isoformat(), 0)])
        d += dt.timedelta(days=1)
    total = sum(n for _, n in days)

    # Countries: GoatCounter, plus history while any history day is in the window.
    counts, names = collections.Counter(), {}
    ctotal = 0
    if any(day >= start.isoformat() for day, _ in hist.get("days") or []):
        for cid, name, n in hist.get("countries") or []:
            counts[cid] += int(n)
            names[cid] = name
        ctotal += int(hist.get("countries_total") or sum(int(c[2]) for c in hist.get("countries") or []))
    if gc_count_from <= today:
        loc = get_range("/stats/locations", gc_count_from, today, limit=50)
        for c in loc.get("stats") or loc.get("hits") or []:
            cid = (c.get("id") or "").strip()
            n = int(c.get("count") or 0)
            ctotal += n
            if not cid:
                continue
            counts[cid] += n
            names[cid] = c.get("name") or cid
    countries = [[cid, names[cid], n] for cid, n in counts.most_common(6)]

    period = (f"since {start:%b} {start.day}, {start.year}"
              if (today - start).days < WINDOW - 1 else f"last {WINDOW} days")
    out = {
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "since": SINCE,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "period": period,
        "total": total,
        "events": events,
        "top_events": top_events[:10],
        "days": days,
        "countries": countries,
        "countries_total": max(ctotal, sum(counts.values())),
    }
    if hist:
        out["history"] = hist
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
        f.write("\n")
    print(f"{OUT}: {total} visitors, {len(days)} days ({period}), {len(countries)} countries")


if __name__ == "__main__":
    main()
