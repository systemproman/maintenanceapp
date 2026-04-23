from nicegui import ui, app
from services import db

# ─── Perfis suportados ──────────────────────────────────────────────────────
# ADMIN     → acesso total (gerencia, cria, edita, exclui, encerra OS, vê logs)
# GERENCIA  → visualiza tudo + pode criar/editar (mas não excluir nem encerrar)
# TECNICO   → vê árvore e aponta OS; não encerra OS, não acessa cadastros
# COMPLETO  → legado — mapeado para GERENCIA
# VISUALIZACAO → legado — mapeado para TECNICO
# ─────────────────────────────────────────────────────────────────────────────

PERFIS = ('ADMIN', 'GERENCIA', 'TECNICO', 'COMPLETO', 'VISUALIZACAO')

_MAP = {
    'COMPLETO':    'GERENCIA',
    'VISUALIZACAO': 'TECNICO',
}


def _normalize_role(role: str) -> str:
    r = str(role or 'TECNICO').upper().strip()
    return _MAP.get(r, r) if r not in ('ADMIN', 'GERENCIA', 'TECNICO') else r


def validate_user(username: str, password: str):
    return db.autenticar_usuario(username, password)


def save_session(username: str, user_data: dict):
    raw_role = str(user_data.get('nivel_acesso', 'TECNICO') or 'TECNICO').upper()
    # admin username always → ADMIN role
    if str(username or '').strip().lower() == 'admin':
        raw_role = 'ADMIN'
    role = _normalize_role(raw_role)

    app.storage.user['authenticated'] = True
    app.storage.user['username'] = str(user_data.get('username', username) or username).upper()
    app.storage.user['name'] = (
        user_data.get('nome_exibicao')
        or user_data.get('nome_funcionario')
        or user_data.get('nome')
        or str(user_data.get('username', username) or username).upper()
    )
    app.storage.user['role'] = role
    app.storage.user['usuario_id'] = user_data.get('id')
    app.storage.user['funcionario_id'] = user_data.get('funcionario_id')
    app.storage.user['must_change_password'] = bool(user_data.get('deve_trocar_senha', 0))
    app.storage.user['can_view_logs'] = bool(user_data.get('pode_ver_logs', 0))
    app.storage.user['email'] = user_data.get('email')


def require_auth() -> bool:
    return bool(app.storage.user.get('authenticated', False))


def needs_password_change() -> bool:
    return bool(app.storage.user.get('must_change_password', False))


def get_role() -> str:
    raw = str(app.storage.user.get('role', 'TECNICO') or 'TECNICO').upper()
    return _normalize_role(raw)


# ─── helpers de permissão ────────────────────────────────────────────────────

def is_admin() -> bool:
    return get_role() == 'ADMIN'


def is_gerencia() -> bool:
    return get_role() in ('ADMIN', 'GERENCIA')


def is_tecnico() -> bool:
    """Técnico puro — não é admin nem gerencia."""
    return get_role() == 'TECNICO'


# compatibilidade com código antigo
def get_access_level() -> str:
    return get_role()


def is_read_only() -> bool:
    """Técnico só lê / aponta. Gerencia e Admin podem editar."""
    return is_tecnico()


def can_edit() -> bool:
    return not is_tecnico()


def can_delete() -> bool:
    """Somente Admin pode excluir registros."""
    return is_admin()


def can_close_os() -> bool:
    """Somente Admin pode encerrar OS."""
    return is_admin()


def can_manage_users() -> bool:
    """Somente Admin gerencia usuários."""
    return is_admin()


def can_view_logs() -> bool:
    return is_admin() or bool(app.storage.user.get('can_view_logs', False))


# ─── menus visíveis por perfil ───────────────────────────────────────────────
# Retorna lista de rotas que o perfil atual pode acessar.
def allowed_menu_routes() -> set:
    role = get_role()
    base = {'/home', '/arvore', '/os'}
    if role in ('ADMIN', 'GERENCIA'):
        base |= {'/equipamentos', '/equipes', '/funcionarios', '/dashboard'}
    if role == 'ADMIN':
        base |= {'/usuarios', '/logs'}
    return base


# ─── logout ──────────────────────────────────────────────────────────────────
def logout():
    try:
        db.registrar_logout_usuario(app.storage.user.get('usuario_id'))
    except Exception:
        pass
    app.storage.user.clear()
    ui.navigate.to('/')


