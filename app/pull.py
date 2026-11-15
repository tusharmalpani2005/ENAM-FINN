"""Pull: grounded research from primary data only (annual-report tables, AR notes, concall transcripts).
Everything numeric is computed here; every claim carries a source tag. No outside knowledge is used."""
import re
from . import store, network, llm

RANGE = r"(\d{1,2}(?:\.\d)?)\s*(?:-|to|–)\s*(\d{1,2}(?:\.\d)?)\s*%"
POINT = r"(?:around|about|approximately)\s*(\d{1,2}(?:\.\d)?)\s*%"


def _horizon(text):
    """-> list of quarter labels the guidance covers, and a human label"""
    m = re.search(r"\b(Q[1-4])\s?FY(\d{2})\b", text)
    if m: return [f"{m.group(1)}FY{m.group(2)}"], f"{m.group(1)} FY{m.group(2)}"
    m = re.search(r"\b(H[12])\s?FY(\d{2})\b", text)
    if m:
        qs = ["Q1", "Q2"] if m.group(1) == "H1" else ["Q3", "Q4"]
        return [f"{q}FY{m.group(2)}" for q in qs], f"{m.group(1)} FY{m.group(2)}"
    m = re.search(r"\bFY(\d{2})\b", text)
    if m: return [f"Q{i}FY{m.group(1)}" for i in range(1, 5)], f"FY{m.group(1)}"
    return [], ""


def _extract_rules(transcripts):
    """Rule-based extractor over transcript sentences (fast, free, and the fallback)."""
    out = []
    for tx in transcripts:
        for sent in re.split(r"(?<=[.!?])\s+", tx["text"]):
            low = sent.lower()
            if not re.search(r"\b(we expect|we see|we plan|guidance|we target)\b", low):
                continue
            metric = ("revenue" if re.search(r"revenue|sales|top.?line", low) else
                      "ebitda_margin" if "ebitda margin" in low else
                      "capex" if "capex" in low else None)
            if not metric:
                continue
            qs, hz = _horizon(sent)
            hz = hz or "next FY"
            if metric == "capex":
                m = re.search(r"Rs\.?\s?([\d,]+)\s*crore", sent)
                if m:
                    out.append(dict(metric="capex", said_in=tx["quarter"], date=tx["date"], horizon=hz, quarters=qs,
                                    low=float(m.group(1).replace(",", "")), high=float(m.group(1).replace(",", "")), unit="Rs cr", sentence=sent.strip()))
                continue
            m = re.search(RANGE, sent)
            if m:
                lo, hi = float(m.group(1)), float(m.group(2))
            else:
                m = re.search(POINT, sent)
                if not m: continue
                lo = hi = float(m.group(1))
            out.append(dict(metric=metric, said_in=tx["quarter"], date=tx["date"], horizon=hz, quarters=qs, low=lo, high=hi,
                            unit="%", point=(lo == hi), sentence=sent.strip()))
    return out


CUE = re.compile(r"\b(expect|guid|target|aim|plan|intend|anticipat|outlook|see|looking|project|forecast|believe|confident|should|will)\w*", re.I)
NUMERIC = re.compile(r"\d|crore|per ?cent", re.I)


def _candidates(text, cap=45):
    """Only sentences that could carry guidance go to the model (cuts tokens ~95% on a real transcript)."""
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if 25 < len(x) < 420]
    return [x for x in sents if CUE.search(x) and NUMERIC.search(x)][:cap]


