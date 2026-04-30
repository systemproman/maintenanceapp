from nicegui import ui, app
from components.menu import build_menu, hide_page_loader
from auth import has_permission
from services import db
from pathlib import Path
from datetime import datetime
import io

TABELAS_EXPORT = {
    'ativos': 'Ativos / Equipamentos',
    'ordens_servico': 'Ordens de Serviço',
    'os_atividades': 'Atividades de OS',
    'os_materiais': 'Materiais de OS',
    'funcionarios': 'Funcionários',
    'equipes': 'Equipes',
    'usuarios': 'Usuários',
    'audit_logs': 'Log de Ações',
}

TABELAS_IMPORT = {
    'ativos': 'Ativos / Equipamentos',
    'funcionarios': 'Funcionários',
    'equipes': 'Equipes',
}


def _xlsx_bytes(rows: list[dict], sheet_name: str = 'DADOS') -> bytes:
    try:
        from openpyxl import Workbook
    except Exception as ex:
        raise RuntimeError('Biblioteca openpyxl não encontrada. Inclua openpyxl no requirements.txt.') from ex

    wb = Workbook()
    ws = wb.active
    ws.title = str(sheet_name or 'DADOS')[:31]
    campos = sorted({str(k) for row in rows for k in (row or {}).keys()}) if rows else []
    if campos:
        ws.append(campos)
        for row in rows:
            ws.append([row.get(c) for c in campos])
    else:
        ws.append(['SEM_DADOS'])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _read_xlsx_rows(raw: bytes) -> list[dict]:
    try:
        from openpyxl import load_workbook
    except Exception as ex:
        raise RuntimeError('Biblioteca openpyxl não encontrada. Inclua openpyxl no requirements.txt.') from ex

    wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        headers_raw = next(rows_iter)
    except StopIteration:
        return []
    headers = [str(h).strip() if h is not None else '' for h in headers_raw]
    rows = []
    for values in rows_iter:
        item = {}
        for idx, header in enumerate(headers):
            if not header:
                continue
            val = values[idx] if idx < len(values) else None
            if val is None:
                val = ''
            item[header] = val
        if any(str(v).strip() for v in item.values()):
            rows.append(item)
    return rows


