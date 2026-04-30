from nicegui import ui
from components.menu import build_menu, hide_page_loader
from auth import can_manage_users, can_manage_permissions, refresh_permissions, is_admin
from services import db

CAMPOS_PERMISSAO = [
    ('ver_menu',            'Ver menu'),
    ('abrir_tela',          'Abrir tela'),
    ('criar',               'Criar'),
    ('editar',              'Editar'),
    ('excluir',             'Excluir'),
    ('exportar',            'Exportar'),
    ('aprovar_liberar',     'Aprovar/Liberar'),
    ('ver_logs',            'Ver logs'),
    ('gerenciar_usuarios',  'Gerenciar usuários'),
    ('gerenciar_permissoes','Gerenciar permissões'),
]

MODULO_LABEL = {
    'HOME':         'Home',
    'ARVORE':       'Árvore de Equipamentos',
    'EQUIPAMENTOS': 'Equipamentos',
    'OS':           'Ordens de Serviço',
    'EQUIPES':      'Equipes',
    'FUNCIONARIOS': 'Funcionários',
    'USUARIOS':     'Usuários',
    'DASHBOARD':    'Dashboard',
    'LOGS':         'Log de Ações',
    'GESTAO_DADOS': 'Gestão de Dados',
}


def usuarios_page():
    if not can_manage_users():
        ui.notify('Acesso restrito ao Administrador.', type='negative')
        ui.navigate.to('/home')
        return

    with ui.row().classes('fsl-app-shell w-full h-screen no-wrap bg-slate-100'):
        build_menu('/usuarios')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4'):
                with ui.row().classes('w-full items-center justify-between'):
                    with ui.column().classes('gap-0'):
                        ui.label('USUÁRIOS').classes('text-xl font-bold text-slate-800')
                        ui.label('Perfis, permissões e cadastro de usuários.').classes('text-xs text-slate-500')
                    ui.button('NOVO USUÁRIO', icon='add', on_click=lambda: form_usuario()).props('unelevated').classes('bg-amber-500 text-black font-bold')
            area = ui.column().classes('w-full gap-3 overflow-auto')

    def render():
        area.clear()
        with area:
            if can_manage_permissions():
                render_perfis()
            render_usuarios()

    # ─── PERFIS ──────────────────────────────────────────────────────────────
    def render_perfis():
        perfis = db.listar_perfis_acesso()
        with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-3'):
            with ui.row().classes('w-full items-center justify-between mb-1'):
                with ui.column().classes('gap-0'):
                    ui.label('PERFIS E PERMISSÕES').classes('text-lg font-bold text-slate-800')
                    ui.label('Configure as permissões de cada perfil e clique em SALVAR PERFIL para gravar tudo.').classes('text-xs text-slate-500')
                if is_admin():
                    ui.button('NOVO PERFIL', icon='add', on_click=novo_perfil_dialog).props('outline').classes('text-slate-700')

            for perfil in perfis:
                nome = str(perfil.get('nome') or '').upper()
                eh_admin = nome == 'ADMIN'

                with ui.expansion().classes('w-full rounded-xl border border-slate-200 overflow-hidden mb-1') as exp:
                    with exp.add_slot('header'):
                        with ui.row().classes(f'w-full items-center justify-between px-4 py-3 {"bg-red-50" if eh_admin else "bg-slate-50"}'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('shield' if eh_admin else 'manage_accounts').classes(
                                    'text-red-500' if eh_admin else 'text-amber-500'
                                )
                                ui.label(nome).classes('text-sm font-bold text-slate-800')
                                if eh_admin:
                                    ui.html('<span class="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700 font-bold">FIXO</span>')
                                else:
                                    desc = str(perfil.get('descricao') or '')
                                    if desc:
                                        ui.label(f'— {desc}').classes('text-xs text-slate-400')
                            if not eh_admin and is_admin():
                                with ui.row().classes('gap-1').on('click', lambda e: e.stop()):
                                    ui.button(icon='edit', on_click=lambda e=None, n=nome: editar_perfil_dialog(n)).props('flat round dense').tooltip('Renomear / editar descrição')
                                    ui.button(icon='delete', on_click=lambda e=None, n=nome: confirmar_excluir_perfil(n)).props('flat round dense color=negative').tooltip('Excluir perfil')

                    _render_matriz_permissoes(nome, eh_admin)

    def _render_matriz_permissoes(perfil_nome: str, somente_leitura: bool):
        permissoes_atuais = db.listar_permissoes_perfil(perfil_nome)
        perm_por_modulo: dict[str, dict] = {
            str(p.get('modulo') or '').upper(): p for p in permissoes_atuais
        }
        # controles[modulo][campo] = switch_widget — dict criado aqui para closure correta
        controles: dict[str, dict[str, ui.switch]] = {}

        with ui.column().classes('w-full p-3 gap-2'):
            for modulo in db.MODULOS_SISTEMA:
                vals = perm_por_modulo.get(modulo, {})
                controles[modulo] = {}

                with ui.card().classes('w-full rounded-xl shadow-none border border-slate-100 p-3 gap-1'):
                    with ui.row().classes('items-center justify-between'):
                        ui.label(MODULO_LABEL.get(modulo, modulo)).classes('text-sm font-bold text-slate-700')
                        if not somente_leitura and is_admin():
                            with ui.row().classes('gap-1'):
                                def _sel(m=modulo):
                                    for sw in controles[m].values(): sw.value = True
                                def _clr(m=modulo):
                                    for sw in controles[m].values(): sw.value = False
                                ui.button('Todos', on_click=_sel).props('flat dense no-caps').classes('text-xs text-amber-600')
                                ui.button('Nenhum', on_click=_clr).props('flat dense no-caps').classes('text-xs text-slate-400')

                    with ui.row().classes('w-full gap-x-5 gap-y-1 flex-wrap pt-1'):
                        for campo, lbl in CAMPOS_PERMISSAO:
                            sw = ui.switch(lbl, value=bool(vals.get(campo, 0))).classes('text-xs')
                            if somente_leitura or not is_admin():
                                sw.disable()
                            controles[modulo][campo] = sw

            # ── ÚNICO botão SALVAR por perfil ─────────────────────────────
            if not somente_leitura and is_admin():
                def _salvar(pn=perfil_nome, ctrl=controles):
                    payload = {
                        mod: {c: bool(ctrl[mod][c].value) for c, _ in CAMPOS_PERMISSAO}
                        for mod in db.MODULOS_SISTEMA
                    }
                    try:
                        db.salvar_permissoes_perfil_completo(pn, payload)
                        refresh_permissions()
                        ui.notify(f'Permissões do perfil {pn} salvas com sucesso.', type='positive')
                    except Exception as ex:
                        ui.notify(str(ex), type='negative', multi_line=True)

                with ui.row().classes('w-full justify-end pt-2'):
                    ui.button('SALVAR PERFIL', icon='save', on_click=_salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')

    # ── diálogos criar / editar / excluir perfil ──────────────────────────────
    def novo_perfil_dialog():
        with ui.dialog() as dialog, ui.card().classes('w-[480px] max-w-[96vw] p-5 gap-3 rounded-2xl'):
            ui.label('NOVO PERFIL').classes('text-lg font-bold')
            nome_inp = ui.input('Nome do perfil').props('outlined dense').classes('w-full')
            desc_inp = ui.input('Descrição (opcional)').props('outlined dense').classes('w-full')

            def salvar():
                try:
                    db.criar_perfil_acesso(nome_inp.value, desc_inp.value or '')
                    ui.notify('Perfil criado. Configure as permissões abaixo.', type='positive')
                    dialog.close()
                    render()
                except Exception as ex:
                    ui.notify(str(ex), type='negative', multi_line=True)

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('CRIAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def editar_perfil_dialog(nome_atual: str):
        info = next((p for p in db.listar_perfis_acesso() if str(p.get('nome') or '').upper() == nome_atual), {})
        with ui.dialog() as dialog, ui.card().classes('w-[480px] max-w-[96vw] p-5 gap-3 rounded-2xl'):
            ui.label('EDITAR PERFIL').classes('text-lg font-bold')
            nome_inp = ui.input('Nome do perfil', value=info.get('nome', nome_atual)).props('outlined dense').classes('w-full')
            desc_inp = ui.input('Descrição', value=info.get('descricao', '') or '').props('outlined dense').classes('w-full')

            def salvar():
                try:
                    db.renomear_perfil_acesso(nome_atual, nome_inp.value, desc_inp.value or '')
                    ui.notify('Perfil atualizado.', type='positive')
                    dialog.close()
                    render()
                except Exception as ex:
                    ui.notify(str(ex), type='negative', multi_line=True)

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def confirmar_excluir_perfil(nome: str):
        with ui.dialog() as dialog, ui.card().classes('p-5 gap-3 rounded-2xl'):
            ui.label(f'Excluir o perfil "{nome}"?').classes('text-lg font-bold')
            ui.label('Usuários vinculados precisam ter o perfil alterado antes.').classes('text-sm text-slate-500')

            def confirmar():
                try:
                    db.excluir_perfil_acesso(nome)
                    ui.notify(f'Perfil "{nome}" excluído.', type='positive')
                    dialog.close()
                    render()
                except Exception as ex:
                    ui.notify(str(ex), type='negative', multi_line=True)

            with ui.row().classes('justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=confirmar).props('unelevated').classes('bg-red-600 text-white')
        dialog.open()

    # ─── USUÁRIOS ─────────────────────────────────────────────────────────────
    def render_usuarios():
        for u in db.listar_usuarios():
            with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4'):
                with ui.row().classes('w-full items-center justify-between gap-3'):
                    with ui.column().classes('gap-0'):
                        ui.label(u['username']).classes('text-base font-bold text-slate-800')
                        ui.label(f"NOME: {u.get('nome_exibicao') or '-'} | E-MAIL: {u.get('email') or '-'}").classes('text-xs text-slate-500')
                        ui.label(f"TIPO: {'FUNCIONÁRIO' if u.get('funcionario_id') else 'EXTERNO'} | FUNCIONÁRIO: {u.get('nome_funcionario') or '-'} | EQUIPE: {u.get('equipe_nome') or '-'}").classes('text-xs text-slate-500')
                        ui.label(f"PERFIL: {u.get('nivel_acesso') or '-'} | VER LOGS: {'SIM' if bool(u.get('pode_ver_logs', 0)) else 'NÃO'}").classes('text-xs text-slate-600')
                        ui.label(f"ATIVO: {'SIM' if bool(u.get('ativo', 1)) else 'NÃO'} | TROCAR SENHA: {'SIM' if bool(u.get('deve_trocar_senha', 0)) else 'NÃO'}").classes('text-xs text-slate-600')
                    with ui.row().classes('gap-1'):
                        ui.button(icon='edit', on_click=lambda e=None, item=u: form_usuario(item)).props('flat round dense')
                        ui.button(icon='mail', on_click=lambda e=None, item=u: reenviar_credenciais(item)).props('flat round dense').tooltip('ENVIAR LINK DE REDEFINIÇÃO')
                        if u['username'] != 'admin':
                            ui.button(icon='delete', on_click=lambda e=None, item=u: excluir_usuario(item)).props('flat round dense color=negative')

    def form_usuario(item=None):
        funcionarios_rows = db.listar_funcionarios(apenas_ativos=True)
        funcionarios = {f['id']: f['nome'] for f in funcionarios_rows}
        perfis = {p['nome']: p['nome'] for p in db.listar_perfis_acesso()}

        with ui.dialog() as dialog, ui.card().classes('w-[760px] max-w-[96vw] p-5 gap-3 rounded-2xl'):
            ui.label('EDITAR USUÁRIO' if item else 'NOVO USUÁRIO').classes('text-lg font-bold')
            tipo = ui.radio(
                {'funcionario': 'Funcionário', 'externo': 'Externo'},
                value='funcionario' if (item and item.get('funcionario_id')) else 'externo',
            ).props('inline')
            funcionario = ui.select(funcionarios, label='FUNCIONÁRIO').props('outlined dense clearable').classes('w-full')
            nome = ui.input('NOME').props('outlined dense').classes('w-full')
            email = ui.input('E-MAIL').props('outlined dense').classes('w-full')
            username_inp = ui.input('USUÁRIO').props('outlined dense').classes('w-full')
            nivel = ui.select(perfis, label='PERFIL').props('outlined dense').classes('w-full')
            ativo = ui.switch('ATIVO', value=bool(item.get('ativo', 1)) if item else True)
            pode_ver_logs = ui.switch('PODE VER LOGS', value=bool(item.get('pode_ver_logs', 0)) if item else False)
            if not is_admin():
                pode_ver_logs.disable()
                pode_ver_logs.tooltip('Somente o admin pode liberar acesso ao log.')
            ui.label('Ao salvar, o sistema envia por e-mail um link seguro para definição ou redefinição de senha.').classes('text-xs text-slate-500')

            if item:
                funcionario.value = item.get('funcionario_id')
                nome.value = item.get('nome_exibicao') or item.get('nome') or item.get('nome_funcionario') or ''
                email.value = item.get('email') or ''
                username_inp.value = item.get('username') or ''
                nivel.value = str(item.get('nivel_acesso') or 'VISUALIZACAO').upper()
                tipo.value = 'funcionario' if item.get('funcionario_id') else 'externo'
            else:
                nivel.value = 'VISUALIZACAO'

            def sync_campos():
                eh_func = tipo.value == 'funcionario'
                funcionario.set_visibility(eh_func)
                if eh_func and funcionario.value:
                    try:
                        dados = db.get_funcionario(funcionario.value) or {}
                        if not item or not nome.value:
                            nome.value = dados.get('nome') or nome.value
                        if not item and not username_inp.value:
                            username_inp.value = db.sugerir_username_funcionario(funcionario.value)
                    except Exception:
                        pass
                if not eh_func and funcionario.value:
                    funcionario.value = None

            tipo.on('update:model-value', lambda e: sync_campos())
            funcionario.on('update:model-value', lambda e: sync_campos())
            sync_campos()

            def salvar():
                try:
                    payload = dict(
                        funcionario_id=funcionario.value if tipo.value == 'funcionario' else None,
                        nome=nome.value,
                        email=email.value,
                        username=username_inp.value,
                        nivel_acesso=nivel.value,
                        ativo=bool(ativo.value),
                        pode_ver_logs=bool(pode_ver_logs.value),
                        enviar_email=not item,
                    )
                    resultado = db.atualizar_usuario(item['id'], **payload) if item else db.criar_usuario(**payload)
                    dialog.close()
                    render()
                    if resultado.get('email_enviado'):
                        ui.notify('Usuário salvo e credenciais enviadas por e-mail.', type='positive')
                    else:
                        ui.notify('Usuário salvo. SMTP não configurado ou envio falhou.', type='warning', multi_line=True)
                except Exception as ex:
                    ui.notify(str(ex), type='negative', multi_line=True)

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def reenviar_credenciais(item):
        with ui.dialog() as dialog, ui.card().classes('p-5 gap-3 rounded-2xl'):
            ui.label(f"Enviar link de redefinição para {item['username']}?").classes('text-lg font-bold')
            ui.label('Será enviado um link seguro com validade limitada.').classes('text-sm text-slate-600')

            def confirmar():
                try:
                    resultado = db.resetar_senha_usuario(item['id'], enviar_email=True)
                    dialog.close()
                    if resultado.get('email_enviado'):
                        ui.notify('Link enviado com sucesso.', type='positive')
                    else:
                        ui.notify('Link gerado, mas o e-mail não foi enviado. Configure o SMTP.', type='warning', multi_line=True)
                except Exception as ex:
                    ui.notify(str(ex), type='negative')

            with ui.row().classes('justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('REENVIAR', on_click=confirmar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def excluir_usuario(item):
        with ui.dialog() as dialog, ui.card().classes('p-5 gap-3 rounded-2xl'):
            ui.label(f"Excluir {item['username']}?").classes('text-lg font-bold')

            def confirmar():
                try:
                    db.excluir_usuario(item['id'])
                    dialog.close()
                    render()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')

            with ui.row().classes('justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=confirmar).props('unelevated').classes('bg-red-600 text-white')
        dialog.open()

    render()
    ui.timer(0.25, hide_page_loader, once=True)
