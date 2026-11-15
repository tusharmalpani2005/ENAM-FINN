"""Push: classify filings, rank them, parse results, compute alerts, enrich with the Network."""
import re
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from . import store, network, llm

# ---- classification (rules mirror the reference categories; an LLM can replace this for ambiguous items)
RULES = [
    ("results", r"financial results|outcome of board meeting.*results|quarter ended"),
    ("management_change", r"appointment of .*(officer|director|ceo|cfo|md)|resignation|cessation|retire"),
    ("litigation", r"warning letter|show.?cause|penalty|fine\b|insolvency|court|order from|regulatory order"),
    ("m&a", r"acquisition|merger|demerger|joint venture|stake in|divest"),
    ("fund_raise", r"qip|rights issue|preferential|buyback|commercial paper|fund raising|ncd"),
    ("credit_rating", r"rating|crisil|icra|care ratings|reaffirm"),
    ("dividend", r"dividend"),
    ("bonus_split", r"bonus issue|stock split|sub-?division"),
    ("capex", r"capex|capacity expansion|new plant|greenfield"),
    ("pledging", r"pledge|invocation|release of pledge"),
    ("insider_trading", r"reg(ulation)? ?7\b|sale of shares by|purchase of shares by|promoter group"),
    ("earnings_call", r"conference call|earnings call|transcript|audio recording"),
    ("investor_meetings", r"analyst|institutional investor|investor meet"),
    ("postal_ballot", r"postal ballot"),
    ("agm_egm", r"annual general meeting|extraordinary general|\bagm\b|\begm\b"),
    ("press_release", r"press release|clarification|partnership|order win|launch"),
]
BASE = dict(results=88, management_change=82, litigation=78, **{"m&a": 85}, fund_raise=70, pledging=66, capex=62,
            credit_rating=55, insider_trading=52, dividend=46, bonus_split=46, press_release=40, earnings_call=34,
            investor_meetings=20, agm_egm=18, postal_ballot=14, unclassified=5)
LABEL = {"m&a": "M&A", "management_change": "Management change", "fund_raise": "Fund raise", "credit_rating": "Credit rating",
         "insider_trading": "Insider trade", "bonus_split": "Bonus / split", "earnings_call": "Earnings call",
         "investor_meetings": "Investor meetings", "postal_ballot": "Postal ballot", "agm_egm": "AGM / EGM",
         "press_release": "Press release"}


def classify(subject):
    s = subject.lower()
    for cat, pat in RULES:
        if re.search(pat, s):
            return cat
    return "unclassified"


# ---- results parser: works on the exchange's table text; header dates decide which column is QoQ / YoY
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
ROWS = dict(revenue=r"revenue from operations|net sales|total revenue|total income",
            ebitda=r"ebitda|operating profit",
            pbt=r"profit before tax|\bpbt\b",
            pat=r"profit after tax|net profit|profit for the (?:period|quarter)|\bpat\b")


def _nums(line):
    out = []
    for m in re.finditer(r"\(?-?[\d,]+\.?\d*\)?", line):
        t = m.group(0)
        neg = t.startswith("(") or t.startswith("-")
        t = re.sub(r"[(),-]", "", t)
        if t and any(ch.isdigit() for ch in t):
            out.append(-float(t.replace(",", "")) if neg else float(t.replace(",", "")))
    return out


def _col_order(header):
    ds = []
    for m in re.finditer(r"(\d{1,2})[-/ ]([A-Za-z]{3})[a-z]*[-/ ](\d{2,4})", header):
        y = int(m.group(3)); y += 2000 if y < 100 else 0
        ds.append((y, MONTHS.get(m.group(2).lower(), 0)))
    if len(ds) < 3:
        return [0, 1, 2]  # assume current, prev quarter, year-ago
    idx = sorted(range(len(ds)), key=lambda i: -(ds[i][0] * 12 + ds[i][1]))
    cur = idx[0]
    q = min(range(len(ds)), key=lambda i: abs((ds[cur][0] * 12 + ds[cur][1]) - 3 - (ds[i][0] * 12 + ds[i][1])) if i != cur else 99)
    y = min(range(len(ds)), key=lambda i: abs((ds[cur][0] * 12 + ds[cur][1]) - 12 - (ds[i][0] * 12 + ds[i][1])) if i != cur else 99)
    return [cur, q, y]


def parse_results(text):
    lines = [l for l in text.splitlines() if l.strip()]
    hdr = next((l for l in lines if re.search(r"particulars|quarter ended", l.lower()) and re.search(r"\d{2}", l)), "")
    order = _col_order(hdr)
    out = {}
    for key, pat in ROWS.items():
        for l in lines:
            if re.search(pat, l.lower()):
                n = _nums(l.split("|", 1)[1] if "|" in l else l)
                if len(n) >= 3:
                    out[key] = dict(cur=n[order[0]], qoq_base=n[order[1]], yoy_base=n[order[2]])
                    break
    if not out:
        return None
    for k, v in out.items():
        pct = lambda a, b: round((a - b) / abs(b) * 100, 1) if b else None
        v["qoq"], v["yoy"] = pct(v["cur"], v["qoq_base"]), pct(v["cur"], v["yoy_base"])
    if "revenue" in out and "ebitda" in out:
        r, e = out["revenue"], out["ebitda"]
        out["ebitda"]["margin"] = round(e["cur"] / r["cur"] * 100, 1)
        out["ebitda"]["margin_yoy_bps"] = round((e["cur"] / r["cur"] - e["yoy_base"] / r["yoy_base"]) * 10000)
    return out