def _extract_llm(transcripts):
    out = []
    for tx in transcripts:
        cand = _candidates(tx["text"])
        if not cand:
            continue
        numbered = "\n".join(f"{i}. {x}" for i, x in enumerate(cand))
        prompt = (
            "Below are numbered sentences from an earnings-call transcript. Pick ONLY sentences where MANAGEMENT gives explicit forward "
            "guidance on one of: revenue growth (a % growth rate, not an absolute revenue figure), EBITDA margin (%), or capex (amount in Rs crore).\n"
            'Return {"items": [{"i": sentence number, "metric": "revenue" | "ebitda_margin" | "capex", "low": number, "high": number, '
            '"horizon": e.g. "FY27" or "Q4 FY26" or "H2 FY26"}]}. For a single value use low = high. Skip past results, analyst questions and vague talk. '
            "Do not invent numbers: they must appear in the sentence.\n\n" + numbered)
        res = llm.complete_json(prompt, tier="cheap", max_tokens=700, purpose="guidance_extraction")
        for it in (res or {}).get("items", []) if isinstance(res, dict) else []:
            try:
                i, metric = int(it["i"]), it["metric"]
                lo, hi = float(it["low"]), float(it["high"])
                sent = cand[i]
            except (KeyError, ValueError, TypeError, IndexError):
                continue
            nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*\.?\d*", sent)]
            # validation: metric known, numbers really are in the source sentence, plausible magnitude
            if metric not in ("revenue", "ebitda_margin", "capex") or not any(abs(lo - n) < 1e-6 for n in nums) or not any(abs(hi - n) < 1e-6 for n in nums):
                continue
            if metric != "capex" and not (-50 <= lo <= hi <= 100):
                continue
            qs, hz = _horizon(str(it.get("horizon", "")) or sent)
            if not qs:
                qs, hz = _horizon(sent)
            out.append(dict(metric=metric, said_in=tx["quarter"], date=tx["date"], horizon=hz or "next FY", quarters=qs, low=lo, high=hi,
                            unit="Rs cr" if metric == "capex" else "%", point=(lo == hi), sentence=sent, via="llm"))
    return out


def _qkey(label):
    m = re.match(r"Q([1-4])FY(\d\d)", label or "")
    return (int(m.group(2)), int(m.group(1))) if m else (0, 0)


def _resolve_horizon(g):
    """Guidance with no explicit period ('next year', 'this year', nothing) is anchored to the call it was said in:
    'next year' = the fiscal year after the call's FY; otherwise the current FY (or, on a Q4 call, the year just starting)."""
    if g["quarters"]:
        return g
    fy, q = _qkey(g["said_in"])
    if not fy:
        return g
    nxt = re.search(r"next (year|fiscal|fy)|coming year|following year", g["sentence"].lower())
    tfy = fy + 1 if (nxt or q == 4) else fy
    g["quarters"] = [f"Q{i}FY{tfy}" for i in range(1, 5)]
    g["horizon"] = f"FY{tfy}"
    return g


def extract_guidance(transcripts):
    """Rules first; if an LLM is configured, add what it finds. Duplicates (same call/metric/range) are merged."""
    found = _extract_rules(transcripts)
    if llm.enabled():
        seen = {(g["said_in"], g["metric"], g["low"], g["high"]) for g in found}
        found += [g for g in _extract_llm(transcripts) if (g["said_in"], g["metric"], g["low"], g["high"]) not in seen]
    return [_resolve_horizon(g) for g in found]


def _qidx(f):
    return {l: i for i, l in enumerate(f["quarters"]["labels"])}


def _delivered(f, g):
    idx, q = _qidx(f), f["quarters"]
    have = [l for l in g["quarters"] if l in idx]
    if not have or g["metric"] == "capex":
        return None
    if g["metric"] == "revenue":
        cur = prev = 0
        for l in have:
            i = idx[l]
            if i < 4: return None  # need year-ago quarter
            cur += q["revenue"][i]; prev += q["revenue"][i - 4]
        val = round((cur / prev - 1) * 100, 1)
    else:
        val = round(sum(q["ebitda"][idx[l]] for l in have) / sum(q["revenue"][idx[l]] for l in have) * 100, 1)
    return val, len(have), len(g["quarters"])


def narrative_vs_numbers(f):
    rows = []
    for g in extract_guidance(f["transcripts"]):
        if g["metric"] == "capex":
            rows.append(dict(g, status="pending", note="Plan, not scored (capex is not reported by period here)", delivered=None))
            continue
        d = _delivered(f, g)
        if not d:
            rows.append(dict(g, status="pending", delivered=None, note="Period not yet reported"))
            continue
        val, n, tot = d
        if n < tot:   # a half-reported year cannot be graded: show the run-rate, don't call it a beat/miss
            pos = "above" if val > g["high"] else "below" if val < g["low"] else "within"
            rows.append(dict(g, status="pending", delivered=val, note=f"Part-year ({n} of {tot} quarters reported): running {pos} the guided range so far"))
            continue
        tol = 0.5 if g.get("point") else 0
        if val < g["low"] - tol: status, gap = "missed", round(val - g["low"], 1)
        elif val > g["high"] + tol: status, gap = "beat", round(val - g["high"], 1)
        else: status, gap = "met", 0
        rows.append(dict(g, delivered=val, status=status, gap=gap, note=""))
    scored = [r for r in rows if r["status"] != "pending"]
    part = [r for r in rows if r["status"] == "pending" and r["metric"] != "capex"]
    capex = sorted((r for r in rows if r["metric"] == "capex"), key=lambda r: _qkey(r["said_in"]), reverse=True)[:2]   # latest two plans only
    key = lambda r: _qkey(r["said_in"])
    return sorted(scored, key=key, reverse=True) + sorted(part, key=key, reverse=True) + capex


