from calendar import monthrange
from datetime import datetime

from nicegui import ui
from services import db
from components.menu import build_menu, show_page_loader, hide_page_loader


MESES_PT = {
    1: 'JANEIRO', 2: 'FEVEREIRO', 3: 'MARÇO', 4: 'ABRIL', 5: 'MAIO', 6: 'JUNHO',
    7: 'JULHO', 8: 'AGOSTO', 9: 'SETEMBRO', 10: 'OUTUBRO', 11: 'NOVEMBRO', 12: 'DEZEMBRO',
}


def _fmt_moeda(valor):
    try:
        return f"R$ {float(valor or 0):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return 'R$ 0,00'


def _fmt_data(valor):
    txt = str(valor or '').strip()
    if not txt:
        return '-'
    if len(txt) >= 10 and txt[4] == '-' and txt[7] == '-':
        return f"{txt[8:10]}/{txt[5:7]}/{txt[0:4]}"
    return txt


def _fmt_percent(valor):
    try:
        return f"{float(valor or 0):.2f}%".replace('.', ',')
    except Exception:
        return '0,00%'


def _card(titulo: str, valor: str):
    with ui.card().classes('w-full h-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-1'):
        ui.label(titulo).classes('text-xs font-bold text-slate-500')
        ui.label(valor).classes('text-3xl font-black text-slate-800')


def _chart_os_mensal(dados):
    categorias = [str(x.get('ano_mes') or '') for x in dados]
    abertas = [int(x.get('abertas') or 0) for x in dados]
    encerradas = [int(x.get('encerradas') or 0) for x in dados]
    return {
        'tooltip': {'trigger': 'axis'},
        'legend': {'data': ['Abertas', 'Encerradas']},
        'xAxis': {'type': 'category', 'data': categorias},
        'yAxis': {'type': 'value'},
        'series': [
            {'name': 'Abertas', 'type': 'bar', 'data': abertas},
            {'name': 'Encerradas', 'type': 'bar', 'data': encerradas},
        ],
    }


def _chart_custo_mensal(dados):
    categorias = [str(x.get('ano_mes') or '') for x in dados]
    custo_total = [float(x.get('custo_total') or 0) for x in dados]
    return {
        'tooltip': {'trigger': 'axis'},
        'xAxis': {'type': 'category', 'data': categorias},
        'yAxis': {'type': 'value'},
        'series': [
            {'name': 'Custo Total', 'type': 'line', 'smooth': True, 'data': custo_total},
        ],
    }


def _chart_barra_custo_equipamento(dados):
    ordenados = list(dados or [])[:20]
    categorias = [str(x.get('equipamento_tag') or '-') for x in ordenados]
    valores = [float(x.get('custo_total') or 0) for x in ordenados]
    return {
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'grid': {'left': 140, 'right': 24, 'top': 20, 'bottom': 20},
        'xAxis': {'type': 'value'},
        'yAxis': {'type': 'category', 'data': categorias},
        'series': [
            {'name': 'Custo Total', 'type': 'bar', 'data': valores},
        ],
    }


def _chart_barra_retrabalho(dados):
    ordenados = list(dados or [])[:15]
    categorias = []
    valores = []
    for x in ordenados:
        equip = str(x.get('equipamento_tag') or '-')
        comp = str(x.get('componente_tag') or '').strip()
        categorias.append(f'{equip} / {comp}' if comp else equip)
        valores.append(int(x.get('qtd_reincidencias') or 0))
    return {
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'}},
        'grid': {'left': 180, 'right': 24, 'top': 20, 'bottom': 20},
        'xAxis': {'type': 'value'},
        'yAxis': {'type': 'category', 'data': categorias},
        'series': [
            {'name': 'Reincidências', 'type': 'bar', 'data': valores},
        ],
    }