def results_headline(t, p):
    r, pat = p.get("revenue"), p.get("pat")
    if not (r and pat):
        return None
    good = (pat["yoy"] or 0) > 0
    s = f"PAT {'up' if good else 'down'} {abs(pat['yoy'])}% YoY, revenue {r['yoy']:+}% YoY"
    m = p.get("ebitda", {}).get("margin_yoy_bps")
    if m is not None:
        s += f"; EBITDA margin {'+' if m >= 0 else ''}{m} bps YoY"
    return s


# ---- scoring & enrichment
def _held(t):
    c = store.company(t)
    return c["weight"] if c else 0


def _price_reaction(t):
    s = store.prices().get(t)
    if not s or len(s) < 2:
        return 0
    return (s[-1]["c"] / s[-2]["c"] - 1) * 100


def _summarise(items, limit=6):
    """One-line 'so what' for the highest-priority items that have no explanation. Cached; runs in parallel; silent on failure."""
    todo = [i for i in items if not i["why"] and i["tier"] != "routine" and i["category"] != "results"][:limit]
    if not (todo and llm.enabled()):
        return

    def one(i):
        text = (i["subject"] + ". " + (i["body"] or ""))[:700]
        out = llm.complete(
            "In at most 18 words, say what this stock-exchange filing means for a shareholder of the company. Use ONLY the text below; "
            f"if it lacks detail, say what is disclosed and nothing more. No hype, no advice.\nCompany: {i['company']}\nFiling: {text}",
            tier="cheap", max_tokens=60, purpose="filing_summary")
        if out and len(out) < 220:
            i["why"], i["why_via"] = out.strip().strip('"'), "llm"
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, todo))


def digest(hours=24, cats=None):
    cutoff = store.now() - dt.timedelta(hours=hours)
    items = []
    for a in store.announcements():
        ts = dt.datetime.fromisoformat(a["ts"])
        if ts < cutoff:
            continue
        cat = classify(a["subject"])
        if cat == "unclassified" and a.get("body"):
            cat = classify(a["body"])
        c = store.company(a["ticker"])
        score = BASE[cat] + min(_held(a["ticker"]), 9) * 0.8
        enrich = {}
        why = ""
        if cat == "results":
            p = parse_results(a["body"])
            if p:
                enrich["results"] = p
                why = results_headline(a["ticker"], p) or ""
                score += min(abs(p["pat"]["yoy"] or 0) / 4, 6) if "pat" in p else 0
        if cat == "management_change" and a.get("person"):
            ext, conf = network.resolve_person(a["person"])
            if ext:
                enrich["person"] = dict(id=ext["id"], name=ext["name"], role=ext["role"], match_confidence=conf,
                                        history=ext["history"], edu=ext["edu"])
                ov = network.overlaps(ext)
                sd = network.second_degree(ext)
                enrich["network"] = dict(overlaps=ov[:3], second_degree=sd[:2])
                if ov:
                    score += 6
                    top = ov[0]
                    why = f"{ext['name']}: {top['evidence'][0]} - {top['contact']['name']} is in your network."
                else:
                    why = f"{ext['name']}: no overlap with your network."
        if cat in ("m&a", "capex", "litigation") and "network" not in enrich:
            wc = network.who_to_call(ticker=a["ticker"], limit=2)
            if wc:
                enrich["network"] = dict(who_to_call=wc)
                score += 5
                why = f"Best person to ask: {wc[0]['name']} - {wc[0]['why']}"
        if cat == "pledging":
            why = "Promoter pledges are a risk signal; check the pledged % of holding and the price reaction."
        if cat == "dividend":
            why = "Cash dividend; note the record date."
        rx = _price_reaction(a["ticker"])
        if abs(rx) >= 3:
            score += 4
            why = (why.rstrip(".") + ". " if why else "") + f"Stock {rx:+.1f}% on the day."
        tier = "must" if score >= 80 else "look" if score >= 45 else "routine"
        items.append(dict(id=a["id"], ticker=a["ticker"], company=c["name"] if c else a["ticker"], category=cat,
                          label=LABEL.get(cat, cat.capitalize()), ts=a["ts"], subject=a["subject"],
                          body=a["body"] if cat != "results" else "", why=why.strip(), score=round(score, 1), tier=tier, **enrich))
    if cats:
        items = [i for i in items if i["category"] in cats or i["tier"] == "must"]
    items.sort(key=lambda i: -i["score"])
    _summarise(items)
    counts = {k: sum(1 for i in items if i["tier"] == k) for k in ("must", "look", "routine")}
    return dict(window_hours=hours, counts=counts, items=items,
                reconnect=network.keep_warm([a for a in store.announcements() if dt.datetime.fromisoformat(a["ts"]) >= cutoff])[:3])


def alerts(vol_x=2.0, px=4.0, lookback=20):
    out = []
    for t, s in store.prices().items():
        if len(s) < lookback + 2:
            continue
        last, prev = s[-1], s[-2]
        avg = sum(x["v"] for x in s[-lookback - 1:-1]) / lookback
        vr = last["v"] / avg if avg else 0
        chg = (last["c"] / prev["c"] - 1) * 100
        hits = []
        if vr >= vol_x:
            hits.append(f"Volume {vr:.1f}x its {lookback}-day average")
        if abs(chg) >= px:
            hits.append(f"Price {chg:+.1f}% on the day")
        if hits:
            c = store.company(t)
            out.append(dict(ticker=t, company=c["name"] if c else t, date=last["d"], change=round(chg, 1), vol_ratio=round(vr, 1),
                            reasons=hits, spark=[x["c"] for x in s[-30:]]))
    out.sort(key=lambda a: -(abs(a["change"]) / max(px, .1) + a["vol_ratio"] / max(vol_x, .1)))
    return out
