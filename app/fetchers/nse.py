"""Live corporate announcements from NSE -> data/live/announcements.json. Usage: python3 -m app.fetchers.nse
NSE needs a cookie warm-up and often blocks scripted/cloud traffic; this prints exactly what it got.
`body` holds only NSE's short attachment text. For results filings the numbers sit in the attached XBRL/PDF,
so the results parser only fires if you extend fetch_body() to download that attachment."""
import json, os, datetime as dt, time
from .. import seed, store

H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
     "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"}
ALIAS = {"TATAMOTORS": "TMPV"}


def run(tickers=None, days=10):
    import requests
    s = requests.Session(); s.headers.update(H)
    r = s.get("https://www.nseindia.com", timeout=20)
    print("warm-up status", r.status_code)
    out = []
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%m-%Y")
    for t in tickers or [c[0] for c in seed.COMPANIES]:
        sym = ALIAS.get(t, t)
        try:
            r = s.get("https://www.nseindia.com/api/corporate-announcements",
                      params=dict(index="equities", symbol=sym, from_date=since, to_date=dt.date.today().strftime("%d-%m-%Y")), timeout=20)
            data = r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f"  ! {t}: {type(e).__name__}: {e}"); continue
        if r.status_code != 200:
            print(f"  ! {t}: HTTP {r.status_code}"); continue
        for a in data:
            ts = dt.datetime.strptime(a["an_dt"], "%d-%b-%Y %H:%M:%S").replace(tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
            out.append(dict(id=f"{t}-{a.get('seq_id', a['an_dt'])}", ticker=t, ts=ts.isoformat(), subject=a.get("desc", ""),
                            body=a.get("attchmntText", ""), person=None, url=a.get("attchmntFile")))
        print(f"  ok {t:<11} {len(data)} announcements")
        time.sleep(0.6)
    if out:
        os.makedirs(store.LIVE, exist_ok=True)
        json.dump(out, open(os.path.join(store.LIVE, "announcements.json"), "w"))
        print(f"saved {len(out)} announcements")
    else:
        print("NOTHING saved (NSE returned no data / blocked you). The app stays on synthetic filings. Try a different network or later.")
    return out


if __name__ == "__main__":
    run()
