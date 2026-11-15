import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import push, pull, network, store

c = TestClient(app)


def test_classifier():
    assert push.classify("Outcome of Board Meeting - Unaudited Financial Results for the quarter ended September 30, 2026") == "results"
    assert push.classify("Appointment of Ms. X as Chief Financial Officer") == "management_change"
    assert push.classify("Receipt of Warning Letter from US FDA") == "litigation"
    assert push.classify("Disclosure of pledge of shares by promoter under Reg 31") == "pledging"
    assert push.classify("Disclosure under Reg 7 - sale of shares by promoter group entity") == "insider_trading"
    assert push.classify("Allotment of equity shares pursuant to ESOP") == "unclassified"


def test_results_parser_handles_column_order():
    # columns deliberately shuffled: year-ago first, then current, then prev quarter
    t = "Particulars | 30-Sep-25 | 30-Sep-26 | 30-Jun-26\nRevenue from operations | 100 | 130 | 120\nEBITDA | 20 | 30 | 25\nProfit before tax | 15 | 24 | 20\nProfit after tax | 11 | 18 | 15"
    p = push.parse_results(t)
    assert p["revenue"]["cur"] == 130 and p["revenue"]["yoy"] == 30.0 and p["revenue"]["qoq"] == 8.3
    assert p["pat"]["yoy"] == pytest.approx(63.6, abs=.1)


def test_guidance_extraction_and_scoring():
    r = pull.analyse("DEEPAKNTR")
    st = {(g["said_in"], g["metric"]): g["status"] for g in r["narrative_vs_numbers"]}
    assert st[("Q3FY26", "ebitda_margin")] == "missed"      # guided 17-18, delivered 15.8
    assert st[("Q2FY26", "revenue")] == "beat"              # guided 8-10, delivered 10.2
    assert r["scorecard"] == dict(measured=3, kept=2)      # FY27 guidance is only half-reported, so it is not graded


def test_every_claim_has_a_source():
    r = pull.analyse("TITAN")
    assert all(x["source"] for x in r["bull"] + r["bear"])


def test_entity_linking():
    p, conf = network.resolve_person("Mr. Sandeep Joshi")
    assert p["id"] == "x02" and conf >= .9
    assert network.resolve_person("Totally Unknown Person")[0] is None


def test_second_order_network():
    d = network.diligence("x01")
    assert any(o["kind"] == "worked_together" for o in d["overlaps"])      # Infosys overlap with Meera
    assert d["second_degree"] and d["second_degree"][0]["via"]["name"] == "Karan Shah"


def test_who_to_call_prefers_direct_experience():
    r = network.who_to_call(ticker="DEEPAKNTR")
    assert r[0]["id"] == "p31"                                       # current Deepak Nitrite employee first
    assert {"p15", "p03"} <= {x["id"] for x in r[:4]}                # ex-Deepak and ex-competitor still surface


def test_digest_tiers_and_network_enrichment():
    d = c.get("/api/digest?hours=24").json()
    assert d["counts"]["must"] >= 3
    mc = next(i for i in d["items"] if i["category"] == "management_change" and i["ticker"] == "KPITTECH")
    assert mc["person"]["name"] == "Ananya Rao" and mc["network"]["overlaps"]
    assert all(i["tier"] != "routine" for i in d["items"][:3])


def test_alert_thresholds_configurable():
    loose = c.get("/api/alerts?vol_x=1.2&px=1").json()
    strict = c.get("/api/alerts?vol_x=5&px=20").json()
    assert len(loose) > len(strict) and strict == []


def test_ask_routes():
    for q, intent in [("what changed today", "digest"), ("dig into Titan", "research"), ("who is Ananya Rao", "diligence"),
                      ("who should I call about Asian Paints", "who_to_call"), ("go-to people on rural lending", "expertise")]:
        assert c.post("/api/ask", json={"q": q}).json()["intent"] == intent


def test_unknown_ticker_degrades_gracefully():
    assert c.get("/api/research/ZZZZ").status_code == 404


def test_incomplete_live_data_is_refused_not_analysed(tmp_path, monkeypatch):
    bad = dict(ticker="ZZZ", name="Z", source="x", business=dict(summary="", segments=[]),
               quarters=dict(labels=["Q1FY26"], revenue=[0], ebitda=[0], pat=[0]),
               annual=dict(years=["FY26"], revenue=[0], ebitda=[0], pat=[0], cfo=[0], capex=[0], debt=[0], equity=[0], receivable_days=[0], inventory_days=[0], payable_days=[0]),
               ar_notes=dict(contingent=dict(text="", amount=0), rpt="", auditor=""), transcripts=[], quality=["revenue missing"])
    monkeypatch.setattr(store, "fundamentals", lambda t: bad)
    assert c.get("/api/research/ZZZ").status_code == 422
    assert c.post("/api/ask", json={"q": "dig into Titan"}).json()["card"] == "text"


