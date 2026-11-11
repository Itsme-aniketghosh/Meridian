"""The one LLM client (GAPS.md gap 1): llm(model, prompt) -> {"reply", "tokens_in", "tokens_out", "cost_usd"}.

    from meridian.llm import Client
    llm = Client()                       # project from $MERIDIAN_GCP_PROJECT, settings.toml [llm]
    r = llm("claude-haiku-5-5", "...")   # what agents.write, diffs.run and jev.Router already take

Every model in the comparison (GAPS.md, the model comparison) goes through Google Cloud, so one login covers them:
    claude    Claude on Vertex AI (anthropic[vertex], AnthropicVertex)
    gemini    Gemini on Vertex AI (google-genai, vertexai=True)
    maas      the open models on Vertex's model-as-a-service API (Llama 4, Gemma 4), its chat completions endpoint
    mistral   Mistral on Vertex (publishers/mistralai ... :rawPredict)
    ollama    Phi-4-mini on this machine through Ollama (Jev only), $0
Auth is Google's application default credentials: `gcloud auth application-default login` once per machine.

The prompt goes in as one user message, the same bytes for every vendor, so the comparison compares models and not
framings. The reply is the model's text only; thinking tokens are billed as output and counted in tokens_out.
A refusal or an empty answer comes back as reply "" (not an error): the writer reads it as bad JSON and moves up the
ladder, as it does for any reply it can't use.

Cost is tokens x the price in MODELS, per 1M tokens in / out. A model with no price is refused before any call: the
open models' rates come from third-party listings (GAPS.md, cost) and must be checked first. The client also stops
at its own spend cap (settings.toml [llm] max_usd, per process), under Aniket's $150 account alert.

Never logged or written here: the prompt or the reply. agents.py logs counts and labels only.
"""
import os
import threading
import time
from dataclasses import dataclass

from meridian.common import load_settings


@dataclass(frozen=True)
class Model:
    provider: str      # claude, gemini, maas, mistral, ollama
    id: str            # the id the provider takes
    usd_in: float      # per 1M tokens in; None = not checked yet, so never called
    usd_out: float     # per 1M tokens out
    region: str = "global"


# The names on the left are what ladders, cache keys and logged rows carry. Claude's prices: GAPS.md (list rates,
# Nov 11). The rest are marked None until someone checks them on the Vertex pricing page and fills them in.
MODELS = {
    "claude-haiku-5-5": Model("claude", "claude-haiku-5-5", 0.10, 0.50),
    "claude-sonnet-5-5": Model("claude", "claude-sonnet-5-5", 2.00, 10.00),
    "claude-opus-5-5": Model("claude", "claude-opus-5-5", 4.00, 20.00),
    "gemini-flash": Model("gemini", "gemini-flash-latest", None, None),
    "gemini-pro": Model("gemini", "gemini-pro-latest", None, None),
    "mistral-medium": Model("mistral", "mistral-medium-3", None, None, "us-central1"),
    "llama-4-maverick": Model("maas", "meta/llama-4-maverick-17b-128e-instruct-maas", None, None, "us-east5"),
    "llama-4-scout": Model("maas", "meta/llama-4-scout-17b-16e-instruct-maas", None, None, "us-east5"),
    "gemma-4": Model("maas", "google/gemma-4-31b-it-maas", None, None, "us-central1"),
    "phi4-mini": Model("ollama", "phi4-mini", 0.0, 0.0, "local"),
}
MAX_OUT = 4000          # reply tokens, thinking included: the writer's texts are ~800 (GAPS.md, cost)
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class BudgetExceeded(RuntimeError):
    """The client's spend cap is reached: no more calls in this process."""


class Unpriced(ValueError):
    """The model has no checked price in MODELS, so a call's cost can't be known."""


def cost(m, tokens_in, tokens_out):
    return round((tokens_in * m.usd_in + tokens_out * m.usd_out) / 1e6, 8)