def _cagr(a, n):
    return round(((a[-1] / a[0]) ** (1 / n) - 1) * 100, 1) if a[0] > 0 and a[-1] > 0 else None


def analyse(ticker):
    f = store.fundamentals(ticker)
    if not f:
        return None
    A, Q = f["annual"], f["quarters"]
    if len(Q["labels"]) < 8 or len(A["years"]) < 3 or not any(A["revenue"]) or not any(Q["revenue"]):
        raise ValueError(f"Fundamentals for {ticker.upper()} are incomplete: " + "; ".join(f.get("quality") or ["missing revenue / quarterly history"]))
    rev, eb, pat = A["revenue"], A["ebitda"], A["pat"]
    sd = lambda a, b: a / b if b else 0   # safe divide: scraped data can contain zeros
    n = len(rev) - 1
    margin = [round(sd(e, r) * 100, 1) for e, r in zip(eb, rev)]
    cfo_pat = [round(sd(c, p), 2) for c, p in zip(A["cfo"], pat)]
    fcf = [round(c - x) for c, x in zip(A["cfo"], A["capex"])]
    de = [round(sd(d, e), 2) for d, e in zip(A["debt"], A["equity"])]
    roe = [round(sd(p, e) * 100, 1) for p, e in zip(pat, A["equity"])]
    wc = [round(r + i - p) for r, i, p in zip(A["receivable_days"], A["inventory_days"], A["payable_days"])]
    yoy = lambda i: round((sd(Q["revenue"][i], Q["revenue"][i - 4]) - 1) * 100, 1)
    qm = [round(sd(e, r) * 100, 1) for e, r in zip(Q["ebitda"], Q["revenue"])]
    nvn = narrative_vs_numbers(f)
    scored = [r for r in nvn if r["status"] != "pending"]
    kept = sum(1 for r in scored if r["status"] in ("met", "beat"))

    snapshot = dict(revenue_fy=rev[-1], ebitda_margin_fy=margin[-1], pat_fy=pat[-1], roe=roe[-1], de=de[-1],
                    latest_q=dict(label=Q["labels"][-1], revenue_yoy=yoy(7), pat_yoy=round((sd(Q["pat"][7], Q["pat"][3]) - 1) * 100, 1), margin=qm[-1]))
    traj = dict(years=A["years"], revenue=rev, margin=margin, pat=pat, revenue_cagr=_cagr(rev, n), pat_cagr=_cagr(pat, n),
                margin_change_pts=round(margin[-1] - margin[0], 1),
                quarterly_yoy=[dict(q=Q["labels"][i], rev_yoy=yoy(i), margin=qm[i]) for i in range(4, 8)])
    bs = dict(years=A["years"], debt=A["debt"], de=de, wc_days=wc, receivable_days=A["receivable_days"], inventory_days=A["inventory_days"])
    cash = dict(years=A["years"], cfo=A["cfo"], cfo_to_pat=cfo_pat, fcf=fcf, capex=A["capex"],
                cum_cfo_to_pat=round(sd(sum(A["cfo"]), sum(pat)), 2), cum_fcf=sum(fcf))

    bull, bear = [], []
    src = f"FY22-FY26 annual tables"
    if traj["revenue_cagr"] and traj["revenue_cagr"] >= 12: bull.append((traj["revenue_cagr"], f"Revenue compounding at {traj['revenue_cagr']}% a year over FY22-26.", src))
    if traj["margin_change_pts"] >= 1: bull.append((traj["margin_change_pts"] * 3, f"EBITDA margin up {traj['margin_change_pts']} pts since FY22 ({margin[0]}% to {margin[-1]}%).", src))
    if cash["cum_cfo_to_pat"] >= 1.1: bull.append((cash["cum_cfo_to_pat"] * 8, f"Profits convert to cash: cumulative operating cash flow is {cash['cum_cfo_to_pat']}x PAT.", src))
    if de[-1] <= 0.15: bull.append((6, f"Almost debt-free (D/E {de[-1]}).", "FY26 balance sheet"))
    if scored and kept / len(scored) >= 0.75: bull.append((kept * 3, f"Management delivers: {kept} of {len(scored)} measurable guidance calls met or beaten.", "Last four concalls vs reported quarters"))
    if traj["margin_change_pts"] <= -1: bear.append((abs(traj["margin_change_pts"]) * 3, f"EBITDA margin down {abs(traj['margin_change_pts'])} pts since FY22 ({margin[0]}% to {margin[-1]}%).", src))
    if cash["cum_cfo_to_pat"] < 0.9: bear.append(((1 - cash["cum_cfo_to_pat"]) * 25, f"Weak cash conversion: five-year operating cash flow is only {cash['cum_cfo_to_pat']}x PAT.", src))
    if de[-1] > 0.3 and de[-1] > de[0] * 1.5: bear.append((de[-1] * 10, f"Leverage rising: D/E {de[0]} to {de[-1]} while FCF cumulated Rs {cash['cum_fcf']:,} cr.", "FY22-26 balance sheet"))
    if wc[-1] - wc[0] >= 10: bear.append(((wc[-1] - wc[0]) / 2, f"Working-capital cycle stretched from {wc[0]} to {wc[-1]} days.", "Receivable, inventory, payable days"))
    if scored and kept / len(scored) <= 0.5: bear.append((len(scored) - kept + 3, f"Guidance credibility: only {kept} of {len(scored)} measurable calls met.", "Last four concalls vs reported quarters"))
    cl = f["ar_notes"]["contingent"]
    if A["equity"][-1] and cl["amount"] / A["equity"][-1] > 0.05: bear.append((cl["amount"] / A["equity"][-1] * 50, f"Contingent liabilities of Rs {cl['amount']} cr ({round(cl['amount'] / A['equity'][-1] * 100, 1)}% of equity): {cl['text']}", "Annual report notes"))
    if yoy(7) < 6: bear.append((6 - yoy(7) + 3, f"Latest-quarter revenue growth is only {yoy(7)}% YoY.", f"{Q['labels'][7]} results"))
    top = lambda L: [dict(point=t, source=s) for _, t, s in sorted(L, key=lambda x: -x[0])[:3]]

    result = dict(ticker=ticker.upper(), name=f["name"], source=f["source"], business=f["business"], snapshot=snapshot, trajectory=traj,
                  balance_sheet=bs, cash_quality=cash, narrative_vs_numbers=nvn, bull=top(bull), bear=top(bear),
                  scorecard=dict(measured=len(scored), kept=kept),
                  red_flags=[dict(label="Related-party transactions", text=f["ar_notes"]["rpt"]), dict(label="Auditor", text=f["ar_notes"]["auditor"]), dict(label="Contingent liabilities", text=cl["text"])])
    result["verdict"] = _verdict(result)
    result["network"] = network.who_to_call(ticker=ticker, limit=3) if store.company(ticker) else []
    return result


def _verdict(r):
    sc, s = r["scorecard"], r["snapshot"]
    track = (f"management met or beat {sc['kept']} of {sc['measured']} guidance calls that can be fully checked."
             if sc["measured"] else "no guidance covers a period that has been fully reported yet, so management's track record is not yet measurable.")
    base = (f"{r['name']} grew revenue {s['latest_q']['revenue_yoy']}% YoY last quarter with {r['trajectory']['revenue_cagr']}% five-year CAGR; " + track)
    prompt = ("Using ONLY these computed facts (no outside knowledge), write exactly 2 plain sentences (no markdown, no bullet points) on whether the numbers back the narrative. FACTS: "
              + str(dict(snapshot=s, scorecard=sc, bull=r["bull"], bear=r["bear"], guidance=[(g["said_in"], g["metric"], g["low"], g["high"], g.get("delivered"), g["status"]) for g in r["narrative_vs_numbers"]])))
    return llm.complete(prompt, tier="strong", purpose="verdict") or base