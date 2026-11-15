"""screener.in -> fundamentals schema used by app/pull.py.   Usage: python3 -m app.fetchers.screener TICKER
Scrapes the standardised annual / quarterly tables, then concall-transcript PDFs (pypdf).
Written WITHOUT access to the live site: expect to adjust row labels. It prints a data-quality report so you can
see exactly what was found vs. missing, and the research engine refuses to analyse data that fails the checks.
For personal research only; respect screener.in's terms of use."""
import io, re, sys, json, os

CLEAN = re.compile(r"[\s +]+$")


def _num(x):
    x = x.replace(",", "").replace("%", "").replace(" ", "").strip()
    try: return float(x)
    except ValueError: return None


def _table(soup, sid):
    """-> {row label: {column header: value}}  (aligned to headers, so TTM / half-year columns can be filtered out)"""
    sec = soup.find("section", id=sid)
    rows = {}
    if not sec:
        return rows
    heads = [th.get_text(strip=True) for th in sec.select("thead th")][1:]
    for tr in sec.select("tbody tr"):
        tds = [td.get_text(strip=True) for td in tr.select("td")]
        if len(tds) < 2:
            continue
        rows[CLEAN.sub("", tds[0])] = {h: _num(v) for h, v in zip(heads, tds[1:])}
    return rows


def _pick(rows, names, cols):
    for n in names:
        if n in rows:
            return [rows[n].get(c) or 0.0 for c in cols]
    return [0.0] * len(cols)


def qlabel(h):
    """'Sep 2025' -> 'Q2FY26' (Indian fiscal year ends in March)"""
    mon, yr = h.split()[0][:3], int(h.split()[-1])
    q = dict(Jun=1, Sep=2, Dec=3, Mar=4).get(mon, 0)
    return f"Q{q}FY{str(yr + (0 if mon == 'Mar' else 1))[-2:]}"


def call_quarter(mon, yr):
    """Earnings-call month -> quarter being discussed. Nov 2025 -> Q2FY26; Feb 2026 -> Q3FY26; May 2026 -> Q4FY26; Aug 2026 -> Q1FY27."""
    m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].index(mon[:3]) + 1
    q, fy = (4, yr) if 4 <= m <= 6 else (1, yr + 1) if 7 <= m <= 9 else (2, yr + 1) if 10 <= m <= 12 else (3, yr)
    return f"Q{q}FY{str(fy)[-2:]}"


def fetch(ticker):
    import requests
    from bs4 import BeautifulSoup
    s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"
    html = None
    for path in ("consolidated/", ""):
        r = s.get(f"https://www.screener.in/company/{ticker}/{path}", timeout=25)
        if r.status_code == 200 and 'id="profit-loss"' in r.text:
            html = r.text; break
    if html is None:
        raise RuntimeError(f"screener.in has no page for {ticker} (HTTP {r.status_code})")
    soup = BeautifulSoup(html, "html.parser")
    name = (soup.find("h1") or soup.title).get_text(strip=True)

    q, pl, bs, cf, ra = (_table(soup, i) for i in ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios"))
    first = lambda d: next(iter(d.values()), {})
    qcols = [h for h in first(q) if re.match(r"[A-Z][a-z]{2} \d{4}", h)][-8:]
    ycols = [h for h in first(pl) if h.startswith("Mar")][-5:]           # excludes the TTM column
    REV, OP = ("Sales", "Revenue"), ("Operating Profit", "Financing Profit")
    cfo = _pick(cf, ("Cash from Operating Activity",), ycols)
    inv = _pick(cf, ("Fixed assets purchased", "Cash from Investing Activity"), ycols)
    ann = dict(years=["FY" + c[-2:] for c in ycols], revenue=_pick(pl, REV, ycols), ebitda=_pick(pl, OP, ycols), pat=_pick(pl, ("Net Profit",), ycols),
               cfo=cfo, capex=[abs(x) for x in inv],   # capex proxy: investing outflow if the capex sub-row isn't in the static HTML
               debt=_pick(bs, ("Borrowings",), ycols),
               equity=[a + b for a, b in zip(_pick(bs, ("Equity Capital",), ycols), _pick(bs, ("Reserves",), ycols))],
               receivable_days=_pick(ra, ("Debtor Days",), ycols), inventory_days=_pick(ra, ("Inventory Days",), ycols),
               payable_days=_pick(ra, ("Days Payable", "Payable Days"), ycols))
    quarters = dict(labels=[qlabel(h) for h in qcols], revenue=_pick(q, REV, qcols), ebitda=_pick(q, OP, qcols), pat=_pick(q, ("Net Profit",), qcols))

    tx = []   # concall transcripts: <a>Transcript</a> inside the documents section
    for link in soup.select("a"):
        if link.get_text(strip=True).lower() == "transcript" and len(tx) < 4:
            try:
                from pypdf import PdfReader
                pdf = s.get(link["href"], timeout=40).content
                text = re.sub(r"\s+", " ", " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages))
                li = link.find_parent("li")
                label = li.get_text(" ", strip=True) if li else ""
                m = re.search(r"([A-Z][a-z]{2})\s+(\d{4})", label)
                tx.append(dict(quarter=call_quarter(m.group(1), int(m.group(2))) if m else "", date=m.group(0) if m else "", text=text))
            except Exception as e:
                print("  ! transcript skipped:", type(e).__name__, e)
    about = soup.select_one(".company-profile .about")
    f = dict(ticker=ticker, name=name, source="screener.in (live)",
             business=dict(summary=about.get_text(" ", strip=True)[:600] if about else "", segments=[]),
             quarters=quarters, annual=ann,
             ar_notes=dict(contingent=dict(text="Not extracted: read the annual report notes", amount=0), rpt="Not extracted", auditor="Not extracted"),
             transcripts=list(reversed(tx)))
    f["quality"] = quality(f)
    return f


def quality(f):
    """List of problems; empty list = usable."""
    bad, A, Q = [], f["annual"], f["quarters"]
    if len(A["years"]) < 4: bad.append(f"only {len(A['years'])} annual columns found")
    if len(Q["labels"]) < 8: bad.append(f"only {len(Q['labels'])} quarters found (need 8 for YoY on the last 4)")
    for k in ("revenue", "ebitda", "pat", "cfo", "debt", "equity"):
        if not any(A[k]): bad.append(f"annual '{k}' is all zero / missing (row label probably differs)")
    for k in ("receivable_days", "inventory_days", "payable_days", "capex"):
        if not any(A[k]): bad.append(f"annual '{k}' missing (shown as 0; working-capital / FCF views will be weaker)")
    if not f["transcripts"]: bad.append("no concall transcripts parsed: 'narrative vs numbers' will be empty")
    return bad


if __name__ == "__main__":
    from .. import store
    t = sys.argv[1].upper()
    f = fetch(t)
    print(f"{f['name']}: {len(f['annual']['years'])} years {f['annual']['years']}, {len(f['quarters']['labels'])} quarters, {len(f['transcripts'])} transcripts")
    print("last 3 quarters revenue/ebitda/pat:", [f['quarters'][k][-3:] for k in ("revenue", "ebitda", "pat")])
    print("DATA QUALITY:", f["quality"] or "all checks passed")
    os.makedirs(store.LIVE, exist_ok=True)
    json.dump(f, open(os.path.join(store.LIVE, f"fundamentals_{t}.json"), "w"))
    print("saved data/live/fundamentals_%s.json" % t)
