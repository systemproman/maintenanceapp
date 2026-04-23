
from nicegui import ui
from components.menu import build_menu, show_page_loader, hide_page_loader
from auth import can_edit, get_role
from services import db

def _fmt_brl(valor):
    try:
        return f'R$ {float(valor or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return 'R$ 0,00'


def funcionarios_page():
    if get_role() == 'TECNICO':
        ui.notify('Acesso não permitido para o perfil Técnico.', type='negative')
        ui.navigate.to('/home')
        return
    editavel = can_edit()
    with ui.row().classes('w-full h-screen no-wrap bg-slate-100'):
        build_menu('/funcionarios')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label('FUNCIONÁRIOS').classes('text-xl font-bold text-slate-800')
                    if editavel:
                        ui.button('NOVO FUNCIONÁRIO', icon='add', on_click=lambda: form_funcionario()).props('unelevated').classes('bg-amber-500 text-black font-bold')
            area = ui.column().classes('w-full gap-3 overflow-auto')

    def render():
        area.clear()
        for f in db.listar_funcionarios():
            with area:
                with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4'):
                    with ui.row().classes('w-full items-center justify-between'):
                        with ui.column().classes('gap-0'):
                            ui.label(f['nome']).classes('text-base font-bold text-slate-800')
                            ui.label(f"EQUIPE: {f.get('equipe_nome') or '-'} | ESCALA: {f.get('escala_nome') or '-'} | CARGO: {f.get('cargo') or '-'}").classes('text-xs text-slate-500')
                            ui.label(f"CUSTO MENSAL BRUTO: {_fmt_brl(f.get('custo_mensal_bruto', 0))} | CARGA MENSAL: {f.get('carga_horaria_mensal') or 0}H | CUSTO HH: {_fmt_brl(f.get('custo_hh', 0))}").classes('text-xs text-slate-600')
                        if editavel:
                            with ui.row().classes('gap-1'):
                                ui.button(icon='edit', on_click=lambda e=None, item=f: form_funcionario(item)).props('flat round dense')
                                ui.button(icon='delete', on_click=lambda e=None, item=f: excluir_funcionario(item)).props('flat round dense color=negative')

    def form_funcionario(item=None):
        equipes = {e['id']: e['nome'] for e in db.listar_equipes()}
        escalas = {s['id']: s['nome'] for s in db.listar_escalas()}
        with ui.dialog() as dialog, ui.card().classes('w-[760px] max-w-[96vw] p-5 gap-3 rounded-2xl'):
            ui.label('EDITAR FUNCIONÁRIO' if item else 'NOVO FUNCIONÁRIO').classes('text-lg font-bold')
            nome = ui.input('NOME').props('outlined dense').classes('w-full')
            matricula = ui.input('MATRÍCULA').props('outlined dense').classes('w-full')
            equipe = ui.select(equipes, label='EQUIPE').props('outlined dense clearable').classes('w-full')
            escala = ui.select(escalas, label='ESCALA').props('outlined dense clearable').classes('w-full')
            cargo = ui.input('CARGO').props('outlined dense').classes('w-full')
            custo = ui.number('CUSTO MENSAL BRUTO').props('outlined dense').classes('w-full')
            carga = ui.number('CARGA HORÁRIA MENSAL').props('outlined dense').classes('w-full')
            custo_hh = ui.label('CUSTO HH: R$ 0,00').classes('text-sm font-bold text-slate-700')
            ativo = ui.switch('ATIVO', value=bool(item.get('ativo', 1)) if item else True)
            if item:
                nome.value = item.get('nome') or ''
                matricula.value = item.get('matricula') or ''
                equipe.value = item.get('equipe_id')
                escala.value = item.get('escala_id')
                cargo.value = item.get('cargo') or ''
                custo.value = float(item.get('custo_mensal_bruto') or 0)
                carga.value = float(item.get('carga_horaria_mensal') or 220)
            else:
                carga.value = 220
            def refresh_custo_hh():
                try:
                    v = float(custo.value or 0) / float(carga.value or 0)
                except Exception:
                    v = 0
                custo_hh.set_text(f'CUSTO HH: {_fmt_brl(v)}')
            custo.on('update:model-value', lambda e: refresh_custo_hh())
            carga.on('update:model-value', lambda e: refresh_custo_hh())
            refresh_custo_hh()
            def salvar():
                try:
                    args = dict(nome=nome.value, matricula=matricula.value, equipe_id=equipe.value, escala_id=escala.value, cargo=cargo.value,
                                custo_mensal_bruto=float(custo.value or 0), carga_horaria_mensal=float(carga.value or 220), ativo=bool(ativo.value))
                    if item:
                        db.atualizar_funcionario(item['id'], **args)
                    else:
                        db.criar_funcionario(**args)
                    dialog.close(); render()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def excluir_funcionario(item):
        with ui.dialog() as dialog, ui.card().classes('p-5 gap-3 rounded-2xl'):
            ui.label(f"Excluir {item['nome']}?").classes('text-lg font-bold')
            def confirmar():
                try:
                    db.excluir_funcionario(item['id'])
                    dialog.close(); render()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=confirmar).props('unelevated').classes('bg-red-600 text-white')
        dialog.open()

    render()
    ui.timer(0.25, hide_page_loader, once=True)
