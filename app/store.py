"""Data access. Seed data is the default; JSON files in data/live/ (written by the fetchers)
override it per dataset, so the same app runs on synthetic or live data."""
import json
import os
import datetime as dt
from functools import lru_cache
from . import seed

LIVE = os.path.join(os.path.dirname(__file__), "..", "data", "live")


def _live(name):
    p = os.path.join(LIVE, name)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def now():
    return dt.datetime.now(dt.timezone.utc)


@lru_cache(maxsize=1)
def companies():
    return [dict(ticker=t, name=n, sector=s, cap=c, weight=w, aliases=a) for t, n, s, c, w, _, a in seed.COMPANIES]


def company(ticker):
    return next((c for c in companies() if c["ticker"] == ticker.upper()), None)


def relations(ticker):
    return seed.RELATIONS.get(ticker.upper(), {"competitors": [], "suppliers": [], "customers": []})


@lru_cache(maxsize=1)
def prices():
    base = seed.gen_prices()
    live = _live("prices.json") or {}
    ok = lambda r: isinstance(r.get("c"), (int, float)) and r["c"] == r["c"] and r["c"] > 0   # NaN != NaN
    live = {t: [r for r in v if ok(r)] for t, v in live.items()}
    base.update({t: v for t, v in live.items() if len(v) > 25})   # live data wins per ticker; empty live series fall back to seed
    return base


def live_layers():
    """Which layers are running on real data (for the UI badge)."""
    ann = bool(_live("announcements.json"))
    px = any(v for v in (_live("prices.json") or {}).values())
    fund = sorted(f[13:-5] for f in (os.listdir(LIVE) if os.path.isdir(LIVE) else []) if f.startswith("fundamentals_") and f.endswith(".json"))
    return dict(prices=px, announcements=ann, fundamentals=fund)


def announcements():
    live = _live("announcements.json")
    if live:
        return live
    n = now()
    return [dict(id=i, ticker=t, ts=(n - dt.timedelta(hours=h)).isoformat(), subject=s, body=b, person=p)
            for i, t, h, s, b, p in seed.ANNOUNCEMENTS]


@lru_cache(maxsize=1)
def people():
    return _live("people.json") or [dict(p, edu=[dict(school=a, year=b) for a, b in p["edu"]]) for p in seed.PEOPLE]


@lru_cache(maxsize=1)
def ext_people():
    return _live("ext_people.json") or [dict(p, edu=[dict(school=a, year=b) for a, b in p["edu"]]) for p in seed.EXT_PEOPLE]


def fundamentals(ticker):
    t = ticker.upper()
    live = _live(f"fundamentals_{t}.json")   # live file (from the screener fetcher) beats the synthetic seed
    if live:
        return live
    if t in seed.FUNDAMENTALS:
        return seed.FUNDAMENTALS[t]
    try:  # best-effort live fetch (needs network); cached to data/live
        from .fetchers import screener
        f = screener.fetch(t)
        os.makedirs(LIVE, exist_ok=True)
        with open(os.path.join(LIVE, f"fundamentals_{t}.json"), "w") as fh:
            json.dump(f, fh)
        return f
    except Exception:
        return None
