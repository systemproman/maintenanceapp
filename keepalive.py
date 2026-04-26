"""
keepalive.py — Keep-Alive com Telegram + PWA instalável com cache offline
=========================================================================
Adicione ao main.py:

    from keepalive import setup_keepalive   # ← import
    setup_keepalive()                       # ← antes de ui.run()

Variáveis de ambiente no Render:
    TELEGRAM_BOT_TOKEN   token do bot (@BotFather)
    TELEGRAM_CHAT_ID     ID do chat/grupo
    KEEPALIVE_INTERVAL   intervalo em segundos (padrão: 290)
    KEEPALIVE_ADMIN_PASS senha da página /keepalive (padrão: STORAGE_SECRET)
"""

import os
import asyncio
import httpx
from datetime import datetime
from nicegui import ui, app
from fastapi.responses import JSONResponse, Response

# ─── Config ──────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
INTERVAL   = int(os.getenv("KEEPALIVE_INTERVAL", "290"))
ADMIN_PASS = os.getenv("KEEPALIVE_ADMIN_PASS") or os.getenv("STORAGE_SECRET", "admin")
SELF_URL   = os.getenv("APP_BASE_URL", "https://maintenanceapp-8epo.onrender.com")

# ─── Estado ──────────────────────────────────────────────────────────────────
_state = {
    "running": False,
    "task": None,
    "count": 0,
    "last": "—",
    "last_ok": True,
    "log": [],
}


def _add_log(msg: str, ok: bool = True):
    now = datetime.now().strftime("%H:%M:%S")
    _state["log"].insert(0, {"t": now, "msg": msg, "ok": ok})
    if len(_state["log"]) > 50:
        _state["log"].pop()


# ─── Telegram ────────────────────────────────────────────────────────────────
async def _telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            )
        return r.status_code == 200
    except Exception:
        return False


# ─── Self-ping ───────────────────────────────────────────────────────────────
async def _ping() -> bool:
    url = SELF_URL.rstrip("/") + "/ping"
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url)
        return r.status_code == 200
    except Exception:
        return False


# ─── Loop keep-alive ─────────────────────────────────────────────────────────
async def _loop():
    _state["running"] = True
    await _telegram(
        f"🟢 <b>Keep-Alive INICIADO</b>\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"⏱ Intervalo: {INTERVAL}s\n"
        f"🌐 {SELF_URL}"
    )
    _add_log("Keep-alive iniciado", ok=True)

    while _state["running"]:
        await asyncio.sleep(INTERVAL)
        if not _state["running"]:
            break

        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        _state["count"] += 1
        _state["last"] = now
        n = _state["count"]

        ok = await _ping()
        _state["last_ok"] = ok

        icon = "✅" if ok else "⚠️"
        msg = (
            f"{icon} <b>Keep-Alive #{n}</b>\n"
            f"🕐 {now}\n"
            f"💬 Tudo ok por aqui!\n"
            f"📡 DB ping: {'ok' if ok else 'falhou'}"
        )
        t_ok = await _telegram(msg)
        _add_log(f"#{n} ping {'ok' if ok else 'FALHOU'} | telegram {'ok' if t_ok else 'FALHOU'}", ok=(ok and t_ok))

    _state["running"] = False


def _start():
    if _state["running"]:
        return
    loop = asyncio.get_event_loop()
    _state["task"] = loop.create_task(_loop())


def _stop():
    _state["running"] = False
    if _state["task"] and not _state["task"].done():
        _state["task"].cancel()
    _add_log("Keep-alive parado", ok=False)


# ─── /health ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health_endpoint():
    return JSONResponse({
        "status": "ok",
        "keepalive": _state["running"],
        "pings": _state["count"],
        "last": _state["last"],
    })


# ─── /ka-sw.js  (Service Worker) ─────────────────────────────────────────────
_SW_JS = r"""
const CACHE = 'ka-v1';
const OFFLINE = ['/keepalive', '/ka-manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(OFFLINE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  // só intercepta GET
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request)
      .then(res => {
        // atualiza cache em background para páginas offline
        if (OFFLINE.some(u => e.request.url.includes(u))) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

// Recebe mensagem do cliente para mostrar notificação
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'NOTIFY') {
    self.registration.showNotification(e.data.title || 'Keep-Alive', {
      body: e.data.body || '',
      icon: '/assets/logo_app.png',
      badge: '/assets/logo_app.png',
    });
  }
});
"""


