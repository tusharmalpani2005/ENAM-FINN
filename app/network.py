"""The Network layer: a small graph over the investor's contacts + people in filings."""
import re
import difflib
from . import store

NORM = re.compile(r"\b(ltd|limited|pvt|private|india|company|co|industries|the)\b|[^a-z0-9& ]")


def norm(s):
    return re.sub(r"\s+", " ", NORM.sub("", s.lower())).strip()


def _all():
    return {p["id"]: p for p in store.people() + store.ext_people()}


def person(pid):
    return _all().get(pid)


def brief(p, **extra):
    return dict(id=p["id"], name=p["name"], org=p["org"], role=p["role"], **extra)


def _toks(x):
    return norm(x).split()


def _contig(small, big):
    n = len(small)
    return n > 0 and any(big[i:i + n] == small for i in range(len(big) - n + 1))


def org_matches(org, company_names):
    """Whole-word match only. Short aliases (<=3 chars, e.g. RIL, TCS, ITC) must equal the whole organisation name,
    so 'RIL' can never match inside 'Agrilend'."""
    o = _toks(org)
    for n in company_names:
        t = _toks(n)
        if not t or not o:
            continue
        if o == t:
            return True
        if (len(t) == 1 and len(t[0]) <= 3) or (len(o) == 1 and len(o[0]) <= 3):
            continue
        if _contig(t, o) or _contig(o, t):
            return True
    return False


def resolve_person(name):
    """Entity-link a name from a filing to a known person (fuzzy, title-stripped)."""
    q = re.sub(r"^(mr|ms|mrs|dr|shri|smt)\.?\s+", "", name.strip().lower())
    best, score = None, 0
    for p in _all().values():
        n = re.sub(r"^(mr|ms|mrs|dr)\.?\s+", "", p["name"].lower())
        r = difflib.SequenceMatcher(None, q, n).ratio()
        if r > score:
            best, score = p, r
    return (best, round(score, 2)) if score >= 0.75 else (None, 0)


def _company_names(ticker):
    c = store.company(ticker)
    rel = store.relations(ticker)
    return c, [c["name"]] + c["aliases"], rel


def _role_insight(role, org, company):
    r = role.lower()
    if any(k in r for k in ("cfo", "finance", "treasury")): return f"how {company} budgets, reports and guides numbers"
    if any(k in r for k in ("operation", "plant", "projects", "logistics")): return f"ground-level capacity, utilisation and execution at {company}"
    if any(k in r for k in ("regulat", "quality")): return f"how {company} handles regulators and remediation"
    if any(k in r for k in ("strategy", "bd", "category", "brand", "sales", "marketing")): return f"demand, pricing and competitive positioning around {company}"
    if any(k in r for k in ("credit", "risk")): return f"underwriting culture and asset quality at {company}"
    if any(k in r for k in ("founder", "ceo", "md", "partner", "director", "head")): return f"how leadership at {company} and its peers think"
    return f"the business around {company}"


SECTOR_THEMES = {
    "Energy & Conglomerate": ["energy transition", "telecom", "retail"], "IT Services": ["it services", "genai", "deal flow"],
    "Banking": ["banking", "credit", "psu banks", "rural lending", "capital markets"], "Capital Goods / EPC": ["infra", "epc", "capex cycle"],
    "Pharma": ["pharma", "usfda", "generics", "regulatory", "us pricing"], "Auto": ["auto", "ev", "auto software", "sdv"],
    "Paints / Consumer": ["paints", "decor"], "Specialty Chemicals": ["specialty chemicals", "nitration", "intermediates", "fluorochemicals"],
    "Auto Software": ["auto software", "sdv", "automotive silicon", "semiconductors"], "NBFC": ["nbfc", "rural lending", "agri-credit", "credit"],
    "Consumer / Jewellery": ["jewellery"], "Cement": ["cement", "logistics", "coal"], "FMCG": ["fmcg", "d2c"],
    "Adhesives / Specialty Chem": ["specialty chemicals", "paints", "decor"], "Electricals": ["cables", "electricals", "copper"],
}
THIS_YEAR = 2026


