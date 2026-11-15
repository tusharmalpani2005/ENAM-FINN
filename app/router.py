"""Ask: turns a free-text/voice question into a typed card.
Tier 1: deterministic keyword/regex routing (instant, free).  Tier 2: if that finds nothing, a cheap LLM
classifies the question into {intent, ticker, person, theme} and we run the SAME card builders. The LLM never
produces the answer itself, only the routing, and every field it returns is validated against real data."""
import re
from . import store, network, pull, push, seed, llm

INTENTS = ("digest", "research", "who_to_call", "diligence", "expertise", "keep_warm")


def _find_company(q):
    ql = q.lower()
    for c in store.companies():
        if c["ticker"].lower() in re.findall(r"[a-z]+", ql) or any(a.lower() in ql for a in [c["name"]] + c["aliases"] if len(a) > 2):
            return c
    m = re.search(r"\b[A-Z]{3,12}\b", q)
    if m and store.fundamentals(m.group(0)):
        return dict(ticker=m.group(0), name=m.group(0))
    return None


def _find_theme(q):
    ql = q.lower()
    for t in seed.THEMES + ["chemicals", "lending", "semis", "pharmaceutical", "software", "cables"]:
        if t in ql:
            return {"chemicals": "specialty chemicals", "lending": "rural lending", "semis": "semiconductors", "software": "auto software"}.get(t, t)
    return None


# ---------------- card builders (shared by both tiers)
def _digest():
    d = push.digest(24)
    return dict(intent="digest", title="Last 24 hours", card="digest", data=dict(counts=d["counts"], items=d["items"][:4]))


def _research(c):
    try:
        r = pull.analyse(c["ticker"])
    except ValueError as e:
        return dict(intent="no_data", title="Data incomplete", card="text", data=dict(text=str(e)))
    if r:
        return dict(intent="research", title=f"{r['name']} research", card="research", data=r)
    return dict(intent="no_data", title="No fundamentals available", card="text",
                data=dict(text=f"I couldn't load annual reports / transcripts for {c['ticker']}. Run `python3 -m app.fetchers.screener {c['ticker']}` with network access."))


def _who(c):
    return dict(intent="who_to_call", title=f"Who to call about {c['name']}", card="people",
                data=dict(people=network.who_to_call(ticker=c["ticker"], limit=5), ticker=c["ticker"]))


def _expertise(theme):
    return dict(intent="expertise", title=f"Your go-to people on {theme}", card="people", data=dict(people=network.expertise(theme)))


def _warm():
    return dict(intent="keep_warm", title="Worth reconnecting with", card="warm", data=dict(items=network.keep_warm(store.announcements())))


def _person(name):
    d = network.diligence(name)
    return dict(intent="diligence", title=f"Diligence: {d['person']['name']}", card="person", data=d) if d else None


def _help():
    return dict(intent="help", title="Try one of these", card="text",
                data=dict(text="Ask me about a company (\"dig into Asian Paints\"), a contact (\"who should I call about Deepak Nitrite\"), a person (\"who is Ananya Rao\"), a theme (\"my go-to people on semiconductors\"), or \"what changed today\"."))


# ---------------- tier 1: rules
def _rules(q):
    ql = q.lower().strip()
    c, theme = _find_company(q), _find_theme(q)
    if re.search(r"who is|diligence|background|bio of|before i meet|meeting with", ql):
        m = re.search(r"(?:who is|diligence on|bio of|meeting with|background of)\s+(.+?)[?.]*$", ql)
        r = _person((m.group(1) if m else q).strip())
        if r:
            return r
    if re.search(r"who (should|can|do) i|who knows|who.*call|who.*speak|talk to", ql) or (c and "who" in ql):
        if c:
            return _who(c)
        if theme:
            return _expertise(theme)
    if re.search(r"expert|go-?to|go to|map|know about", ql) and theme:
        return _expertise(theme)
    if re.search(r"warm|reconnect|catch up|haven.t spoken", ql):
        return _warm()
    if re.search(r"what changed|digest|today|news|what.?s new|catch me up|filings", ql):
        return _digest()
    if c:
        return _research(c)
    if theme:
        return _expertise(theme)
    return _help()


# ---------------- tier 2: LLM classification, fully validated
def _plan(q):
    names = "; ".join(f"{c['ticker']}={c['name']}" for c in store.companies())
    people = "; ".join(p["name"] for p in store.people() + store.ext_people())
    prompt = (
        "You route an investor's question to one tool. Tools: digest (what changed / news across the portfolio), "
        "research (analyse one company's fundamentals), who_to_call (which contact can explain a company), "
        "diligence (background on a named person), expertise (which contacts know a theme/sector), keep_warm (who to reconnect with).\n"
        f"Portfolio tickers: {names}\nKnown people: {people}\nThemes: {', '.join(seed.THEMES)}\n"
        f"Question: {q!r}\n"
        'Return {"intent": one of the tools or "none", "ticker": ticker from the list or an NSE ticker the user named, else null, '
        '"person": exact name from Known people or null, "theme": short sector/theme phrase or null}. '
        'Use "none" if the question is not about any of these tools.')
    p = llm.complete_json(prompt, tier="cheap", max_tokens=150, purpose="ask_routing")
    return p if isinstance(p, dict) and p.get("intent") in INTENTS else None


def _run_plan(p):
    t = (p.get("ticker") or "").upper().strip()
    c = store.company(t) if t and store.company(t) else (dict(ticker=t, name=t) if re.fullmatch(r"[A-Z0-9&-]{2,15}", t) and store.fundamentals(t) else None)
    theme, intent = (p.get("theme") or "").strip() or None, p["intent"]
    if intent == "digest": return _digest()
    if intent == "keep_warm": return _warm()
    if intent == "diligence" and p.get("person"): return _person(p["person"])
    if intent == "research" and c: return _research(c)
    if intent == "who_to_call" and c: return _who(c)
    if intent in ("expertise", "who_to_call") and theme: return _expertise(theme)
    return None


def ask(q):
    r = _rules(q)
    if r["intent"] != "help" or not llm.enabled():
        r["via"] = "rules"
        return r
    plan = _plan(q)
    out = _run_plan(plan) if plan else None
    if out:
        out["via"], out["title"] = "llm", out["title"] + "  (AI-routed)"
        return out
    r["via"] = "rules"
    return r
