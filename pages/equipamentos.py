import tempfile
import uuid
from urllib.parse import quote
from pathlib import Path

from nicegui import ui
from openpyxl import Workbook

from components.menu import build_menu, show_page_loader, hide_page_loader, loader_props
from config.settings import ENABLE_CRITICIDADE
from auth import can_access_route, can_create, can_edit, can_delete, can_export
from services.db import (
    get_ativos,
    get_ativo,
    listar_pais_possiveis,
    criar_ativo,
    atualizar_ativo,
    excluir_ativo,
    listar_anexos,
    adicionar_anexo,
    remover_anexo,
)

LOGO_FSL_SRC = '/assets/logo_fsl.png'

UNIDADES_PECA = ['UN', 'PÇ', 'PÇS', 'PC', 'PCS', 'CJ', 'JG', 'KIT', 'PAR', 'M', 'MM', 'CM', 'M2', 'M3', 'KG', 'G', 'L', 'ML']

BUSCA_NEUTRA_CSS = '''
<style>
.busca-neutra .q-field__native,
.busca-neutra input {
    color: #0f172a !important;
    caret-color: #0f172a !important;
}
.busca-neutra .q-field__native::selection,
.busca-neutra input::selection,
.busca-neutra textarea::selection {
    background: transparent !important;
    color: #0f172a !important;
}
.busca-neutra .q-field__control:before,
.busca-neutra .q-field__control:after,
.busca-neutra.q-field--focused .q-field__control:after {
    border-color: #cbd5e1 !important;
}
.busca-neutra .q-field__label,
.busca-neutra .q-icon {
    color: #64748b !important;
}
.busca-neutra input:-webkit-autofill,
.busca-neutra input:-webkit-autofill:hover,
.busca-neutra input:-webkit-autofill:focus,
.busca-neutra textarea:-webkit-autofill,
.busca-neutra select:-webkit-autofill {
    -webkit-text-fill-color: #0f172a !important;
    caret-color: #0f172a !important;
    -webkit-box-shadow: 0 0 0px 1000px white inset !important;
    box-shadow: 0 0 0px 1000px white inset !important;
    transition: background-color 9999s ease-in-out 0s;
}
</style>
'''


def _normalizar_busca(valor) -> str:
    import unicodedata
    texto = str(valor or '').strip().lower()
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')


def _label_pai(item: dict) -> str:
    return f"{item['tag']} - {item['descricao']}"


def _normalizar_id(valor):
    if valor in (None, '', 'None'):
        return None
    return str(valor)


def _normalizar_pecas_linhas(linhas):
    resultado = []
    for linha in linhas:
        referencia = str(linha.get('referencia', '')).strip().upper()
        descricao = str(linha.get('descricao', '')).strip().upper()
        quantidade_txt = str(linha.get('quantidade', '')).strip()
        unidade = str(linha.get('unidade', 'UN') or 'UN').strip().upper()

        if not referencia and not descricao and not quantidade_txt:
            continue

        try:
            quantidade = float(quantidade_txt.replace(',', '.')) if quantidade_txt else 0
        except Exception:
            quantidade = 0

        if unidade not in UNIDADES_PECA:
            unidade = 'UN'

        resultado.append({
            'referencia': referencia,
            'descricao': descricao,
            'quantidade': quantidade,
            'unidade': unidade,
        })
    return resultado


def _get_upload_name(e) -> str:
    if hasattr(e, 'file') and getattr(e.file, 'name', None):
        return str(e.file.name)
    if hasattr(e, 'name') and e.name:
        return str(e.name)
    if hasattr(e, 'file_name') and e.file_name:
        return str(e.file_name)
    return 'arquivo'


def _arquivo_url(anexo: dict) -> str:
    nome = quote(str(anexo.get('nome_original') or anexo.get('nome_salvo') or 'arquivo'))
    return f"/arquivo/{anexo['id']}/{nome}"


def _nome_truncado(nome: str, limite: int = 24) -> str:
    nome = str(nome or '')
    return nome if len(nome) <= limite else nome[:limite - 3] + '...'


def _abrir_nova_aba(url: str):
    ui.run_javascript(f"window.open('{url}', '_blank')")


def _status_visual(row: dict):
    tipo = str(row.get('tipo') or '').upper()
    if tipo != 'EQUIPAMENTO':
        return '-', 'bg-slate-100 text-slate-500'

    if int(row.get('qtd_os_abertas') or 0) > 0 or bool(row.get('tem_os_aberta')):
        return 'OS ABERTA', 'bg-amber-100 text-amber-800'

    ativo = bool(row.get('ativo', 1))
    if ativo:
        return 'ATIVO', 'bg-emerald-100 text-emerald-800'
    return 'INATIVO', 'bg-red-100 text-red-700'


def _event_input_value(e, fallback=''):
    valor = getattr(e, 'value', None)
    if valor is None:
        args = getattr(e, 'args', None)
        if isinstance(args, dict):
            valor = args.get('value', fallback)
        else:
            valor = fallback
    return str(valor or '')


