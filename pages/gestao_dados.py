from nicegui import ui, app
from components.menu import build_menu, hide_page_loader
from auth import has_permission, can_export
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
        ui.notify('Você não possui permissão para acessar Gestão de Dados.', type='negative')
        ui.navigate.to('/home')
        return

    pode_importar = has_permission('/gestao-dados', 'criar') or has_permission('/gestao-dados', 'editar')
    pode_exportar = has_permission('/gestao-dados', 'exportar')

    with ui.row().classes('fsl-app-shell w-full h-screen no-wrap bg-slate-100'):
        build_menu('/gestao-dados')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4'):
                with ui.row().classes('w-full items-center justify-between'):
                    with ui.column().classes('gap-0'):
                        ui.label('GESTÃO DE DADOS').classes('text-xl font-bold text-slate-800')
                        ui.label('Exportação e carga em massa com controle por permissão.').classes('text-xs text-slate-500')
                    ui.badge('CG-MS / America-Campo_Grande').classes('bg-slate-700 text-white')
            with ui.row().classes('w-full flex-1 gap-4 overflow-hidden'):
                with ui.card().classes('w-1/2 h-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-3 overflow-auto'):
                    ui.label('BAIXAR DADOS').classes('text-base font-bold text-slate-800')
                    tabela_exp = ui.select(TABELAS_EXPORT, label='MENU / TABELA', value='ativos').props('outlined dense options-dense').classes('w-full')
                    limite = ui.number('LIMITE DE LINHAS', value=5000, min=1, max=50000, step=500).props('outlined dense').classes('w-full')
                    status_export = ui.label('').classes('text-xs text-slate-500')

                    def baixar():
                        if not pode_exportar:
                            ui.notify('Sem permissão para exportar.', type='negative')
                            return
                        try:
                            rows = db.listar_tabela_generica(tabela_exp.value, int(limite.value or 5000))
                            nome = f"{tabela_exp.value}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                            out = Path('exports'); out.mkdir(exist_ok=True)
                            path = out / nome
                            path.write_bytes(_csv_bytes(rows))
                            status_export.set_text(f'{len(rows)} linha(s) exportada(s).')
                            ui.download(str(path), filename=nome)
                        except Exception as ex:
                            try:
                                db.registrar_erro_sistema('GESTAO_DADOS_EXPORTAR', ex, {'tabela': tabela_exp.value}, app.storage.user.get('usuario_id'))
                            except Exception:
                                pass
                            ui.notify(str(ex), type='negative')

                    ui.button('BAIXAR CSV', icon='download', on_click=baixar).props('unelevated').classes('bg-amber-500 text-black font-bold')

                with ui.card().classes('w-1/2 h-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-3 overflow-auto'):
                    ui.label('IMPORTAR DADOS').classes('text-base font-bold text-slate-800')
                    ui.label('Importação liberada inicialmente para Ativos, Funcionários e Equipes. O restante fica por permissão/exportação para evitar corromper OS.').classes('text-xs text-slate-500')
                    tabela_imp = ui.select(TABELAS_IMPORT, label='DESTINO', value='ativos').props('outlined dense options-dense').classes('w-full')
                    modo = ui.select({'upsert': 'Atualizar se ID existir / inserir se não existir', 'insert': 'Somente inserir'}, label='MODO', value='upsert').props('outlined dense options-dense').classes('w-full')
                    resultado = ui.label('').classes('text-xs text-slate-600')

                    def process_upload(e):
                        if not pode_importar:
                            ui.notify('Sem permissão para importar.', type='negative')
                            return
                        try:
                            content = e.content.read().decode('utf-8-sig')
                            sample = content[:2048]
                            delimiter = ';' if sample.count(';') >= sample.count(',') else ','
                            rows = list(csv.DictReader(io.StringIO(content), delimiter=delimiter))
                            res = db.importar_tabela_generica(tabela_imp.value, rows, modo.value)
                            resultado.set_text(f"Importação concluída: {res.get('linhas', 0)} linha(s).")
                            ui.notify('CARGA IMPORTADA COM SUCESSO.', type='positive')
                        except Exception as ex:
                            try:
                                db.registrar_erro_sistema('GESTAO_DADOS_IMPORTAR', ex, {'tabela': tabela_imp.value}, app.storage.user.get('usuario_id'))
                            except Exception:
                                pass
                            ui.notify(str(ex), type='negative')

                    ui.upload(label='SELECIONAR CSV', auto_upload=True, on_upload=process_upload).props('accept=.csv').classes('w-full')
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
                    carregar_erros()
    hide_page_loader()
