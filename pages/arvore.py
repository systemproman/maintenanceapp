from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

from nicegui import ui

from components.menu import build_menu, show_page_loader, hide_page_loader
from config.settings import ENABLE_CRITICIDADE
from services.db import get_ativos, get_ativo, listar_anexos, listar_os, listar_os_por_ativo

INDENT = 22

BUSCA_NEUTRA_CSS = '''
<style>
.busca-neutra .q-field__native,
.busca-neutra input {
    color: #0f172a !important;
    caret-color: #0f172a !important;
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


def normalizar_texto(valor) -> str:
    import unicodedata
    texto = str(valor or '').strip().lower()
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')




def nome_truncado(nome: str, limite: int = 24) -> str:
    nome = str(nome or '')
    return nome if len(nome) <= limite else nome[:limite - 3] + '...'


def arquivo_url(anexo: dict) -> str:
    nome = quote(str(anexo.get('nome_original') or anexo.get('nome_salvo') or 'arquivo'))
    return f"/arquivo/{anexo['id']}/{nome}"


def download_url(anexo: dict) -> str:
    nome = quote(str(anexo.get('nome_original') or anexo.get('nome_salvo') or 'arquivo'))
    return f"/download/{anexo['id']}/{nome}"


def abrir_nova_aba(url: str):
    ui.run_javascript(f"window.open('{url}', '_blank')")


def build_tree(data, mostrar_pecas: bool = True):
    nodes = {}
    roots = []

    for item in data:
        item_id = str(item['id'])
        parent_id = str(item['parent_id']) if item.get('parent_id') else None
        tag = str(item.get('tag') or '')
        descricao = str(item.get('descricao') or '')
        tipo = str(item.get('tipo') or '')

        nodes[item_id] = {
            'id': item_id,
            'tag': tag,
            'descricao': descricao,
            'tipo': tipo,
            'label': f'{tag} - {descricao}',
            'children': [],
            '_parent_id': parent_id,
            'matched': False,
        }

    for _, node in nodes.items():
        parent_id = node['_parent_id']
        if parent_id and parent_id in nodes:
            nodes[parent_id]['children'].append(node)
        else:
            roots.append(node)

    if mostrar_pecas:
        for item in data:
            tipo = str(item.get('tipo') or '').upper()
            if tipo not in ('EQUIPAMENTO', 'COMPONENTE') or not item.get('pecas_ativas'):
                continue
            pecas = item.get('pecas_json') or []
            for idx, peca in enumerate(pecas):
                referencia = str(peca.get('referencia') or '').strip()
                descricao = str(peca.get('descricao') or '').strip()
                quantidade = peca.get('quantidade') or 0
                piece_id = f"peca::{item['id']}::{idx}"
                nodes[str(item['id'])]['children'].append({
                    'id': piece_id,
                    'tag': referencia,
                    'descricao': descricao,
                    'tipo': 'PECA',
                    'label': f"{referencia or 'PEÇA'} - {descricao or '-'}",
                    'children': [],
                    'matched': False,
                    'quantidade': quantidade,
                    'origem_ativo_id': str(item['id']),
                })

    def limpar_aux(no):
        no.pop('_parent_id', None)
        for filho in no['children']:
            limpar_aux(filho)

    for raiz in roots:
        limpar_aux(raiz)

    return roots


def clone_node(node):
    return {
        'id': node['id'],
        'tag': node.get('tag', ''),
        'descricao': node.get('descricao', ''),
        'tipo': node.get('tipo', ''),
        'label': node['label'],
        'matched': node.get('matched', False),
        'children': [clone_node(f) for f in node.get('children', [])],
    }


def filtrar_tree_preservando_descendentes(nodes, termo=''):
    termo = normalizar_texto(termo)

    if not termo:
        return deepcopy(nodes), set()

    expandir_ids = set()

    def filtrar_no(no):
        texto_base = ' '.join([
            normalizar_texto(no.get('tag')),
            normalizar_texto(no.get('descricao')),
            normalizar_texto(no.get('tipo')),
            normalizar_texto(no.get('label')),
        ])

        bateu_no = termo in texto_base
        filhos_filtrados = []

        for filho in no.get('children', []):
            filtrado = filtrar_no(filho)
            if filtrado is not None:
                filhos_filtrados.append(filtrado)

        if bateu_no:
            no_completo = clone_node(no)
            no_completo['matched'] = True
            return no_completo

        if filhos_filtrados:
            expandir_ids.add(no['id'])
            return {
                'id': no['id'],
                'tag': no.get('tag', ''),
                'descricao': no.get('descricao', ''),
                'tipo': no.get('tipo', ''),
                'label': no['label'],
                'matched': False,
                'children': filhos_filtrados,
            }

        return None

    resultado = []
    for raiz in nodes:
        filtrado = filtrar_no(raiz)
        if filtrado is not None:
            resultado.append(filtrado)

    return resultado, expandir_ids


def coletar_ids_com_filhos(nodes):
    ids = set()

    def percorrer(lista):
        for no in lista:
            if no.get('children'):
                ids.add(no['id'])
                percorrer(no['children'])

    percorrer(nodes)
    return ids


def icone_tipo(tipo: str) -> str:
    return {
        'LOCAL': 'apartment',
        'EQUIPAMENTO': 'precision_manufacturing',
        'COMPONENTE': 'settings',
        'PECA': 'inventory_2',
    }.get((tipo or '').upper(), 'device_unknown')




def _tem_os_aberta(item: dict) -> bool:
    for chave in ('os_aberta', 'tem_os_aberta', 'possui_os_aberta', 'com_os_aberta'):
        if bool(item.get(chave)):
            return True
    for chave in ('qtd_os_abertas', 'os_abertas', 'os_abertas_count', 'quantidade_os_abertas'):
        try:
            if int(item.get(chave) or 0) > 0:
                return True
        except Exception:
            pass
    return False


def _preparar_status_mapa(data):
    filhos_por_pai = {}
    por_id = {}
    for item in data:
        iid = str(item['id'])
        por_id[iid] = item
        pid = str(item.get('parent_id')) if item.get('parent_id') else None
        if pid:
            filhos_por_pai.setdefault(pid, []).append(iid)

    status = {}

    def resolver(iid: str):
        if iid in status:
            return status[iid]
        item = por_id[iid]
        if not bool(item.get('ativo', 1)):
            status[iid] = 'red'
            return 'red'
        amarelo = _tem_os_aberta(item)
        for filho_id in filhos_por_pai.get(iid, []):
            if resolver(filho_id) == 'yellow':
                amarelo = True
        status[iid] = 'yellow' if amarelo else 'green'
        return status[iid]

    for iid in por_id:
        resolver(iid)
    return status


def _badge_status_style(tipo: str, status_cor: str) -> str:
    return 'text-[11px] px-2 py-1 rounded-full shrink-0 font-medium'


def _badge_status_inline_style(tipo: str, status_cor: str) -> str:
    tipo = (tipo or '').upper()
    if status_cor == 'red':
        bg, fg = '#fee2e2', '#b91c1c'
    elif status_cor == 'yellow':
        bg, fg = '#fef3c7', '#b45309'
    else:
        bg, fg = '#dcfce7', '#166534'
    return f'background:{bg}; color:{fg};'


def _os_status_badge_style(status: str) -> str:
    status = str(status or '').upper()
    if status == 'ABERTA':
        return 'background:#dbeafe; color:#1d4ed8;'
    if status == 'EM EXECUÇÃO':
        return 'background:#fef3c7; color:#b45309;'
    if status in ('ENCERRADA', 'CONCLUÍDA'):
        return 'background:#dcfce7; color:#166534;'
    return 'background:#e2e8f0; color:#334155;'

def arvore_page():
    show_page_loader('CARREGANDO PÁGINA...')
    ui.add_head_html(BUSCA_NEUTRA_CSS)

    estado = {
        'tree_data_atual': [],
        'arvore_completa': [],
        'expanded_ids': set(),
        'selected_id': None,
        'status_por_id': {},
        'pecas_por_id': {},
        'mostrar_pecas': False,
    }

    galeria = {'anexos': [], 'indice': 0, 'zoom': 100}

    with ui.dialog() as dialog_img, ui.card().classes('w-[92vw] max-w-[1500px] h-[92vh] bg-black text-white rounded-2xl overflow-hidden p-0'):
        with ui.row().classes('w-full items-center justify-between px-4 py-3 bg-black/70 shrink-0'):
            titulo_imagem = ui.label('IMAGEM').classes('text-sm font-medium max-w-[70vw] truncate')
            with ui.row().classes('items-center gap-2'):
                ui.button(icon='remove', on_click=lambda: ajustar_zoom(-10)).props('flat round color=white')
                ui.button(icon='add', on_click=lambda: ajustar_zoom(10)).props('flat round color=white')
                ui.button(icon='download', on_click=lambda: abrir_nova_aba(download_url(galeria['anexos'][galeria['indice']])) if galeria['anexos'] else None).props('flat round color=white')
                ui.button(icon='close', on_click=dialog_img.close).props('flat round color=white')
        with ui.row().classes('w-full flex-1 items-center justify-between px-2'):
            ui.button(icon='chevron_left', on_click=lambda: navegar_imagem(-1)).props('flat round color=white').classes('text-2xl')
            imagem_grande = ui.image().classes('max-w-[82vw] max-h-[76vh] object-contain')
            ui.button(icon='chevron_right', on_click=lambda: navegar_imagem(1)).props('flat round color=white').classes('text-2xl')

    def atualizar_galeria():
        if not galeria['anexos']:
            return
        anexo = galeria['anexos'][galeria['indice']]
        imagem_grande.set_source(arquivo_url(anexo))
        imagem_grande.style(f"max-width: min(92vw, {galeria['zoom']}%); max-height: min(78vh, {galeria['zoom']}%);")
        titulo_imagem.set_text(str(anexo.get('nome_original') or 'IMAGEM'))

    def abrir_galeria(anexos_foto, indice):
        galeria['anexos'] = list(anexos_foto)
        galeria['indice'] = indice
        galeria['zoom'] = 100
        atualizar_galeria()
        dialog_img.open()

    def navegar_imagem(direcao):
        if not galeria['anexos']:
            return
        galeria['indice'] = (galeria['indice'] + direcao) % len(galeria['anexos'])
        galeria['zoom'] = 100
        atualizar_galeria()

    def ajustar_zoom(delta):
        galeria['zoom'] = max(30, min(250, galeria['zoom'] + delta))
        atualizar_galeria()

    with ui.row().classes('w-full h-screen no-wrap bg-slate-100'):
        build_menu('/arvore')
        with ui.column().classes('flex-1 h-full p-4 overflow-hidden'):
            with ui.splitter(value=42).classes('w-full h-full') as splitter:
                with splitter.before:
                    with ui.column().classes('w-full h-full pr-2'):
                        with ui.card().classes('w-full h-full rounded-2xl shadow-sm border-0 bg-white p-0 overflow-hidden'):
                            with ui.row().classes('w-full items-center justify-between px-4 py-4 bg-slate-50 shrink-0').style(
                                'border-bottom: 1px solid #e5e7eb;'
                            ):
                                with ui.column().classes('gap-0'):
                                    ui.label('ÁRVORE').classes('text-xl font-bold text-slate-800')
                                    ui.label('VISUALIZAÇÃO HIERÁRQUICA').classes('text-xs text-slate-500')

                            with ui.column().classes('w-full px-4 py-3 gap-2 shrink-0').style(
                                'border-bottom: 1px solid #e5e7eb;'
                            ):
                                with ui.row().classes('w-full items-center gap-2'):
                                    busca = ui.input(
                                        placeholder='BUSCAR TAG, DESCRIÇÃO OU TIPO...'
                                    ).props('outlined dense clearable').classes('flex-1 busca-neutra')
                                    btn_buscar = ui.button(icon='search').props('flat round dense').classes('shrink-0 self-center')

                                with ui.row().classes('w-full items-center justify-between'):
                                    with ui.row().classes('items-center gap-1'):
                                        btn_expandir = ui.button(icon='unfold_more').props('flat round dense').classes('shrink-0')
                                        btn_recolher = ui.button(icon='unfold_less').props('flat round dense').classes('shrink-0')
                                        btn_refresh = ui.button(icon='refresh').props('flat round dense').classes('shrink-0')

                                    toggle_pecas = ui.switch('MOSTRAR PEÇAS', value=estado['mostrar_pecas']).props('dense size=xs').classes('text-[11px] text-slate-500 scale-[0.85] origin-right')

                            area_scroll = ui.scroll_area().classes('w-full flex-1')
                            with area_scroll:
                                area_arvore = ui.column().classes('w-full px-3 py-2 gap-0').style('min-width: 0;')

                with splitter.after:
                    with ui.column().classes('w-full h-full pl-2'):
                        with ui.card().classes('w-full h-full rounded-2xl shadow-sm border-0 bg-white p-0 overflow-hidden'):
                            with ui.row().classes('w-full items-center justify-between px-5 py-4 bg-slate-50 shrink-0').style(
                                'border-bottom: 1px solid #e5e7eb;'
                            ):
                                with ui.column().classes('gap-0'):
                                    ui.label('DETALHES').classes('text-xl font-bold text-slate-800')
                                    ui.label('VISUALIZAÇÃO DO ITEM SELECIONADO').classes('text-xs text-slate-500')

                            detalhe_scroll = ui.scroll_area().classes('w-full flex-1')
                            with detalhe_scroll:
                                detalhe_container = ui.column().classes('w-full p-5 gap-4')

    def carregar_dados():
        dados = get_ativos()
        estado['status_por_id'] = _preparar_status_mapa(dados)
        pecas_por_id = {}
        for item in dados:
            tipo = str(item.get('tipo') or '').upper()
            if tipo in ('EQUIPAMENTO', 'COMPONENTE') and item.get('pecas_ativas'):
                for idx, peca in enumerate(item.get('pecas_json') or []):
                    pecas_por_id[f"peca::{item['id']}::{idx}"] = {
                        'origem_ativo_id': str(item['id']),
                        'tag': str(peca.get('referencia') or '').strip(),
                        'descricao': str(peca.get('descricao') or '').strip(),
                        'quantidade': peca.get('quantidade') or 0,
                        'unidade': str(peca.get('unidade') or 'UN').strip(),
                    }
        estado['pecas_por_id'] = pecas_por_id
        estado['arvore_completa'] = build_tree(dados, mostrar_pecas=estado['mostrar_pecas'])
        estado['tree_data_atual'] = deepcopy(estado['arvore_completa'])

    def render_tree():
        area_arvore.clear()

        if not estado['tree_data_atual']:
            with area_arvore:
                with ui.card().classes('w-full rounded-xl shadow-none bg-slate-50 border border-slate-200 p-4'):
                    ui.label('NENHUM RESULTADO ENCONTRADO.').classes('text-sm font-medium text-slate-600')
            return

        def toggle_node(node_id: str):
            if node_id in estado['expanded_ids']:
                estado['expanded_ids'].remove(node_id)
            else:
                estado['expanded_ids'].add(node_id)
            render_tree()

        def selecionar_no(node_id: str):
            estado['selected_id'] = node_id
            render_tree()
            render_detalhes()

        def render_node(node, nivel=0):
            tem_filhos = bool(node.get('children'))
            matched = bool(node.get('matched', False))
            selecionado = estado['selected_id'] == node['id']

            fundo = '#fff7d6' if matched else ('#e2e8f0' if selecionado else 'transparent')
            borda = '#eab308' if matched else ('#94a3b8' if selecionado else 'transparent')

            with area_arvore:
                with ui.row().classes('w-full').style(
                    f'min-height: 38px; padding-left: {nivel * INDENT}px; min-width: 0;'
                ):
                    with ui.row().classes('items-start gap-1 w-full').style('min-height: 38px; min-width: 0;'):
                        with ui.element('div').style(
                            'width: 32px; min-width: 32px; display:flex; align-items:center; justify-content:center; padding-top: 4px;'
                        ):
                            if tem_filhos:
                                simbolo = 'remove' if node['id'] in estado['expanded_ids'] else 'add'
                                ui.button(
                                    icon=simbolo,
                                    on_click=lambda e=None, nid=node['id']: toggle_node(nid)
                                ).props('flat round dense').classes('min-w-0 w-7 h-7 text-slate-700')
                            else:
                                ui.html('<div style="width:28px;height:28px;"></div>')

                        with ui.row().classes('items-start gap-2 cursor-pointer flex-1').style(
                            f'background: {fundo}; border: 1px solid {borda}; border-radius: 10px; padding: 7px 10px; min-height: 34px; min-width: 0;'
                        ).on('click', lambda e=None, nid=node['id']: selecionar_no(nid)):
                            ui.icon(icone_tipo(node['tipo'])).classes('text-slate-700 shrink-0 mt-[2px]')
                            with ui.column().classes('flex-1 gap-1').style('min-width: 0;'):
                                ui.label(node['label']).classes('text-[14px] text-slate-900').style(
                                    'white-space: normal; word-break: break-word; overflow-wrap: anywhere; line-height: 1.2;'
                                )
                            if (node.get('tipo') or '').upper() == 'PECA':
                                qtd = node.get('quantidade') or 0
                                try:
                                    qtd_txt = str(int(float(qtd))) if float(qtd).is_integer() else str(qtd)
                                except Exception:
                                    qtd_txt = str(qtd)
                                ui.label(f"{qtd_txt}x").classes('text-[11px] px-2 py-1 rounded-full shrink-0 font-medium text-slate-700').style('background:#f1f5f9; color:#334155;')
                            else:
                                status_base_id = str(node['id']).split('::')[1] if str(node['id']).startswith('peca::') else str(node['id'])
                                status_cor = estado['status_por_id'].get(status_base_id, 'green')
                                ui.label(node['tipo']).classes(_badge_status_style(node['tipo'], status_cor)).style(_badge_status_inline_style(node['tipo'], status_cor))

            if tem_filhos and node['id'] in estado['expanded_ids']:
                for filho in node['children']:
                    render_node(filho, nivel + 1)

        for raiz in estado['tree_data_atual']:
            render_node(raiz, 0)

    def aplicar_pesquisa():
        termo = normalizar_texto(busca.value)

        if not termo:
            estado['tree_data_atual'] = deepcopy(estado['arvore_completa'])
            estado['expanded_ids'] = set()
            estado['selected_id'] = None
            render_tree()
            render_detalhes()
            return

        tree_filtrada, ids_para_expandir = filtrar_tree_preservando_descendentes(
            estado['arvore_completa'], termo
        )
        estado['tree_data_atual'] = tree_filtrada
        estado['expanded_ids'] = set(ids_para_expandir)

        ids_visiveis = set()

        def coletar_ids(lista):
            for no in lista:
                ids_visiveis.add(no['id'])
                coletar_ids(no.get('children', []))

        coletar_ids(tree_filtrada)

        if estado['selected_id'] not in ids_visiveis:
            estado['selected_id'] = None

        render_tree()
        render_detalhes()


    def alterar_mostrar_pecas():
        estado['mostrar_pecas'] = bool(toggle_pecas.value)
        refresh_tela()

    def expandir_tudo():
        estado['expanded_ids'] = coletar_ids_com_filhos(estado['tree_data_atual'])
        render_tree()

    def recolher_tudo():
        estado['expanded_ids'] = set()
        render_tree()

    def refresh_tela():
        carregar_dados()
        termo = normalizar_texto(busca.value)
        if termo:
            aplicar_pesquisa()
        else:
            estado['selected_id'] = None
            render_tree()
            render_detalhes()

    def executar_pesquisa():
        aplicar_pesquisa()
        ui.run_javascript(
            """
            const el = document.activeElement;
            if (el) {
                el.blur();
                if (typeof el.setSelectionRange === 'function') {
                    const pos = String(el.value || '').length;
                    el.setSelectionRange(pos, pos);
                }
            }
            """
        )

    def render_detalhes():
        detalhe_container.clear()

        ativo_id = estado['selected_id']
        if not ativo_id:
            with detalhe_container:
                with ui.card().classes('w-full rounded-2xl shadow-none bg-slate-50 border border-slate-200 p-6'):
                    ui.label('SELECIONE UM ITEM NA ÁRVORE').classes('text-lg font-bold text-slate-700')
                    ui.label('OS DETALHES VÃO APARECER AQUI.').classes('text-sm text-slate-500')
            return

        if str(ativo_id).startswith('peca::'):
            peca = estado['pecas_por_id'].get(str(ativo_id))
            if not peca:
                estado['selected_id'] = None
                render_tree()
                render_detalhes()
                return
            ativo_pai = get_ativo(peca['origem_ativo_id'])
            with detalhe_container:
                with ui.card().classes('w-full rounded-2xl shadow-none border border-slate-200 p-5 gap-3'):
                    with ui.row().classes('w-full items-start justify-between'):
                        with ui.column().classes('gap-1'):
                            ui.label(peca.get('tag') or 'PEÇA').classes('text-2xl font-bold text-slate-800')
                            ui.label(peca.get('descricao') or '-').classes('text-base text-slate-600')
                        ui.label('PEÇA').classes('text-xs px-3 py-2 rounded-full font-bold').style('background:#e2e8f0;color:#334155;')
                    with ui.grid(columns=2).classes('w-full gap-3'):
                        def box(titulo, valor):
                            with ui.card().classes('shadow-none bg-slate-50 border border-slate-200 p-3'):
                                ui.label(titulo).classes('text-xs text-slate-500')
                                ui.label(str(valor or '-')).classes('text-sm font-bold text-slate-800')
                        box('REFERÊNCIA', peca.get('tag') or '-')
                        box('QUANTIDADE', f"{peca.get('quantidade') or 0} {peca.get('unidade') or 'UN'}")
                        box('PAI', (ativo_pai or {}).get('tag') or '-')
                        box('TIPO DO PAI', (ativo_pai or {}).get('tipo') or '-')
            return

        ativo = get_ativo(ativo_id)
        if not ativo:
            estado['selected_id'] = None
            render_tree()
            render_detalhes()
            return

        anexos = listar_anexos(ativo_id)
        pecas = ativo.get('pecas_json') or []

        with detalhe_container:
            with ui.card().classes('w-full rounded-2xl shadow-none border border-slate-200 p-5 gap-3'):
                with ui.row().classes('w-full items-start justify-between'):
                    with ui.column().classes('gap-1'):
                        ui.label(ativo['tag']).classes('text-2xl font-bold text-slate-800')
                        ui.label(ativo['descricao']).classes('text-base text-slate-600')
                    ui.label(ativo['tipo']).classes('text-xs px-3 py-2 rounded-full font-bold').style(_badge_status_inline_style(ativo['tipo'], estado['status_por_id'].get(str(ativo['id']), 'green'))) 

                with ui.grid(columns=2).classes('w-full gap-3'):
                    def box(titulo, valor):
                        with ui.card().classes('shadow-none bg-slate-50 border border-slate-200 p-3'):
                            ui.label(titulo).classes('text-xs text-slate-500')
                            ui.label(str(valor or '-')).classes('text-sm font-bold text-slate-800')

                    box('TAG BASE', ativo.get('tag_base') or '-')

                    pai = '-'
                    if ativo.get('parent_id'):
                        pai_row = get_ativo(ativo['parent_id'])
                        if pai_row:
                            pai = pai_row['tag']
                    box('PAI', pai)

                    if ENABLE_CRITICIDADE and (ativo.get('tipo') or '').upper() != 'LOCAL':
                        box('CRITICIDADE', ativo.get('criticidade') or '-')

                    if (ativo.get('tipo') or '').upper() != 'LOCAL':
                        box('FABRICANTE', ativo.get('fabricante') or '-')
                        box('MODELO', ativo.get('modelo') or '-')
                        box('NÚMERO DE SÉRIE', ativo.get('numero_serie') or '-')

                    if (ativo.get('tipo') or '').upper() == 'EQUIPAMENTO':
                        box('ANO DE FABRICAÇÃO', ativo.get('ano_fabricacao') or '-')

                    if (ativo.get('tipo') or '').upper() == 'COMPONENTE':
                        box('PEÇAS VINCULADAS', len(pecas) if ativo.get('pecas_ativas') else 0)

            with ui.card().classes('w-full rounded-2xl shadow-none border border-slate-200 p-5 gap-3'):
                ui.label('OBSERVAÇÕES').classes('text-lg font-bold text-slate-800')
                ui.label(ativo.get('observacoes') or '-').classes('text-sm text-slate-700').style(
                    'white-space: pre-wrap; word-break: break-word;'
                )

            if (ativo.get('tipo') or '').upper() in ('EQUIPAMENTO', 'COMPONENTE') and ativo.get('pecas_ativas'):
                with ui.card().classes('w-full rounded-2xl shadow-none border border-slate-200 p-5 gap-3'):
                    ui.label('PEÇAS VINCULADAS').classes('text-lg font-bold text-slate-800')
                    if not pecas:
                        ui.label('NENHUMA PEÇA VINCULADA.').classes('text-sm text-slate-500')
                    else:
                        for item in pecas:
                            with ui.row().classes('w-full items-center gap-3 border border-slate-200 rounded-xl px-4 py-3'):
                                ui.label(str(item.get('referencia') or '-')).classes('w-48 text-sm font-bold text-slate-800')
                                ui.label(str(item.get('descricao') or '-')).classes('flex-1 text-sm text-slate-700')
                                ui.label(f"{item.get('quantidade') or 0} {item.get('unidade') or 'UN'}").classes('w-24 text-right text-sm text-slate-700')

            tipo_ativo = (ativo.get('tipo') or '').upper()
            if tipo_ativo in ('EQUIPAMENTO', 'COMPONENTE'):
                if tipo_ativo == 'COMPONENTE':
                    os_relacionadas = listar_os_por_ativo(str(ativo['id']), incluir_componentes=True) if 'listar_os_por_ativo' in globals() else [x for x in listar_os() if str(x.get('componente_id') or '') == str(ativo['id'])]
                    subtitulo_os = 'LISTA DE OS DESTE COMPONENTE'
                else:
                    os_relacionadas = listar_os_por_ativo(str(ativo['id']), incluir_componentes=False) if 'listar_os_por_ativo' in globals() else [x for x in listar_os() if str(x.get('equipamento_id') or '') == str(ativo['id'])]
                    subtitulo_os = 'LISTA DE OS DESTE EQUIPAMENTO'

                with ui.card().classes('w-full rounded-2xl shadow-none border border-slate-200 p-5 gap-3'):
                    with ui.row().classes('w-full items-center justify-between'):
                        with ui.column().classes('gap-0'):
                            ui.label('OS').classes('text-lg font-bold text-slate-800')
                            ui.label(subtitulo_os).classes('text-xs text-slate-500')
                        ui.label(f"{len(os_relacionadas)} ITEM(NS)").classes('text-xs text-slate-500')

                    if not os_relacionadas:
                        with ui.card().classes('w-full shadow-none bg-slate-50 border border-slate-200 p-4'):
                            ui.label('NENHUMA OS RELACIONADA.').classes('text-sm text-slate-500')
                    else:
                        for os_item in os_relacionadas:
                            tag_alvo = os_item.get('componente_tag') or os_item.get('equipamento_tag') or '-'
                            with ui.row().classes('w-full items-start justify-between gap-3 border border-slate-200 rounded-xl px-4 py-3 bg-white'):
                                with ui.column().classes('gap-0 flex-1'):
                                    ui.label(str(os_item.get('numero') or '-')).classes('text-sm font-bold text-slate-800')
                                    ui.label(tag_alvo).classes('text-xs font-medium text-slate-500')
                                    ui.label(str(os_item.get('descricao') or '-')).classes('text-sm text-slate-700').style('white-space: normal; word-break: break-word; overflow-wrap: anywhere;')
                                ui.label(str(os_item.get('status') or '-')).classes('text-[11px] px-2 py-1 rounded-full font-bold shrink-0').style(_os_status_badge_style(os_item.get('status')))

            with ui.card().classes('w-full rounded-2xl shadow-none border border-slate-200 p-5 gap-3'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label('ANEXOS').classes('text-lg font-bold text-slate-800')
                    ui.label(f'{len(anexos)} ITEM(NS)').classes('text-xs text-slate-500')

                if not anexos:
                    with ui.card().classes('w-full shadow-none bg-slate-50 border border-slate-200 p-4'):
                        ui.label('NENHUM ANEXO CADASTRADO.').classes('text-sm text-slate-500')
                else:
                    fotos = [a for a in anexos if str(a.get('tipo') or '').upper() == 'FOTO']
                    pdfs = [a for a in anexos if str(a.get('tipo') or '').upper() == 'PDF']

                    if fotos:
                        ui.label('IMAGENS').classes('text-sm font-bold text-slate-700')
                        with ui.row().classes('w-full gap-3 flex-wrap'):
                            for idx, anexo in enumerate(fotos):
                                with ui.column().classes('w-[120px] gap-1'): 
                                    ui.image(arquivo_url(anexo)).classes('w-[120px] h-[90px] object-contain bg-white rounded-lg border border-slate-200 cursor-pointer').on('click', lambda e=None, url=arquivo_url(anexo): abrir_nova_aba(url))
                                    with ui.row().classes('w-full items-center justify-center gap-1'):
                                        ui.label(nome_truncado(anexo.get('nome_original') or '', 14)).classes('text-[11px] text-slate-600 text-center').style('overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 88px;')
                                        ui.button(icon='download', on_click=lambda e=None, url=download_url(anexo): abrir_nova_aba(url)).props('flat round dense').classes('text-slate-600')

                    if pdfs:
                        ui.label('PDFS').classes('text-sm font-bold text-slate-700 mt-2')
                        for anexo in pdfs:
                            with ui.row().classes('w-full items-center justify-between border border-slate-200 rounded-xl px-4 py-3'):
                                with ui.row().classes('items-center gap-3 min-w-0 flex-1'):
                                    ui.icon('picture_as_pdf').classes('text-slate-700')
                                    ui.link(nome_truncado(anexo.get('nome_original') or '', 42), arquivo_url(anexo), new_tab=True).classes('text-sm font-medium text-slate-800 max-w-full truncate')
                                with ui.row().classes('items-center gap-1'):
                                    ui.button(icon='download', on_click=lambda e=None, url=download_url(anexo): abrir_nova_aba(url)).props('flat round dense')

    busca.on('keydown.enter', lambda e: executar_pesquisa())
    busca.on('update:model-value', lambda e: refresh_tela() if not str(getattr(busca, 'value', '') or '').strip() else None)
    btn_buscar.on_click(executar_pesquisa)
    toggle_pecas.on('update:model-value', lambda e: alterar_mostrar_pecas())
    btn_expandir.on_click(expandir_tudo)
    btn_recolher.on_click(recolher_tudo)
    btn_refresh.on_click(refresh_tela)

    carregar_dados()
    render_tree()
    render_detalhes()
    ui.timer(0.25, hide_page_loader, once=True)