def test_live_fundamentals_override_seed(monkeypatch):
    live = dict(store.fundamentals("TITAN")); live["name"] = "LIVE Titan"
    monkeypatch.setattr(store, "_live", lambda n: live if n == "fundamentals_TITAN.json" else None)
    assert store.fundamentals("TITAN")["name"] == "LIVE Titan"


def test_nan_prices_on_disk_do_not_break_api(monkeypatch):
    series = [dict(d=f"2026-10-{i:02d}", c=100.0 + i, v=1000) for i in range(1, 29)] + [dict(d="2026-11-13", c=float("nan"), v=5)]
    store.prices.cache_clear()
    monkeypatch.setattr(store, "_live", lambda n: {"TITAN": series} if n == "prices.json" else None)
    px = store.prices()["TITAN"]
    assert len(px) == 28 and all(r["c"] == r["c"] for r in px)
    assert c.get("/api/alerts").status_code == 200
    store.prices.cache_clear()


def test_call_month_maps_to_reported_quarter():
    from app.fetchers.screener import call_quarter
    assert [call_quarter(m, y) for m, y in (("Nov", 2025), ("Feb", 2026), ("May", 2026), ("Aug", 2026))] == ["Q2FY26", "Q3FY26", "Q4FY26", "Q1FY27"]


# ---------------------------------------------------------------- LLM features (model faked; no network)
import json as _json
import httpx
from app import llm, router