def _props_input_neutro(base: str = 'outlined dense') -> str:
    return f"{base} autocomplete=off autocorrect=off autocapitalize=off spellcheck=false"


def _coletar_pecas_widgets(peca_widgets):
    linhas = []
    for item in peca_widgets:
        referencia = str(item['ref'].value or '').strip().upper()
        descricao = str(item['desc'].value or '').strip().upper()
        quantidade = str(item['qtd'].value or '').strip()
        unidade = str(item['unidade'].value or 'UN').strip().upper()
        if not referencia and not descricao and not quantidade:
            continue
        linhas.append({
            'referencia': referencia,
            'descricao': descricao,
            'quantidade': quantidade,
            'unidade': unidade,
        })
    return _normalizar_pecas_linhas(linhas)


def _copiar_pecas_lista(linhas):
    return [
        {
            'referencia': str(item.get('referencia', '') or ''),
            'descricao': str(item.get('descricao', '') or ''),
            'quantidade': item.get('quantidade', ''),
            'unidade': str(item.get('unidade', 'UN') or 'UN'),
        }
        for item in (linhas or [])
    ]


def equipamentos_page():
    if not can_access_route('/equipamentos'):
        ui.notify('Você não possui permissão para acessar esta tela.', type='negative')
        ui.navigate.to('/home')
        return
    pode_criar = can_create('/equipamentos')
    editavel = can_edit('/equipamentos')
    pode_excluir = can_delete('/equipamentos')
    pode_exportar = can_export('/equipamentos')
    ui.add_head_html(BUSCA_NEUTRA_CSS)
    with ui.row().classes('fsl-app-shell w-full h-screen no-wrap bg-slate-100'):
        build_menu('/equipamentos')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-0 overflow-hidden'):
                with ui.row().classes('w-full items-center justify-between px-5 py-4 bg-slate-50').style(
                    'border-bottom: 1px solid #e5e7eb;'
                ):
                    with ui.column().classes('gap-0'):
                        ui.label('EQUIPAMENTOS').classes('text-xl font-bold text-slate-800')
                        ui.label('CADASTRO DE LOCAL, EQUIPAMENTO E COMPONENTE').classes('text-xs text-slate-500')

                    with ui.row().classes('items-center gap-2'):
                        busca = ui.input(
                            placeholder='BUSCAR TAG, DESCRIÇÃO, TIPO, MODELO, FABRICANTE...'
                        ).props('outlined dense clearable').classes('w-[430px] busca-neutra')
                        btn_buscar = ui.button(icon='search').props(f"flat round dense {loader_props('CARREGANDO EQUIPAMENTOS...')}").classes('shrink-0 self-center')

                        btn_exportar = ui.button('EXPORTAR XLSX', icon='download').props('unelevated').classes(
                            'bg-emerald-600 text-white font-bold'
                        )
                        btn_novo = ui.button('NOVO', icon='add').props(f"unelevated {loader_props('ABRINDO FORMULÁRIO...')}").classes(
                            'bg-amber-500 text-black font-bold'
                        )
                        btn_refresh = ui.button(icon='refresh').props(f"flat round dense {loader_props('ATUALIZANDO EQUIPAMENTOS...')}")

                area_tabela = ui.column().classes('w-full p-4 gap-3 overflow-auto')

    def _set_loading(ativo: bool, texto: str = 'CARREGANDO...'):
        if ativo:
            show_page_loader(texto)
        else:
            hide_page_loader()

    def _run_busy(fn, texto: str = 'CARREGANDO...'):
        _set_loading(True, texto)
        try:
            return fn()
        finally:
            _set_loading(False)


    def carregar_linhas():
        termo = str(busca.value or '').strip()
        try:
            dados = get_ativos(termo)
        except TypeError:
            dados = get_ativos()
            termo_norm = _normalizar_busca(termo)
            if termo_norm:
                filtrados = []
                for row in dados:
                    status_txt, _ = _status_visual(row)
                    texto = ' '.join([
                        str(row.get('tipo') or ''),
                        str(row.get('tag') or ''),
                        str(row.get('tag_base') or ''),
                        str(row.get('descricao') or ''),
                        str(row.get('observacoes') or ''),
                        str(row.get('fabricante') or ''),
                        str(row.get('modelo') or ''),
                        str(row.get('numero_serie') or ''),
                        status_txt,
                    ])
                    if termo_norm in _normalizar_busca(texto):
                        filtrados.append(row)
                dados = filtrados

        dados.sort(key=lambda x: (str(x.get('tipo') or ''), str(x.get('tag') or '')))
        return dados

    def exportar_xlsx():
        try:
            dados = carregar_linhas()

            wb = Workbook()
            ws = wb.active
            ws.title = 'EQUIPAMENTOS'
            ws.append([
                'ID',
                'TIPO',
                'ATIVO',
                'TAG',
                'TAG_BASE',
                'DESCRICAO',
                'PAI_ID',
                'PAI_TAG',
                'CRITICIDADE',
                'FABRICANTE',
                'MODELO',
                'NUMERO_SERIE',
                'ANO_FABRICACAO',
                'OBSERVACOES',
                'PECAS_ATIVAS',
            ])

            for row in dados:
                pai_tag = str(row.get('parent_tag') or '')

                tipo_row = str(row.get('tipo') or '').upper()
                ativo_export = 'SIM' if (tipo_row == 'EQUIPAMENTO' and bool(row.get('ativo', 1))) else ('NAO' if tipo_row == 'EQUIPAMENTO' else '-')

                ws.append([
                    row.get('id'),
                    row.get('tipo'),
                    ativo_export,
                    row.get('tag'),
                    row.get('tag_base'),
                    row.get('descricao'),
                    row.get('parent_id'),
                    pai_tag,
                    row.get('criticidade'),
                    row.get('fabricante'),
                    row.get('modelo'),
                    row.get('numero_serie'),
                    row.get('ano_fabricacao'),
                    row.get('observacoes'),
                    'SIM' if row.get('pecas_ativas') else 'NAO',
                ])

            arquivo = Path(tempfile.gettempdir()) / f'equipamentos_{uuid.uuid4().hex[:8]}.xlsx'
            wb.save(arquivo)
            ui.download(str(arquivo))
            ui.notify('XLSX GERADO COM SUCESSO', type='positive')
        except Exception as ex:
            ui.notify(str(ex), type='negative')

    def render_tabela():
        area_tabela.clear()
        linhas = carregar_linhas()

        with area_tabela:
            if not linhas:
                with ui.card().classes('w-full rounded-2xl shadow-none bg-slate-50 border border-slate-200 p-5'):
                    ui.label('NENHUM REGISTRO ENCONTRADO.').classes('text-sm font-medium text-slate-600')
                return

            with ui.row().classes('w-full px-3 py-2 bg-slate-200 rounded-xl items-center font-bold text-slate-700'):
                ui.label('TIPO').classes('w-28 shrink-0')
                ui.label('STATUS').classes('w-24 shrink-0')
                ui.label('TAG').classes('w-56 shrink-0')
                ui.label('DESCRIÇÃO').classes('flex-1')
                ui.label('PAI').classes('w-52 shrink-0')
                ui.label('AÇÕES').classes('w-36 text-right shrink-0')

            for row in linhas:
                pai_tag = str(row.get('parent_tag') or '-')

                status_txt, status_cls = _status_visual(row)

                with ui.row().classes('w-full px-3 py-3 bg-white rounded-xl items-center border border-slate-200'):
                    ui.label(str(row.get('tipo') or '-')).classes('w-28 shrink-0 text-sm font-medium text-slate-700')

                    with ui.row().classes('w-24 shrink-0'):
                        ui.label(status_txt).classes(f'text-[11px] px-2 py-1 rounded-full font-bold {status_cls}')

                    ui.label(str(row.get('tag') or '-')).classes(
                        'w-56 shrink-0 text-sm font-bold text-slate-800'
                    ).style('white-space: normal; word-break: break-word; overflow-wrap: anywhere;')

                    ui.label(str(row.get('descricao') or '-')).classes(
                        'flex-1 text-sm text-slate-700'
                    ).style('white-space: normal; word-break: break-word; overflow-wrap: anywhere;')

                    ui.label(pai_tag).classes(
                        'w-52 shrink-0 text-sm text-slate-600'
                    ).style('white-space: normal; word-break: break-word; overflow-wrap: anywhere;')

                    with ui.row().classes('w-36 justify-end gap-1 shrink-0'):
                        ui.button(
                            icon='edit',
                            on_click=lambda e=None, ativo_id=row['id']: abrir_formulario(ativo_id)
                        ).props('flat round dense').classes('text-slate-700')

                        ui.button(
                            icon='delete',
                            on_click=lambda e=None, ativo_id=row['id']: confirmar_exclusao(ativo_id)
                        ).props('flat round dense color=negative')

    def abrir_formulario(ativo_id=None):
        try:
            ativo = get_ativo(ativo_id) if ativo_id else None

            if ativo is not None and not hasattr(ativo, 'get'):
                try:
                    ativo = dict(ativo)
                except Exception:
                    raise Exception(f'get_ativo({ativo_id}) retornou tipo inválido: {type(ativo).__name__}')

            editando = ativo is not None
            tipo_inicial = (ativo.get('tipo') if ativo else 'LOCAL') or 'LOCAL'
            tipos_validos = ['LOCAL', 'EQUIPAMENTO', 'COMPONENTE']
            if tipo_inicial not in tipos_validos:
                raise Exception(f'TIPO INVÁLIDO PARA EDIÇÃO: {tipo_inicial}')

            parent_id_inicial = _normalizar_id(ativo.get('parent_id')) if ativo else None
            ativo_inicial = bool(ativo.get('ativo', 1)) if ativo else True

            anexos_pendentes = []
            pecas_linhas = list(ativo.get('pecas_json') or []) if ativo else []
            ativos_para_copia = []
            try:
                ativos_para_copia = [dict(item) for item in get_ativos()]
            except Exception:
                ativos_para_copia = []

            with ui.dialog() as dialog, ui.card().classes('w-[1400px] max-w-[99vw] h-[94vh] max-h-[94vh] p-0 rounded-2xl overflow-hidden'):
                with ui.row().classes('w-full items-center justify-between px-5 py-4 bg-slate-800 text-white shrink-0'):
                    with ui.row().classes('items-center gap-4'):
                        ui.image(LOGO_FSL_SRC).classes('w-14 h-14 object-contain rounded-lg bg-slate-100 p-1')
                        with ui.column().classes('gap-0'):
                            ui.label('EDITAR REGISTRO' if editando else 'NOVO REGISTRO').classes('text-lg font-bold')
                            ui.label('CADASTRO DE ATIVOS').classes('text-xs text-slate-200')
                    ui.button(icon='close', on_click=dialog.close).props('flat round dense color=white')

                with ui.row().classes('w-full flex-1 h-[calc(94vh-72px)] no-wrap overflow-hidden'):

                    with ui.column().classes('w-[78%] h-full overflow-hidden border-r border-slate-200'):
                        with ui.column().classes('flex-1 w-full items-stretch p-6 gap-4 overflow-auto'):

                            tipo = ui.select(
                                ['LOCAL', 'EQUIPAMENTO', 'COMPONENTE'],
                                value=tipo_inicial,
                                label='TIPO',
                            ).props(_props_input_neutro()).classes('w-full busca-neutra')
                            if editando:
                                tipo.disable()

                            ativo_toggle = ui.switch(
                                'ATIVO',
                                value=ativo_inicial,
                            )

                            tag_base = ui.input(
                                label='TAG BASE',
                                value=ativo.get('tag_base') if ativo else '',
                            ).props(_props_input_neutro()).classes('w-full busca-neutra')

                            descricao = ui.input(
                                label='DESCRIÇÃO',
                                value=ativo.get('descricao') if ativo else '',
                            ).props(_props_input_neutro()).classes('w-full busca-neutra')

                            pai_select = ui.select(
                                options={},
                                label='PAI',
                                value=None,
                            ).props('outlined dense clearable').classes('w-full')

                            criticidade = ui.number(
                                label='CRITICIDADE',
                                value=ativo.get('criticidade') if ativo else None,
                                min=1,
                                max=10,
                                step=1,
                            ).props(_props_input_neutro()).classes('w-full busca-neutra')

                            fabricante = ui.input(
                                label='FABRICANTE',
                                value=ativo.get('fabricante') if ativo else '',
                            ).props(_props_input_neutro()).classes('w-full busca-neutra')

                            modelo = ui.input(
                                label='MODELO',
                                value=ativo.get('modelo') if ativo else '',
                            ).props(_props_input_neutro()).classes('w-full busca-neutra')

                            numero_serie = ui.input(
                                label='NÚMERO DE SÉRIE',
                                value=ativo.get('numero_serie') if ativo else '',
                            ).props(_props_input_neutro()).classes('w-full busca-neutra')

                            ano_fabricacao = ui.number(
                                label='ANO DE FABRICAÇÃO',
                                value=ativo.get('ano_fabricacao') if ativo else None,
                                min=1900,
                                max=2100,
                                step=1,
                            ).props(_props_input_neutro()).classes('w-full busca-neutra')

                            observacoes = ui.textarea(
                                label='OBSERVAÇÕES',
                                value=ativo.get('observacoes') if ativo else '',
                            ).props('outlined autogrow autocomplete=off autocorrect=off autocapitalize=off spellcheck=false').classes('w-full busca-neutra')

                            pecas_toggle = ui.switch(
                                'VINCULAR PEÇAS NESTE ATIVO',
                                value=bool(ativo.get('pecas_ativas')) if ativo else False,
                            )

                            pecas_box = ui.column().classes('w-full gap-2')
                            pecas_origem_box = ui.column().classes('w-full gap-2')
                            pecas_lista = ui.column().classes('w-full gap-2')
                            peca_widgets = []
                            copiar_pecas_origem = ui.select(
                                options={},
                                label='COPIAR PEÇAS DE',
                                value=None,
                            ).props('outlined dense clearable options-dense').classes('w-full busca-neutra')

                            def _opcoes_copia_pecas():
                                tipo_atual = str(tipo.value or '').upper()
                                opcoes = {}
                                if tipo_atual not in ('EQUIPAMENTO', 'COMPONENTE'):
                                    return opcoes
                                for item in ativos_para_copia:
                                    if str(item.get('tipo') or '').upper() != tipo_atual:
                                        continue
                                    if editando and str(item.get('id')) == str(ativo.get('id')):
                                        continue
                                    pecas_item = list(item.get('pecas_json') or [])
                                    if not pecas_item:
                                        continue
                                    rotulo = f"{str(item.get('tag') or '-') } - {str(item.get('descricao') or '-') }"
                                    opcoes[str(item.get('id'))] = rotulo
                                return opcoes

                            def atualizar_opcoes_copia_pecas():
                                opcoes = _opcoes_copia_pecas()
                                copiar_pecas_origem.options = opcoes
                                valor_atual = _normalizar_id(copiar_pecas_origem.value)
                                copiar_pecas_origem.set_value(valor_atual if valor_atual in opcoes else None)
                                copiar_pecas_origem.update()

                            def copiar_pecas_do_origem(modo='substituir'):
                                origem_id = _normalizar_id(copiar_pecas_origem.value)
                                if not origem_id:
                                    ui.notify('SELECIONE O ATIVO DE ORIGEM PARA COPIAR AS PEÇAS', type='warning')
                                    return
                                origem = next((item for item in ativos_para_copia if str(item.get('id')) == str(origem_id)), None)
                                if not origem:
                                    ui.notify('ATIVO DE ORIGEM NÃO ENCONTRADO', type='negative')
                                    return
                                pecas_origem = _copiar_pecas_lista(origem.get('pecas_json') or [])
                                if not pecas_origem:
                                    ui.notify('O ATIVO SELECIONADO NÃO POSSUI PEÇAS CADASTRADAS', type='warning')
                                    return
                                if modo == 'somar':
                                    pecas_linhas.extend(pecas_origem)
                                    ui.notify('PEÇAS ADICIONADAS COM SUCESSO', type='positive')
                                else:
                                    pecas_linhas.clear()
                                    pecas_linhas.extend(pecas_origem)
                                    ui.notify('PEÇAS COPIADAS COM SUCESSO', type='positive')
                                render_pecas()

                            def remover_peca(index):
                                if 0 <= index < len(pecas_linhas):
                                    pecas_linhas.pop(index)
                                render_pecas()

                            def render_pecas():
                                nonlocal peca_widgets
                                pecas_lista.clear()
                                peca_widgets = []
                                with pecas_lista:
                                    if not pecas_linhas:
                                        with ui.card().classes('w-full shadow-none bg-slate-50 border border-slate-200 p-3'):
                                            ui.label('NENHUMA PEÇA VINCULADA.').classes('text-sm text-slate-500')
                                    else:
                                        for idx, item in enumerate(pecas_linhas):
                                            with ui.row().classes('w-full items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl p-3 no-wrap'):
                                                ref = ui.input(
                                                    label='REFERÊNCIA',
                                                    value=item.get('referencia', ''),
                                                ).props(_props_input_neutro()).classes('w-[220px] shrink-0 busca-neutra').style('text-transform: uppercase;')

                                                desc = ui.input(
                                                    label='DESCRIÇÃO',
                                                    value=item.get('descricao', ''),
                                                ).props(_props_input_neutro()).classes('flex-1 min-w-0 busca-neutra').style('text-transform: uppercase;')

                                                qtd = ui.input(
                                                    label='QTD',
                                                    value=str(item.get('quantidade', '')),
                                                ).props(_props_input_neutro()).classes('w-28 shrink-0 busca-neutra')

                                                unidade = ui.select(
                                                    UNIDADES_PECA,
                                                    value=item.get('unidade', 'UN') or 'UN',
                                                    label='UNIDADE',
                                                ).props('outlined dense options-dense').classes('w-28 shrink-0 busca-neutra')

                                                ui.button(
                                                    icon='close',
                                                    on_click=lambda e=None, i=idx: remover_peca(i)
                                                ).props('flat round dense color=negative').classes('shrink-0')

                                                peca_widgets.append({
                                                    'ref': ref,
                                                    'desc': desc,
                                                    'qtd': qtd,
                                                    'unidade': unidade,
                                                })

                            with pecas_box:
                                with pecas_origem_box:
                                    ui.label('COPIAR PEÇAS DE OUTRO ATIVO').classes('text-sm font-bold text-slate-700')
                                    with ui.row().classes('w-full items-end gap-2 no-wrap'):
                                        copiar_pecas_origem.classes('flex-1')
                                        ui.button(
                                            'SUBSTITUIR',
                                            icon='content_copy',
                                            on_click=lambda: copiar_pecas_do_origem('substituir')
                                        ).props('unelevated').classes('bg-slate-700 text-white font-bold shrink-0')
                                        ui.button(
                                            'SOMAR',
                                            icon='library_add',
                                            on_click=lambda: copiar_pecas_do_origem('somar')
                                        ).props('unelevated').classes('bg-slate-200 text-slate-800 font-bold shrink-0')

                                ui.button(
                                    'ADICIONAR PEÇA',
                                    icon='add',
                                    on_click=lambda: (
                                        pecas_linhas.append({
                                            'referencia': '',
                                            'descricao': '',
                                            'quantidade': '',
                                            'unidade': 'UN',
                                        }),
                                        render_pecas()
                                    )
                                ).props('flat').classes('self-start')

                                render_pecas()
                                atualizar_opcoes_copia_pecas()

                        with ui.row().classes('w-full justify-end gap-3 p-5 bg-white shrink-0').style('border-top: 1px solid #e5e7eb;'):
                            ui.button('CANCELAR', on_click=dialog.close).props('flat')
                            ui.button('SALVAR', on_click=lambda: salvar()).props('unelevated').classes(
                                'bg-amber-500 text-black font-bold px-6'
                            )

                    with ui.column().classes('w-[25%] h-full p-5 gap-4 bg-slate-50 overflow-auto'):
                        ui.label('ANEXOS').classes('text-lg font-bold text-slate-800')
                        ui.label('FOTOS E PDFS').classes('text-xs text-slate-500')
                        anexos_container = ui.column().classes('w-full gap-2')

                        def recarregar_pais():
                            tipo_atual = str(tipo.value or '').upper()
                            pais = listar_pais_possiveis(
                                tipo_atual,
                                ignorar_id=ativo['id'] if editando else None,
                            )
                            opcoes = {_normalizar_id(item['id']): _label_pai(item) for item in pais}
                            pai_select.options = opcoes

                            if tipo_atual == 'COMPONENTE':
                                pai_select.label = 'EQUIPAMENTO PAI'
                            elif tipo_atual == 'EQUIPAMENTO':
                                pai_select.label = 'LOCAL PAI'
                            else:
                                pai_select.label = 'PAI'

                            valor_destino = None
                            if editando and parent_id_inicial in opcoes:
                                valor_destino = parent_id_inicial
                            elif not editando and _normalizar_id(pai_select.value) in opcoes:
                                valor_destino = _normalizar_id(pai_select.value)

                            pai_select.set_value(valor_destino)
                            pai_select.update()

                        def ajustar_campos_por_tipo():
                            tipo_atual = str(tipo.value or '').upper()

                            ativo_toggle.set_visibility(tipo_atual == 'EQUIPAMENTO')

                            if tipo_atual == 'LOCAL':
                                pai_select.set_visibility(True)
                                criticidade.set_visibility(False)
                                fabricante.set_visibility(False)
                                modelo.set_visibility(False)
                                numero_serie.set_visibility(False)
                                ano_fabricacao.set_visibility(False)
                                pecas_toggle.set_visibility(False)
                                pecas_box.set_visibility(False)
                                pecas_origem_box.set_visibility(False)

                            elif tipo_atual == 'EQUIPAMENTO':
                                pai_select.set_visibility(True)
                                criticidade.set_visibility(bool(ENABLE_CRITICIDADE))
                                fabricante.set_visibility(True)
                                modelo.set_visibility(True)
                                numero_serie.set_visibility(True)
                                ano_fabricacao.set_visibility(True)
                                pecas_toggle.set_visibility(True)
                                pecas_box.set_visibility(bool(pecas_toggle.value))
                                pecas_origem_box.set_visibility(bool(pecas_toggle.value))

                            elif tipo_atual == 'COMPONENTE':
                                pai_select.set_visibility(True)
                                criticidade.set_visibility(bool(ENABLE_CRITICIDADE))
                                fabricante.set_visibility(True)
                                modelo.set_visibility(True)
                                numero_serie.set_visibility(True)
                                ano_fabricacao.set_visibility(False)
                                pecas_toggle.set_visibility(True)
                                pecas_box.set_visibility(bool(pecas_toggle.value))
                                pecas_origem_box.set_visibility(bool(pecas_toggle.value))

                            recarregar_pais()
                            atualizar_opcoes_copia_pecas()

                        tipo.on('update:model-value', lambda e: ajustar_campos_por_tipo())
                        pecas_toggle.on('update:model-value', lambda e: (pecas_box.set_visibility(bool(pecas_toggle.value)), pecas_origem_box.set_visibility(bool(pecas_toggle.value)), atualizar_opcoes_copia_pecas()))

                        def salvar():
                            try:
                                parent_id_salvar = _normalizar_id(pai_select.value)
                                tipo_salvar = str(tipo.value or '').upper()

                                payload = {
                                    'tag_base': tag_base.value,
                                    'descricao': descricao.value,
                                    'tipo': tipo_salvar,
                                    'parent_id': parent_id_salvar,
                                    'criticidade': int(criticidade.value) if criticidade.visible and criticidade.value else None,
                                    'observacoes': observacoes.value,
                                    'fabricante': fabricante.value,
                                    'modelo': modelo.value,
                                    'numero_serie': numero_serie.value,
                                    'ano_fabricacao': int(ano_fabricacao.value) if ano_fabricacao.visible and ano_fabricacao.value else None,
                                    'ativo': bool(ativo_toggle.value) if tipo_salvar == 'EQUIPAMENTO' else True,
                                    'pecas_ativas': bool(tipo_salvar in ('EQUIPAMENTO', 'COMPONENTE') and pecas_toggle.value),
                                    'pecas_json': _coletar_pecas_widgets(peca_widgets),
                                }

                                if editando:
                                    atualizar_ativo(
                                        ativo_id=ativo['id'],
                                        tag_base=payload['tag_base'],
                                        descricao=payload['descricao'],
                                        parent_id=payload['parent_id'],
                                        criticidade=payload['criticidade'],
                                        observacoes=payload['observacoes'],
                                        fabricante=payload['fabricante'],
                                        modelo=payload['modelo'],
                                        numero_serie=payload['numero_serie'],
                                        ano_fabricacao=payload['ano_fabricacao'],
                                        ativo=payload['ativo'],
                                        pecas_ativas=payload['pecas_ativas'],
                                        pecas_json=payload['pecas_json'],
                                    )
                                    ui.notify('REGISTRO ATUALIZADO COM SUCESSO', type='positive')
                                    dialog.close()
                                    ui.timer(0.15, lambda: _run_busy(render_tabela, 'CARREGANDO EQUIPAMENTOS...'), once=True)
                                    return

                                novo = criar_ativo(
                                    tag_base=payload['tag_base'],
                                    descricao=payload['descricao'],
                                    tipo=payload['tipo'],
                                    parent_id=payload['parent_id'],
                                    criticidade=payload['criticidade'],
                                    observacoes=payload['observacoes'],
                                    fabricante=payload['fabricante'],
                                    modelo=payload['modelo'],
                                    numero_serie=payload['numero_serie'],
                                    ano_fabricacao=payload['ano_fabricacao'],
                                    ativo=payload['ativo'],
                                    pecas_ativas=payload['pecas_ativas'],
                                    pecas_json=payload['pecas_json'],
                                )

                                for item in list(anexos_pendentes):
                                    adicionar_anexo(
                                        ativo_id=novo['id'],
                                        origem_path=item['temp_path'],
                                        nome_original=item['nome_original'],
                                    )
                                    try:
                                        Path(item['temp_path']).unlink(missing_ok=True)
                                    except Exception:
                                        pass

                                ui.notify('REGISTRO CRIADO COM SUCESSO', type='positive')
                                dialog.close()
                                ui.timer(0.12, lambda: _run_busy(render_tabela, 'CARREGANDO EQUIPAMENTOS...'), once=True)

                            except Exception as ex:
                                ui.notify(str(ex), type='negative')

                        def excluir_temp(temp_id):
                            nonlocal anexos_pendentes
                            item = next((x for x in anexos_pendentes if x['temp_id'] == temp_id), None)
                            if item and item.get('temp_path'):
                                try:
                                    Path(item['temp_path']).unlink(missing_ok=True)
                                except Exception:
                                    pass
                            anexos_pendentes = [x for x in anexos_pendentes if x['temp_id'] != temp_id]
                            render_anexos()

                        def excluir_definitivo(anexo_id):
                            try:
                                remover_anexo(anexo_id)
                                ui.notify('ANEXO REMOVIDO', type='positive')
                                render_anexos()
                            except Exception as ex:
                                ui.notify(str(ex), type='negative')

                        async def upload_handler(e):
                            temp_path = None
                            try:
                                nome_arquivo = _get_upload_name(e)
                                extensao = Path(nome_arquivo).suffix or ''
                                conteudo = await e.file.read()

                                with tempfile.NamedTemporaryFile(delete=False, suffix=extensao) as temp_file:
                                    temp_file.write(conteudo)
                                    temp_path = temp_file.name

                                if editando:
                                    adicionar_anexo(
                                        ativo_id=ativo['id'],
                                        origem_path=temp_path,
                                        nome_original=nome_arquivo,
                                    )
                                    ui.notify('ANEXO ADICIONADO COM SUCESSO', type='positive')
                                else:
                                    anexos_pendentes.append({
                                        'temp_id': str(uuid.uuid4()),
                                        'temp_path': temp_path,
                                        'nome_original': nome_arquivo,
                                    })
                                    temp_path = None
                                    ui.notify('ANEXO PRONTO PARA SALVAR COM O CADASTRO', type='positive')

                                render_anexos()

                            except Exception as ex:
                                ui.notify(str(ex), type='negative')

                            finally:
                                if temp_path:
                                    try:
                                        Path(temp_path).unlink(missing_ok=True)
                                    except Exception:
                                        pass

                        def render_anexos():
                            anexos_container.clear()
                            anexos_existentes = listar_anexos(ativo['id']) if editando else []

                            with anexos_container:
                                ui.upload(
                                    label='ADICIONAR FOTO OU PDF',
                                    auto_upload=True,
                                    multiple=True,
                                    on_upload=upload_handler,
                                ).props('accept=.png,.jpg,.jpeg,.webp,.bmp,.gif,.pdf').classes('w-full')

                                if not anexos_existentes and not anexos_pendentes:
                                    with ui.card().classes('w-full shadow-none bg-white border border-slate-200 p-4'):
                                        ui.label('NENHUM ANEXO ADICIONADO.').classes('text-sm text-slate-500')
                                else:
                                    for anexo in anexos_existentes:
                                        tipo_anexo = str(anexo.get('tipo') or '').upper()
                                        with ui.row().classes('w-full items-center justify-between border border-slate-200 rounded-xl px-4 py-3 bg-white gap-3'):
                                            with ui.row().classes('items-center gap-3 min-w-0 flex-1'):
                                                if tipo_anexo == 'FOTO':
                                                    ui.image(_arquivo_url(anexo)).classes('w-16 h-16 object-contain bg-white rounded border border-slate-200 cursor-pointer').on('click', lambda e=None, url=_arquivo_url(anexo): _abrir_nova_aba(url))
                                                else:
                                                    ui.icon('picture_as_pdf').classes('text-slate-700 text-2xl shrink-0')
                                                with ui.column().classes('gap-0 min-w-0 flex-1'):
                                                    nome_label = ui.link(_nome_truncado(anexo.get('nome_original') or '', 26), _arquivo_url(anexo), new_tab=True).classes('text-sm font-medium text-slate-800 max-w-[170px] block')
                                                    nome_label.style('overflow:hidden;text-overflow:ellipsis;white-space:nowrap;')
                                                    ui.label('SALVO').classes('text-xs text-slate-500')
                                            with ui.row().classes('items-center gap-1 shrink-0'):
                                                ui.button(
                                                    icon='close',
                                                    on_click=lambda e=None, anexo_id=anexo['id']: excluir_definitivo(anexo_id)
                                                ).props('flat round dense color=negative')

                                    for item in anexos_pendentes:
                                        with ui.row().classes('w-full items-center justify-between border border-dashed border-slate-300 rounded-xl px-4 py-3 bg-white'):
                                            with ui.row().classes('items-center gap-3'):
                                                ui.icon('attach_file').classes('text-slate-700')
                                                with ui.column().classes('gap-0'):
                                                    ui.label(item['nome_original']).classes('text-sm font-medium text-slate-800')
                                                    ui.label('PENDENTE DE SALVAR').classes('text-xs text-amber-600')

                                            ui.button(
                                                icon='close',
                                                on_click=lambda e=None, temp_id=item['temp_id']: excluir_temp(temp_id)
                                            ).props('flat round dense color=negative')

                        ajustar_campos_por_tipo()
                        render_anexos()

            dialog.open()

        except Exception as ex:
            ui.notify(f'ERRO AO ABRIR EDIÇÃO: {ex}', type='negative')

    def confirmar_exclusao(ativo_id):
        ativo = get_ativo(ativo_id)
        if not ativo:
            ui.notify('REGISTRO NÃO ENCONTRADO', type='negative')
            return

        with ui.dialog() as dialog, ui.card().classes('w-[500px] max-w-[96vw] p-5 rounded-2xl'):
            ui.label('CONFIRMAR EXCLUSÃO').classes('text-lg font-bold text-red-700')
            ui.label(f"TAG: {ativo['tag']}").classes('text-sm font-medium')
            ui.label('SE O REGISTRO POSSUIR FILHOS, A EXCLUSÃO SERÁ BLOQUEADA.').classes('text-sm text-slate-600')

            def excluir():
                try:
                    excluir_ativo(ativo_id)
                    dialog.close()
                    ui.notify('REGISTRO EXCLUÍDO COM SUCESSO', type='positive')
                    render_tabela()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')

            with ui.row().classes('w-full justify-end gap-2 pt-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=excluir).props('unelevated').classes('bg-red-600 text-white')

        dialog.open()

    def executar_pesquisa():
        _run_busy(render_tabela, 'CARREGANDO EQUIPAMENTOS...')

    busca.on('keydown.enter', lambda e: executar_pesquisa())
    busca.on('update:model-value', lambda e: render_tabela() if not str(getattr(busca, 'value', '') or '').strip() else None)
    busca.on('focus', lambda e: ui.run_javascript("setTimeout(() => { const el = document.activeElement; if (el && typeof el.setSelectionRange === 'function') { const pos = String(el.value || '').length; el.setSelectionRange(pos, pos); } }, 0)"))
    btn_buscar.on_click(executar_pesquisa)
    btn_novo.on_click(lambda: abrir_formulario())
    btn_refresh.on_click(lambda: _run_busy(render_tabela, 'ATUALIZANDO EQUIPAMENTOS...'))
    btn_exportar.on_click(exportar_xlsx)

    render_tabela()
    ui.timer(0.25, hide_page_loader, once=True)