def _abrir_grafico_expandido(titulo, options):
    with ui.dialog().props('maximized') as dialog, ui.card().classes('w-full h-full rounded-none bg-white p-4 gap-3'):
        with ui.row().classes('w-full items-center justify-between'):
            ui.label(titulo).classes('text-xl font-black text-slate-800')
            ui.button('FECHAR', on_click=dialog.close).props('unelevated').classes('bg-slate-700 text-white')
        ui.echart(options).classes('w-full h-[82vh]')
    dialog.open()


def _tabela(colunas, linhas, titulo):
    with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-3'):
        ui.label(titulo).classes('text-base font-bold text-slate-800')
        ui.table(columns=colunas, rows=linhas, row_key='id').classes('w-full').props('flat bordered dense wrap-cells')


def _lista_scroll_os(linhas):
    with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-3'):
        ui.label('OS POR EQUIPAMENTO').classes('text-base font-bold text-slate-800')
        with ui.scroll_area().classes('w-full h-[420px]'):
            with ui.column().classes('w-full gap-0'):
                for item in linhas:
                    with ui.row().classes('w-full items-center justify-between no-wrap border-b border-slate-200 py-2 gap-3'):
                        with ui.column().classes('min-w-0 gap-0'):
                            ui.label(item.get('equipamento') or '-').classes('text-sm font-bold text-slate-800 truncate')
                            ui.label(item.get('descricao') or '-').classes('text-xs text-slate-500 truncate')
                        with ui.row().classes('items-center gap-4 shrink-0'):
                            ui.label(f"OS: {int(item.get('qtd_os') or 0)}").classes('text-sm font-bold text-slate-700')
                            ui.label(f"AB: {int(item.get('abertas') or 0)}").classes('text-xs font-bold text-slate-500')
                            ui.label(f"EX: {int(item.get('em_execucao') or 0)}").classes('text-xs font-bold text-slate-500')
                            ui.label(f"EN: {int(item.get('encerradas') or 0)}").classes('text-xs font-bold text-slate-500')


def _lista_scroll_retrabalho(linhas):
    with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-3'):
        ui.label('RETRABALHO / REINCIDÊNCIA').classes('text-base font-bold text-slate-800')
        with ui.scroll_area().classes('w-full h-[420px]'):
            with ui.column().classes('w-full gap-0'):
                for item in linhas:
                    equip = item.get('equipamento_tag') or '-'
                    comp = item.get('componente_tag') or '-'
                    with ui.row().classes('w-full items-center justify-between no-wrap border-b border-slate-200 py-2 gap-3'):
                        with ui.column().classes('min-w-0 gap-0'):
                            ui.label(equip).classes('text-sm font-bold text-slate-800 truncate')
                            ui.label(comp if comp != '-' else 'SEM COMPONENTE').classes('text-xs text-slate-500 truncate')
                        with ui.row().classes('items-center gap-4 shrink-0'):
                            ui.label(f"OS ENC.: {int(item.get('qtd_os_encerradas') or 0)}").classes('text-sm font-bold text-slate-700')
                            ui.label(f"REINC.: {int(item.get('qtd_reincidencias') or 0)}").classes('text-xs font-bold text-slate-500')


def _meses_disponiveis():
    meses = {'TODOS': 'TODOS'}
    for item in db.dashboard_os_mensal():
        ano_mes = str(item.get('ano_mes') or '').strip()
        if len(ano_mes) == 7 and ano_mes[4] == '-':
            ano = int(ano_mes[:4])
            mes = int(ano_mes[5:7])
            meses[ano_mes] = f"{MESES_PT.get(mes, str(mes).zfill(2))}/{ano}"
    return meses


def _equipamentos_disponiveis():
    opcoes = {'TODOS': 'TODOS'}
    for item in db.listar_ativos():
        if str(item.get('tipo') or '').upper() != 'EQUIPAMENTO':
            continue
        ativo_id = str(item.get('id') or '').strip()
        tag = str(item.get('tag') or '').strip()
        desc = str(item.get('descricao') or '').strip()
        if ativo_id:
            opcoes[ativo_id] = f'{tag} - {desc}' if desc else tag or ativo_id
    return opcoes