def _recency(end):
    """Current role = 1.0; the longer ago they left, the less their inside knowledge is worth."""
    if end is None:
        return 1.0
    yrs = THIS_YEAR - end
    return 0.9 if yrs <= 2 else 0.7 if yrs <= 5 else 0.5 if yrs <= 9 else 0.3


def who_to_call(ticker=None, theme=None, limit=5):
    """Rank contacts by closeness to a company (worked there / competitor / supplier / customer) or a theme.
    Direct experience always outranks 'adjacent' sector knowledge; stale (long-departed) context is discounted and labelled."""
    direct, adjacent = [], []
    if ticker:
        c, names, rel = _company_names(ticker)
        keys = set(SECTOR_THEMES.get(c["sector"], []))
        for p in store.people():
            best = None
            for h in p["history"]:
                if org_matches(h["org"], names):
                    kind, w = "worked at", 5
                elif any(org_matches(h["org"], [x]) for x in rel["competitors"]):
                    kind, w = "worked at competitor", 3
                elif any(org_matches(h["org"], [x]) for x in rel["suppliers"]):
                    kind, w = "worked at a supplier", 2.5
                elif any(org_matches(h["org"], [x]) for x in rel["customers"]):
                    kind, w = "worked at a customer", 2.5
                else:
                    continue
                s = w * _recency(h["end"])
                if best is None or s > best[0]:
                    best = (s, kind, h)
            strength = 0.6 + 0.1 * p["strength"]
            if best:
                s, kind, h = best
                yrs = THIS_YEAR - h["end"] if h["end"] else 0
                note = f" Left {yrs}y ago, so context may be dated." if yrs >= 5 else ""
                label = kind.replace("worked at", "works at") if h["end"] is None else kind   # "Works at" for current roles
                why = (f"{label.capitalize()} {h['org']} ({h['role']}, {h['start']}-{h['end'] or 'now'}). "
                       f"Can speak to {_role_insight(h['role'], h['org'], c['name'])}.{note}")
                direct.append((s * strength, p, why, yrs >= 5))
            else:
                hit = [t for t in p["themes"] if t.lower() in keys]
                if hit:
                    adjacent.append((0.8 * strength, p, f"Adjacent: follows {', '.join(hit[:2])} ({p['role']}, {p['org']}). No direct tie to {c['name']}.", False))
        direct.sort(key=lambda x: -x[0]); adjacent.sort(key=lambda x: -x[0])
        scored = direct + adjacent[:max(0, min(2, limit - len(direct)))]   # adjacent only fills spare slots, max 2
    else:
        scored, tl = [], (theme or "").lower()
        for p in store.people():
            hit = [t for t in p["themes"] if tl == t.lower() or (len(tl) > 3 and (tl in t.lower() or t.lower() in tl))]
            hist = [h for h in p["history"] if tl and re.search(r"\b" + re.escape(tl) + r"\b", (h["org"] + " " + h["role"]).lower())]
            if hit or hist:
                s = (3 if hit else 1) + len(hist) + (1 if p["org"] != "Independent" else 0.5)
                scored.append((s * (0.6 + 0.1 * p["strength"]), p, f"Go-to on {', '.join(hit[:2]) or theme}: {p['role']} at {p['org']}.", False))
        scored.sort(key=lambda x: -x[0])
    return [brief(p, score=round(s, 1), why=why, strength=p["strength"], last_contacted_days=p["last"], themes=p["themes"], dated=dated)
            for s, p, why, dated in scored[:limit]]