@app.get("/ka-sw.js")
def ka_sw():
    return Response(content=_SW_JS, media_type="application/javascript")


# ─── /ka-manifest.json ───────────────────────────────────────────────────────
@app.get("/ka-manifest.json")
def ka_manifest():
    return JSONResponse({
        "name": "Keep-Alive — Maintenance APP",
        "short_name": "KeepAlive",
        "description": "Monitor e keep-alive do servidor",
        "start_url": "/keepalive",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0a0a0f",
        "theme_color": "#7850ff",
        "icons": [
            {"src": "/assets/logo_app.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/assets/logo_app.png", "sizes": "512x512", "type": "image/png"},
        ],
        "categories": ["utilities"],
    })


# ─── Página /keepalive ────────────────────────────────────────────────────────
@ui.page("/keepalive")
async def keepalive_page():
    role    = app.storage.user.get("role", "")
    is_sys  = app.storage.user.get("authenticated") and role == "ADMIN"
    is_ka   = app.storage.user.get("ka_authed", False)

    if not is_sys and not is_ka:
        _build_login()
        return
    _build_dashboard()


# ── Login ─────────────────────────────────────────────────────────────────────
def _build_login():
    ui.add_head_html(_HEAD)
    with ui.element("div").classes("ka-root"):
        with ui.element("div").classes("ka-card"):
            ui.html('<div class="ka-logo">■ KEEP-ALIVE</div>')
            ui.html('<div class="ka-title">Acesso Admin</div>')
            ui.html('<div class="ka-sub">// autenticação necessária</div>')
            pwd = ui.input("Senha", password=True).classes("ka-input")
            err = ui.label("").classes("ka-err")

            def do_login():
                if pwd.value == ADMIN_PASS:
                    app.storage.user["ka_authed"] = True
                    ui.navigate.to("/keepalive")
                else:
                    err.text = "✗ Senha incorreta"

            ui.button("Entrar", on_click=do_login).classes("ka-btn ka-btn-start")
            pwd.on("keydown.enter", do_login)