@pytest.fixture
def fake_llm(monkeypatch, tmp_path):
    """Pretend Groq is configured and answers according to `responder(prompt) -> str`."""
    box = dict(responder=lambda p: "{}", calls=[])
    monkeypatch.setenv("LLM_PROVIDER", "groq"); monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(llm, "CACHE", str(tmp_path))
    for k in ("calls", "cache_hits", "errors"): llm.STATS[k] = 0
    llm.STATS["by_purpose"] = {}; llm.STATS["last_error"] = None

    def post(url, **kw):
        prompt = kw["json"]["messages"][0]["content"]; box["calls"].append(prompt)
        out = box["responder"](prompt)
        if isinstance(out, Exception): raise out
        return httpx.Response(200, json={"choices": [{"message": {"content": out}}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    return box


def test_ask_falls_back_to_llm_routing_and_validates(fake_llm):
    q = "is the paint maker worth my time?"          # nothing the keyword router recognises
    assert router._rules(q)["intent"] == "help"
    fake_llm["responder"] = lambda p: '```json\n{"intent":"research","ticker":"ASIANPAINT","person":null,"theme":null}\n```'
    r = router.ask(q)
    assert r["via"] == "llm" and r["intent"] == "research" and r["data"]["ticker"] == "ASIANPAINT"
    assert llm.STATS["by_purpose"]["ask_routing"]["calls"] == 1
    # hallucinated person / unknown tool output must not produce a card
    fake_llm["responder"] = lambda p: '{"intent":"diligence","person":"Elon Musk"}'
    assert router.ask("tell me about that guy from the other day")["intent"] == "help"
    fake_llm["responder"] = lambda p: '{"intent":"delete_everything"}'
    assert router.ask("blah blah zzz")["intent"] == "help"


def test_rules_still_answer_without_calling_the_model(fake_llm):
    assert router.ask("what changed today")["via"] == "rules"
    assert "ask_routing" not in llm.STATS["by_purpose"]          # routing never hit the model (digest summaries may)


def test_llm_guidance_extraction_rejects_invented_numbers(fake_llm):
    tx = [dict(quarter="Q4FY26", date="2026-05-06", text=(
        "We delivered a good quarter overall and thank everyone on the call for joining us today. "
        "Looking ahead we expect FY27 revenue growth of around 14 to 16 percent on the back of new capacity. "
        "Management intends to keep the dividend payout steady at around 30 percent of profits going forward."))]
    cands = pull._candidates(tx[0]["text"])
    assert len(cands) == 2 and "thank everyone" not in " ".join(cands)       # prefilter: only guidance-like sentences reach the model
    good = next(i for i, c in enumerate(cands) if "revenue growth" in c)
    fake_llm["responder"] = lambda p: _json.dumps(dict(items=[
        dict(i=good, metric="revenue", low=14, high=16, horizon="FY27"),
        dict(i=good, metric="ebitda_margin", low=22, high=24, horizon="FY27"),      # numbers not in sentence -> rejected
        dict(i=99, metric="revenue", low=14, high=16, horizon="FY27"),              # bad index -> rejected
        dict(i=good, metric="made_up", low=14, high=16, horizon="FY27")]))          # unknown metric -> rejected
    got = pull._extract_llm(tx)
    assert [(g["metric"], g["low"], g["high"], g["horizon"]) for g in got] == [("revenue", 14.0, 16.0, "FY27")]
    assert got[0]["sentence"] in tx[0]["text"]                                       # sentence comes from the source, never from the model


def test_llm_summaries_and_graceful_failure(fake_llm):
    fake_llm["responder"] = lambda p: "Board approved a large capacity addition."
    d = push.digest(72)
    assert any(i.get("why_via") == "llm" for i in d["items"]) and llm.STATS["by_purpose"]["filing_summary"]["calls"] >= 1
    n = len(fake_llm["calls"]); push.digest(72)
    assert len(fake_llm["calls"]) == n and llm.STATS["cache_hits"] >= 1            # second load is served from the on-disk cache
    # provider down: digest and ask still work, errors are counted and visible
    fake_llm["responder"] = lambda p: httpx.ConnectError("down")
    assert c.get("/api/digest?hours=72").status_code == 200
    assert c.post("/api/ask", json={"q": "is the paint maker worth my time?"}).json()["intent"] == "help"
    assert llm.STATS["errors"] >= 1 and "ConnectError" in llm.STATS["last_error"]
    assert c.get("/api/llm").json()["provider"] == "groq"


# ---------------------------------------------------------------- network ranking regressions
def test_short_aliases_do_not_match_inside_words():
    assert not network.org_matches("Agrilend", ["Reliance Industries", "RIL", "Jio"])     # 'ril' inside 'agrilend'
    assert network.org_matches("Reliance Retail", ["Reliance Industries", "Reliance"])
    assert network.org_matches("TCS", ["TCS"])


def test_unrelated_sectors_are_not_suggested():
    names = lambda t: [p["name"] for p in network.who_to_call(ticker=t)]
    assert "Farah Khan" not in names("RELIANCE")                                           # Agrilend founder
    for t in ("TCS", "INFY"):                                                              # 'it' in 'credit'/'capital' bug
        assert not {"Siddharth Rao", "Karan Shah", "Farah Khan", "Nikhil Agarwal"} & set(names(t))
    assert "Karan Shah" not in names("LT")


def test_direct_experience_beats_adjacent_and_stale_is_flagged():
    r = network.who_to_call(ticker="DEEPAKNTR")
    assert r[0]["name"] == "Sneha Patil" and not r[0]["dated"]                              # current beats departed
    nik = next(x for x in r if x["name"] == "Nikhil Agarwal")
    assert nik["dated"] and "may be dated" in nik["why"] and nik["score"] < r[0]["score"]
    cur = network.who_to_call(ticker="TATAMOTORS")
    assert cur[0]["name"] == "Pooja Nair" and not cur[0]["dated"]                         # current employee, not flagged
    adj = [p for p in network.who_to_call(ticker="TATAMOTORS") if p["why"].startswith("Adjacent")]
    assert all(p["score"] < cur[0]["score"] for p in adj)


def test_every_company_has_a_current_employee_in_the_network():
    for co in store.companies():
        names = [co["name"]] + co["aliases"]
        current = [p for p in store.people() if any(h["end"] is None and network.org_matches(h["org"], names) for h in p["history"])]
        assert current, f"no current employee for {co['ticker']}"
        top = network.who_to_call(ticker=co["ticker"])[0]
        assert not top["dated"] and any(top["id"] == p["id"] for p in current), co["ticker"]
        assert top["why"].startswith("Works at ") and top["why"].count("now") == 1            # shown as current ("20xx-now")


def test_relative_horizons_resolve_from_the_call():
    mk = lambda said, sent: dict(metric="revenue", said_in=said, sentence=sent, quarters=[], horizon="next FY", low=10, high=12)
    assert pull._resolve_horizon(mk("Q4FY26", "We expect growth of 10-12% this year"))["horizon"] == "FY27"    # Q4 call: the year just starting
    assert pull._resolve_horizon(mk("Q2FY26", "We expect growth of 10-12% this year"))["horizon"] == "FY26"    # mid-year call: current year
    assert pull._resolve_horizon(mk("Q2FY26", "We see growth of 10-12% next year"))["horizon"] == "FY27"
    assert pull._resolve_horizon(dict(mk("Q2FY26", "x"), quarters=["Q4FY26"], horizon="Q4 FY26"))["quarters"] == ["Q4FY26"]  # explicit horizon untouched


def test_part_year_guidance_is_not_graded_and_capex_is_capped():
    r = pull.analyse("DEEPAKNTR")
    fy27 = next(g for g in r["narrative_vs_numbers"] if g["metric"] == "revenue" and g["horizon"] == "FY27")
    assert fy27["status"] == "pending" and fy27["delivered"] is not None and "Part-year" in fy27["note"]
    assert r["scorecard"]["measured"] == sum(1 for g in r["narrative_vs_numbers"] if g["status"] != "pending")
    assert sum(1 for g in r["narrative_vs_numbers"] if g["metric"] == "capex") <= 2


def test_verdict_does_not_claim_track_record_when_nothing_is_measurable(monkeypatch):
    monkeypatch.setattr(pull, "narrative_vs_numbers", lambda f: [])
    v = pull.analyse("TITAN")["verdict"]
    assert "not yet measurable" in v and "met or beat" not in v