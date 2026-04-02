from nicegui import ui, app

SIDEBAR_OPEN_KEY = 'sidebar_open'


def _sidebar_aberta() -> bool:
    return bool(app.storage.user.get(SIDEBAR_OPEN_KEY, True))


def _set_sidebar_aberta(valor: bool) -> None:
    app.storage.user[SIDEBAR_OPEN_KEY] = bool(valor)


def _is_read_only() -> bool:
    role = str(app.storage.user.get('role', 'VISUALIZACAO') or 'VISUALIZACAO').upper()
    return role == 'VISUALIZACAO'


def ensure_page_loader():
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
      body.fsl-busy, body.fsl-busy * { cursor: progress !important; }
      @keyframes fslSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
    </style>
    <script>
      window.fslEnsurePageLoader = function() {
        if (document.getElementById('fsl-page-loader')) return;
        const wrap = document.createElement('div');
        wrap.id = 'fsl-page-loader';
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
      window.addEventListener('load', function() {
        if (window.fslHidePageLoader) window.fslHidePageLoader();
      });
      window.addEventListener('pageshow', function() {
        if (window.fslHidePageLoader) window.fslHidePageLoader();
      });
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
          window.fslEnsurePageLoader();
          window.fslHidePageLoader();
        }, {once: true});
      } else {
        window.fslEnsurePageLoader();
        window.fslHidePageLoader();
      }
    </script>
    """)


def show_page_loader(texto: str = 'CARREGANDO...'):
    ensure_page_loader()
    try:
        ui.run_javascript(f"window.fslShowPageLoader({texto!r});")
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
    )


def build_menu(current_route: str | None = None):
    ensure_page_loader()

    itens = [
        ('home', 'Home', '/home'),
        ('account_tree', 'Árvore de Equipamentos', '/arvore'),
        ('precision_manufacturing', 'Equipamentos', '/equipamentos'),
        ('assignment', 'OS', '/os'),
        ('groups', 'Equipes', '/equipes'),
        ('badge', 'Funcionários', '/funcionarios'),
        ('manage_accounts', 'Usuários', '/usuarios'),
        ('bar_chart', 'Dashboard', '/dashboard'),
    ]

    nome_usuario = str(app.storage.user.get('name', 'USUÁRIO') or 'USUÁRIO').upper()
    modo_usuario = 'VISUALIZAÇÃO' if _is_read_only() else 'COMPLETO'

    sidebar = ui.column().classes(
        'h-full bg-slate-800 text-white p-3 gap-2 shrink-0 shadow-lg transition-all duration-300'
    )

    def ir_para(rota, titulo):
        if rota:
            show_page_loader(f'ABRINDO {titulo}...')
            ui.navigate.to(rota)
        else:
            ui.notify(f'{titulo} em breve', type='warning')

    def logout():
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
                    ui.label(f'MODO: {modo_usuario}').classes('text-[11px] text-amber-300 font-medium')
                ui.label('Painel principal').classes('text-xs text-slate-400 mb-1')

            for icone, titulo, rota in itens:
                ativo = bool(rota and rota == current_route)
                with ui.button(on_click=lambda e=None, r=rota, t=titulo: ir_para(r, t)).props("flat no-caps align=left").classes(classes_botao_menu(ativo)):
                    with ui.row().classes('items-center w-full no-wrap'):
                        ui.icon(icone).classes('text-[20px] shrink-0')
                        if aberta:
                            ui.label(titulo).classes('ml-3 text-sm truncate')

            ui.separator().classes('my-2 bg-white/10')
            with ui.button(on_click=logout).props("flat no-caps align=left").classes(classes_botao_logout()):
                with ui.row().classes('items-center w-full no-wrap'):
                    ui.icon('logout').classes('text-[20px]')
                    if aberta:
                        ui.label('Logout').classes('ml-3 text-sm')

    render_menu()