# ── Dashboard ─────────────────────────────────────────────────────────────────
def _build_dashboard():
    ui.add_head_html(_HEAD)

    # Registra Service Worker + pede permissão de notificação
    ui.add_body_html("""
    <script>
    (async () => {
      if ('serviceWorker' in navigator) {
        try {
          await navigator.serviceWorker.register('/ka-sw.js');
        } catch(e) { console.warn('SW:', e); }
      }
      if ('Notification' in window && Notification.permission === 'default') {
        await Notification.requestPermission();
      }
    })();

    // Notificação local via SW
    async function kaNotify(title, body) {
      if (!('serviceWorker' in navigator)) return;
      const reg = await navigator.serviceWorker.ready;
      reg.active && reg.active.postMessage({ type: 'NOTIFY', title, body });
    }
    </script>
    """)

    with ui.element("div").classes("ka-root"):
        with ui.element("div").classes("ka-card"):
            ui.html('<div class="ka-logo">■ MAINTENANCE APP</div>')
            ui.html('<div class="ka-title">Keep-Alive</div>')
            ui.html(f'<div class="ka-sub">// {SELF_URL}</div>')

            status_el = ui.html(_badge())

            with ui.element("div").classes("ka-stats"):
                count_el = ui.html(_stat("Pings", str(_state["count"])))
                last_el  = ui.html(_stat("Último", _state["last"], small=True))

            # Toggle start/stop
            def toggle():
                if _state["running"]:
                    _stop()
                else:
                    _start()
                _refresh_all()

            lbl = "⏹ Parar" if _state["running"] else "▶ Iniciar"
            cls = "ka-btn-stop" if _state["running"] else "ka-btn-start"
            toggle_btn = ui.button(lbl, on_click=toggle).classes(f"ka-btn {cls}")

            # Testar Telegram
            async def test_tg():
                now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                ok = await _telegram(
                    f"🧪 <b>Teste manual</b>\n🕐 {now}\n💬 Tudo ok por aqui!\n🌐 {SELF_URL}"
                )
                _add_log(f"Teste Telegram: {'ok' if ok else 'FALHOU'}", ok=ok)
                tipo = "positive" if ok else "negative"
                txt  = "✅ Mensagem enviada!" if ok else "❌ Falhou — verifique TOKEN/CHAT_ID"
                ui.notify(txt, type=tipo)
                log_el.set_content(_render_log())

            ui.button("📨 Testar Telegram", on_click=test_tg).classes("ka-btn ka-btn-outline")

            # Instalar PWA
            ui.add_body_html("""
            <script>
            let _deferredPrompt = null;
            window.addEventListener('beforeinstallprompt', e => {
              e.preventDefault();
              _deferredPrompt = e;
              const btn = document.getElementById('ka-install-btn');
              if (btn) btn.style.display = 'block';
            });
            function kaInstall() {
              if (!_deferredPrompt) return;
              _deferredPrompt.prompt();
              _deferredPrompt.userChoice.then(() => { _deferredPrompt = null; });
            }
            </script>
            """)
            ui.html("""
            <button id="ka-install-btn" class="ka-btn ka-btn-ghost"
                    style="display:none" onclick="kaInstall()">
              📲 Instalar como App
            </button>
            """)

            # Config info
            tok_ok = "✅" if BOT_TOKEN else "❌ não configurado"
            cid_ok = "✅" if CHAT_ID else "❌ não configurado"
            ui.html(f"""
            <div class="ka-config">
              <div>BOT_TOKEN <span class="{'ok' if BOT_TOKEN else 'err'}">{tok_ok}</span></div>
              <div>CHAT_ID <span class="{'ok' if CHAT_ID else 'err'}">{cid_ok}</span></div>
              <div>INTERVALO <span class="ok">{INTERVAL}s (~{INTERVAL//60}min)</span></div>
            </div>
            """)

            ui.html('<div class="ka-log-title">// log recente</div>')
            log_el = ui.html(_render_log())

            # Auto-refresh a cada 30s
            def _refresh_all():
                status_el.set_content(_badge())
                count_el.set_content(_stat("Pings", str(_state["count"])))
                last_el.set_content(_stat("Último", _state["last"], small=True))
                toggle_btn.text = "⏹ Parar" if _state["running"] else "▶ Iniciar"
                toggle_btn.classes(remove="ka-btn-stop ka-btn-start")
                toggle_btn.classes(add="ka-btn-stop" if _state["running"] else "ka-btn-start")
                log_el.set_content(_render_log())

            ui.timer(30, _refresh_all)


# ─── Helpers de UI ───────────────────────────────────────────────────────────
def _badge() -> str:
    r = _state["running"]
    return (
        f'<div class="ka-badge">'
        f'<span class="dot{"" if r else " off"}"></span>'
        f'{"ATIVO" if r else "PARADO"}'
        f'</div>'
    )


def _stat(label: str, value: str, small: bool = False) -> str:
    cls = "ka-stat-value" + (" ka-stat-sm" if small else "")
    return (
        f'<div class="ka-stat">'
        f'<div class="ka-stat-label">{label}</div>'
        f'<div class="{cls}">{value}</div>'
        f'</div>'
    )


def _render_log() -> str:
    if not _state["log"]:
        return '<div class="ka-log">sem registros ainda</div>'
    lines = "".join(
        f'<div class="ka-log-line {"ok" if e["ok"] else "err"}">'
        f'<span class="ka-log-t">{e["t"]}</span> {e["msg"]}</div>'
        for e in _state["log"][:20]
    )
    return f'<div class="ka-log">{lines}</div>'