# ─── estilos do login ────────────────────────────────────────────────────────
def _styles():
    ui.add_head_html("""
    <style>
        :root {
            --panel-bg: rgba(40, 167, 69, 0.42);
            --panel-bg-2: rgba(40, 167, 69, 0.24);
            --panel-border: rgba(255, 210, 140, 0.42);
            --panel-shadow: rgba(0, 0, 0, 0.24);
            --brand-color: #0d2140;
            --subtitle-color: #20324d;
            --field-bg: rgba(255, 255, 255, 0.96);
            --field-border: rgba(13, 33, 64, 0.14);
            --field-text: #101828;
            --field-caret: #0d2140;
            --button-bg-1: #0b3b60;
            --button-bg-2: #072a44;
            --button-text: #ffffff;
            --button-shadow: rgba(7, 42, 68, 0.32);
            --error-text: #b42318;
            --error-bg: rgba(255, 59, 48, 0.10);
            --error-border: rgba(180, 35, 24, 0.20);
        }
        html, body { margin:0; padding:0; width:100%; height:100%; overflow:hidden; font-family:'Segoe UI', Arial, sans-serif; background:url('/assets/fundo_fsl.png') center center / cover no-repeat fixed; }
        body, .nicegui-content, .q-page, .q-layout, .q-page-container { background: transparent !important; }
        .login-root { width:100vw; height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; box-sizing:border-box; }
        .login-panel { width:100%; max-width:430px; padding:30px; border-radius:30px; background:linear-gradient(180deg, var(--panel-bg), var(--panel-bg-2)); backdrop-filter: blur(18px); border:1px solid var(--panel-border); box-shadow:0 20px 55px var(--panel-shadow); }
        .brand-box { width:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; margin-bottom:18px; text-align:center; }
        .brand-logo { width:165px; height:122px; object-fit:contain; display:block; margin:0 auto 10px auto; }
        .brand-title { width:100%; text-align:center; font-size:30px; font-weight:900; color:var(--brand-color); line-height:1.05; }
        .subtitle-box { width:100%; text-align:center; font-size:18px; font-weight:800; color:var(--subtitle-color); margin:2px 0 18px 0; }
        .nice-field { width:100%; margin-bottom:14px; }
        .nice-field .q-field__control { min-height:58px !important; border-radius:18px !important; background:var(--field-bg) !important; border:1px solid var(--field-border) !important; }
        .nice-field .q-field__native, .nice-field input { color:var(--field-text) !important; font-size:15px !important; font-weight:600 !important; caret-color:var(--field-caret) !important; }
        .nice-field input:-webkit-autofill, .nice-field input:-webkit-autofill:hover, .nice-field input:-webkit-autofill:focus, .nice-field input:-webkit-autofill:active {
            -webkit-text-fill-color: var(--field-text) !important; caret-color: var(--field-caret) !important;
            -webkit-box-shadow: 0 0 0 1000px var(--field-bg) inset !important; box-shadow: 0 0 0 1000px var(--field-bg) inset !important;
            border-radius: 18px !important; transition: background-color 999999s ease-in-out 0s !important;
        }
        .nice-field .q-field__control:before, .nice-field .q-field__control:after { display:none !important; }
        .error-box { width:100%; box-sizing:border-box; border-radius:14px; padding:10px 12px; text-align:center; font-size:13px; font-weight:800; color:var(--error-text); background:var(--error-bg); border:1px solid var(--error-border); }
        .q-btn.login-button { width:100% !important; min-height:54px !important; border-radius:20px !important; background:linear-gradient(135deg, var(--button-bg-1), var(--button-bg-2)) !important; color:var(--button-text) !important; font-size:17px !important; font-weight:900 !important; box-shadow:0 10px 24px var(--button-shadow) !important; }
    </style>
    """)


