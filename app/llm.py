"""Optional LLM layer (single seam). Provider chosen by LLM_PROVIDER in the environment or a .env file:
  groq       -> GROQ_API_KEY, GROQ_MODEL (default openai/gpt-oss-20b)   [OpenAI-compatible endpoint]
  hyde       -> HYDE_INFERENCE_API_KEY, HYDE_MODEL (default hyde/glm-5.3-flash)
Responses are never persisted. Any failure (no key, no network, 429, bad output) returns None
and callers fall back to deterministic logic, so the app never depends on the model. STATS (see /api/llm) shows
whether calls are really happening."""
import json
import os
import re
import threading
import time

ROOT = os.path.join(os.path.dirname(__file__), "..")
PATIENT = False   # batch jobs (app/warm.py) set this: on a 429 wait out Retry-After and retry. Live requests fail fast instead.
STATS = dict(calls=0, errors=0, last_error=None, by_purpose={})
_lock = threading.Lock()


def _load_dotenv(path=os.path.join(ROOT, ".env")):
    """Minimal .env reader (no dependency). Real environment variables win over the file."""
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_dotenv()


def provider():
    p = os.environ.get("LLM_PROVIDER", "").lower()
    if p == "groq" and os.environ.get("GROQ_API_KEY"):
        return "groq"
    if p == "hyde" and os.environ.get("HYDE_INFERENCE_API_KEY"):
        return "hyde"
    return None


def enabled():
    return provider() is not None


def model_name(tier="cheap"):
    p = provider()
    if p == "groq":
        return os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
    if p == "hyde":
        return os.environ.get("HYDE_MODEL", "hyde/glm-5.3-flash")
    return None


def _bump(purpose, key):
    with _lock:
        STATS[key] += 1
        STATS["by_purpose"].setdefault(purpose, dict(calls=0, errors=0))[key] += 1


class RateLimited(Exception):
    def __init__(self, after):
        self.after = after


def _groq(prompt, max_tokens):
    import httpx
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=40,
                   headers={"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]},
                   json={"model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
                         "messages": [{"role": "user", "content": prompt}],
                         "reasoning_effort": "low",                     # gpt-oss is a reasoning model; keep it cheap and fast
                         "max_completion_tokens": max_tokens + 500,     # headroom: reasoning tokens count against the cap
                         "temperature": 0.1})
    if r.status_code == 429:
        try:
            after = float(r.headers.get("retry-after", 20))
        except ValueError:
            after = 20.0
        raise RateLimited(after)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def _hyde(prompt, max_tokens):
    import httpx
    r = httpx.post("https://inference.hyde.tech/v1/chat/completions", timeout=40,
                   headers={"Authorization": "Bearer " + os.environ["HYDE_INFERENCE_API_KEY"]},
                   json={"model": model_name(),
                         "messages": [{"role": "user", "content": prompt}],
                         "reasoning_effort": "low",
                         "max_tokens": max_tokens,
                         "temperature": 0.1})
    if r.status_code == 429:
        try:
            after = float(r.headers.get("retry-after", 20))
        except ValueError:
            after = 20.0
        raise RateLimited(after)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"].get("content") or "").strip()


def complete(prompt, tier="cheap", max_tokens=300, purpose="misc"):
    prov = provider()
    if not prov:
        return None
    _bump(purpose, "calls")
    err = None
    for attempt in range(8):
        try:
            if prov == "groq":
                text = _groq(prompt, max_tokens)
            elif prov == "hyde":
                text = _hyde(prompt, max_tokens)
            else:
                raise ValueError(f"unsupported provider: {prov}")
            if not text:
                raise ValueError("empty completion (reasoning may have used the whole token budget)")
            return text
        except RateLimited as e:
            if PATIENT and attempt < 7 and e.after <= 90:     # a long wait means the DAILY cap: don't sit there
                time.sleep(e.after + 1)
                continue
            err = f"RateLimited: {prov} 429, retry-after {e.after:.0f}s"
            break
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"
            break
    _bump(purpose, "errors")
    with _lock:
        STATS["last_error"] = err
    return None


def complete_json(prompt, tier="cheap", max_tokens=600, purpose="misc"):
    """Ask for JSON and parse tolerantly (strips code fences / surrounding prose). None on any problem."""
    text = complete(prompt + "\n\nReply with JSON only, no prose, no code fences.", tier, max_tokens, purpose)
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for pat in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pat, text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                continue
    with _lock:
        STATS["errors"] += 1
        STATS["last_error"] = "model returned non-JSON: " + text[:120]
    return None