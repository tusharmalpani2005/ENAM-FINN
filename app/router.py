"""Answer free-text investor questions with the configured LLM."""
import datetime as dt
import json
import re

from . import store, seed, network, llm


def _context():
    companies = []
    for c in store.companies():
        prices = store.prices().get(c["ticker"], [])
        fundamentals = store.fundamentals(c["ticker"]) or {}
        quarters = fundamentals.get("quarters") or {}
        revenue = quarters.get("revenue") or []
        pat = quarters.get("pat") or []
        latest = prices[-1]["c"] if prices else None
        price_change = ((prices[-1]["c"] / prices[-21]["c"]) - 1) * 100 if len(prices) > 20 else None
        companies.append({
            "ticker": c["ticker"],
            "name": c["name"],
            "sector": c["sector"],
            "portfolio_weight": c["weight"],
            "latest_price": latest,
            "20_session_price_change_pct": round(price_change, 2) if price_change is not None else None,
            "latest_quarter": quarters.get("labels", [])[-1:] or [],
            "revenue": revenue[-2:],
            "pat": pat[-2:],
            "data_quality": fundamentals.get("quality", []),
            "recommended_contacts": [
                {"name": p["name"], "org": p["org"], "role": p["role"], "why": p["why"]}
                for p in network.who_to_call(ticker=c["ticker"], limit=3)
            ],
        })
    people = [
        {
            "name": p["name"],
            "org": p["org"],
            "role": p["role"],
            "themes": p.get("themes", []),
            "history": p.get("history", [])[-2:],
            "relationship_note": p.get("met"),
        }
        for p in store.people() + store.ext_people()
    ]
    cutoff = store.now() - dt.timedelta(hours=24)
    announcements = []
    for item in store.announcements():
        if dt.datetime.fromisoformat(item["ts"]) >= cutoff:
            announcements.append({
                "company": item["ticker"],
                "subject": item["subject"],
                "body": (item.get("body") or "")[:500],
            })
    return {
        "portfolio": companies,
        "people": people,
        "themes": seed.THEMES,
        "recent_announcements": announcements[:30],
    }


def ask(q):
    if not llm.enabled():
        raise RuntimeError("Ask mode requires a configured LLM provider.")
    prompt = (
        "You are FINN, an investment-network copilot. Answer the investor directly using the supplied portfolio, "
        "market, fundamentals, filing, and contact data. Lead with the conclusion and cite the most relevant company, "
        "person, metric, or comparison. Keep the answer under 90 words. Use Markdown with a blank line between "
        "paragraphs. For contact questions, use one short bullet per person rather than a single paragraph. "
        "Do not repeat the question or provide a long preamble. "
        "Speak in first person as FINN. Never refer to 'the snapshot', 'the supplied context', 'the data I have', "
        "or 'the user'. If direct contact details are absent, say: 'I don't have their phone number or email.' "
        "For contact questions, use the company's recommended_contacts in the supplied order; the first is FINN's "
        "top recommendation. Do not replace a direct current employee with a less-relevant former employee. "
        "If asked how to contact someone, use relationship_note to suggest a warm introduction when available. "
        "FINN cannot place calls or send messages, and should not suggest generic corporate channels when a network path exists. "
        "Do not invent facts or hide behind a generic disclaimer when the context supports an answer. "
        "Treat the supplied snapshot as the app's available data; do not claim you lack real-time access when a relevant "
        "price, fundamentals, filing, or contact value is present below. "
        "If the context truly lacks the requested fact, say what is missing. Plain text, no JSON, no canned help.\n\n"
        "STRICT SCOPE: Only answer questions about the investor's portfolio, holdings, market data, "
        "company research, filings, fundamentals, or FINN network contacts. For anything else, reply exactly: "
        "'I can only help with your portfolio, holdings, market data, company research, filings, and FINN network contacts.'\n\n"
        f"FINN context:\n{json.dumps(_context(), ensure_ascii=False)}\n\n"
        f"Investor question: {q}"
    )
    answer = llm.complete(prompt, tier="cheap", max_tokens=400, purpose="ask_answer")
    if not answer:
        raise RuntimeError("The configured LLM did not return an answer.")
    if re.search(r"\b(?:who|whom|call|contact|speak)\b", q, re.I):
        answer = re.sub(r"\s+(?=(?:First|Second|Third|Backup|Alternative)\s*:)", "\n\n", answer)
    return {
        "intent": "answer",
        "title": "FINN",
        "card": "text",
        "data": {"text": answer},
        "via": "llm",
    }
