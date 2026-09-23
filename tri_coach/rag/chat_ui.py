#!/usr/bin/env python3
"""
Local dialogue verification UI for tri-coach.

This is intentionally stdlib-only: it serves a small HTML app and forwards each
question to rag_answer.answer_question(), so UI checks exercise the same RAG path
as the CLI.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tri_coach.rag import answer as rag_answer


ALLOWED_PROVIDERS = {"ollama", "openai-compatible", "dry-run"}
ALLOWED_DOMAINS = {"", "bike", "run", "swim", "strength", "nutrition", "recovery", "race"}


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>tri-coach 对话验证</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7f6;
      --panel: #ffffff;
      --ink: #1f2724;
      --muted: #64706b;
      --line: #dbe2de;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --accent-soft: #d8f3ef;
      --warn: #a16207;
      --warn-soft: #fff6d7;
      --blue: #1d4ed8;
      --blue-soft: #dbeafe;
      --red: #b91c1c;
      --red-soft: #fee2e2;
      --shadow: 0 18px 45px rgba(31, 39, 36, 0.08);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
    }

    button,
    select,
    textarea,
    input {
      font: inherit;
    }

    button {
      border: 0;
      cursor: pointer;
    }

    .app {
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
    }

    aside {
      border-right: 1px solid var(--line);
      background: #fbfcfb;
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    main {
      min-width: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      height: 100vh;
    }

    .brand {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .brand h1 {
      margin: 0;
      font-size: 20px;
      line-height: 1.2;
      letter-spacing: 0;
    }

    .brand p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .control-panel {
      box-shadow: none;
      padding: 14px;
      display: grid;
      gap: 12px;
    }

    .field {
      display: grid;
      gap: 6px;
    }

    .field label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }

    .field select,
    .field input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      color: var(--ink);
      height: 38px;
      padding: 0 10px;
      outline: none;
    }

    .field select:focus,
    .field input:focus,
    textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-soft);
    }

    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    .status {
      padding: 14px;
      display: grid;
      gap: 10px;
      box-shadow: none;
    }

    .status-line {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-height: 24px;
      font-size: 13px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      min-height: 24px;
      padding: 3px 9px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }

    .ok {
      color: var(--accent-strong);
      background: var(--accent-soft);
    }

    .warn {
      color: var(--warn);
      background: var(--warn-soft);
    }

    .bad {
      color: var(--red);
      background: var(--red-soft);
    }

    .samples {
      display: grid;
      gap: 8px;
    }

    .sample-btn {
      text-align: left;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px;
      background: #fff;
      color: var(--ink);
      line-height: 1.45;
      font-size: 13px;
    }

    .sample-btn:hover {
      border-color: #9ccbc5;
      background: #f6fffd;
    }

    .topbar {
      min-width: 0;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.82);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }

    .topbar h2 {
      margin: 0;
      font-size: 17px;
      letter-spacing: 0;
    }

    .meta {
      color: var(--muted);
      font-size: 13px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .conversation {
      min-height: 0;
      overflow: auto;
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .empty {
      max-width: 720px;
      margin: auto;
      text-align: center;
      color: var(--muted);
      line-height: 1.7;
    }

    .turn {
      display: grid;
      gap: 10px;
      max-width: 980px;
      width: min(100%, 980px);
    }

    .turn.user {
      margin-left: auto;
      width: min(100%, 720px);
    }

    .bubble {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px 16px;
      line-height: 1.7;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .turn.user .bubble {
      background: #ecfdf5;
      border-color: #b7e4d2;
    }

    .answer-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }

    .sources {
      display: grid;
      gap: 8px;
    }

    details.source {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }

    details.source summary {
      list-style: none;
      cursor: pointer;
      padding: 10px 12px;
      display: grid;
      gap: 5px;
    }

    details.source summary::-webkit-details-marker {
      display: none;
    }

    .source-main {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }

    .source-title {
      font-size: 13px;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .source-sub {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .source-body {
      border-top: 1px solid var(--line);
      padding: 10px 12px 12px;
      color: #39443f;
      font-size: 13px;
      line-height: 1.6;
      background: #fafbfa;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .source-badge {
      color: var(--blue);
      background: var(--blue-soft);
    }

    .composer {
      border-top: 1px solid var(--line);
      background: #fbfcfb;
      padding: 16px 22px 20px;
    }

    .composer-inner {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: end;
      max-width: 1120px;
      margin: 0 auto;
    }

    textarea {
      width: 100%;
      min-height: 96px;
      max-height: 240px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      line-height: 1.55;
      color: var(--ink);
      background: #fff;
      outline: none;
    }

    .actions {
      display: grid;
      gap: 8px;
      width: 132px;
    }

    .primary,
    .secondary {
      height: 42px;
      border-radius: 7px;
      font-weight: 750;
    }

    .primary {
      color: #fff;
      background: var(--accent);
    }

    .primary:hover {
      background: var(--accent-strong);
    }

    .primary:disabled,
    .secondary:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }

    .secondary {
      color: var(--ink);
      background: #fff;
      border: 1px solid var(--line);
    }

    .spinner {
      display: inline-block;
      width: 12px;
      height: 12px;
      border: 2px solid #c9d4d0;
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      vertical-align: -2px;
      margin-right: 6px;
    }

    @keyframes spin {
      to {
        transform: rotate(360deg);
      }
    }

    @media (max-width: 840px) {
      .app {
        grid-template-columns: 1fr;
      }

      aside {
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }

      main {
        height: auto;
        min-height: 78vh;
      }

      .composer-inner {
        grid-template-columns: 1fr;
      }

      .actions {
        grid-template-columns: 1fr 1fr;
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <div class="brand">
        <h1>tri-coach 对话验证</h1>
        <p>本地 RAG 问答路径</p>
      </div>

      <section class="panel control-panel" aria-label="配置">
        <div class="field">
          <label for="provider">Provider</label>
          <select id="provider">
            <option value="ollama" selected>ollama</option>
            <option value="openai-compatible">openai-compatible</option>
            <option value="dry-run">dry-run</option>
          </select>
        </div>
        <div class="field">
          <label for="domain">Domain</label>
          <select id="domain">
            <option value="">all</option>
            <option value="swim">swim</option>
            <option value="bike">bike</option>
            <option value="run">run</option>
            <option value="nutrition">nutrition</option>
            <option value="recovery">recovery</option>
            <option value="strength">strength</option>
            <option value="race">race</option>
          </select>
        </div>
        <div class="grid-2">
          <div class="field">
            <label for="topK">Top K</label>
            <input id="topK" type="number" min="1" max="12" value="6">
          </div>
          <div class="field">
            <label for="neighbors">Neighbors</label>
            <input id="neighbors" type="number" min="0" max="3" value="1">
          </div>
        </div>
      </section>

      <section class="panel status" aria-label="状态">
        <div class="status-line">
          <span>Vector DB</span>
          <span id="dbStatus" class="pill warn">checking</span>
        </div>
        <div class="status-line">
          <span>Ollama</span>
          <span id="ollamaStatus" class="pill warn">checking</span>
        </div>
        <div id="healthDetail" class="meta"></div>
      </section>

      <section class="samples" aria-label="样例">
        <button class="sample-btn" data-sample="我50米自由泳0:27，200米2:00，400米4:20，帮我算游泳CSS和训练区间。">游泳 CSS 固定样例</button>
        <button class="sample-btn" data-sample="这周跑量比上周加了30%，左边膝盖外侧下坡刺痛，明天还能跑15K吗？">伤病拦截样例</button>
        <button class="sample-btn" data-sample="帮我安排一份大铁赛前最后一周的减量补给计划。">补给边界样例</button>
      </section>
    </aside>

    <main>
      <header class="topbar">
        <div>
          <h2>Dialogue Console</h2>
          <div class="meta" id="runMeta">ready</div>
        </div>
        <span id="modePill" class="pill ok">local</span>
      </header>

      <section id="conversation" class="conversation" aria-live="polite">
        <div class="empty" id="emptyState">
          输入一个训练、RAG 或计算器问题，运行后这里会显示回答、来源和耗时。
        </div>
      </section>

      <footer class="composer">
        <div class="composer-inner">
          <textarea id="question" placeholder="输入要验证的问题"></textarea>
          <div class="actions">
            <button id="sendBtn" class="primary">运行</button>
            <button id="clearBtn" class="secondary">清空</button>
          </div>
        </div>
      </footer>
    </main>
  </div>

  <script>
    const conversation = document.getElementById("conversation");
    const emptyState = document.getElementById("emptyState");
    const questionEl = document.getElementById("question");
    const sendBtn = document.getElementById("sendBtn");
    const clearBtn = document.getElementById("clearBtn");
    const runMeta = document.getElementById("runMeta");

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function renderText(value) {
      return escapeHtml(value).replace(/\n/g, "<br>");
    }

    function setPill(id, state, text) {
      const node = document.getElementById(id);
      node.className = `pill ${state}`;
      node.textContent = text;
    }

    function addUserTurn(question) {
      emptyState?.remove();
      const node = document.createElement("article");
      node.className = "turn user";
      node.innerHTML = `<div class="bubble">${renderText(question)}</div>`;
      conversation.appendChild(node);
      conversation.scrollTop = conversation.scrollHeight;
    }

    function addLoadingTurn() {
      const node = document.createElement("article");
      node.className = "turn assistant";
      node.innerHTML = `
        <div class="bubble">
          <div class="answer-head"><span><span class="spinner"></span>running</span></div>
          正在检索和生成...
        </div>`;
      conversation.appendChild(node);
      conversation.scrollTop = conversation.scrollHeight;
      return node;
    }

    function sourceHtml(source) {
      const label = escapeHtml(source.label);
      const title = escapeHtml(source.title || "Untitled");
      const heading = source.heading ? ` / ${escapeHtml(source.heading)}` : "";
      const score = Number(source.score ?? 0).toFixed(4);
      const domain = escapeHtml(source.domain || "");
      const trust = escapeHtml(source.trust_level || "");
      const chunk = escapeHtml(source.chunk_id || "");
      const path = escapeHtml(source.source_path || "");
      const excerpt = renderText(source.excerpt || "");
      return `
        <details class="source">
          <summary>
            <div class="source-main">
              <span class="pill source-badge">${label}</span>
              <span class="source-title">${title}${heading}</span>
            </div>
            <div class="source-sub">${domain}/${trust} · score ${score}</div>
          </summary>
          <div class="source-body">chunk_id=${chunk}
source_path=${path}

${excerpt}</div>
        </details>`;
    }

    function renderResult(node, payload) {
      const result = payload.result || {};
      const provider = escapeHtml(result.provider || "");
      const model = escapeHtml(result.chat_model || "");
      const elapsed = payload.elapsed_ms ? `${payload.elapsed_ms} ms` : "";
      const sources = Array.isArray(result.sources) ? result.sources : [];
      runMeta.textContent = `${provider} ${model} ${elapsed}`.trim();
      node.innerHTML = `
        <div class="bubble">
          <div class="answer-head">
            <span>${provider || "provider"} · ${elapsed}</span>
            <span>${sources.length} sources</span>
          </div>
          ${renderText(result.answer || "")}
        </div>
        <div class="sources">${sources.map(sourceHtml).join("")}</div>`;
      conversation.scrollTop = conversation.scrollHeight;
    }

    function renderError(node, payload) {
      runMeta.textContent = "error";
      const hint = payload.hint ? `<br><br>${renderText(payload.hint)}` : "";
      node.innerHTML = `
        <div class="bubble">
          <div class="answer-head"><span>error</span></div>
          ${renderText(payload.error || "Request failed")}${hint}
        </div>`;
    }

    function readConfig() {
      return {
        provider: document.getElementById("provider").value,
        domain: document.getElementById("domain").value,
        top_k: Number(document.getElementById("topK").value || 6),
        expand_neighbors: Number(document.getElementById("neighbors").value || 1)
      };
    }

    async function runQuestion() {
      const question = questionEl.value.trim();
      if (!question || sendBtn.disabled) {
        return;
      }
      addUserTurn(question);
      const pending = addLoadingTurn();
      questionEl.value = "";
      sendBtn.disabled = true;
      clearBtn.disabled = true;
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, ...readConfig() })
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          renderError(pending, payload);
        } else {
          renderResult(pending, payload);
        }
      } catch (error) {
        renderError(pending, { error: String(error) });
      } finally {
        sendBtn.disabled = false;
        clearBtn.disabled = false;
        questionEl.focus();
      }
    }

    async function loadHealth() {
      try {
        const response = await fetch("/api/health");
        const health = await response.json();
        setPill("dbStatus", health.db_ok ? "ok" : "bad", health.db_ok ? "ok" : "missing");
        setPill("ollamaStatus", health.ollama_ok ? "ok" : "warn", health.ollama_ok ? "online" : "offline");
        document.getElementById("healthDetail").textContent = health.detail || "";
      } catch (error) {
        setPill("dbStatus", "warn", "unknown");
        setPill("ollamaStatus", "warn", "unknown");
        document.getElementById("healthDetail").textContent = String(error);
      }
    }

    sendBtn.addEventListener("click", runQuestion);
    clearBtn.addEventListener("click", () => {
      conversation.innerHTML = '<div class="empty" id="emptyState">输入一个训练、RAG 或计算器问题，运行后这里会显示回答、来源和耗时。</div>';
      runMeta.textContent = "ready";
    });
    questionEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        runQuestion();
      }
    });
    document.querySelectorAll("[data-sample]").forEach((button) => {
      button.addEventListener("click", () => {
        questionEl.value = button.dataset.sample;
        questionEl.focus();
      });
    });

    loadHealth();
  </script>
</body>
</html>
"""