def build_login_page():
    _styles()
    with ui.element('div').classes('login-root'):
        with ui.element('div').classes('login-panel'):
            with ui.element('div').classes('brand-box'):
                ui.image('/assets/logo_app.png').classes('brand-logo')
                ui.label('MAINTENANCE APP').classes('brand-title')
            ui.label('Acesso ao sistema').classes('subtitle-box')
            username = ui.input(placeholder='Usuário').classes('w-full nice-field').props('outlined autocomplete=off autocorrect=off autocapitalize=off spellcheck=false')
            password = ui.input(placeholder='Senha', password=True, password_toggle_button=True).classes('w-full nice-field').props('outlined autocomplete=off autocorrect=off autocapitalize=off spellcheck=false')
            error_container = ui.column().classes('w-full')

            def show_error(message: str):
                error_container.clear()
                with error_container:
                    ui.html(f'<div class="error-box">{message}</div>')

            def clear_error():
                error_container.clear()

            def login():
                clear_error()
                ok, msg, user = validate_user(username.value, password.value)
                if not ok:
                    show_error(msg)
                    return
                save_session(username.value, user)
                try:
                    db.registrar_login_usuario(user.get('id'))
                except Exception:
                    pass
                if bool(user.get('deve_trocar_senha', 0)):
                    ui.navigate.to('/trocar-senha')
                else:
                    ui.navigate.to('/home')

            ui.button('ENTRAR', on_click=login).classes('login-button')
            username.on('focus', lambda e: clear_error())
            password.on('focus', lambda e: clear_error())
            username.on('keydown.enter', lambda e: password.run_method('focus'))
            password.on('keydown.enter', lambda e: login())


def _render_password_form(title: str, subtitle: str, on_save, success_redirect: str = '/home'):
    _styles()
    with ui.element('div').classes('login-root'):
        with ui.element('div').classes('login-panel'):
            with ui.element('div').classes('brand-box'):
                ui.image('/assets/logo_app.png').classes('brand-logo')
                ui.label(title).classes('brand-title')
            ui.label(subtitle).classes('subtitle-box')
            nova = ui.input(placeholder='Nova senha', password=True, password_toggle_button=True).classes('w-full nice-field').props('outlined autocomplete=off autocorrect=off autocapitalize=off spellcheck=false')
            conf = ui.input(placeholder='Confirmar senha', password=True, password_toggle_button=True).classes('w-full nice-field').props('outlined autocomplete=off autocorrect=off autocapitalize=off spellcheck=false')
            error_container = ui.column().classes('w-full')

            def show_error(message: str):
                error_container.clear()
                with error_container:
                    ui.html(f'<div class="error-box">{message}</div>')

            def salvar():
                senha1 = str(nova.value or '').strip()
                senha2 = str(conf.value or '').strip()
                if len(senha1) < 4:
                    show_error('Informe uma senha com pelo menos 4 caracteres.')
                    return
                if senha1 != senha2:
                    show_error('As senhas não conferem.')
                    return
                try:
                    on_save(senha1)
                    ui.notify('Senha alterada com sucesso.', type='positive')
                    ui.navigate.to(success_redirect)
                except Exception as ex:
                    show_error(str(ex))

            ui.button('SALVAR', on_click=salvar).classes('login-button')
            nova.on('keydown.enter', lambda e: conf.run_method('focus'))
            conf.on('keydown.enter', lambda e: salvar())


def build_change_password_page():
    def _save_password(nova_senha: str):
        usuario_id = app.storage.user.get('usuario_id')
        if not usuario_id:
            raise ValueError('Sessão inválida. Faça login novamente.')
        db.alterar_senha_usuario(usuario_id, nova_senha, False)
        app.storage.user['must_change_password'] = False

    _render_password_form('TROCAR SENHA', 'Primeiro acesso', _save_password, '/home')


def build_reset_password_page(token: str):
    token = str(token or '').strip()
    info = db.validar_token_redefinicao(token)
    if not info:
        _styles()
        with ui.element('div').classes('login-root'):
            with ui.element('div').classes('login-panel'):
                with ui.element('div').classes('brand-box'):
                    ui.image('/assets/logo_app.png').classes('brand-logo')
                    ui.label('LINK INVÁLIDO').classes('brand-title')
                ui.label('O link expirou ou já foi utilizado.').classes('subtitle-box')
                ui.button('VOLTAR AO LOGIN', on_click=lambda: ui.navigate.to('/')).classes('login-button')
        return

    nome = info.get('nome_exibicao') or info.get('username') or 'Usuário'

    def _save_password(nova_senha: str):
        db.consumir_token_redefinicao(token, nova_senha)

    _render_password_form('DEFINIR SENHA', nome, _save_password, '/')
