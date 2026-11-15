"""Live EOD prices -> data/live/prices.json (same schema as the seed). Usage: python3 -m app.fetchers.yahoo
Needs network + yfinance. Tickers that return no data are reported and left on the synthetic series."""
import json, os
from .. import seed, store

# Yahoo symbols that differ from our portfolio tickers (corporate actions / renames). Verify on finance.yahoo.com.
ALIAS = {"TATAMOTORS": "TMPV"}   # Tata Motors demerged into TMPV (passenger) and TMCV (commercial) in 2025


def run(tickers=None, period="6mo"):
    import yfinance as yf
    out, bad = {}, []
    for t in tickers or [c[0] for c in seed.COMPANIES]:
        sym = ALIAS.get(t, t)
        try:
            df = yf.Ticker(f"{sym}.NS").history(period=period)
        except Exception as e:
            df = None
            print(f"  ! {t} ({sym}.NS): {e}")
        if df is not None:
            df = df.dropna(subset=["Close"])        # today's unfinished session can have a NaN close
        if df is None or df.empty:
            bad.append(t); continue
        out[t] = [dict(d=i.date().isoformat(), c=round(float(r["Close"]), 2), v=int(r["Volume"] or 0)) for i, r in df.iterrows()]
        print(f"  ok {t:<11} {len(out[t])} days, last close {out[t][-1]['c']} on {out[t][-1]['d']}")
    if out:
        os.makedirs(store.LIVE, exist_ok=True)
        json.dump(out, open(os.path.join(store.LIVE, "prices.json"), "w"))
    print(f"saved {len(out)} tickers; no data for: {bad or 'none'}")
    return out


if __name__ == "__main__":
    run()