def clamp_int(value: Any, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(lower, min(upper, parsed))


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, status: int, body: str, content_type: str) -> None:
    payload = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def friendly_error(exc: BaseException) -> tuple[str, str | None]:
    text = str(exc)
    if "Connection refused" in text or "Errno 61" in text:
        return (
            "本地 embedding 服务连接失败。",
            "启动 Ollama 后重试：ollama serve。RAG 检索需要 bge-m3:latest 可用。",
        )
    return (text or exc.__class__.__name__, None)


def build_rag_config(payload: dict[str, Any]) -> rag_answer.RagConfig:
    provider = str(payload.get("provider") or os.getenv("TRI_RAG_PROVIDER", "ollama"))
    if provider not in ALLOWED_PROVIDERS:
        provider = "ollama"
    domain = str(payload.get("domain") or "")
    if domain not in ALLOWED_DOMAINS:
        domain = ""
    return rag_answer.RagConfig(
        retrieval=rag_answer.RetrievalConfig(
            db=Path(str(payload.get("db") or rag_answer.DEFAULT_DB)),
            embedding_model=payload.get("embedding_model") or None,
            embedding_ollama_url=str(
                payload.get("embedding_ollama_url")
                or os.getenv("TRI_RAG_EMBEDDING_OLLAMA_URL", rag_answer.DEFAULT_EMBEDDING_OLLAMA_URL)
            ),
            top_k=clamp_int(payload.get("top_k"), 6, 1, 12),
            expand_neighbors=clamp_int(payload.get("expand_neighbors"), 1, 0, 3),
            domain=domain or None,
            hybrid=True,
            lexical_weight=0.55,
            use_query_expansion=True,
        ),
        generation=rag_answer.GenerationConfig(
            provider=provider,
            chat_model=str(payload.get("chat_model") or os.getenv("TRI_RAG_CHAT_MODEL", rag_answer.DEFAULT_CHAT_MODEL)),
            chat_base_url=str(
                payload.get("chat_base_url")
                or os.getenv("TRI_RAG_CHAT_BASE_URL", rag_answer.DEFAULT_EMBEDDING_OLLAMA_URL)
            ),
            api_key=os.getenv(str(payload.get("api_key_env") or "OPENAI_API_KEY")),
            temperature=0.2,
            max_tokens=1200,
            timeout=180,
        ),
    )