def _periodo_do_mes(ano_mes: str):
    txt = str(ano_mes or '').strip()
    if not txt or txt == 'TODOS':
        return None, None
    try:
        ano = int(txt[:4])
        mes = int(txt[5:7])
        ultimo_dia = monthrange(ano, mes)[1]
        return f'{ano:04d}-{mes:02d}-01', f'{ano:04d}-{mes:02d}-{ultimo_dia:02d}'
    except Exception:
        return None, None


def dashboard_page():
    show_page_loader('CARREGANDO PÁGINA...')
    ui.add_head_html("""
    <style>
        .dash-scroll { width: 100%; height: 100%; overflow: auto; }
    </style>
    """)

    estado = {
        'mes': 'TODOS',
        'equipamento_id': 'TODOS',
    }
    meses = _meses_disponiveis()
    equipamentos = _equipamentos_disponiveis()

    @ui.refreshable
    def render_dashboard():
        periodo_inicio, periodo_fim = _periodo_do_mes(estado['mes'])
        equipamento_id = None if estado['equipamento_id'] == 'TODOS' else estado['equipamento_id']

        cards = db.dashboard_cards(periodo_inicio=periodo_inicio, periodo_fim=periodo_fim, equipamento_id=equipamento_id)
        os_mensal = db.dashboard_os_mensal(periodo_inicio=periodo_inicio, periodo_fim=periodo_fim, equipamento_id=equipamento_id)
        custo_mensal = db.dashboard_custo_mensal(periodo_inicio=periodo_inicio, periodo_fim=periodo_fim, equipamento_id=equipamento_id)
        custo_equipamentos = db.dashboard_top_equipamentos_custo(999999, periodo_inicio=periodo_inicio, periodo_fim=periodo_fim, equipamento_id=equipamento_id)
        top_os = db.dashboard_top_equipamentos_os(999999, periodo_inicio=periodo_inicio, periodo_fim=periodo_fim, equipamento_id=equipamento_id)
        retrabalho = db.dashboard_retrabalho(periodo_inicio=periodo_inicio, periodo_fim=periodo_fim, equipamento_id=equipamento_id, limit=999999)
        os_paradas = db.listar_os_paradas(periodo_inicio=periodo_inicio, periodo_fim=periodo_fim, equipamento_id=equipamento_id)
        os_sem_apontamento = db.listar_os_sem_apontamento(periodo_inicio=periodo_inicio, periodo_fim=periodo_fim, equipamento_id=equipamento_id)

        linhas_paradas = []
        for item in os_paradas:
            linhas_paradas.append({
                'id': item.get('id') or item.get('numero'),
                'numero': item.get('numero') or '-',
                'status': item.get('status') or '-',
                'equipamento': item.get('equipamento_tag') or '-',
                'componente': item.get('componente_tag') or '-',
                'abertura': _fmt_data(item.get('data_abertura')),
                'ultimo_apontamento': _fmt_data(item.get('ultima_data_apontamento')),
                'dias_sem_apontamento': int(item.get('dias_sem_apontamento') or 0),
                'atividades_pendentes': int(item.get('atividades_pendentes') or 0),
            })

        linhas_sem_apontamento = []
        for item in os_sem_apontamento:
            linhas_sem_apontamento.append({
                'id': item.get('id') or item.get('numero'),
                'numero': item.get('numero') or '-',
                'status': item.get('status') or '-',
                'equipamento': item.get('equipamento_tag') or '-',
                'componente': item.get('componente_tag') or '-',
                'tipo_os': item.get('tipo_os') or '-',
                'abertura': _fmt_data(item.get('data_abertura')),
                'total_atividades': int(item.get('total_atividades') or 0),
            })

        linhas_top_os = []
        for item in top_os:
            linhas_top_os.append({
                'id': item.get('equipamento_id') or item.get('equipamento_tag'),
                'equipamento': item.get('equipamento_tag') or '-',
                'descricao': item.get('equipamento_descricao') or '-',
                'qtd_os': int(item.get('qtd_os') or 0),
                'abertas': int(item.get('abertas') or 0),
                'em_execucao': int(item.get('em_execucao') or 0),
                'encerradas': int(item.get('encerradas') or 0),
            })

        linhas_retrabalho = []
        for item in retrabalho.get('linhas') or []:
            linhas_retrabalho.append({
                'id': f"{item.get('equipamento_id') or '0'}-{item.get('componente_id') or '0'}",
                'equipamento': item.get('equipamento_tag') or '-',
                'componente': item.get('componente_tag') or '-',
                'qtd_os_encerradas': int(item.get('qtd_os_encerradas') or 0),
                'qtd_reincidencias': int(item.get('qtd_reincidencias') or 0),
                'ultima_data_encerramento': _fmt_data(item.get('ultima_data_encerramento')),
            })

        chart_os = _chart_os_mensal(os_mensal)
        chart_custo_mensal = _chart_custo_mensal(custo_mensal)
        chart_custo_equips = _chart_barra_custo_equipamento(custo_equipamentos)
        chart_retrabalho = _chart_barra_retrabalho(retrabalho.get('linhas') or [])

        with ui.column().classes('dash-scroll gap-4'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-5 gap-3'):
                ui.label('DASHBOARD').classes('text-2xl font-black text-slate-800')
                with ui.row().classes('w-full items-end gap-3 flex-wrap'):
                    ui.select(
                        options=meses,
                        value=estado['mes'],
                        label='MÊS',
                        on_change=lambda e: (estado.__setitem__('mes', e.value), render_dashboard.refresh()),
                    ).classes('min-w-[220px]')
                    ui.select(
                        options=equipamentos,
                        value=estado['equipamento_id'],
                        label='EQUIPAMENTO',
                        on_change=lambda e: (estado.__setitem__('equipamento_id', e.value), render_dashboard.refresh()),
                    ).classes('min-w-[360px]')
                    ui.button(
                        'LIMPAR FILTROS',
                        on_click=lambda: (estado.update({'mes': 'TODOS', 'equipamento_id': 'TODOS'}), render_dashboard.refresh()),
                    ).props('unelevated').classes('bg-slate-700 text-white')

            with ui.grid(columns=6).classes('w-full gap-4'):
                _card('OS ABERTAS', str(cards.get('abertas') or 0))
                _card('OS EM EXECUÇÃO', str(cards.get('em_execucao') or 0))
                _card('OS PARADAS', str(cards.get('os_paradas') or 0))
                _card('CUSTO TOTAL', _fmt_moeda(cards.get('custo_total') or 0))
                _card('RETRABALHOS', str(retrabalho.get('total_retrabalho') or 0))
                _card('% RETRABALHO', _fmt_percent(retrabalho.get('percentual_os_retrabalho') or 0))

            with ui.grid(columns=2).classes('w-full gap-4'):
                with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-2'):
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('OS ABERTAS X ENCERRADAS').classes('text-base font-bold text-slate-800')
                        ui.button('EXPANDIR', on_click=lambda o=chart_os: _abrir_grafico_expandido('OS ABERTAS X ENCERRADAS', o)).props('flat dense')
                    ui.echart(chart_os).classes('w-full h-80')

                with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-2'):
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('CUSTO POR MÊS').classes('text-base font-bold text-slate-800')
                        ui.button('EXPANDIR', on_click=lambda o=chart_custo_mensal: _abrir_grafico_expandido('CUSTO POR MÊS', o)).props('flat dense')
                    ui.echart(chart_custo_mensal).classes('w-full h-80')

            with ui.grid(columns=2).classes('w-full gap-4'):
                with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-2'):
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('CUSTO POR EQUIPAMENTO').classes('text-base font-bold text-slate-800')
                        ui.button('EXPANDIR', on_click=lambda o=chart_custo_equips: _abrir_grafico_expandido('CUSTO POR EQUIPAMENTO', o)).props('flat dense')
                    ui.echart(chart_custo_equips).classes('w-full h-[420px]')

                with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-4 gap-2'):
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label('RETRABALHO POR EQUIPAMENTO / COMPONENTE').classes('text-base font-bold text-slate-800')
                        ui.button('EXPANDIR', on_click=lambda o=chart_retrabalho: _abrir_grafico_expandido('RETRABALHO POR EQUIPAMENTO / COMPONENTE', o)).props('flat dense')
                    ui.echart(chart_retrabalho).classes('w-full h-[420px]')

            with ui.grid(columns=2).classes('w-full gap-4'):
                _lista_scroll_os(linhas_top_os)
                _lista_scroll_retrabalho(retrabalho.get('linhas') or [])

            _tabela(
                [
                    {'name': 'equipamento', 'label': 'EQUIPAMENTO', 'field': 'equipamento', 'align': 'left'},
                    {'name': 'componente', 'label': 'COMPONENTE', 'field': 'componente', 'align': 'left'},
                    {'name': 'qtd_os_encerradas', 'label': 'OS ENCERRADAS', 'field': 'qtd_os_encerradas', 'align': 'right'},
                    {'name': 'qtd_reincidencias', 'label': 'REINCIDÊNCIAS', 'field': 'qtd_reincidencias', 'align': 'right'},
                    {'name': 'ultima_data_encerramento', 'label': 'ÚLTIMO ENCERRAMENTO', 'field': 'ultima_data_encerramento', 'align': 'left'},
                ],
                linhas_retrabalho,
                'TABELA DE RETRABALHO'
            )

            _tabela(
                [
                    {'name': 'numero', 'label': 'OS', 'field': 'numero', 'align': 'left'},
                    {'name': 'status', 'label': 'STATUS', 'field': 'status', 'align': 'left'},
                    {'name': 'equipamento', 'label': 'EQUIPAMENTO', 'field': 'equipamento', 'align': 'left'},
                    {'name': 'componente', 'label': 'COMPONENTE', 'field': 'componente', 'align': 'left'},
                    {'name': 'abertura', 'label': 'ABERTURA', 'field': 'abertura', 'align': 'left'},
                    {'name': 'ultimo_apontamento', 'label': 'ÚLTIMO APONTAMENTO', 'field': 'ultimo_apontamento', 'align': 'left'},
                    {'name': 'dias_sem_apontamento', 'label': 'DIAS SEM APONTAMENTO', 'field': 'dias_sem_apontamento', 'align': 'right'},
                    {'name': 'atividades_pendentes', 'label': 'ATIVIDADES PENDENTES', 'field': 'atividades_pendentes', 'align': 'right'},
                ],
                linhas_paradas,
                'OS PARADAS'
            )

            _tabela(
                [
                    {'name': 'numero', 'label': 'OS', 'field': 'numero', 'align': 'left'},
                    {'name': 'status', 'label': 'STATUS', 'field': 'status', 'align': 'left'},
                    {'name': 'equipamento', 'label': 'EQUIPAMENTO', 'field': 'equipamento', 'align': 'left'},
                    {'name': 'componente', 'label': 'COMPONENTE', 'field': 'componente', 'align': 'left'},
                    {'name': 'tipo_os', 'label': 'TIPO OS', 'field': 'tipo_os', 'align': 'left'},
                    {'name': 'abertura', 'label': 'ABERTURA', 'field': 'abertura', 'align': 'left'},
                    {'name': 'total_atividades', 'label': 'ATIVIDADES', 'field': 'total_atividades', 'align': 'right'},
                ],
                linhas_sem_apontamento,
                'OS SEM APONTAMENTO'
            )

    with ui.row().classes('w-full h-screen no-wrap bg-slate-100'):
        build_menu('/dashboard')
        with ui.column().classes('flex-1 h-full p-4 overflow-hidden'):
            render_dashboard()

    ui.timer(0.25, hide_page_loader, once=True)