# ─── CSS + head ──────────────────────────────────────────────────────────────
_HEAD = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<link rel="manifest" href="/ka-manifest.json">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0a0a0f">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="KeepAlive">
<link rel="apple-touch-icon" href="/assets/logo_app.png">
<style>
  .ka-root{min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0a0a0f;padding:1.5rem;font-family:'Syne',sans-serif;}
  .ka-card{width:100%;max-width:440px;background:#13131f;border:1px solid #222238;border-radius:20px;padding:2rem;box-shadow:0 0 80px rgba(120,80,255,.10);}
  .ka-logo{font-size:.65rem;letter-spacing:.3em;text-transform:uppercase;color:#7850ff;margin-bottom:.4rem;font-family:'JetBrains Mono',monospace;}
  .ka-title{font-size:1.9rem;font-weight:800;color:#f0f0ff;margin-bottom:.15rem;}
  .ka-sub{font-size:.72rem;color:#555;font-family:'JetBrains Mono',monospace;margin-bottom:1.5rem;word-break:break-all;}
  .ka-badge{display:inline-flex;align-items:center;gap:.5rem;background:#0e0e1a;border:1px solid #1e1e35;border-radius:100px;padding:.35rem .9rem;font-size:.72rem;font-family:'JetBrains Mono',monospace;color:#aaa;margin-bottom:1.2rem;}
  .dot{width:8px;height:8px;border-radius:50%;background:#00ff88;box-shadow:0 0 8px #00ff88;animation:pulse 2s infinite;}
  .dot.off{background:#ff4444;box-shadow:0 0 8px #ff4444;animation:none;}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
  .ka-stats{display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-bottom:1.2rem;}
  .ka-stat{background:#0e0e1a;border:1px solid #1a1a30;border-radius:12px;padding:.85rem 1rem;}
  .ka-stat-label{font-size:.6rem;text-transform:uppercase;letter-spacing:.15em;color:#444;font-family:'JetBrains Mono',monospace;margin-bottom:.2rem;}
  .ka-stat-value{font-size:1.5rem;font-weight:700;color:#7850ff;font-family:'JetBrains Mono',monospace;}
  .ka-stat-sm{font-size:.8rem!important;}
  .ka-btn{width:100%;padding:.85rem;border-radius:12px;border:none;cursor:pointer;font-family:'Syne',sans-serif;font-size:.9rem;font-weight:700;transition:all .18s;margin-bottom:.65rem;display:block;}
  .ka-btn-start{background:#7850ff;color:#fff;}
  .ka-btn-start:hover{background:#9070ff;transform:translateY(-1px);}
  .ka-btn-stop{background:#2a1520;color:#ff6060;border:1px solid #3a2030!important;}
  .ka-btn-stop:hover{background:#3a2030;}
  .ka-btn-outline{background:transparent;color:#7850ff;border:1px solid #7850ff;}
  .ka-btn-outline:hover{background:#7850ff22;}
  .ka-btn-ghost{background:#0e0e1a;color:#888;border:1px solid #1e1e35;}
  .ka-btn-ghost:hover{background:#1a1a2e;color:#aaa;}
  .ka-config{background:#080810;border:1px solid #161628;border-radius:10px;padding:.85rem 1rem;font-family:'JetBrains Mono',monospace;font-size:.7rem;color:#666;margin-bottom:1rem;display:flex;flex-direction:column;gap:.3rem;}
  .ka-config .ok{color:#00cc66;}
  .ka-config .err{color:#ff4444;}
  .ka-log-title{font-size:.65rem;letter-spacing:.2em;text-transform:uppercase;color:#333;font-family:'JetBrains Mono',monospace;margin-bottom:.4rem;}
  .ka-log{background:#080810;border:1px solid #161628;border-radius:10px;padding:.85rem 1rem;font-family:'JetBrains Mono',monospace;font-size:.68rem;max-height:160px;overflow-y:auto;color:#555;}
  .ka-log-line{padding:.15rem 0;border-bottom:1px solid #0e0e1a;}
  .ka-log-line.ok{color:#00cc66;}
  .ka-log-line.err{color:#ff6060;}
  .ka-log-t{color:#333;margin-right:.5rem;}
  .ka-input .q-field__control{background:#0e0e1a!important;border-radius:10px!important;font-family:'JetBrains Mono',monospace;font-size:.85rem;}
  .ka-input{width:100%;margin-bottom:.75rem;}
  .ka-err{color:#ff6060;font-size:.75rem;font-family:'JetBrains Mono',monospace;margin-bottom:.5rem;display:block;min-height:1.1rem;}
</style>
"""


# ─── Ponto de entrada ─────────────────────────────────────────────────────────
def setup_keepalive():
    """Registra tudo e inicia o loop no startup do app."""
    @app.on_startup
    async def _on_startup():
        _start()
        print(f"🔔 Keep-Alive ativo — intervalo {INTERVAL}s → /keepalive", flush=True)
