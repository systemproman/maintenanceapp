from tempfile import NamedTemporaryFile
from openpyxl import Workbook
from openpyxl.styles import Font

from nicegui import ui
from components.menu import build_menu, hide_page_loader
from auth import can_view_logs
from services import db

# Mapeamento legível das ações e entidades
_ACAO_LABEL = {
    'CRIAR': '➕ Criar',
    'ATUALIZAR': '✏️ Atualizar',
    'EXCLUIR': '🗑️ Excluir',
    'LOGIN': '🔑 Login',
    'LOGOUT': '🚪 Logout',
    'ALTERAR_SENHA': '🔒 Alterar Senha',
    'RESETAR_SENHA': '🔄 Resetar Senha',
    'CONSUMIR_LINK_SENHA': '🔗 Link Senha Usado',
}
_ENTIDADE_LABEL = {
    'ATIVO': 'Ativo/Equipamento',
    'OS': 'Ordem de Serviço',
    'OS_ATIVIDADE': 'Atividade OS',
    'OS_APONTAMENTO': 'Apontamento OS',
    'OS_MATERIAL': 'Material OS',
    'EQUIPE': 'Equipe',
    'ESCALA': 'Escala',
    'FUNCIONARIO': 'Funcionário',
    'USUARIO': 'Usuário',
}
_ACAO_COLOR = {
    'CRIAR': 'bg-green-100 text-green-800',
    'ATUALIZAR': 'bg-blue-100 text-blue-800',
    'EXCLUIR': 'bg-red-100 text-red-800',
    'LOGIN': 'bg-amber-100 text-amber-800',
    'LOGOUT': 'bg-slate-200 text-slate-700',
    'ALTERAR_SENHA': 'bg-purple-100 text-purple-800',
    'RESETAR_SENHA': 'bg-orange-100 text-orange-800',
    'CONSUMIR_LINK_SENHA': 'bg-indigo-100 text-indigo-800',
}


def _fmt_acao(acao: str) -> str:
    return _ACAO_LABEL.get(str(acao or '').upper(), str(acao or '-'))


def _fmt_entidade(entidade: str) -> str:
    return _ENTIDADE_LABEL.get(str(entidade or '').upper(), str(entidade or '-'))


def _color_acao(acao: str) -> str:
    return _ACAO_COLOR.get(str(acao or '').upper(), 'bg-slate-200 text-slate-700')


