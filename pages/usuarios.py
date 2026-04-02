
from nicegui import ui
from components.menu import build_menu, show_page_loader, hide_page_loader
from auth import can_edit
from services import db

def _fmt_brl(valor):
    try:
        return f'R$ {float(valor or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return 'R$ 0,00'


def usuarios_page():
    show_page_loader('CARREGANDO PÁGINA...')
    editavel = can_edit()
    with ui.row().classes('w-full h-screen no-wrap bg-slate-100'):
        build_menu('/usuarios')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label('USUÁRIOS').classes('text-xl font-bold text-slate-800')
                    if editavel:
                        ui.button('NOVO USUÁRIO', icon='add', on_click=lambda: form_usuario()).props('unelevated').classes('bg-amber-500 text-black font-bold')
            area = ui.column().classes('w-full gap-3 overflow-auto')

    def render():
        area.clear()
        for u in db.listar_usuarios():
            with area:
                with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4'):
                    with ui.row().classes('w-full items-center justify-between'):
                        with ui.column().classes('gap-0'):
                            ui.label(u['username']).classes('text-base font-bold text-slate-800')
                            ui.label(f"FUNCIONÁRIO: {u.get('nome_funcionario') or '-'} | EQUIPE: {u.get('equipe_nome') or '-'}").classes('text-xs text-slate-500')
                            ui.label(f"PERMISSÃO: {u.get('nivel_acesso') or '-'} | TROCAR SENHA NO PRÓXIMO LOGIN: {'SIM' if bool(u.get('deve_trocar_senha', 0)) else 'NÃO'}").classes('text-xs text-slate-600')
                        if editavel:
                            with ui.row().classes('gap-1'):
                                ui.button(icon='edit', on_click=lambda e=None, item=u: form_usuario(item)).props('flat round dense')
                                ui.button(icon='restart_alt', on_click=lambda e=None, item=u: resetar_senha(item)).props('flat round dense').tooltip('RESETAR SENHA')
                                if u['username'] != 'admin':
                                    ui.button(icon='delete', on_click=lambda e=None, item=u: excluir_usuario(item)).props('flat round dense color=negative')

    def form_usuario(item=None):
        funcionarios = {f['id']: f['nome'] for f in db.listar_funcionarios(apenas_ativos=True)}
        with ui.dialog() as dialog, ui.card().classes('w-[620px] max-w-[96vw] p-5 gap-3 rounded-2xl'):
            ui.label('EDITAR USUÁRIO' if item else 'NOVO USUÁRIO').classes('text-lg font-bold')
            funcionario = ui.select(funcionarios, label='FUNCIONÁRIO').props('outlined dense clearable').classes('w-full')
            nivel = ui.select(['VISUALIZACAO', 'COMPLETO'], label='PERMISSÃO').props('outlined dense').classes('w-full')
            ativo = ui.switch('ATIVO', value=bool(item.get('ativo', 1)) if item else True)
            info = ui.label('USUÁRIO SERÁ GERADO AUTOMATICAMENTE: NOME + ÚLTIMO NOME | SENHA PADRÃO: funsolos1980').classes('text-xs text-slate-500')
            if item:
                funcionario.value = item.get('funcionario_id')
                nivel.value = item.get('nivel_acesso') or 'VISUALIZACAO'
                info.set_text(f"USUÁRIO: {item.get('username')} | RESETA COM SENHA PADRÃO funsolos1980")
            else:
                nivel.value = 'VISUALIZACAO'
            def preview():
                try:
                    if funcionario.value and not item:
                        info.set_text(f"USUÁRIO SUGERIDO: {db.sugerir_username_funcionario(funcionario.value)} | SENHA PADRÃO: funsolos1980")
                except Exception:
                    pass
            funcionario.on('update:model-value', lambda e: preview())
            preview()
            def salvar():
                try:
                    if item:
                        db.atualizar_usuario(item['id'], funcionario.value, nivel.value, bool(ativo.value))
                    else:
                        db.criar_usuario(funcionario.value, nivel.value, bool(ativo.value))
                    dialog.close(); render()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def resetar_senha(item):
        with ui.dialog() as dialog, ui.card().classes('p-5 gap-3 rounded-2xl'):
            ui.label(f"Resetar senha de {item['username']}?").classes('text-lg font-bold')
            ui.label('A senha será funsolos1980 e o usuário deverá trocar no próximo login.').classes('text-sm text-slate-600')
            def confirmar():
                try:
                    db.resetar_senha_usuario(item['id'])
                    dialog.close(); render()
                    ui.notify('Senha resetada com sucesso', type='positive')
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('RESETAR', on_click=confirmar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def excluir_usuario(item):
        with ui.dialog() as dialog, ui.card().classes('p-5 gap-3 rounded-2xl'):
            ui.label(f"Excluir {item['username']}?").classes('text-lg font-bold')
            def confirmar():
                try:
                    db.excluir_usuario(item['id'])
                    dialog.close(); render()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=confirmar).props('unelevated').classes('bg-red-600 text-white')
        dialog.open()

    render()
    ui.timer(0.25, hide_page_loader, once=True)
