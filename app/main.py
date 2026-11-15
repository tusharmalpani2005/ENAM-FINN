import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import store, push, pull, network, router, seed, llm

app = FastAPI(title="FINN")
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


@app.get("/api/meta")
def meta():
    L = store.live_layers()
    synth = [n for n, ok in (("prices", L["prices"]), ("filings", L["announcements"])) if not ok] + ["network"]
    return dict(synthetic=True, synthetic_layers=synth, live_fundamentals=L["fundamentals"],
                portfolio=store.companies(), themes=seed.THEMES, categories=sorted(push.BASE), labels=push.LABEL,
                research_ready=sorted(seed.FUNDAMENTALS), llm=llm.provider())


@app.get("/api/llm")
def llm_status():
    """Open this in a browser to see whether model calls are really happening."""
    return dict(provider=llm.provider(), model=llm.model_name(), **llm.STATS)


@app.get("/api/digest")
def digest(hours: int = Query(24, ge=1, le=720), cats: Optional[str] = None):
    return push.digest(hours, cats.split(",") if cats else None)


@app.get("/api/alerts")
def alerts(vol_x: float = 2.0, px: float = 4.0):
    return push.alerts(vol_x, px)


@app.get("/api/research/{ticker}")
def research(ticker: str):
    try:
        r = pull.analyse(ticker)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not r:
        raise HTTPException(404, f"No fundamentals for {ticker}. Run: python -m app.fetchers.screener {ticker}")
    return r


@app.get("/api/network/graph/{ticker}")
def graph(ticker: str):
    if not store.company(ticker):
        raise HTTPException(404, "Unknown ticker")
    return network.graph(ticker.upper())


@app.get("/api/network/expertise")
def expertise(theme: str):
    return network.expertise(theme)


@app.get("/api/network/person/{pid}")
def person(pid: str):
    d = network.diligence(pid)
    if not d:
        raise HTTPException(404, "Unknown person")
    return d


@app.get("/api/network/warm")
def warm():
    return network.keep_warm(store.announcements())


class Q(BaseModel):
    q: str


@app.post("/api/ask")
def ask(body: Q):
    try:
        return router.ask(body.q)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