class Client:
    """llm(model, prompt). One per run: it holds the provider clients and the spend so far (thread-safe)."""

    def __init__(self, project=None, *, max_usd=None, models=None, retries=None, timeout=None, transport=None):
        s = load_settings("llm")
        self.project = project or os.environ.get(s.get("project_env", "MERIDIAN_GCP_PROJECT"))
        self.max_usd = float(max_usd if max_usd is not None else s.get("max_usd", 25.0))
        self.retries = int(retries if retries is not None else s.get("retries", 3))
        self.timeout = float(timeout if timeout is not None else s.get("timeout_s", 120))
        self.ollama = s.get("ollama", "http://localhost:11434")
        self.models = dict(models or MODELS)
        self.spent, self.calls = 0.0, 0
        self._lock = threading.Lock()
        self._clients = {}
        self._transport = transport   # tests: provider -> fn(model, prompt) -> (reply, tokens_in, tokens_out)

    def __call__(self, model, prompt):
        m = self.models.get(model)
        if m is None:
            raise ValueError(f"unknown model {model!r}: one of {sorted(self.models)}")
        if m.usd_in is None or m.usd_out is None:
            raise Unpriced(f"{model}: no checked price in meridian.llm.MODELS, so no call")
        if m.provider != "ollama" and not self.project and self._transport is None:
            raise SystemExit("no GCP project: set $MERIDIAN_GCP_PROJECT (settings.toml [llm] project_env)")
        with self._lock:
            if self.spent >= self.max_usd:
                raise BudgetExceeded(f"spent ${self.spent:.4f} of the ${self.max_usd:.2f} cap: no more calls")
        reply, t_in, t_out = self._retry(m, prompt)
        usd = cost(m, t_in, t_out)
        with self._lock:
            self.spent += usd
            self.calls += 1
        return {"reply": reply, "tokens_in": int(t_in), "tokens_out": int(t_out), "cost_usd": usd}

    def _retry(self, m, prompt):
        send = self._transport[m.provider] if self._transport else getattr(self, f"_{m.provider}")
        for attempt in range(self.retries + 1):
            try:
                return send(m, prompt)
            except Exception as e:  # noqa: BLE001: only the retryable ones go round again
                if attempt == self.retries or not _retryable(e):
                    raise
                time.sleep(min(2 ** attempt, 30))

    # ---- providers: each returns (reply text, tokens in, tokens out) ----

    def _client(self, name, make):
        with self._lock:
            if name not in self._clients:
                self._clients[name] = make()
            return self._clients[name]

    def _claude(self, m, prompt):
        from anthropic import AnthropicVertex
        c = self._client(("claude", m.region), lambda: AnthropicVertex(
            project_id=self.project, region=m.region, timeout=self.timeout, max_retries=0))
        r = c.messages.create(model=m.id, max_tokens=MAX_OUT, output_config={"effort": "low"},
                              messages=[{"role": "user", "content": prompt}])
        text = "" if r.stop_reason == "refusal" else "".join(b.text for b in r.content if b.type == "text")
        return text, r.usage.input_tokens, r.usage.output_tokens

    def _gemini(self, m, prompt):
        from google import genai
        from google.genai import types
        c = self._client(("gemini", m.region), lambda: genai.Client(
            vertexai=True, project=self.project, location=m.region,
            http_options=types.HttpOptions(timeout=int(self.timeout * 1000))))
        r = c.models.generate_content(model=m.id, contents=prompt,
                                      config=types.GenerateContentConfig(max_output_tokens=MAX_OUT))
        u = r.usage_metadata
        out = (u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
        return r.text or "", u.prompt_token_count or 0, out

    def _session(self):
        def make():
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            return AuthorizedSession(creds)
        return self._client("session", make)

    def _host(self, region):
        return "aiplatform.googleapis.com" if region == "global" else f"{region}-aiplatform.googleapis.com"

    def _chat(self, url, body):
        r = self._session().post(url, json=body, timeout=self.timeout)
        if r.status_code >= 400:
            raise HTTPError(r.status_code, r.text[:300])
        d = r.json()
        u = d.get("usage") or {}
        choice = (d.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or "", u.get("prompt_tokens", 0), \
            u.get("completion_tokens", 0)

    def _maas(self, m, prompt):
        url = (f"https://{self._host(m.region)}/v1/projects/{self.project}/locations/{m.region}"
               "/endpoints/openapi/chat/completions")
        return self._chat(url, {"model": m.id, "max_tokens": MAX_OUT,
                                "messages": [{"role": "user", "content": prompt}]})

    def _mistral(self, m, prompt):
        url = (f"https://{self._host(m.region)}/v1/projects/{self.project}/locations/{m.region}"
               f"/publishers/mistralai/models/{m.id}:rawPredict")
        return self._chat(url, {"model": m.id, "max_tokens": MAX_OUT,
                                "messages": [{"role": "user", "content": prompt}]})

    def _ollama(self, m, prompt):
        import json
        import urllib.request
        req = urllib.request.Request(f"{self.ollama}/api/chat", method="POST",
                                     headers={"Content-Type": "application/json"},
                                     data=json.dumps({"model": m.id, "stream": False,
                                                      "messages": [{"role": "user", "content": prompt}]}).encode())
        with urllib.request.urlopen(req, timeout=self.timeout) as f:
            d = json.loads(f.read())
        return (d.get("message") or {}).get("content") or "", d.get("prompt_eval_count", 0), d.get("eval_count", 0)


class HTTPError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status_code = status


def _retryable(e):
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None) \
        or getattr(e, "code", None)
    if isinstance(status, int):
        return status in RETRY_STATUS
    return isinstance(e, (TimeoutError, ConnectionError)) or type(e).__name__ in (
        "APIConnectionError", "APITimeoutError", "ConnectionError", "Timeout", "ReadTimeout")
