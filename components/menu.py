from nicegui import ui, app

SIDEBAR_OPEN_KEY = 'sidebar_open'
LOADER_BOOT_KEY = '_page_loader_bootstrapped'


def _sidebar_aberta() -> bool:
    return bool(app.storage.user.get(SIDEBAR_OPEN_KEY, True))


def _set_sidebar_aberta(valor: bool) -> None:
    app.storage.user[SIDEBAR_OPEN_KEY] = bool(valor)


def _get_role() -> str:
    from auth import get_role
    return get_role()


def _can_view_logs() -> bool:
    from auth import can_view_logs
    return can_view_logs()


def _allowed_routes() -> set:
    from auth import allowed_menu_routes
    return allowed_menu_routes()


def ensure_page_loader():
    if app.storage.user.get(LOADER_BOOT_KEY):
        return

    ui.add_head_html("""
    <style>
      #fsl-page-loader {
        position: fixed; inset: 0; z-index: 99999;
        display: flex; align-items: center; justify-content: center;
        background: rgba(241,245,249,.62); backdrop-filter: blur(2px);
        opacity: 0; visibility: hidden; pointer-events: none;
        transition: opacity .12s ease, visibility .12s ease;
      }
      #fsl-page-loader.visible {
        opacity: 1; visibility: visible; pointer-events: all;
      }
      #fsl-page-loader-card {
        min-width: 200px; max-width: 320px;
        background: rgba(255,255,255,.98);
        border: 1px solid rgba(148,163,184,.24);
        border-radius: 20px;
        box-shadow: 0 18px 50px rgba(15,23,42,.18);
        padding: 24px 28px;
        display:flex; flex-direction:column; align-items:center; gap:12px;
      }
      #fsl-page-loader-spinner {
        width: 54px; height: 54px; border-radius: 9999px;
        border: 5px solid rgba(148,163,184,.35);
        border-top-color: #f59e0b;
        animation: fslSpin .8s linear infinite;
      }
      #fsl-page-loader-text {
        font: 700 13px/1.2 Arial, sans-serif; color: #334155;
        text-transform: uppercase; letter-spacing: .04em; text-align:center;
      }
      /* ===== CORRECAO GLOBAL DE LAYOUT / PWA / MOBILE ===== */
      html, body { width: 100% !important; height: 100% !important; margin: 0 !important; padding: 0 !important; overflow: hidden !important; overscroll-behavior: none !important; background: #f1f5f9 !important; }
      body { position: fixed !important; inset: 0 !important; touch-action: manipulation; }
      #app, .q-layout, .q-page-container, .q-page, .nicegui-content { width: 100vw !important; height: 100dvh !important; max-width: 100vw !important; max-height: 100dvh !important; margin: 0 !important; padding: 0 !important; overflow: hidden !important; background: #f1f5f9 !important; }
      .fsl-app-shell { width: 100vw !important; height: 100dvh !important; min-height: 100dvh !important; max-height: 100dvh !important; overflow: hidden !important; background: #f1f5f9 !important; }
      .fsl-sidebar { height: 100dvh !important; min-height: 100dvh !important; max-height: 100dvh !important; align-self: stretch !important; overflow-y: auto !important; overflow-x: hidden !important; background: #1e293b !important; -webkit-overflow-scrolling: touch; scrollbar-width: thin; }
      .fsl-sidebar::-webkit-scrollbar { width: 6px; }
      .fsl-sidebar::-webkit-scrollbar-thumb { background: rgba(255,255,255,.18); border-radius: 999px; }
      @media (orientation: landscape) and (max-height: 560px) { .fsl-sidebar { padding: 8px !important; } .fsl-sidebar .q-btn { min-height: 38px !important; padding-top: 8px !important; padding-bottom: 8px !important; } .fsl-sidebar .q-card { padding: 8px !important; } }
      @supports (-webkit-touch-callout: none) { .fsl-app-shell, .fsl-sidebar, #app, .q-layout, .q-page-container, .q-page, .nicegui-content { height: -webkit-fill-available !important; max-height: -webkit-fill-available !important; } }
      body.fsl-busy, body.fsl-busy * { cursor: progress !important; }
      @keyframes fslSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    </style>
    <script>
      window.fslEnsurePageLoader = function() {
        if (document.getElementById('fsl-page-loader')) return;
        const wrap = document.createElement('div');
        wrap.id = 'fsl-page-loader';
        wrap.className = 'visible';
        wrap.innerHTML = `
          <div id="fsl-page-loader-card">
            <div id="fsl-page-loader-spinner"></div>
            <div id="fsl-page-loader-text">CARREGANDO...</div>
          </div>`;
        document.body.appendChild(wrap);
      };
      window.fslShowPageLoader = function(texto) {
        window.fslEnsurePageLoader();
        const el = document.getElementById('fsl-page-loader');
        const tx = document.getElementById('fsl-page-loader-text');
        if (tx) tx.textContent = (texto || 'CARREGANDO...').toUpperCase();
        if (el) {
          el.classList.add('visible');
          document.body.classList.add('fsl-busy');
        }
      };
      window.fslHidePageLoader = function() {
        const el = document.getElementById('fsl-page-loader');
        if (el) el.classList.remove('visible');
        document.body.classList.remove('fsl-busy');
      };
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', window.fslEnsurePageLoader, {once: true});
      } else {
        window.fslEnsurePageLoader();
      }
    
      window.fslPatchButtonsOnce = function(){
        if (window.__fslBtnPatch) return; window.__fslBtnPatch = true;
        document.addEventListener('click', function(ev){
          const btn = ev.target.closest('.q-btn');
          if (!btn) return;
          if (btn.dataset.fslBusyClick === '1') { ev.preventDefault(); ev.stopPropagation(); return; }
          btn.dataset.fslBusyClick = '1';
          setTimeout(function(){ if(btn) btn.dataset.fslBusyClick='0'; }, 650);
        }, true);
      };
      window.fslPatchButtonsOnce();
    </script>
    <style id="fsl-no-double-click-patch">
      .nicegui-content > div:first-child { width:100vw !important; height:100dvh !important; overflow:hidden !important; }
      .q-page { min-height:100dvh !important; overflow:hidden !important; }
      .q-dialog__inner { overflow:auto !important; }
    </style>
    """)

    app.storage.user[LOADER_BOOT_KEY] = True


