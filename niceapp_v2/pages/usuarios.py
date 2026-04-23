from nicegui import ui
from components.menu import build_menu, hide_page_loader
from auth import can_edit, is_admin, can_manage_users, get_role
from services import db


def usuarios_page():
    role = get_role()
    if not can_manage_users():
        ui.notify('Acesso restrito ao Administrador.', type='negative')
        ui.navigate.to('/home')
        return

    admin = is_admin()

    with ui.row().classes('w-full h-screen no-wrap bg-slate-100'):
        build_menu('/usuarios')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label('USUÁRIOS').classes('text-xl font-bold text-slate-800')
                    ui.button('NOVO USUÁRIO', icon='add', on_click=lambda: form_usuario()).props('unelevated').classes('bg-amber-500 text-black font-bold')
            area = ui.column().classes('w-full gap-3 overflow-auto')

    def render():
        area.clear()
        for u in db.listar_usuarios():
            with area:
                with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4'):
                    with ui.row().classes('w-full items-center justify-between gap-3'):
                        with ui.column().classes('gap-0'):
                            ui.label(u['username']).classes('text-base font-bold text-slate-800')
                            ui.label(f"NOME: {u.get('nome_exibicao') or '-'} | E-MAIL: {u.get('email') or '-'}").classes('text-xs text-slate-500')
                            ui.label(f"TIPO: {'FUNCIONÁRIO' if u.get('funcionario_id') else 'EXTERNO'} | FUNCIONÁRIO: {u.get('nome_funcionario') or '-'} | EQUIPE: {u.get('equipe_nome') or '-'}").classes('text-xs text-slate-500')
                            ui.label(f"PERFIL: {u.get('nivel_acesso') or '-'} | VER LOGS: {'SIM' if bool(u.get('pode_ver_logs', 0)) else 'NÃO'}").classes('text-xs text-slate-600')
                            ui.label(f"ATIVO: {'SIM' if bool(u.get('ativo', 1)) else 'NÃO'} | TROCAR SENHA NO PRÓXIMO LOGIN: {'SIM' if bool(u.get('deve_trocar_senha', 0)) else 'NÃO'}").classes('text-xs text-slate-600')
                        with ui.row().classes('gap-1'):
                            ui.button(icon='edit', on_click=lambda e=None, item=u: form_usuario(item)).props('flat round dense')
                            ui.button(icon='mail', on_click=lambda e=None, item=u: reenviar_credenciais(item)).props('flat round dense').tooltip('ENVIAR LINK DE REDEFINIÇÃO')
                            if u['username'] != 'admin':
                                ui.button(icon='delete', on_click=lambda e=None, item=u: excluir_usuario(item)).props('flat round dense color=negative')

    def form_usuario(item=None):
        funcionarios_rows = db.listar_funcionarios(apenas_ativos=True)
        funcionarios = {f['id']: f['nome'] for f in funcionarios_rows}
        with ui.dialog() as dialog, ui.card().classes('w-[760px] max-w-[96vw] p-5 gap-3 rounded-2xl'):
            ui.label('EDITAR USUÁRIO' if item else 'NOVO USUÁRIO').classes('text-lg font-bold')
            tipo = ui.radio({'funcionario': 'Funcionário', 'externo': 'Externo'}, value='funcionario' if (item and item.get('funcionario_id')) else 'externo').props('inline')
            funcionario = ui.select(funcionarios, label='FUNCIONÁRIO').props('outlined dense clearable').classes('w-full')
            nome = ui.input('NOME').props('outlined dense').classes('w-full')
            email = ui.input('E-MAIL').props('outlined dense').classes('w-full')
            username = ui.input('USUÁRIO').props('outlined dense').classes('w-full')
            # Perfis disponíveis para Admin criar
            perfis = {'ADMIN': 'Admin (acesso total)', 'GERENCIA': 'Gerência (vê/edita tudo)', 'TECNICO': 'Técnico (árvore + OS)'}
            nivel = ui.select(perfis, label='PERFIL').props('outlined dense').classes('w-full')
            ativo = ui.switch('ATIVO', value=bool(item.get('ativo', 1)) if item else True)
            pode_ver_logs = ui.switch('PODE VER LOGS', value=bool(item.get('pode_ver_logs', 0)) if item else False)
            ui.label('Ao salvar, o sistema envia por e-mail um link seguro para definição ou redefinição de senha.').classes('text-xs text-slate-500')

            if item:
                funcionario.value = item.get('funcionario_id')
                nome.value = item.get('nome_exibicao') or item.get('nome') or item.get('nome_funcionario') or ''
                email.value = item.get('email') or ''
                username.value = item.get('username') or ''
                # normaliza perfil legado
                raw = str(item.get('nivel_acesso') or 'TECNICO').upper()
                from auth import _MAP
                nivel.value = _MAP.get(raw, raw) if raw not in ('ADMIN', 'GERENCIA', 'TECNICO') else raw
                tipo.value = 'funcionario' if item.get('funcionario_id') else 'externo'
            else:
                nivel.value = 'TECNICO'

            def sync_campos():
                eh_func = tipo.value == 'funcionario'
                funcionario.set_visibility(eh_func)
                if eh_func and funcionario.value:
                    try:
                        dados = db.get_funcionario(funcionario.value) or {}
                        if not item or not nome.value:
                            nome.value = dados.get('nome') or nome.value
                        if not item and not username.value:
                            username.value = db.sugerir_username_funcionario(funcionario.value)
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
                        username=username.value,
                        nivel_acesso=nivel.value,
                        ativo=bool(ativo.value),
                        pode_ver_logs=bool(pode_ver_logs.value),
                        enviar_email=not item,
                    )
                    if item:
                        resultado = db.atualizar_usuario(item['id'], **payload)
                    else:
                        resultado = db.criar_usuario(**payload)
                    dialog.close()
                    render()
                    if resultado.get('email_enviado'):
                        ui.notify('Usuário salvo e credenciais enviadas por e-mail.', type='positive')
                    else:
                        ui.notify('Usuário salvo. SMTP não configurado ou envio falhou; verifique o servidor.', type='warning', multi_line=True)
                except Exception as ex:
                    ui.notify(str(ex), type='negative', multi_line=True)

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def reenviar_credenciais(item):
        with ui.dialog() as dialog, ui.card().classes('p-5 gap-3 rounded-2xl'):
            ui.label(f"Gerar nova senha e enviar credenciais para {item['username']}?").classes('text-lg font-bold')
            ui.label('Será enviado um link seguro de redefinição de senha com validade limitada.').classes('text-sm text-slate-600')
            def confirmar():
                try:
                    resultado = db.resetar_senha_usuario(item['id'], enviar_email=True)
                    dialog.close()
                    render()
                    if resultado.get('email_enviado'):
                        ui.notify('Link de redefinição enviado com sucesso.', type='positive')
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