def health_payload() -> dict[str, Any]:
    db_path = rag_answer.DEFAULT_DB
    db_ok = db_path.exists()
    chunks: int | None = None
    if db_ok:
        try:
            conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
            chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            conn.close()
        except sqlite3.Error:
            db_ok = False

    ollama_ok = False
    models: list[str] = []
    try:
        req = urllib.request.Request(f"{rag_answer.DEFAULT_EMBEDDING_OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=1.5) as response:
            data = json.loads(response.read().decode("utf-8"))
        models = [item.get("name", "") for item in data.get("models", []) if item.get("name")]
        ollama_ok = True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        ollama_ok = False

    detail_parts = []
    if chunks is not None:
        detail_parts.append(f"{chunks} chunks")
    if models:
        detail_parts.append(", ".join(models[:3]))
    return {
        "db_ok": db_ok,
        "ollama_ok": ollama_ok,
        "detail": " · ".join(detail_parts),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "TriCoachChatUI/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            text_response(self, 200, HTML, "text/html; charset=utf-8")
            return
        if self.path == "/api/health":
            json_response(self, 200, health_payload())
            return
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        json_response(self, 404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            json_response(self, 404, {"ok": False, "error": "Not found"})
            return
        started = time.monotonic()
        try:
            payload = read_json(self)
            question = str(payload.get("question") or "").strip()
            if not question:
                json_response(self, 400, {"ok": False, "error": "Question is required"})
                return
            result = rag_answer.answer_question(question, build_rag_config(payload))
            json_response(
                self,
                200,
                {
                    "ok": True,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "result": result,
                },
            )
        except Exception as exc:  # noqa: BLE001 - this is a local debugging UI.
            error, hint = friendly_error(exc)
            print(traceback.format_exc())
            json_response(
                self,
                500,
                {
                    "ok": False,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "error": error,
                    "hint": hint,
                },
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"tri-coach chat UI: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
