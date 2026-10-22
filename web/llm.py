"""The owner's own language model, for the Analysis page.

Three ways to reach one, chosen on the Settings page (or by LLM_* variables in .env):

- anthropic: the Claude API (https://api.anthropic.com), with the owner's API key;
- openai: any OpenAI-compatible chat completions API with the owner's key or none: OpenAI,
  OpenRouter, Mistral, Groq, a local Ollama (http://localhost:11434/v1) or LM Studio;
- cli: a command on this computer that reads the prompt on stdin and prints the answer, such as
  `claude -p` or `ollama run llama3.1`. It is taken only from LLM_CLI_COMMAND in the environment,
  never from the web page, so nobody can make the dashboard run a program of their choice.

Plain HTTP with the standard library keeps the desktop build small and provider-neutral. Only
the data summary of web/analysis.py is sent; Deye credentials and invoices' personal data never are.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request

from collector import appconf, secrets

PROVIDERS = {
    "anthropic": {"label": "Claude API (Anthropic)", "base_url": "https://api.anthropic.com", "model": "claude-opus-5-5",
                  "needs_key": True},
    "openai": {"label": "OpenAI-съвместим API (OpenAI, OpenRouter, Ollama, LM Studio…)", "base_url": "https://api.openai.com/v1",
               "model": None, "needs_key": False},
    "cli": {"label": "Команда на този компютър (напр. claude -p)", "base_url": None, "model": None, "needs_key": False},
}
TIMEOUT_S = 600
MAX_TOKENS = 16000
# Claude Code run from an app may not see the sign-in kept in the macOS Keychain
LOGIN_HINT = "Командата не е влязла в акаунта. В Terminal пусни claude и напиши /login, после опитай пак. Ако там работи, а тук не: пусни claude setup-token, добави ред CLAUDE_CODE_OAUTH_TOKEN=токенът в .env и рестартирай приложението."


class LlmError(Exception):
    pass


def config(conn) -> dict:
    """The effective settings: the environment wins over the Settings page."""
    provider = os.environ.get("LLM_PROVIDER") or appconf.get(conn, "llm_provider")
    if provider not in PROVIDERS:
        provider = None
    p = PROVIDERS.get(provider) or {}
    return {
        "provider": provider,
        "model": os.environ.get("LLM_MODEL") or appconf.get(conn, "llm_model") or p.get("model"),
        "base_url": (os.environ.get("LLM_BASE_URL") or appconf.get(conn, "llm_base_url") or p.get("base_url") or "").rstrip("/"),
        "cli_command": os.environ.get("LLM_CLI_COMMAND") or None,
        "has_key": bool(secrets.get("LLM_API_KEY")),
        "pinned": {k: bool(os.environ.get(v)) for k, v in (("provider", "LLM_PROVIDER"), ("model", "LLM_MODEL"),
                                                          ("base_url", "LLM_BASE_URL"), ("api_key", "LLM_API_KEY"))},
    }


def ready(cfg: dict) -> tuple[bool, str | None]:
    """Whether an analysis can run, and if not, why (shown on the page)."""
    if not cfg["provider"]:
        return False, "Не е избран езиков модел."
    if cfg["provider"] == "cli":
        return (True, None) if cfg["cli_command"] else (False, "За команда на компютъра задай LLM_CLI_COMMAND в .env.")
    if PROVIDERS[cfg["provider"]]["needs_key"] and not cfg["has_key"]:
        return False, "Липсва API ключ."
    if not cfg["model"]:
        return False, "Не е избран модел."
    return True, None


def _post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:800]
        raise LlmError(f"HTTP {exc.code} от {url}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LlmError(f"Няма връзка с {url}: {exc}") from None


def _anthropic(cfg: dict, system: str, prompt: str) -> tuple[str, dict]:
    out = _post(f"{cfg['base_url']}/v1/messages", {
        "model": cfg["model"], "max_tokens": MAX_TOKENS, "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }, {"x-api-key": secrets.get("LLM_API_KEY") or "", "anthropic-version": "2023-06-01"})
    if out.get("stop_reason") == "refusal":
        raise LlmError("Моделът отказа заявката.")
    text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
    if out.get("stop_reason") == "max_tokens":
        raise LlmError("Отговорът на модела беше прекъснат (max_tokens).")
    return text, out.get("usage") or {}


def _openai(cfg: dict, system: str, prompt: str) -> tuple[str, dict]:
    key = secrets.get("LLM_API_KEY")
    out = _post(f"{cfg['base_url']}/chat/completions", {
        "model": cfg["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }, {"Authorization": f"Bearer {key}"} if key else {})
    try:
        return out["choices"][0]["message"]["content"] or "", out.get("usage") or {}
    except (KeyError, IndexError, TypeError):
        raise LlmError(f"Неочакван отговор: {json.dumps(out)[:500]}") from None


def _cli(cfg: dict, system: str, prompt: str) -> tuple[str, dict]:
    try:
        # run in the data folder, not in whatever folder the app started from (on macOS that can mean a
        # permission prompt for Desktop or Documents when a tool like Claude Code looks around)
        cwd = os.path.dirname(os.path.abspath(os.environ.get("DB_PATH", "./data/pv.sqlite")))
        res = subprocess.run(shlex.split(cfg["cli_command"]), input=f"{system}\n\n{prompt}", capture_output=True,
                             text=True, timeout=TIMEOUT_S, cwd=cwd if os.path.isdir(cwd) else None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LlmError(f"Командата не тръгна: {exc}") from None
    if res.returncode != 0:
        # some tools (Claude Code among them) print their error on stdout
        detail = (res.stderr.strip() or res.stdout.strip())[-800:]
        hint = f" {LOGIN_HINT}" if "not logged in" in detail.lower() or "/login" in detail else ""
        raise LlmError(f"Командата завърши с код {res.returncode}: {detail}{hint}")
    return res.stdout, {}


def complete(cfg: dict, system: str, prompt: str) -> tuple[str, dict]:
    fn = {"anthropic": _anthropic, "openai": _openai, "cli": _cli}[cfg["provider"]]
    return fn(cfg, system, prompt)


def parse_json(text: str) -> dict:
    """The first JSON object in the answer (models sometimes wrap it in ``` fences or prose)."""
    start = text.find("{")
    if start < 0:
        raise LlmError("Моделът не върна JSON.")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError as exc:
        raise LlmError(f"Отговорът не е валиден JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise LlmError("Отговорът не е JSON обект.")
    return obj
