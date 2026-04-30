from nicegui import ui, app
from components.menu import build_menu, hide_page_loader
from auth import has_permission
from services import db
import csv
import io
from pathlib import Path
from datetime import datetime

TABELAS_EXPORT = {
    'ativos': 'Ativos / Equipamentos',
    'ordens_servico': 'Ordens de Serviço',
    'os_atividades': 'Atividades de OS',
    'os_materiais': 'Materiais de OS',
    'funcionarios': 'Funcionários',
    'equipes': 'Equipes',
    'usuarios': 'Usuários',
    'audit_logs': 'Log de Ações',
    'system_error_logs': 'Log de Erros do Sistema',
}

TABELAS_IMPORT = {
    'ativos': 'Ativos / Equipamentos',
    'funcionarios': 'Funcionários',
    'equipes': 'Equipes',
}


def _csv_bytes(rows: list[dict]) -> bytes:
    buff = io.StringIO()
    campos = sorted({k for row in rows for k in row.keys()}) if rows else []
    writer = csv.DictWriter(buff, fieldnames=campos, extrasaction='ignore', delimiter=';')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buff.getvalue().encode('utf-8-sig')


def gestao_dados_page():
    if not has_permission('/gestao-dados', 'abrir_tela'):
        ui.notify('Você não possui permissão para acessar Dados.', type='negative')
        ui.navigate.to('/home')
        return

    pode_importar = has_permission('/gestao-dados', 'criar') or has_permission('/gestao-dados', 'editar')
    pode_exportar = has_permission('/gestao-dados', 'exportar')
    pode_reverter = has_permission('/gestao-dados', 'excluir') or has_permission('/gestao-dados', 'admin_modulo')

    with ui.row().classes('fsl-app-shell w-full h-screen no-wrap bg-slate-100'):
        build_menu('/gestao-dados')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4'):
                with ui.column().classes('gap-0'):
                    ui.label('DADOS').classes('text-xl font-bold text-slate-800')
                    ui.label('Exportação, carga em massa e reversão de cargas importadas.').classes('text-xs text-slate-500')

            with ui.row().classes('w-full flex-1 gap-4 overflow-hidden'):
                with ui.card().classes('w-1/2 h-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-3 overflow-auto'):
                    ui.label('BAIXAR DADOS').classes('text-base font-bold text-slate-800')
                    tabela_exp = ui.select(TABELAS_EXPORT, label='MENU / TABELA', value='ativos').props('outlined dense options-dense').classes('w-full')
                    status_export = ui.label('Sem limite de linhas.').classes('text-xs text-slate-500')

                    def baixar():
                        if not pode_exportar:
                            ui.notify('Sem permissão para exportar.', type='negative')
                            return
                        try:
                            rows = db.listar_tabela_generica(tabela_exp.value, None)
                            nome = f"{tabela_exp.value}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                            out = Path('exports')
                            out.mkdir(exist_ok=True)
                            path = out / nome
                            path.write_bytes(_csv_bytes(rows))
                            status_export.set_text(f'{len(rows)} linha(s) exportada(s).')
                            ui.download(str(path), filename=nome)
                        except Exception as ex:
                            try:
                                db.registrar_erro_sistema('DADOS_EXPORTAR', ex, {'tabela': tabela_exp.value}, app.storage.user.get('usuario_id'))
                            except Exception:
                                pass
                            ui.notify(str(ex), type='negative')

                    ui.button('BAIXAR CSV', icon='download', on_click=baixar).props('unelevated').classes('bg-amber-500 text-black font-bold')

                with ui.card().classes('w-1/2 h-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-3 overflow-auto'):
                    ui.label('IMPORTAR DADOS').classes('text-base font-bold text-slate-800')
                    ui.label('Carga em massa por CSV. Cada importação gera um lote reversível.').classes('text-xs text-slate-500')
                    tabela_imp = ui.select(TABELAS_IMPORT, label='DESTINO', value='ativos').props('outlined dense options-dense').classes('w-full')
                    modo = ui.select({'upsert': 'Atualizar se ID existir / inserir se não existir', 'insert': 'Somente inserir'}, label='MODO', value='upsert').props('outlined dense options-dense').classes('w-full')
                    resultado = ui.label('').classes('text-xs text-slate-600')

                    def process_upload(e):
                        if not pode_importar:
                            ui.notify('Sem permissão para importar.', type='negative')
                            return
                        try:
                            raw = e.content.read()
                            content = raw.decode('utf-8-sig')
                            sample = content[:2048]
                            delimiter = ';' if sample.count(';') >= sample.count(',') else ','
                            rows = list(csv.DictReader(io.StringIO(content), delimiter=delimiter))
                            res = db.importar_tabela_generica(tabela_imp.value, rows, modo.value, usuario_id=app.storage.user.get('usuario_id'))
                            lote = res.get('batch_id') or '-'
                            resultado.set_text(f"Importação concluída: {res.get('linhas', 0)} linha(s). Lote: {lote}")
                            ui.notify('CARGA IMPORTADA COM SUCESSO.', type='positive')
                            carregar_lotes()
                        except Exception as ex:
                            try:
                                db.registrar_erro_sistema('DADOS_IMPORTAR', ex, {'tabela': tabela_imp.value}, app.storage.user.get('usuario_id'))
                            except Exception:
                                pass
                            ui.notify(str(ex), type='negative')

                    ui.upload(label='CLIQUE AQUI PARA UPAR CSV', auto_upload=True, on_upload=process_upload).props('accept=.csv color=amber').classes('w-full')
                    ui.separator()
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('CARGAS IMPORTADAS').classes('text-sm font-bold text-slate-700')
                        ui.button('ATUALIZAR', icon='refresh', on_click=lambda: carregar_lotes()).props('flat dense')
                    area_lotes = ui.column().classes('w-full gap-2')

                    def carregar_lotes():
                        area_lotes.clear()
                        with area_lotes:
                            try:
                                lotes = db.listar_cargas_dados(30)
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

                    ui.separator()
                    ui.label('LOG DE ERROS DO SISTEMA').classes('text-sm font-bold text-slate-700')
                    area_erros = ui.column().classes('w-full gap-2')

                    def carregar_erros():
                        area_erros.clear()
                        with area_erros:
                            erros = db.listar_erros_sistema(80)
                            if not erros:
                                ui.label('Nenhum erro registrado.').classes('text-xs text-slate-500')
                                return
                            for item in erros[:80]:
                                with ui.card().classes('w-full bg-slate-50 shadow-none border p-2 gap-1'):
                                    ui.label(f"{item.get('created_at')} • {item.get('origem')}").classes('text-[11px] font-bold text-slate-700')
                                    ui.label(str(item.get('mensagem') or '')[:220]).classes('text-[11px] text-red-700')
                    ui.button('ATUALIZAR LOG DE ERROS', icon='refresh', on_click=carregar_erros).props('flat dense')
                    carregar_lotes()
                    carregar_erros()
    hide_page_loader()