def logs_page():
    if not can_view_logs():
        ui.notify('Você não possui permissão para visualizar o log de ações.', type='negative')
        ui.navigate.to('/home')
        return

    filtros = {'busca': '', 'acao': '', 'entidade': ''}

    with ui.row().classes('fsl-app-shell w-full h-screen no-wrap bg-slate-100'):
        build_menu('/logs')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-3'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label('LOG DE AÇÕES').classes('text-xl font-bold text-slate-800')
                    ui.label('Todas as operações realizadas no sistema ficam registradas aqui.').classes('text-xs text-slate-500')
                with ui.row().classes('w-full gap-2 items-end flex-wrap'):
                    busca = ui.input('Buscar por usuário, detalhes ou ID').props('outlined dense clearable').classes('min-w-[280px] flex-1')
                    acoes_opts = {
                        '': '— Todas as ações —',
                        'CRIAR': 'Criar',
                        'ATUALIZAR': 'Atualizar',
                        'EXCLUIR': 'Excluir',
                        'LOGIN': 'Login',
                        'LOGOUT': 'Logout',
                        'ALTERAR_SENHA': 'Alterar Senha',
                        'RESETAR_SENHA': 'Resetar Senha',
                    }
                    acao = ui.select(acoes_opts, value='', label='Ação').props('outlined dense').classes('w-48')
                    entidades_opts = {
                        '': '— Todas as entidades —',
                        'ATIVO': 'Ativo/Equipamento',
                        'OS': 'Ordem de Serviço',
                        'OS_ATIVIDADE': 'Atividade OS',
                        'OS_APONTAMENTO': 'Apontamento OS',
                        'OS_MATERIAL': 'Material OS',
                        'EQUIPE': 'Equipe',
                        'ESCALA': 'Escala',
                        'FUNCIONARIO': 'Funcionário',
                        'USUARIO': 'Usuário',
                    }
                    entidade = ui.select(entidades_opts, value='', label='Entidade').props('outlined dense').classes('w-52')
                    btn_filtrar = ui.button('FILTRAR', icon='search').props('unelevated').classes('bg-amber-500 text-black font-bold')
                    btn_limpar = ui.button('LIMPAR', icon='filter_alt_off').props('flat')
                    btn_exportar = ui.button('EXPORTAR XLSX', icon='download').props('outline').classes('text-slate-700')
            area = ui.column().classes('w-full gap-2 overflow-auto')

    def render():
        area.clear()
        logs = db.listar_logs_acoes(
            limit=300,
            busca=filtros['busca'],
            acao=filtros['acao'],
            entidade=filtros['entidade'],
        )
        if not logs:
            with area:
                with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4'):
                    ui.label('Nenhum registro encontrado.').classes('text-sm text-slate-600')
            return

        for item in logs:
            acao_str = str(item.get('acao') or '')
            entidade_str = str(item.get('entidade') or '')
            badge_cls = _color_acao(acao_str)
            with area:
                with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-3 gap-1'):
                    with ui.row().classes('w-full items-start justify-between gap-3 flex-wrap'):
                        with ui.column().classes('gap-0 min-w-0 flex-1'):
                            with ui.row().classes('items-center gap-2 flex-wrap'):
                                ui.html(f'<span class="text-xs font-bold px-2 py-0.5 rounded-full {badge_cls}">{_fmt_acao(acao_str)}</span>')
                                ui.label(_fmt_entidade(entidade_str)).classes('text-sm font-semibold text-slate-800')
                            with ui.row().classes('gap-4 flex-wrap mt-0.5'):
                                ui.label(f"👤 {item.get('usuario_nome') or item.get('usuario_username') or 'SISTEMA'}").classes('text-xs text-slate-500')
                                ui.label(f"🕐 {item.get('created_at') or '-'}").classes('text-xs text-slate-500')
                                if item.get('registro_id'):
                                    ui.label(f"🆔 {item.get('registro_id')}").classes('text-xs text-slate-400 truncate max-w-[220px]')
                        # detalhes
                    detalhes = str(item.get('detalhes_texto') or '').strip()
                    if detalhes:
                        with ui.expansion('Ver detalhes').classes('w-full text-xs text-slate-500'):
                            ui.label(detalhes).classes('text-sm text-slate-700 whitespace-pre-wrap p-2')

    def exportar_xlsx():
        logs = db.listar_logs_acoes(
            limit=5000,
            busca=filtros['busca'],
            acao=filtros['acao'],
            entidade=filtros['entidade'],
        )
        wb = Workbook()
        ws = wb.active
        ws.title = 'Log de Ações'
        headers = ['Data/Hora', 'Ação', 'Entidade', 'Registro ID', 'Usuário (Nome)', 'Usuário (Login)', 'Detalhes']
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for item in logs:
            ws.append([
                item.get('created_at') or '',
                _fmt_acao(item.get('acao') or ''),
                _fmt_entidade(item.get('entidade') or ''),
                item.get('registro_id') or '',
                item.get('usuario_nome') or '',
                item.get('usuario_username') or '',
                item.get('detalhes_texto') or '',
            ])
        for col, width in {'A': 20, 'B': 22, 'C': 22, 'D': 38, 'E': 28, 'F': 22, 'G': 90}.items():
            ws.column_dimensions[col].width = width
        tmp = NamedTemporaryFile(prefix='log_acoes_', suffix='.xlsx', delete=False)
        tmp.close()
        wb.save(tmp.name)
        ui.download(tmp.name, filename='log_acoes.xlsx')

    def aplicar_filtros():
        filtros['busca'] = str(busca.value or '').strip()
        filtros['acao'] = str(acao.value or '').strip()
        filtros['entidade'] = str(entidade.value or '').strip()
        render()

    def limpar():
        busca.value = ''
        acao.value = ''
        entidade.value = ''
        aplicar_filtros()

    btn_filtrar.on('click', lambda e: aplicar_filtros())
    btn_exportar.on('click', lambda e: exportar_xlsx())
    btn_limpar.on('click', lambda e: limpar())
    busca.on('keydown.enter', lambda e: aplicar_filtros())
    acao.on('update:model-value', lambda e: aplicar_filtros())
    entidade.on('update:model-value', lambda e: aplicar_filtros())

    render()
    ui.timer(0.25, hide_page_loader, once=True)
