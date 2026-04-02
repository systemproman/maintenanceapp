from nicegui import ui
from auth import can_edit
from components.menu import build_menu, show_page_loader, hide_page_loader
from services import db

DIAS = [('SEG', 'seg'), ('TER', 'ter'), ('QUA', 'qua'), ('QUI', 'qui'), ('SEX', 'sex'), ('SAB', 'sab'), ('DOM', 'dom')]


def _normalize_hhmm(valor: str) -> str:
    txt = ''.join(ch for ch in str(valor or '') if ch.isdigit())[-4:]
    if not txt:
        return ''
    txt = txt.rjust(4, '0')
    hh = int(txt[:-2])
    mm = min(int(txt[-2:]), 59)
    return f'{hh:02d}:{mm:02d}'


def equipes_page():
    editavel = can_edit()
    with ui.row().classes('w-full h-screen no-wrap bg-slate-100'):
        build_menu('/equipes')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.tabs().classes('w-full') as tabs:
                tab_equipes = ui.tab('EQUIPES')
                tab_escalas = ui.tab('ESCALAS')
            with ui.tab_panels(tabs, value=tab_equipes).classes('w-full flex-1 bg-transparent'):
                with ui.tab_panel(tab_equipes).classes('p-0'):
                    root_equipes = ui.column().classes('w-full h-full gap-3 overflow-auto')
                with ui.tab_panel(tab_escalas).classes('p-0'):
                    root_escalas = ui.column().classes('w-full h-full gap-3 overflow-auto')

    def render_equipes():
        root_equipes.clear()
        with root_equipes:
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4'):
                with ui.row().classes('w-full items-center justify-between'):
                    with ui.column().classes('gap-0'):
                        ui.label('EQUIPES').classes('text-xl font-bold text-slate-800')
                        ui.label('CADASTRO DE EQUIPES').classes('text-xs text-slate-500')
                    if editavel:
                        ui.button('NOVA EQUIPE', icon='add', on_click=lambda: abrir_form_equipe()).props('unelevated').classes('bg-amber-500 text-black font-bold')
            for item in db.listar_equipes():
                with root_equipes:
                    with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4'):
                        with ui.row().classes('w-full items-center justify-between'):
                            with ui.column().classes('gap-0'):
                                ui.label(item.get('nome') or '-').classes('text-base font-bold text-slate-800')
                                ui.label('ATIVA' if item.get('ativo') else 'INATIVA').classes('text-xs text-slate-500')
                            if editavel:
                                with ui.row().classes('gap-1'):
                                    ui.button(icon='edit', on_click=lambda e=None, x=item: abrir_form_equipe(x)).props('flat round dense')
                                    ui.button(icon='delete', on_click=lambda e=None, x=item: excluir_equipe(x)).props('flat round dense color=negative')

    def abrir_form_equipe(item=None):
        with ui.dialog() as dialog, ui.card().classes('w-[520px] max-w-[96vw] p-5 gap-4 rounded-2xl'):
            ui.label('EDITAR EQUIPE' if item else 'NOVA EQUIPE').classes('text-lg font-bold')
            nome = ui.input('NOME').props('outlined dense').classes('w-full')
            nome.value = item.get('nome') if item else ''
            ativo = ui.switch('ATIVA', value=bool(item.get('ativo', 1)) if item else True)
            def salvar():
                try:
                    if item:
                        db.atualizar_equipe(item['id'], nome.value, ativo.value)
                    else:
                        db.criar_equipe(nome.value, ativo.value)
                    dialog.close(); render_equipes(); ui.notify('EQUIPE SALVA', type='positive')
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def excluir_equipe(item):
        try:
            db.excluir_equipe(item['id'])
            render_equipes(); ui.notify('EQUIPE EXCLUÍDA', type='positive')
        except Exception as ex:
            ui.notify(str(ex), type='negative')

    def render_escalas():
        root_escalas.clear()
        with root_escalas:
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4'):
                with ui.row().classes('w-full items-center justify-between'):
                    with ui.column().classes('gap-0'):
                        ui.label('ESCALAS').classes('text-xl font-bold text-slate-800')
                        ui.label('HORÁRIOS POR DIA DA SEMANA').classes('text-xs text-slate-500')
                    if editavel:
                        ui.button('NOVA ESCALA', icon='add', on_click=lambda: abrir_form_escala()).props('unelevated').classes('bg-amber-500 text-black font-bold')
            for item in db.listar_escalas():
                with root_escalas:
                    with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4 gap-3'):
                        with ui.row().classes('w-full items-center justify-between'):
                            with ui.column().classes('gap-0'):
                                ui.label(item.get('nome') or '-').classes('text-base font-bold text-slate-800')
                                ui.label('ATIVA' if item.get('ativo') else 'INATIVA').classes('text-xs text-slate-500')
                            if editavel:
                                with ui.row().classes('gap-1'):
                                    ui.button(icon='edit', on_click=lambda e=None, x=item: abrir_form_escala(x)).props('flat round dense')
                                    ui.button(icon='delete', on_click=lambda e=None, x=item: excluir_escala(x)).props('flat round dense color=negative')
                        with ui.row().classes('w-full gap-4 text-xs font-bold text-slate-700'):
                            ui.label('DIA').classes('w-20')
                            ui.label('INÍCIO').classes('w-32')
                            ui.label('INT. INÍCIO').classes('w-32')
                            ui.label('INT. FIM').classes('w-32')
                            ui.label('FIM').classes('w-32')
                        for rotulo, chave in DIAS:
                            with ui.row().classes('w-full items-center gap-4'):
                                ui.label(rotulo).classes('w-20 font-bold text-slate-800')
                                ui.label(item.get(f'{chave}_inicio') or '-').classes('w-32')
                                ui.label(item.get(f'{chave}_int_inicio') or '-').classes('w-32')
                                ui.label(item.get(f'{chave}_int_fim') or '-').classes('w-32')
                                ui.label(item.get(f'{chave}_fim') or '-').classes('w-32')

    def abrir_form_escala(item=None):
        dias_val = item or {}
        with ui.dialog() as dialog, ui.card().classes('w-[1320px] max-w-[98vw] p-5 gap-4 rounded-2xl'):
            ui.label('EDITAR ESCALA' if item else 'NOVA ESCALA').classes('text-lg font-bold')
            nome = ui.input('NOME').props('outlined dense').classes('w-full')
            nome.value = dias_val.get('nome') if item else ''
            ativa = ui.switch('ATIVA', value=bool(dias_val.get('ativo', 1)) if item else True)
            ui.separator()
            with ui.row().classes('w-full gap-4 text-sm font-bold text-slate-800'):
                ui.label('DIA').classes('w-24')
                ui.label('INÍCIO').classes('w-60')
                ui.label('INT. INÍCIO').classes('w-60')
                ui.label('INT. FIM').classes('w-60')
                ui.label('FIM').classes('w-60')
            campos = {}
            for idx, (rotulo, chave) in enumerate(DIAS):
                with ui.row().classes('w-full items-center gap-4'):
                    ui.label(rotulo).classes('w-24 font-bold text-slate-800')
                    ini = ui.input().props('outlined dense').classes('w-60'); ini.value = dias_val.get(f'{chave}_inicio') or ''
                    int_ini = ui.input().props('outlined dense').classes('w-60'); int_ini.value = dias_val.get(f'{chave}_int_inicio') or ''
                    int_fim = ui.input().props('outlined dense').classes('w-60'); int_fim.value = dias_val.get(f'{chave}_int_fim') or ''
                    fim = ui.input().props('outlined dense').classes('w-60'); fim.value = dias_val.get(f'{chave}_fim') or ''
                    campos[chave] = {'inicio': ini, 'int_inicio': int_ini, 'int_fim': int_fim, 'fim': fim}
                    for c in [ini, int_ini, int_fim, fim]:
                        c.on('blur', lambda e, ctrl=c: (setattr(ctrl, 'value', _normalize_hhmm(ctrl.value)), ctrl.update()))
                    if idx == 0 and not item:
                        def propagar(src_key):
                            val = campos['seg'][src_key].value
                            for _, k2 in DIAS[1:]:
                                if not str(campos[k2][src_key].value or '').strip():
                                    campos[k2][src_key].value = val
                                    campos[k2][src_key].update()
                        ini.on('blur', lambda e: propagar('inicio'))
                        int_ini.on('blur', lambda e: propagar('int_inicio'))
                        int_fim.on('blur', lambda e: propagar('int_fim'))
                        fim.on('blur', lambda e: propagar('fim'))
            def salvar():
                try:
                    payload = {}
                    for _, chave in DIAS:
                        payload[f'{chave}_inicio'] = _normalize_hhmm(campos[chave]['inicio'].value)
                        payload[f'{chave}_int_inicio'] = _normalize_hhmm(campos[chave]['int_inicio'].value)
                        payload[f'{chave}_int_fim'] = _normalize_hhmm(campos[chave]['int_fim'].value)
                        payload[f'{chave}_fim'] = _normalize_hhmm(campos[chave]['fim'].value)
                    if item:
                        db.atualizar_escala(item['id'], nome.value, payload, ativa.value)
                    else:
                        db.criar_escala(nome.value, payload, ativa.value)
                    dialog.close(); render_escalas(); ui.notify('ESCALA SALVA', type='positive')
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def excluir_escala(item):
        try:
            db.excluir_escala(item['id'])
            render_escalas(); ui.notify('ESCALA EXCLUÍDA', type='positive')
        except Exception as ex:
            ui.notify(str(ex), type='negative')

    render_equipes()
    render_escalas()
    ui.timer(0.25, hide_page_loader, once=True)