def gestao_dados_page():
    if not has_permission('/gestao-dados', 'abrir_tela'):
        ui.notify('Você não possui permissão para acessar Dados.', type='negative')
        ui.navigate.to('/home')
        return

    pode_importar = has_permission('/gestao-dados', 'criar') or has_permission('/gestao-dados', 'editar')
    pode_exportar = has_permission('/gestao-dados', 'exportar')
    pode_reverter = has_permission('/gestao-dados', 'excluir') or has_permission('/gestao-dados', 'admin_modulo')

    with ui.row().classes('fsl-app-shell w-full h-screen no-wrap bg-slate-100 overflow-hidden'):
        build_menu('/gestao-dados')
        with ui.column().classes('flex-1 h-full min-h-0 p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 shrink-0'):
                with ui.column().classes('gap-0'):
                    ui.label('DADOS').classes('text-xl font-bold text-slate-800')
                    ui.label('Exportação em XLSX, carga em massa por XLSX e reversão de cargas importadas.').classes('text-xs text-slate-500')

            with ui.row().classes('w-full flex-1 min-h-0 gap-4 overflow-hidden'):
                with ui.card().classes('w-1/2 h-full min-h-0 rounded-2xl shadow-sm border-0 bg-white p-4 gap-3 overflow-hidden'):
                    ui.label('EXPORTAR DADOS').classes('text-base font-bold text-slate-800')
                    tabela_exp = ui.select(TABELAS_EXPORT, label='MENU / TABELA', value='ativos').props('outlined dense options-dense').classes('w-full')
                    status_export = ui.label('Exportação XLSX sem limite de linhas.').classes('text-xs text-slate-500')

                    def baixar():
                        if not pode_exportar:
                            ui.notify('Sem permissão para exportar.', type='negative')
                            return
                        try:
                            rows = db.listar_tabela_generica(tabela_exp.value, None)
                            nome = f"{tabela_exp.value}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                            out = Path('exports')
                            out.mkdir(exist_ok=True)
                            path = out / nome
                            path.write_bytes(_xlsx_bytes(rows, tabela_exp.value))
                            status_export.set_text(f'{len(rows)} linha(s) exportada(s) em XLSX.')
                            ui.download(str(path), filename=nome)
                        except Exception as ex:
                            ui.notify(str(ex), type='negative')

                    ui.button('EXPORTAR XLSX', icon='download', on_click=baixar).props('unelevated').classes('bg-amber-500 text-black font-bold')

                with ui.card().classes('w-1/2 h-full min-h-0 rounded-2xl shadow-sm border-0 bg-white p-4 gap-3 overflow-hidden'):
                    ui.label('IMPORTAR DADOS').classes('text-base font-bold text-slate-800')
                    ui.label('Carga em massa por XLSX. Cada importação gera um lote reversível.').classes('text-xs text-slate-500')
                    tabela_imp = ui.select(TABELAS_IMPORT, label='DESTINO', value='ativos').props('outlined dense options-dense').classes('w-full')
                    modo = ui.select({'upsert': 'Atualizar se ID existir / inserir se não existir', 'insert': 'Somente inserir'}, label='MODO', value='upsert').props('outlined dense options-dense').classes('w-full')
                    resultado = ui.label('').classes('text-xs text-slate-600')

                    def process_upload(e):
                        if not pode_importar:
                            ui.notify('Sem permissão para importar.', type='negative')
                            return
                        try:
                            raw = e.content.read()
                            rows = _read_xlsx_rows(raw)
                            res = db.importar_tabela_generica(tabela_imp.value, rows, modo.value, usuario_id=app.storage.user.get('usuario_id'))
                            lote = res.get('batch_id') or '-'
                            resultado.set_text(f"Importação concluída: {res.get('linhas', 0)} linha(s). Lote: {lote}")
                            ui.notify('CARGA IMPORTADA COM SUCESSO.', type='positive')
                            carregar_lotes()
                        except Exception as ex:
                            ui.notify(str(ex), type='negative')

                    ui.upload(label='CLIQUE AQUI PARA IMPORTAR XLSX', auto_upload=True, on_upload=process_upload).props('accept=.xlsx color=amber').classes('w-full')
                    ui.separator()
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('CARGAS IMPORTADAS').classes('text-sm font-bold text-slate-700')
                        ui.button('ATUALIZAR', icon='refresh', on_click=lambda: carregar_lotes()).props('flat dense')
                    area_lotes = ui.column().classes('w-full flex-1 min-h-0 gap-2 overflow-auto')

                    def carregar_lotes():
                        area_lotes.clear()
                        with area_lotes:
                            try:
                                lotes = db.listar_cargas_dados(200)
                            except Exception as ex:
                                ui.label(str(ex)).classes('text-xs text-red-700')
                                return
                            if not lotes:
                                ui.label('Nenhuma carga registrada.').classes('text-xs text-slate-500')
                                return
                            for lote in lotes:
                                revertido = bool(lote.get('revertido'))
                                with ui.card().classes('w-full bg-slate-50 shadow-none border p-2 gap-1'):
                                    ui.label(f"{lote.get('created_at')} • {lote.get('tabela')} • {lote.get('linhas')} linha(s)").classes('text-[11px] font-bold text-slate-700')
                                    ui.label(f"LOTE: {lote.get('id')}" + (' • REVERTIDO' if revertido else '')).classes('text-[11px] text-slate-500')
                                    if pode_reverter and not revertido:
                                        ui.button('REVERTER ESTA CARGA', icon='undo', on_click=lambda e=None, bid=lote.get('id'): reverter(bid)).props('flat dense color=negative')

                    def reverter(batch_id):
                        try:
                            res = db.reverter_carga_dados(batch_id, usuario_id=app.storage.user.get('usuario_id'))
                            ui.notify(f"Carga revertida: {res.get('linhas', 0)} registro(s).", type='positive')
                            carregar_lotes()
                        except Exception as ex:
                            ui.notify(str(ex), type='negative')

                    carregar_lotes()
    hide_page_loader()