def overlaps(ext):
    """How is this outside person connected to the investor's network?"""
    res = []
    for p in store.people():
        notes = []
        for h in ext["history"]:
            for g in p["history"]:
                if norm(h["org"]) == norm(g["org"]):
                    a0, a1 = max(h["start"], g["start"]), min(h["end"] or 2026, g["end"] or 2026)
                    if a0 <= a1:
                        notes.append(("worked_together", f"Overlapped at {h['org']} ({a0}-{a1})", 5))
                    else:
                        notes.append(("same_employer", f"Both worked at {h['org']} (different years)", 2))
        for e in ext["edu"]:
            for f in p["edu"]:
                if norm(e["school"]) == norm(f["school"]):
                    gap = abs(e["year"] - f["year"])
                    if gap <= 2:
                        notes.append(("classmate", f"{e['school']} - same batch window ({e['year']} vs {f['year']})", 4))
                    else:
                        notes.append(("alumni", f"Both {e['school']} alumni", 1.5))
        if ext["id"] in p.get("knows", []):
            notes.append(("knows", "Has a direct connection to them", 3))
        if notes:
            best = max(notes, key=lambda n: n[2])
            res.append(dict(contact=brief(p, strength=p["strength"], last_contacted_days=p["last"]), kind=best[0],
                            evidence=[n[1] for n in notes], score=round(best[2] * (0.6 + 0.1 * p["strength"]), 1)))
    res.sort(key=lambda r: -r["score"])
    return res


def second_degree(ext):
    """You -> contact -> person: only edges the contact has declared, weighted by relationship strength."""
    paths = []
    for p in store.people():
        if ext["id"] in p.get("knows", []):
            paths.append(dict(via=brief(p), strength=p["strength"], path=f"You -> {p['name']} -> {ext['name']}"))
    return sorted(paths, key=lambda x: -x["strength"])


def diligence(pid_or_name):
    p = person(pid_or_name) or resolve_person(pid_or_name)[0]
    if not p:
        return None
    hist = sorted(p["history"], key=lambda h: h["start"])
    qs = []
    for a, b in zip(hist, hist[1:]):
        tenure = (a["end"] or 2026) - a["start"]
        if tenure <= 2:
            qs.append(f"Short stint at {a['org']} ({tenure}y) - why did they leave?")
    if hist and hist[-1]["start"] >= 2025:
        qs.append(f"New to {hist[-1]['org']}: what mandate were they hired for?")
    if len(hist) >= 2 and norm(hist[-1]["org"]) != norm(hist[-2]["org"]):
        qs.append(f"Moved from {hist[-2]['org']} to {hist[-1]['org']}: what does the move say about the role's scope?")
    qs.append("What would they do differently from their predecessor?") if "incoming" in p["role"].lower() else None
    return dict(person=p, overlaps=overlaps(p) if p["id"].startswith("x") else [], second_degree=second_degree(p) if p["id"].startswith("x") else [],
                questions=[q for q in qs if q][:4])


def expertise(theme):
    return who_to_call(theme=theme, limit=6)


def keep_warm(announcements, min_days=60):
    """Contacts not spoken to for a while whose (former or current) employer just made news."""
    out = []
    by_t = {a["ticker"] for a in announcements}
    for p in store.people():
        if p["last"] < min_days:
            continue
        for t in by_t:
            c = store.company(t)
            if not c:
                continue
            for h in p["history"]:
                if org_matches(h["org"], [c["name"]] + c["aliases"]):
                    ev = [a for a in announcements if a["ticker"] == t][0]
                    out.append(dict(contact=brief(p, last_contacted_days=p["last"]), ticker=t, company=c["name"],
                                    event=ev["subject"][:90],
                                    nudge=f"{p['name'].split()[0]} ({'ex-' if h['end'] else 'at '}{h['org']}) - it's been {p['last']} days. {c['name']} just filed; a natural reason to reconnect."))
                    break
    return sorted(out, key=lambda x: -x["contact"]["last_contacted_days"])


def graph(ticker):
    """Radial graph payload for the Network tab."""
    c = store.company(ticker)
    people = who_to_call(ticker=ticker, limit=8)
    return dict(company=dict(ticker=c["ticker"], name=c["name"], sector=c["sector"]), nodes=people)
