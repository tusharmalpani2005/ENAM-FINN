"""Pre-compute model answers so the deployed app serves them from the on-disk cache (data/cache/) instead of
calling a rate-limited API on every visit.   Usage:  python3 -m app.warm [TICKER ...]
Default tickers = the companies you demo. Waits out Groq's rate limit; re-run it safely (cached answers are skipped)."""
import sys
import time
from . import llm, push, pull


def main():
    if not llm.enabled():
        print("No LLM configured (set LLM_PROVIDER / GROQ_API_KEY in .env). Nothing to warm.")
        return
    llm.PATIENT = True
    tickers = [t.upper() for t in sys.argv[1:]] or ["ASIANPAINT", "DEEPAKNTR", "KPITTECH", "TITAN"]
    t0 = time.time()
    for h in (24, 72, 168, 720):
        push.digest(h)
        print(f"filing summaries for the last {h}h done ({time.time() - t0:.0f}s)", flush=True)
    for t in tickers:
        try:
            r = pull.analyse(t)
            print(f"{t:<11} rows={len(r['narrative_vs_numbers'])} scored={r['scorecard']['measured']}  ({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:
            print(f"{t:<11} FAIL {type(e).__name__}: {str(e)[:100]}", flush=True)
    print(llm.STATS)


if __name__ == "__main__":
    main()