def show_page_loader(texto: str = 'CARREGANDO...'):
    ensure_page_loader()
    try:
        import json
        ui.run_javascript(f"window.fslShowPageLoader({json.dumps(str(texto or 'CARREGANDO...'))});")
    except Exception:
        pass


def hide_page_loader():
    try:
        ui.run_javascript("window.fslHidePageLoader();")
    except Exception:
        pass


def loader_props(texto: str = 'CARREGANDO...') -> str:
    texto_js = str(texto or 'CARREGANDO...').replace('\\', '\\\\').replace("'", "\\'")
    return (
        f'onmousedown="window.fslShowPageLoader(\'{texto_js}\')" '
        f'ontouchstart="window.fslShowPageLoader(\'{texto_js}\')" '
        f'onclick="window.fslShowPageLoader(\'{texto_js}\')"'
    )


# ── rótulo amigável do perfil ────────────────────────────────────────────────
_ROLE_LABEL = {
    'ADMIN': 'ADMINISTRADOR',
    'GESTOR': 'GESTOR',
    'PLANEJADOR': 'PLANEJADOR',
    'EXECUTOR': 'EXECUTOR',
    'VISUALIZACAO': 'VISUALIZAÇÃO',
}


def build_menu(current_route: str | None = None):
    ensure_page_loader()

    role = _get_role()
    allowed = _allowed_routes()

    # Todos os itens possíveis — filtrados por perfil
    todos_itens = [
        ('home',                   'Home',                    '/home'),
        ('account_tree',           'Árvore de Equipamentos',  '/arvore'),
        ('precision_manufacturing','Equipamentos',             '/equipamentos'),
        ('assignment',             'OS',                      '/os'),
        ('groups',                 'Equipes',                 '/equipes'),
        ('badge',                  'Funcionários',            '/funcionarios'),
        ('manage_accounts',        'Usuários',                '/usuarios'),
        ('database',               'Gestão de Dados',        '/gestao-dados'),
        ('bar_chart',              'Dashboard',               '/dashboard'),
    ]
    if _can_view_logs():
        todos_itens.append(('history', 'Log de Ações', '/logs'))

    itens = [(ic, tt, rt) for ic, tt, rt in todos_itens if rt in allowed]

    nome_usuario = str(app.storage.user.get('name', 'USUÁRIO') or 'USUÁRIO').upper()
    modo_label = _ROLE_LABEL.get(role, role)

    sidebar = ui.column().classes(
        'fsl-sidebar h-full bg-slate-800 text-white p-3 gap-2 shrink-0 shadow-lg transition-all duration-300'
    )

    def ir_para(rota, titulo):
        if rota:
            show_page_loader(f'ABRINDO {titulo}...')
            ui.timer(0.05, lambda: ui.navigate.to(rota), once=True)
        else:
            ui.notify(f'{titulo} em breve', type='warning')

    def do_logout():
        from auth import logout as auth_logout
        auth_logout()

    def toggle_sidebar():
        _set_sidebar_aberta(not _sidebar_aberta())
        render_menu()

    def estilo_largura():
        largura = '260px' if _sidebar_aberta() else '70px'
        return f'width: {largura}; border-right: 1px solid rgba(255,255,255,0.08);'

    def classes_botao_menu(ativo: bool) -> str:
        base = 'w-full justify-start rounded-xl px-3 py-3 '
        if ativo:
            return base + 'bg-amber-500 text-black font-bold shadow-sm'
        return base + 'hover:bg-orange-500/20 text-white'

    def classes_botao_logout() -> str:
        return 'w-full justify-start rounded-xl px-3 py-3 hover:bg-red-500/20 text-red-300'

    # cor do badge de perfil
    _ROLE_COLOR = {
        'ADMIN': 'text-red-300',
        'GESTOR': 'text-amber-300',
        'PLANEJADOR': 'text-emerald-300',
        'EXECUTOR': 'text-sky-300',
        'VISUALIZACAO': 'text-slate-300',
    }
    badge_color = _ROLE_COLOR.get(role, 'text-amber-300')

    def render_menu():
        sidebar.style(estilo_largura())
        sidebar.clear()
        aberta = _sidebar_aberta()

        with sidebar:
            with ui.row().classes('w-full items-center justify-between mb-1'):
                with ui.row().classes('items-center gap-2 min-w-0'):
                    ui.image('/assets/logo_fsl.png').classes('w-8 h-8 object-contain shrink-0')
                    if aberta:
                        ui.label('MAINTENANCE APP').classes('text-lg font-bold text-orange-400 truncate')
                ui.button(icon='menu_open' if aberta else 'menu').props('flat round dense color=white').on('click', toggle_sidebar)

            if aberta:
                with ui.card().classes('w-full shadow-none border border-white/10 bg-white/5 rounded-xl p-3 gap-1 mb-2'):
                    ui.label(nome_usuario).classes('text-sm font-bold text-white')
                    ui.label(f'PERFIL: {modo_label}').classes(f'text-[11px] {badge_color} font-medium')
                ui.label('Painel principal').classes('text-xs text-slate-400 mb-1')

            for icone, titulo, rota in itens:
                ativo = bool(rota and rota == current_route)
                with ui.button(on_click=lambda e=None, r=rota, t=titulo: ir_para(r, t)).props('flat no-caps align=left').classes(classes_botao_menu(ativo)):
                    with ui.row().classes('items-center w-full no-wrap'):
                        ui.icon(icone).classes('text-[20px] shrink-0')
                        if aberta:
                            ui.label(titulo).classes('ml-3 text-sm truncate')

            ui.separator().classes('my-2 bg-white/10')
            with ui.button(on_click=do_logout).props('flat no-caps align=left').classes(classes_botao_logout()):
                with ui.row().classes('items-center w-full no-wrap'):
                    ui.icon('logout').classes('text-[20px]')
                    if aberta:
                        ui.label('Logout').classes('ml-3 text-sm')

    render_menu()
