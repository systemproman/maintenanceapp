
import tempfile
from datetime import datetime, date
from pathlib import Path
from urllib.parse import quote

from nicegui import ui, app
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


from auth import can_edit
from components.menu import build_menu, show_page_loader, hide_page_loader
from services import db

TIPOS_OS = ['CORRETIVA', 'PREVENTIVA', 'PREVENTIVA PERIÓDICA', 'MELHORIA']
STATUS_OS = ['ABERTA', 'EM EXECUÇÃO', 'ENCERRADA']
STATUS_ATIVIDADE = ['ABERTA', 'EM EXECUÇÃO', 'CONCLUÍDA']
CLASSIFICACOES = ['INTERNA', 'SERVIÇO TERCEIRO']
MODO_HORA = ['HORA FIM', 'DURAÇÃO']


def _fmt_brl(valor):
    try:
        return f'R$ {float(valor or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return 'R$ 0,00'


def _parse_hhmm(valor):
    txt = ''.join(ch for ch in str(valor or '') if ch.isdigit())[-4:]
    if not txt:
        return 0
    txt = txt.rjust(4, '0')
    hh = int(txt[:-2])
    mm = min(int(txt[-2:]), 59)
    return hh * 60 + mm


def _normalize_hhmm(valor, completo=False):
    digits = ''.join(ch for ch in str(valor or '') if ch.isdigit())[:4]
    if not digits:
        return ''
    if not completo:
        if len(digits) <= 2:
            return digits
        return f'{digits[:2]}:{digits[2:]}'
    hh = int(digits[:2] or '0')
    mm = int(digits[2:4] or '0')
    hh = max(0, min(hh, 23))
    mm = max(0, min(mm, 59))
    return f'{hh:02d}:{mm:02d}'


def _fmt_hhmm_from_minutes(mins):
    mins = int(round(float(mins or 0)))
    return f'{mins // 60:02d}:{mins % 60:02d}'


def _status_badge_classes(status: str) -> str:
    status = str(status or '').upper()
    if status == 'ABERTA':
        return 'bg-blue-100 text-blue-800'
    if status == 'EM EXECUÇÃO':
        return 'bg-amber-100 text-amber-800'
    if status in ('ENCERRADA', 'CONCLUÍDA'):
        return 'bg-emerald-100 text-emerald-800'
    return 'bg-slate-100 text-slate-700'


def _arquivo_url(anexo: dict) -> str:
    nome = quote(str(anexo.get('nome_original') or anexo.get('nome_salvo') or 'arquivo'))
    return f"/arquivo/{anexo['id']}/{nome}"


def _get_upload_name(e) -> str:
    if hasattr(e, 'file') and getattr(e.file, 'name', None):
        return str(e.file.name)
    if hasattr(e, 'name') and e.name:
        return str(e.name)
    if hasattr(e, 'file_name') and e.file_name:
        return str(e.file_name)
    return 'arquivo'


def _label_alvo(item: dict) -> str:
    tipo = str(item.get('tipo') or '').upper()
    extra = f" [{item.get('equipamento_tag')}]" if tipo == 'COMPONENTE' and item.get('equipamento_tag') else ''
    return f"{item.get('tag') or '-'} - {item.get('descricao') or '-'} ({tipo}){extra}"




def _validar_data_nao_futura(data_str):
    data = _parse_date_any(data_str)
    if not data:
        raise ValueError('INFORME UMA DATA VÁLIDA.')
    if data > date.today():
        raise ValueError('NÃO É PERMITIDO APONTAMENTO EM DATA FUTURA.')
    return data

def _date_input(label, value=''):
    inp = ui.input(label).props('outlined dense mask=####-##-##').classes('w-full').style('direction:ltr; text-align:left;')
    inp.value = value
    with inp.add_slot('append'):
        ico = ui.icon('event').classes('cursor-pointer')
        with ui.menu() as menu_data:
            ui.date().props('locale=pt-BR').bind_value(inp)
        ico.on('click', menu_data.open)
    return inp


def _time_input(label, native_time=False):
    return ui.input(label, placeholder='HH:MM').props('outlined dense maxlength=5').classes('w-full').style('direction:ltr; text-align:left; unicode-bidi:plaintext;')



def _logo_fsl_path() -> Path | None:
    candidatos = [
        Path('assets/logo_fsl.png'),
        Path(__file__).resolve().parent / 'assets' / 'logo_fsl.png',
        Path.cwd() / 'assets' / 'logo_fsl.png',
    ]
    for caminho in candidatos:
        if caminho.exists():
            return caminho
    return None


def _safe_text(value) -> str:
    return str(value or '-').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _fmt_data_br(valor) -> str:
    txt = str(valor or '').strip()
    if not txt:
        return '-'
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d/%m/%Y'):
        try:
            return datetime.strptime(txt[:19], fmt).strftime('%d/%m/%Y')
        except Exception:
            pass
    return txt


def _parse_date_any(valor):
    txt = str(valor or '').strip()
    if not txt:
        return None
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d/%m/%Y'):
        try:
            return datetime.strptime(txt[:19], fmt).date()
        except Exception:
            pass
    return None


def _date_to_iso(valor):
    data = _parse_date_any(valor)
    return data.strftime('%Y-%m-%d') if data else ''


def _gerar_pdf_os(item: dict, totais: dict, atividades: list, materiais: list, apontamentos: list, anexos: list, incluir_apontamentos: bool, incluir_imagens: bool) -> Path:
    pasta_tmp = Path(tempfile.gettempdir()) / 'nice_os_pdf'
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    numero = str(item.get('numero') or 'OS').replace('/', '-').replace('\\', '-')
    destino = pasta_tmp / f'OS_{numero}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='FSLTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=12, leading=14, textColor=colors.black))
    styles.add(ParagraphStyle(name='FSLBody', parent=styles['BodyText'], fontName='Helvetica', fontSize=9, leading=11, textColor=colors.black))
    styles.add(ParagraphStyle(name='FSLSmall', parent=styles['BodyText'], fontName='Helvetica', fontSize=8, leading=9.5, textColor=colors.black))
    styles.add(ParagraphStyle(name='FSLSection', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=10.5, leading=12, textColor=colors.black, spaceAfter=4))
    styles.add(ParagraphStyle(name='FSLHeaderCompany', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=11, leading=13, textColor=colors.black))
    styles.add(ParagraphStyle(name='FSLHeaderInfo', parent=styles['BodyText'], fontName='Helvetica', fontSize=8.5, leading=10, textColor=colors.black))

    doc = SimpleDocTemplate(
        str(destino),
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=12 * mm,
        title=f"OS {numero}",
    )

    story = []
    logo = _logo_fsl_path()
    logo_cell = ''
    if logo:
        try:
            logo_cell = Image(str(logo), width=28 * mm, height=18 * mm)
        except Exception:
            logo_cell = ''

    empresa_info = [
        Paragraph('FUNSOLOS CONSTRUTORA E ENGENHARIA LTDA', styles['FSLHeaderCompany']),
        Paragraph('CNPJ: 15.404.932/0001-77', styles['FSLHeaderInfo']),
        Paragraph('Rua Anel Rodoviario, 894 - Noroeste, Campo Grande - MS', styles['FSLHeaderInfo']),
        Paragraph('Telefone: (67) 3348-0000', styles['FSLHeaderInfo']),
    ]

    topo = Table([[logo_cell, empresa_info]], colWidths=[34 * mm, 152 * mm])
    topo.hAlign = 'LEFT'
    topo.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(topo)

    data_os = _fmt_data_br(item.get('data_abertura') or datetime.now().strftime('%Y-%m-%d'))
    linha_os = Table([[
        Paragraph(f'<b>ORDEM DE SERVIÇO Nº:</b> {_safe_text(item.get("numero"))}', styles['FSLBody']),
        Paragraph(f'<b>DATA:</b> {_safe_text(data_os)}', styles['FSLBody']),
    ]], colWidths=[126 * mm, 60 * mm])
    linha_os.hAlign = 'LEFT'
    linha_os.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(linha_os)

    alvo = item.get('componente') or item.get('equipamento') or {}
    equipamento_txt = f"{alvo.get('tag') or '-'} - {alvo.get('descricao') or '-'}"
    local_txt = str((alvo.get('local_tag') or alvo.get('local_descricao') or '-') if isinstance(alvo, dict) else '-')
    responsavel_txt = item.get('responsavel') or item.get('solicitante') or '-'

    resumo_header = Table([
        [Paragraph('<b>EQUIPAMENTO</b>', styles['FSLSmall']),
         Paragraph('<b>LOCAL</b>', styles['FSLSmall']),
         Paragraph('<b>STATUS</b>', styles['FSLSmall']),
         Paragraph('<b>RESPONSÁVEL</b>', styles['FSLSmall'])],
        [Paragraph(_safe_text(equipamento_txt), styles['FSLBody']),
         Paragraph(_safe_text(local_txt), styles['FSLBody']),
         Paragraph(_safe_text(item.get('status')), styles['FSLBody']),
         Paragraph(_safe_text(responsavel_txt), styles['FSLBody'])],
    ], colWidths=[70 * mm, 46 * mm, 28 * mm, 42 * mm])
    resumo_header.hAlign = 'LEFT'
    resumo_header.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story += [resumo_header, Spacer(1, 4 * mm)]

    dados_principais = [
        [Paragraph('<b>Status</b>', styles['FSLBody']), Paragraph(_safe_text(item.get('status')), styles['FSLBody'])],
        [Paragraph('<b>Tipo OS</b>', styles['FSLBody']), Paragraph(_safe_text(item.get('tipo_os')), styles['FSLBody'])],
        [Paragraph('<b>Data abertura</b>', styles['FSLBody']), Paragraph(_safe_text(_fmt_data_br(item.get('data_abertura'))), styles['FSLBody'])],
        [Paragraph('<b>Data encerramento</b>', styles['FSLBody']), Paragraph(_safe_text(_fmt_data_br(item.get('data_encerramento'))), styles['FSLBody'])],
        [Paragraph('<b>Encerrado por</b>', styles['FSLBody']), Paragraph(_safe_text(item.get('usuario_encerramento') or '-'), styles['FSLBody'])],
        [Paragraph('<b>Descrição</b>', styles['FSLBody']), Paragraph(_safe_text(item.get('descricao')), styles['FSLBody'])],
        [Paragraph('<b>Observações</b>', styles['FSLBody']), Paragraph(_safe_text(item.get('observacoes') or '-'), styles['FSLBody'])],
    ]
    tbl = Table(dados_principais, colWidths=[36 * mm, 150 * mm])
    tbl.hAlign = 'LEFT'
    tbl.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story += [tbl, Spacer(1, 4 * mm)]

    custo_hh_pdf = float(totais.get('custo_hh', totais.get('custo_hh_total', 0)) or 0)
    custo_materiais_pdf = float(totais.get('custo_materiais', totais.get('custo_materiais_total', 0)) or 0)
    custo_terceiro_pdf = float(totais.get('custo_terceiro', 0) or 0)
    custo_total_pdf = float(totais.get('custo_total_os', totais.get('custo_total', custo_materiais_pdf + custo_hh_pdf + custo_terceiro_pdf)) or 0)
    resumo = Table([
        [Paragraph('<b>Duração total</b>', styles['FSLBody']), Paragraph(_safe_text(_fmt_hhmm_from_minutes(totais.get('duracao_total_min'))), styles['FSLBody']),
         Paragraph('<b>Custo HH</b>', styles['FSLBody']), Paragraph(_safe_text(_fmt_brl(custo_hh_pdf)), styles['FSLBody'])],
        [Paragraph('<b>Materiais</b>', styles['FSLBody']), Paragraph(_safe_text(_fmt_brl(custo_materiais_pdf)), styles['FSLBody']),
         Paragraph('<b>Serviço terceiro</b>', styles['FSLBody']), Paragraph(_safe_text(_fmt_brl(custo_terceiro_pdf)), styles['FSLBody'])],
        [Paragraph('<b>Custo total</b>', styles['FSLBody']), Paragraph(_safe_text(_fmt_brl(custo_total_pdf)), styles['FSLBody']),
         Paragraph('', styles['FSLBody']), Paragraph('', styles['FSLBody'])],
    ], colWidths=[34 * mm, 59 * mm, 34 * mm, 59 * mm])
    resumo.hAlign = 'LEFT'
    resumo.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story += [Paragraph('RESUMO', styles['FSLSection']), resumo, Spacer(1, 4 * mm)]

    story.append(Paragraph('ATIVIDADES', styles['FSLSection']))
    if atividades:
        dados_ativ = [[Paragraph('<b>Seq.</b>', styles['FSLBody']), Paragraph('<b>Descrição</b>', styles['FSLBody']), Paragraph('<b>Status</b>', styles['FSLBody']), Paragraph('<b>Classificação</b>', styles['FSLBody'])]]
        for atv in atividades:
            dados_ativ.append([
                Paragraph(_safe_text(f"{int(atv.get('sequencia') or 0):02d}"), styles['FSLBody']),
                Paragraph(_safe_text(atv.get('descricao')), styles['FSLBody']),
                Paragraph(_safe_text(atv.get('status')), styles['FSLBody']),
                Paragraph(_safe_text(atv.get('classificacao') or 'INTERNA'), styles['FSLBody']),
            ])
        tab_ativ = Table(dados_ativ, colWidths=[20 * mm, 90 * mm, 36 * mm, 40 * mm], repeatRows=1)
        tab_ativ.hAlign = 'LEFT'
        tab_ativ.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story += [tab_ativ, Spacer(1, 4 * mm)]
    else:
        story += [Paragraph('Nenhuma atividade cadastrada.', styles['FSLSmall']), Spacer(1, 4 * mm)]

    if materiais:
        story.append(Paragraph('MATERIAIS', styles['FSLSection']))
        dados_mat = [[Paragraph('<b>Material</b>', styles['FSLBody']), Paragraph('<b>Qtd.</b>', styles['FSLBody']), Paragraph('<b>Un.</b>', styles['FSLBody']), Paragraph('<b>Total</b>', styles['FSLBody'])]]
        for mat in materiais:
            dados_mat.append([
                Paragraph(_safe_text(mat.get('descricao_material')), styles['FSLBody']),
                Paragraph(_safe_text(mat.get('quantidade')), styles['FSLBody']),
                Paragraph(_safe_text(mat.get('unidade') or '-'), styles['FSLBody']),
                Paragraph(_safe_text(_fmt_brl(mat.get('custo_total'))), styles['FSLBody']),
            ])
        tab_mat = Table(dados_mat, colWidths=[108 * mm, 24 * mm, 20 * mm, 34 * mm], repeatRows=1)
        tab_mat.hAlign = 'LEFT'
        tab_mat.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story += [tab_mat, Spacer(1, 4 * mm)]

    if incluir_apontamentos:
        story.append(Paragraph('APONTAMENTOS', styles['FSLSection']))
        if apontamentos:
            dados_ap = [[
                Paragraph('<b>Funcionário / Empresa</b>', styles['FSLBody']),
                Paragraph('<b>Data</b>', styles['FSLBody']),
                Paragraph('<b>Horas</b>', styles['FSLBody']),
                Paragraph('<b>Custo</b>', styles['FSLBody']),
                Paragraph('<b>Descrição</b>', styles['FSLBody']),
            ]]
            for ap in apontamentos:
                nome = ap.get('funcionario_nome') or ap.get('empresa_terceira') or '-'
                horas = _fmt_hhmm_from_minutes(ap.get('duracao_min') or 0)
                dados_ap.append([
                    Paragraph(_safe_text(nome), styles['FSLBody']),
                    Paragraph(_safe_text(_fmt_data_br(ap.get('data_apontamento'))), styles['FSLBody']),
                    Paragraph(_safe_text(horas), styles['FSLBody']),
                    Paragraph(_safe_text(_fmt_brl(ap.get('custo_hh_total') or 0)), styles['FSLBody']),
                    Paragraph(_safe_text(ap.get('descricao_servico') or '-'), styles['FSLBody']),
                ])
            tab_ap = Table(dados_ap, colWidths=[42 * mm, 22 * mm, 20 * mm, 28 * mm, 74 * mm], repeatRows=1)
            tab_ap.hAlign = 'LEFT'
            tab_ap.setStyle(TableStyle([
                ('BOX', (0, 0), (-1, -1), 0.5, colors.black),
                ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.black),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            story += [tab_ap, Spacer(1, 4 * mm)]
        else:
            story += [Paragraph('Nenhum apontamento cadastrado.', styles['FSLSmall']), Spacer(1, 4 * mm)]

    if incluir_imagens:
        imagens = []
        for anexo in anexos or []:
            caminho = Path(str(anexo.get('caminho') or ''))
            if not caminho.exists():
                continue
            ext = caminho.suffix.lower()
            if ext in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}:
                imagens.append((anexo.get('nome_original') or caminho.name, caminho))
        if imagens:
            story.append(Paragraph('IMAGENS', styles['FSLSection']))
            max_w = 170 * mm
            max_h = 110 * mm
            for nome_img, caminho in imagens:
                story.append(Paragraph(_safe_text(nome_img), styles['FSLSmall']))
                try:
                    img = Image(str(caminho))
                    iw = float(getattr(img, 'imageWidth', max_w) or max_w)
                    ih = float(getattr(img, 'imageHeight', max_h) or max_h)
                    fator = min(max_w / iw if iw else 1, max_h / ih if ih else 1, 1)
                    img.drawWidth = iw * fator
                    img.drawHeight = ih * fator
                    story += [img, Spacer(1, 3 * mm)]
                except Exception:
                    story.append(Paragraph('Não foi possível renderizar esta imagem.', styles['FSLSmall']))
                    story.append(Spacer(1, 3 * mm))

    doc.build(story)
    return destino


def os_page():
    editavel = can_edit()
    estado = {'os_id': app.storage.user.get('os_selected_id')}

    with ui.row().classes('w-full h-screen no-wrap bg-slate-100'):
        build_menu('/os')
        with ui.column().classes('flex-1 h-full p-4 gap-4 overflow-hidden'):
            with ui.card().classes('w-full rounded-2xl shadow-sm border-0 bg-white p-0 overflow-hidden'):
                with ui.row().classes('w-full items-center justify-between px-5 py-4 bg-slate-50').style('border-bottom:1px solid #e5e7eb;'):
                    with ui.column().classes('gap-0'):
                        ui.label('OS').classes('text-xl font-bold text-slate-800')
                        ui.label('ORDEM DE SERVIÇO').classes('text-xs text-slate-500')
                    with ui.row().classes('items-center gap-2'):
                        busca = ui.input(placeholder='BUSCAR NÚMERO, TAG, DESCRIÇÃO...').props('outlined dense clearable').classes('w-[420px]')
                        ui.button(icon='search', on_click=lambda: _run_busy(carregar_lista, 'CARREGANDO OS...')).props('flat round dense')
                        if editavel:
                            ui.button('NOVA OS', icon='add', on_click=lambda: abrir_form_os()).props('unelevated').classes('bg-amber-500 text-black font-bold')
                        ui.button(icon='refresh', on_click=lambda: _run_busy(carregar_lista, 'ATUALIZANDO OS...')).props('flat round dense')
            with ui.row().classes('w-full flex-1 gap-4 overflow-hidden'):
                lista = ui.column().classes('w-[32%] h-full gap-3 overflow-auto')
                detalhe = ui.column().classes('flex-1 h-full gap-3 overflow-auto')


    def _set_os_id(os_id):
        estado['os_id'] = os_id or None
        app.storage.user['os_selected_id'] = estado['os_id']

    def _get_os_id():
        os_id = estado.get('os_id') or app.storage.user.get('os_selected_id')
        estado['os_id'] = os_id or None
        return estado['os_id']

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

    def refresh_current_os(reload_list: bool = False, texto: str = 'CARREGANDO...'):
        def _job():
            if reload_list:
                carregar_lista(force=True)
            else:
                render_lista()
            render_detalhe(force=True)
        _run_busy(_job, texto)

    def box(titulo, valor):
        with ui.card().classes('shadow-none bg-slate-50 border border-slate-200 p-3'):
            ui.label(titulo).classes('text-xs text-slate-500')
            ui.label(str(valor or '-')).classes('text-sm font-bold text-slate-800')

    def box_longo(titulo, valor):
        with ui.card().classes('w-full shadow-none bg-slate-50 border border-slate-200 p-3'):
            ui.label(titulo).classes('text-xs text-slate-500')
            ui.label(str(valor or '-')).classes('text-sm text-slate-800').style('white-space: pre-wrap;')

    def render_lista(dados=None):
        dados_render = list(dados) if dados is not None else db.listar_os(str(busca.value or '').strip())
        lista.clear()
        with lista:
            if not dados_render:
                with ui.card().classes('w-full rounded-xl shadow-none bg-slate-50 border border-slate-200 p-5'):
                    ui.label('NENHUMA OS ENCONTRADA.').classes('text-sm text-slate-600')
                return
            for item in dados_render:
                tag_destaque = item.get('componente_tag') or item.get('equipamento_tag') or '-'
                selecionada = str(_get_os_id() or '') == str(item['id'])
                card_cls = 'w-full rounded-xl shadow-none p-4 cursor-pointer transition-all '
                card_cls += 'border-2 border-amber-400 bg-amber-50' if selecionada else 'border border-slate-200 bg-white'
                with ui.card().classes(card_cls).on('click', lambda e=None, i=item: selecionar_os(i['id'])):
                    with ui.row().classes('w-full items-start justify-between'):
                        with ui.column().classes('gap-0'):
                            ui.label(tag_destaque).classes('text-lg font-black text-slate-900')
                            ui.label(item.get('numero') or '-').classes('text-sm font-medium text-slate-500')
                            ui.label(item.get('descricao') or '-').classes('text-sm text-slate-700').style('white-space: normal;')
                        ui.label(item.get('status') or '-').classes(f'text-[11px] px-2 py-1 rounded-full font-bold {_status_badge_classes(item.get("status"))}')

    def carregar_lista(force: bool = False):
        termo_busca = str(busca.value or '').strip()
        dados = db.listar_os(termo_busca)
        atual = _get_os_id()
        ids = {str(item.get('id')) for item in dados}
        if dados and (not atual or str(atual) not in ids):
            _set_os_id(dados[0].get('id'))
        elif not dados:
            _set_os_id(None)
        render_lista(dados)

    def selecionar_os(os_id):
        _set_os_id(os_id)
        render_lista()
        render_detalhe(force=True)

    def render_detalhe(force: bool = False):
        detalhe.clear()
        os_id = _get_os_id()
        if not os_id:
            with detalhe:
                with ui.card().classes('w-full rounded-xl shadow-none bg-slate-50 border border-slate-200 p-6'):
                    ui.label('SELECIONE UMA OS').classes('text-lg font-bold text-slate-700')
            return

        detalhe_os = db.get_os_detalhe(os_id) if hasattr(db, 'get_os_detalhe') else None
        if detalhe_os is None:
            item = db.get_os(os_id)
            if not item:
                _set_os_id(None)
                carregar_lista(force=True)
                with detalhe:
                    with ui.card().classes('w-full rounded-xl shadow-none bg-slate-50 border border-slate-200 p-6'):
                        ui.label('SELECIONE UMA OS').classes('text-lg font-bold text-slate-700')
                return
            detalhe_os = {
                'item': item,
                'totais': db.calcular_totais_os(os_id),
                'atividades': db.listar_os_atividades(os_id),
                'materiais': db.listar_os_materiais(os_id),
                'apontamentos': db.listar_os_apontamentos(os_id),
                'anexos': db.listar_os_anexos(os_id),
            }

        item = detalhe_os['item']
        totais = detalhe_os['totais']
        atividades = detalhe_os['atividades']
        materiais = detalhe_os['materiais']
        apontamentos = detalhe_os['apontamentos']
        anexos = detalhe_os['anexos']
        if not item:
            _set_os_id(None)
            carregar_lista(force=True)
            with detalhe:
                with ui.card().classes('w-full rounded-xl shadow-none bg-slate-50 border border-slate-200 p-6'):
                    ui.label('SELECIONE UMA OS').classes('text-lg font-bold text-slate-700')
            return
        tag_destaque = (item.get('componente') or item.get('equipamento') or {}).get('tag', '-')
        alvo_obj = item.get('componente') or item.get('equipamento') or {}
        alvo_desc = f"{alvo_obj.get('tag') or '-'} - {alvo_obj.get('descricao') or '-'}"
        os_bloqueada = str(item.get('status') or '').upper() == 'ENCERRADA'
        editavel_os = bool(editavel and not os_bloqueada)
        pode_editar_os = bool(editavel)
        with detalhe:
            with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4 gap-3'):
                with ui.row().classes('w-full items-start justify-between'):
                    with ui.column().classes('gap-0'):
                        ui.label(tag_destaque).classes('text-3xl font-black text-slate-900')
                        ui.label(item['numero']).classes('text-sm font-bold text-slate-500')
                        ui.label(alvo_desc).classes('text-sm text-slate-500')
                    with ui.row().classes('items-center gap-1'):
                        if editavel_os:
                            status_os = ui.select(STATUS_OS, value=item.get('status') or 'ABERTA').props('dense outlined options-dense').classes('min-w-[180px]')

                            def _persistir_status_os(novo_status, data_encerramento=None, os_item=item, ctrl=status_os):
                                try:
                                    usuario_encerramento = None
                                    if str(novo_status or '').upper() == 'ENCERRADA':
                                        usuario_encerramento = app.storage.user.get('name') or app.storage.user.get('username') or ''
                                    db.atualizar_os(
                                        os_item['id'],
                                        os_item.get('componente_id') or os_item.get('equipamento_id'),
                                        os_item.get('descricao') or '',
                                        os_item.get('tipo_os') or 'CORRETIVA',
                                        None,
                                        os_item.get('observacoes') or '',
                                        novo_status,
                                        os_item.get('justificativa_encerramento') or '',
                                        os_item.get('data_abertura') or '',
                                        os_item.get('unidade_medidor') or 'HORÍMETRO',
                                        os_item.get('medidor_valor'),
                                        data_encerramento=data_encerramento,
                                        usuario_encerramento=usuario_encerramento,
                                    )
                                    refresh_current_os(reload_list=True, texto='ATUALIZANDO OS...')
                                except Exception as ex:
                                    ui.notify(str(ex), type='negative')
                                    ctrl.value = os_item.get('status') or 'ABERTA'
                                    ctrl.update()

                            def _abrir_dialog_encerramento(os_item=item, ctrl=status_os):
                                datas_validas = [_parse_date_any(ap.get('data_apontamento')) for ap in apontamentos if ap.get('data_apontamento')]
                                datas_validas = [d for d in datas_validas if d]
                                ultima_data = max(datas_validas) if datas_validas else None
                                ultima_data_iso = ultima_data.strftime('%Y-%m-%d') if ultima_data else ''
                                data_padrao = ultima_data_iso or datetime.now().strftime('%Y-%m-%d')
                                with ui.dialog() as dialog, ui.card().classes('min-w-[420px] gap-3'):
                                    ui.label('ENCERRAR OS').classes('text-base font-bold text-slate-800')
                                    ui.label('Confirma o encerramento da OS?').classes('text-sm text-slate-600')
                                    texto_ultima = f'Último apontamento: {_fmt_data_br(ultima_data_iso)}' if ultima_data_iso else 'Não há apontamentos para esta OS.'
                                    ui.label(texto_ultima).classes('text-xs text-slate-500')
                                    usar_ultimo = ui.switch('USAR DATA DO ÚLTIMO APONTAMENTO', value=bool(ultima_data_iso)).props('dense')
                                    data_manual = _date_input('DATA DE ENCERRAMENTO', data_padrao)
                                    if ultima_data_iso:
                                        data_manual.props(f'min={ultima_data_iso}')

                                    def _sync_data_manual():
                                        data_manual.visible = not bool(usar_ultimo.value)
                                        if usar_ultimo.value and ultima_data_iso:
                                            data_manual.value = ultima_data_iso
                                    usar_ultimo.on('update:model-value', lambda e=None: _sync_data_manual())
                                    _sync_data_manual()

                                    with ui.row().classes('w-full justify-end gap-2 pt-2'):
                                        def cancelar():
                                            dialog.close()
                                            ctrl.value = os_item.get('status') or 'ABERTA'
                                            ctrl.update()
                                        ui.button('CANCELAR', on_click=cancelar).props('flat')

                                        def confirmar():
                                            data_final = ultima_data_iso if usar_ultimo.value and ultima_data_iso else _date_to_iso(data_manual.value)
                                            if not data_final:
                                                ui.notify('INFORME A DATA DE ENCERRAMENTO.', type='warning')
                                                return
                                            if ultima_data and _parse_date_any(data_final) < ultima_data:
                                                ui.notify('A DATA DE ENCERRAMENTO NÃO PODE SER INFERIOR AO ÚLTIMO APONTAMENTO.', type='warning')
                                                return
                                            dialog.close()
                                            _persistir_status_os('ENCERRADA', data_final, os_item, ctrl)

                                        ui.button('CONFIRMAR', on_click=confirmar).props('unelevated').classes('bg-amber-500 text-black font-bold')
                                dialog.open()

                            def salvar_status_os(e=None, os_item=item, ctrl=status_os):
                                novo_status = str(ctrl.value or 'ABERTA').upper()
                                status_atual = str(os_item.get('status') or 'ABERTA').upper()
                                if novo_status == status_atual:
                                    return
                                if novo_status == 'ENCERRADA':
                                    _abrir_dialog_encerramento(os_item, ctrl)
                                    return
                                _persistir_status_os(novo_status, None, os_item, ctrl)

                            status_os.on('update:model-value', salvar_status_os)
                        else:
                            ui.label(item.get('status') or '-').classes(f'text-[11px] px-2 py-1 rounded-full font-bold {_status_badge_classes(item.get("status"))}')
                        if pode_editar_os:
                            ui.button(icon='edit', on_click=lambda: abrir_form_os(item)).props('flat round dense')
                        if editavel_os:
                            ui.button(icon='delete', on_click=lambda: confirmar_excluir_os(item)).props('flat round dense color=negative')
                with ui.row().classes('w-full items-center justify-end gap-2'):
                    def abrir_dialog_pdf():
                        with ui.dialog() as dialog, ui.card().classes('min-w-[340px] gap-3'):
                            ui.label('GERAR PDF DA OS').classes('text-base font-bold text-slate-800')
                            ui.label('Escolha o que deseja incluir no PDF.').classes('text-sm text-slate-500')
                            incluir_apont_pdf = ui.switch('INCLUIR APONTAMENTOS', value=False).props('dense')
                            incluir_img_pdf = ui.switch('INCLUIR IMAGENS', value=False).props('dense')
                            with ui.row().classes('w-full justify-end gap-2 pt-2'):
                                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                                def confirmar_pdf():
                                    try:
                                        caminho_pdf = _gerar_pdf_os(
                                            item,
                                            totais,
                                            atividades,
                                            materiais,
                                            apontamentos,
                                            anexos,
                                            bool(incluir_apont_pdf.value),
                                            bool(incluir_img_pdf.value),
                                        )
                                        dialog.close()
                                        ui.download(caminho_pdf, filename=caminho_pdf.name)
                                    except Exception as ex:
                                        ui.notify(str(ex), type='negative')
                                ui.button('GERAR PDF', icon='picture_as_pdf', on_click=confirmar_pdf).props('unelevated').classes('bg-red-600 text-white font-bold')
                        dialog.open()
                    ui.button('GERAR PDF', icon='picture_as_pdf', on_click=abrir_dialog_pdf).props('unelevated').classes('bg-red-600 text-white font-bold')

                with ui.grid(columns=2).classes('w-full gap-3'):
                    box('TIPO', item.get('tipo_os') or '-')
                    box('DATA DE ABERTURA', _fmt_data_br(item.get('data_abertura')))
                    box('DATA DE ENCERRAMENTO', _fmt_data_br(item.get('data_encerramento')))
                    box('ENCERRADO POR', item.get('usuario_encerramento') or '-')
                    box(item.get('unidade_medidor') or 'MEDIDOR', item.get('medidor_valor') or '-')
                    box('CUSTO MATERIAIS', _fmt_brl(totais.get('custo_materiais', 0)))
                    box('CUSTO HH', _fmt_brl(totais.get('custo_hh', 0)))
                    box('CUSTO SERVIÇO TERCEIRO', _fmt_brl(totais.get('custo_terceiro', 0)))
                    box('DURAÇÃO TOTAL', _fmt_hhmm_from_minutes(totais.get('duracao_total_min', 0)))
                    box('CUSTO TOTAL OS', _fmt_brl(totais.get('custo_total_os', 0)))
                box_longo('DESCRIÇÃO', item.get('descricao') or '-')
                box_longo('OBSERVAÇÕES', item.get('observacoes') or '-')
                box_longo('JUSTIFICATIVA', item.get('justificativa_encerramento') or '-')

            with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4 gap-3'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label('FOTOS / PDFS DA OS').classes('text-sm font-bold text-slate-800')
                if editavel_os:
                    async def upload_handler(e):
                        temp_path = None
                        try:
                            nome_arquivo = _get_upload_name(e)
                            conteudo = await e.file.read()
                            ext = Path(nome_arquivo).suffix or ''
                            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_file:
                                temp_file.write(conteudo)
                                temp_path = temp_file.name
                            db.adicionar_os_anexo(os_id, temp_path, nome_arquivo)
                            ui.notify('ANEXO ADICIONADO', type='positive')
                            refresh_current_os(reload_list=False, texto='ATUALIZANDO OS...')
                        except Exception as ex:
                            ui.notify(str(ex), type='negative')
                        finally:
                            if temp_path:
                                try:
                                    Path(temp_path).unlink(missing_ok=True)
                                except Exception:
                                    pass
                    ui.upload(label='ADICIONAR FOTO OU PDF', auto_upload=True, multiple=True, on_upload=upload_handler).props('accept=.png,.jpg,.jpeg,.webp,.bmp,.gif,.pdf').classes('w-full')
                if not anexos:
                    ui.label('NENHUM ANEXO NA OS.').classes('text-xs text-slate-500')
                else:
                    with ui.row().classes('w-full gap-3 flex-wrap'):
                        for anexo in anexos:
                            tipo = str(anexo.get('tipo') or '').upper()
                            with ui.row().classes('items-center gap-2 border border-slate-200 rounded-lg px-3 py-2 bg-slate-50'):
                                if tipo == 'FOTO':
                                    ui.image(_arquivo_url(anexo)).classes('w-16 h-16 object-contain bg-white rounded border border-slate-200')
                                else:
                                    ui.icon('picture_as_pdf').classes('text-2xl text-slate-700')
                                ui.link(str(anexo.get('nome_original') or '-'), _arquivo_url(anexo), new_tab=True).classes('text-sm text-slate-800 max-w-[220px]')
                                if editavel_os:
                                    ui.button(icon='delete', on_click=lambda e=None, aid=anexo['id']: confirmar_excluir_anexo_os(aid)).props('flat round dense color=negative')

            with ui.row().classes('w-full items-center justify-between mt-1'):
                ui.label('ATIVIDADES').classes('text-lg font-bold text-slate-800')
                if editavel_os:
                    ui.button('ADICIONAR ATIVIDADE', icon='add', on_click=lambda: abrir_form_atividade(os_id)).props('unelevated').classes('bg-amber-500 text-black font-bold')

            if not atividades:
                with ui.card().classes('w-full rounded-xl shadow-none bg-slate-50 border border-slate-200 p-4'):
                    ui.label('NENHUMA ATIVIDADE CADASTRADA.').classes('text-sm text-slate-600')

            for atividade in atividades:
                mat_atividade = [m for m in materiais if (m.get('atividade_id') or '') == atividade['id']]
                apont_atividade = [a for a in apontamentos if (a.get('atividade_id') or '') == atividade['id']]
                classif = atividade.get('classificacao') or 'INTERNA'
                dur_atividade = atividade.get('duracao_total_min') or 0
                custo_atividade = atividade.get('custo_total_atividade') or 0

                with ui.card().classes('w-full rounded-xl shadow-none border border-slate-200 p-4 gap-3'):
                    with ui.row().classes('w-full items-start justify-between'):
                        with ui.column().classes('gap-0'):
                            ui.label(f"{atividade['sequencia']:02d} - {atividade['descricao']}").classes('text-base font-bold text-slate-800')
                            ui.label(atividade.get('observacao') or '-').classes('text-xs text-slate-500')
                        with ui.row().classes('items-center gap-1'):
                            ui.label(classif).classes('text-[11px] px-2 py-1 rounded-full font-bold bg-slate-100 text-slate-700')
                            if editavel_os:
                                status_atividade = ui.select(STATUS_ATIVIDADE, value=atividade.get('status') or 'ABERTA').props('dense outlined options-dense').classes('min-w-[180px]')
                                def salvar_status_atividade(e=None, atv=atividade, ctrl=status_atividade):
                                    try:
                                        db.atualizar_os_atividade(
                                            atv['id'],
                                            descricao=atv.get('descricao') or '',
                                            observacao=atv.get('observacao') or '',
                                            status=ctrl.value,
                                            classificacao=atv.get('classificacao') or 'INTERNA',
                                            duracao_min=atv.get('duracao_min') or 0,
                                            custo_hh=atv.get('custo_hh') or 0,
                                            custo_servico_terceiro=atv.get('custo_servico_terceiro') or 0,
                                        )
                                        refresh_current_os(reload_list=True, texto='ATUALIZANDO OS...')
                                    except Exception as ex:
                                        ui.notify(str(ex), type='negative')
                                        ctrl.value = atv.get('status') or 'ABERTA'
                                        ctrl.update()
                                status_atividade.on('update:model-value', salvar_status_atividade)
                            else:
                                ui.label(atividade.get('status') or '-').classes(f'text-[11px] px-2 py-1 rounded-full font-bold {_status_badge_classes("ENCERRADA" if atividade.get("status")=="CONCLUÍDA" else atividade.get("status"))}')
                            if editavel_os:
                                ui.button(icon='edit', on_click=lambda e=None, atv=atividade: abrir_form_atividade(os_id, atv)).props('flat round dense')
                                ui.button(icon='delete', on_click=lambda e=None, atv=atividade: confirmar_excluir_atividade(atv)).props('flat round dense color=negative')

                    with ui.grid(columns=4).classes('w-full gap-3'):
                        box('CLASSIFICAÇÃO', classif)
                        box('DURAÇÃO TOTAL', _fmt_hhmm_from_minutes(dur_atividade))
                        box('CUSTO DA ATIVIDADE', _fmt_brl(custo_atividade))
                        box('MATERIAIS', _fmt_brl(atividade.get('custo_materiais') or 0))

                    if classif == 'INTERNA':
                        with ui.row().classes('w-full items-center justify-between mt-2'):
                            ui.label('APONTAMENTOS').classes('text-sm font-bold text-slate-800')
                            if editavel_os:
                                ui.button('ADICIONAR APONTAMENTO', icon='add', on_click=lambda e=None, atv=atividade: abrir_form_apontamento(os_id, atv)).props('flat').classes('text-slate-700')
                        if not apont_atividade:
                            ui.label('NENHUM APONTAMENTO.').classes('text-xs text-slate-500')
                        for ap in apont_atividade:
                            with ui.row().classes('w-full items-center justify-between border border-slate-200 rounded-lg px-3 py-2 bg-slate-50'):
                                with ui.column().classes('gap-0 flex-1'):
                                    ui.label(ap.get('funcionario_nome') or '-').classes('text-sm font-bold text-slate-800')
                                    ui.label(f"EQUIPE: {ap.get('equipe_nome') or '-'} | DATA: {ap.get('data_apontamento') or '-'}").classes('text-xs text-slate-500')
                                    ui.label(f"INÍCIO: {ap.get('hora_inicio') or '-'} | FIM: {ap.get('hora_fim') or '-'} | DURAÇÃO: {_fmt_hhmm_from_minutes(ap.get('duracao_min') or 0)}").classes('text-xs text-slate-500')
                                    ui.label(f"CUSTO HH: {_fmt_brl(ap.get('custo_hh_total') or 0)}").classes('text-xs text-slate-700')
                                    ui.label(ap.get('descricao_servico') or '-').classes('text-xs text-slate-700')
                                if editavel_os:
                                    with ui.row().classes('items-center gap-1'):
                                        ui.button(icon='edit', on_click=lambda e=None, apx=ap: abrir_form_apontamento(os_id, atividade, apx)).props('flat round dense')
                                        ui.button(icon='delete', on_click=lambda e=None, apx=ap: confirmar_excluir_apontamento(apx)).props('flat round dense color=negative')

                    with ui.row().classes('w-full items-center justify-between mt-2'):
                        ui.label('MATERIAIS').classes('text-sm font-bold text-slate-800')
                        if editavel_os:
                            ui.button('ADICIONAR MATERIAL', icon='add', on_click=lambda e=None, atv=atividade: abrir_form_material(os_id, atv)).props('flat').classes('text-slate-700')
                    if not mat_atividade:
                        ui.label('NENHUM MATERIAL.').classes('text-xs text-slate-500')
                    for mat in mat_atividade:
                        with ui.row().classes('w-full items-center justify-between border border-slate-200 rounded-lg px-3 py-2 bg-slate-50'):
                            with ui.column().classes('gap-0 flex-1'):
                                ui.label(mat.get('descricao_material') or '-').classes('text-sm font-bold text-slate-800')
                                ui.label(f"QTD: {mat.get('quantidade') or 0} {mat.get('unidade') or ''} | UNIT: {_fmt_brl(mat.get('custo_unitario', 0))} | TOTAL: {_fmt_brl(mat.get('custo_total', 0))}").classes('text-xs text-slate-500')
                                ui.label(mat.get('observacao') or '-').classes('text-xs text-slate-700')
                            if editavel_os:
                                ui.button(icon='delete', on_click=lambda e=None, mat_id=mat['id']: confirmar_excluir_material(mat_id)).props('flat round dense color=negative')

    def abrir_form_os(item=None):
        editando = bool(item)
        alvos = db.listar_alvos_os()
        opcoes_alvo = {a['id']: _label_alvo(a) for a in alvos}
        alvo_inicial = item.get('componente_id') or item.get('equipamento_id') if item else None
        with ui.dialog() as dialog, ui.card().classes('w-[980px] max-w-[96vw] p-5 rounded-2xl gap-4'):
            ui.label('EDITAR OS' if editando else 'NOVA OS').classes('text-lg font-bold')
            numero = ui.input('NÚMERO').props('outlined dense readonly').classes('w-full')
            numero.value = item.get('numero') if item else db.proximo_numero_os()
            tipo_os = ui.select(TIPOS_OS, value=item.get('tipo_os') if item else 'CORRETIVA', label='TIPO DE OS').props('outlined dense').classes('w-full')
            status = ui.select(STATUS_OS, value=item.get('status') if item else 'ABERTA', label='STATUS').props('outlined dense').classes('w-full')
            alvo = ui.select(opcoes_alvo, value=alvo_inicial, label='EQUIPAMENTO / COMPONENTE').props('outlined dense').classes('w-full')
            medidor_toggle = ui.toggle(['HORÍMETRO', 'ODÔMETRO'], value=item.get('unidade_medidor') if item else 'HORÍMETRO').props('unelevated')
            medidor = ui.number('HORÍMETRO', value=item.get('medidor_valor') if item else None).props('outlined dense').classes('w-full')
            descricao = ui.input('DESCRIÇÃO').props('outlined dense').classes('w-full')
            descricao.value = item.get('descricao') if item else ''
            data_abertura = _date_input('DATA DE ABERTURA', item.get('data_abertura') if item else datetime.now().strftime('%Y-%m-%d'))
            observacoes = ui.textarea('OBSERVAÇÕES').props('outlined autogrow').classes('w-full')
            observacoes.value = item.get('observacoes') if item else ''
            justificativa = ui.textarea('JUSTIFICATIVA DE ENCERRAMENTO').props('outlined autogrow').classes('w-full')
            justificativa.value = item.get('justificativa_encerramento') if item else ''

            def refresh_medidor():
                medidor.label = medidor_toggle.value
                medidor.update()
            medidor_toggle.on('update:model-value', lambda e: refresh_medidor())
            refresh_medidor()

            def salvar():
                try:
                    if editando:
                        db.atualizar_os(item['id'], alvo.value, descricao.value, tipo_os.value, None, observacoes.value,
                                        status.value, justificativa.value, data_abertura.value, medidor_toggle.value, medidor.value)
                    else:
                        db.criar_os(alvo.value, descricao.value, tipo_os.value, None, observacoes.value,
                                    numero.value, data_abertura.value, medidor_toggle.value, medidor.value, status.value, justificativa.value)
                    dialog.close()
                    carregar_lista()
                    render_detalhe()
                    ui.notify('OS SALVA', type='positive')
                except Exception as ex:
                    ui.notify(str(ex), type='negative')

            with ui.row().classes('justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def excluir_os(item):
        db.excluir_os(item['id'])
        estado['os_id'] = None
        carregar_lista()
        render_detalhe()
        ui.notify('OS EXCLUÍDA', type='positive')

    def confirmar_excluir_os(item):
        with ui.dialog() as dialog, ui.card().classes('w-[420px] max-w-[96vw] p-5 rounded-2xl gap-4'):
            ui.label('CONFIRMAR EXCLUSÃO').classes('text-lg font-bold text-red-700')
            ui.label(f"Deseja excluir a OS {item.get('numero') or '-'}?").classes('text-sm text-slate-700')
            def confirmar():
                try:
                    excluir_os(item)
                    dialog.close()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=confirmar).props('unelevated').classes('bg-red-600 text-white')
        dialog.open()

    def abrir_form_atividade(os_id, atv=None):
        editando = bool(atv)
        with ui.dialog() as dialog, ui.card().classes('w-[760px] max-w-[96vw] p-5 gap-4 rounded-2xl'):
            ui.label('EDITAR ATIVIDADE' if atv else 'NOVA ATIVIDADE').classes('text-lg font-bold')
            descricao = ui.input('DESCRIÇÃO DA ATIVIDADE').props('outlined dense').classes('w-full')
            descricao.value = atv.get('descricao') if atv else ''
            status = ui.select(STATUS_ATIVIDADE, value=atv.get('status') if atv else 'ABERTA', label='STATUS').props('outlined dense').classes('w-full')
            classificacao = ui.select(CLASSIFICACOES, value=atv.get('classificacao') if atv else 'INTERNA', label='CLASSIFICAÇÃO').props('outlined dense').classes('w-full')
            duracao = ui.input('DURAÇÃO TOTAL (HH:MM)').props('outlined dense').classes('w-full')
            duracao.value = _fmt_hhmm_from_minutes(atv.get('duracao_min') or 0) if atv else ''
            custo = ui.number('CUSTO SERVIÇO TERCEIRO').props('outlined dense').classes('w-full')
            custo.value = atv.get('custo_servico_terceiro') if atv else 0
            observacao = ui.textarea('OBSERVAÇÃO').props('outlined autogrow').classes('w-full')
            observacao.value = atv.get('observacao') if atv else ''

            def refresh_classificacao():
                terceiro = classificacao.value == 'SERVIÇO TERCEIRO'
                duracao.set_visibility(terceiro)
                custo.set_visibility(terceiro)
                status.update()

            classificacao.on('update:model-value', lambda e: refresh_classificacao())
            duracao.on('update:model-value', lambda e: setattr(duracao, 'value', _normalize_hhmm(duracao.value)))
            duracao.on('blur', lambda e: (setattr(duracao, 'value', _normalize_hhmm(duracao.value)), duracao.update()))
            refresh_classificacao()

            def salvar():
                try:
                    kwargs = dict(
                        descricao=descricao.value,
                        observacao=observacao.value,
                        status=status.value,
                        classificacao=classificacao.value,
                        duracao_min=_parse_hhmm(duracao.value) if classificacao.value == 'SERVIÇO TERCEIRO' else 0,
                        custo_hh=0,
                        custo_servico_terceiro=float(custo.value or 0) if classificacao.value == 'SERVIÇO TERCEIRO' else 0,
                    )
                    if editando:
                        db.atualizar_os_atividade(atv['id'], **kwargs)
                        _set_os_id(atv.get('os_id') or os_id)
                    else:
                        db.criar_os_atividade(os_id, **kwargs)
                        _set_os_id(os_id)
                    dialog.close()
                    refresh_current_os(reload_list=False, texto='ATUALIZANDO OS...')
                    ui.notify('ATIVIDADE SALVA', type='positive')
                except Exception as ex:
                    ui.notify(str(ex), type='negative')

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def excluir_atividade(atv):
        db.excluir_os_atividade(atv['id'])
        refresh_current_os(reload_list=False, texto='ATUALIZANDO OS...')
        ui.notify('ATIVIDADE EXCLUÍDA', type='positive')

    def confirmar_excluir_atividade(atv):
        with ui.dialog() as dialog, ui.card().classes('w-[420px] max-w-[96vw] p-5 rounded-2xl gap-4'):
            ui.label('CONFIRMAR EXCLUSÃO').classes('text-lg font-bold text-red-700')
            ui.label(f"Deseja excluir a atividade {atv.get('sequencia', 0):02d}?").classes('text-sm text-slate-700')
            def confirmar():
                try:
                    excluir_atividade(atv)
                    dialog.close()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=confirmar).props('unelevated').classes('bg-red-600 text-white')
        dialog.open()

    def abrir_form_apontamento(os_id, atividade, apontamento=None):
        funcionarios = db.listar_funcionarios(apenas_ativos=True)
        opcoes = {f['id']: f"{f.get('nome') or '-'}" for f in funcionarios}
        mapa = {f['id']: f for f in funcionarios}
        editando = bool(apontamento)
        classif = str((atividade or {}).get('classificacao') or 'INTERNA').upper()
        terceiro = classif == 'SERVIÇO TERCEIRO'

        with ui.dialog() as dialog, ui.card().classes('w-[820px] max-w-[96vw] p-5 rounded-2xl gap-4'):
            ui.label('EDITAR APONTAMENTO' if editando else 'NOVO APONTAMENTO').classes('text-lg font-bold text-slate-800')
            empresa_terceira = None
            if terceiro:
                empresa_terceira = ui.input('EMPRESA').props('outlined dense').classes('w-full')
                if apontamento:
                    empresa_terceira.value = (apontamento.get('empresa_terceira') or apontamento.get('funcionario_nome') or '')
            else:
                funcionario_id = ui.select(opcoes, label='FUNCIONÁRIO', value=(apontamento or {}).get('funcionario_id')).props('outlined dense').classes('w-full')
                equipe = ui.input('EQUIPE').props('outlined dense readonly').classes('w-full')
            data_apontamento = _date_input('DATA', (apontamento or {}).get('data_apontamento') or datetime.now().strftime('%Y-%m-%d'))
            data_apontamento.props(f'max={date.today().strftime("%Y-%m-%d")}')
            dia_todo = None
            info_escala = ui.label('').classes('text-xs text-slate-500')
            if not terceiro:
                dia_todo = ui.switch('APONTAR DIA TODO PELA ESCALA', value=((apontamento or {}).get('modo_hora') == 'ESCALA'))
            hora_inicio = _time_input('HORA INÍCIO')
            modo_hora = None
            if not terceiro:
                modo_hora = ui.toggle(MODO_HORA, value='DURAÇÃO' if (apontamento and not (apontamento.get('hora_fim') or '')) else 'HORA FIM').props('unelevated')
            hora_fim = _time_input('HORA FIM')
            duracao = _time_input('DURAÇÃO (HH:MM)')
            descricao_servico = ui.input('SERVIÇO REALIZADO', value=(apontamento or {}).get('descricao_servico') or '').props('outlined dense').classes('w-full')
            observacao = ui.textarea('OBSERVAÇÃO', value=(apontamento or {}).get('observacao') or '').props('outlined autogrow').classes('w-full')

            if apontamento:
                hora_inicio.value = _normalize_hhmm((apontamento or {}).get('hora_inicio') or '', True)
                hora_fim.value = _normalize_hhmm((apontamento or {}).get('hora_fim') or '', True)
                duracao.value = _fmt_hhmm_from_minutes((apontamento or {}).get('duracao_min') or 0)

            def sync_funcionario():
                if terceiro:
                    return
                func = mapa.get(funcionario_id.value)
                equipe.value = (func.get('equipe_nome') if func else '') or ''
                equipe.update()

            def sync_mode():
                if terceiro:
                    return
                usando_escala = bool(dia_todo.value)
                if usando_escala:
                    hora_inicio.disable(); hora_fim.disable(); duracao.disable(); modo_hora.disable()
                else:
                    hora_inicio.enable(); modo_hora.enable()
                    if modo_hora.value == 'HORA FIM':
                        hora_fim.enable(); duracao.disable()
                    else:
                        duracao.enable(); hora_fim.disable()
                hora_inicio.update(); hora_fim.update(); duracao.update(); modo_hora.update()

            def sync_dia_todo(*_):
                if terceiro:
                    return
                func = mapa.get(funcionario_id.value)
                if not dia_todo.value:
                    info_escala.set_text(''); sync_mode(); return
                if not func or not func.get('id'):
                    ui.notify('Selecione o funcionário primeiro', type='warning')
                    dia_todo.value = False; dia_todo.update(); sync_mode(); return
                escala = db.get_escala_para_data(func.get('id'), data_apontamento.value)
                if not escala:
                    ui.notify('Não existe jornada definida na escala para este dia', type='warning')
                    dia_todo.value = False; dia_todo.update(); sync_mode(); return
                inicio = str(escala.get('inicio') or '')
                fim = str(escala.get('fim') or '')
                hora_inicio.value = inicio; hora_fim.value = fim
                mi = db._hora_to_min(inicio) if hasattr(db, '_hora_to_min') else _parse_hhmm(inicio)
                mf = db._hora_to_min(fim) if hasattr(db, '_hora_to_min') else _parse_hhmm(fim)
                if mf < mi: mf += 24 * 60
                total = mf - mi
                int_ini = str(escala.get('intervalo_inicio') or '')
                int_fim = str(escala.get('intervalo_fim') or '')
                if int_ini and int_fim:
                    ii = db._hora_to_min(int_ini) if hasattr(db, '_hora_to_min') else _parse_hhmm(int_ini)
                    ifi = db._hora_to_min(int_fim) if hasattr(db, '_hora_to_min') else _parse_hhmm(int_fim)
                    total -= max(0, ifi - ii)
                    info_escala.set_text(f"ESCALA: {escala.get('nome') or '-'} | 1º APONT.: {inicio} ÀS {int_ini} | 2º APONT.: {int_fim} ÀS {fim} | DURAÇÃO: {_fmt_hhmm_from_minutes(max(0, total))}")
                else:
                    info_escala.set_text(f"ESCALA: {escala.get('nome') or '-'} | JORNADA DO DIA: {inicio} ÀS {fim} | DURAÇÃO: {_fmt_hhmm_from_minutes(max(0, total))}")
                duracao.value = _fmt_hhmm_from_minutes(max(0, total))
                hora_inicio.update(); hora_fim.update(); duracao.update(); sync_mode()

            def ao_digitar_hora(ctrl):
                bruto = ''.join(ch for ch in str(ctrl.value or '') if ch.isdigit())[:4]
                ctrl.value = bruto if len(bruto) <= 2 else f"{bruto[:2]}:{bruto[2:]}"
                ctrl.update()

            def normalizar_hora_final(ctrl):
                ctrl.value = _normalize_hhmm(ctrl.value, True)
                ctrl.update()

            def calc_from_fim():
                if (dia_todo and dia_todo.value) or not hora_inicio.value or not hora_fim.value:
                    return
                mi = _parse_hhmm(hora_inicio.value); mf = _parse_hhmm(hora_fim.value)
                if mf < mi: mf += 24 * 60
                duracao.value = _fmt_hhmm_from_minutes(mf - mi); duracao.update()

            def calc_from_dur():
                if (dia_todo and dia_todo.value) or not hora_inicio.value or not duracao.value:
                    return
                mi = _parse_hhmm(hora_inicio.value); md = _parse_hhmm(duracao.value)
                total = mi + md
                hora_fim.value = f"{(total // 60) % 24:02d}:{total % 60:02d}"; hora_fim.update()

            if terceiro:
                hora_inicio.on('input', lambda e: ao_digitar_hora(hora_inicio))
                hora_fim.on('input', lambda e: ao_digitar_hora(hora_fim))
                duracao.on('input', lambda e: ao_digitar_hora(duracao))
                hora_inicio.on('blur', lambda e: normalizar_hora_final(hora_inicio))
                hora_fim.on('blur', lambda e: (normalizar_hora_final(hora_fim), calc_from_fim()))
                duracao.on('blur', lambda e: (normalizar_hora_final(duracao), calc_from_dur()))
            else:
                funcionario_id.on('update:model-value', lambda e: (sync_funcionario(), sync_dia_todo()))
                modo_hora.on('update:model-value', lambda e: (sync_mode(), calc_from_fim() if modo_hora.value == 'HORA FIM' else calc_from_dur()))
                dia_todo.on('update:model-value', lambda e: sync_dia_todo())
                data_apontamento.on('update:model-value', lambda e: sync_dia_todo())
                hora_inicio.on('input', lambda e: ao_digitar_hora(hora_inicio))
                hora_fim.on('input', lambda e: ao_digitar_hora(hora_fim))
                duracao.on('input', lambda e: ao_digitar_hora(duracao))
                hora_inicio.on('blur', lambda e: (normalizar_hora_final(hora_inicio), calc_from_fim() if modo_hora.value == 'HORA FIM' else calc_from_dur()))
                hora_fim.on('blur', lambda e: (normalizar_hora_final(hora_fim), calc_from_fim() if modo_hora.value == 'HORA FIM' else None))
                duracao.on('blur', lambda e: (normalizar_hora_final(duracao), calc_from_dur() if modo_hora.value == 'DURAÇÃO' else None))
                sync_funcionario(); sync_dia_todo(); sync_mode()

            def salvar():
                try:
                    data_apontamento_iso = _date_to_iso(data_apontamento.value)
                    data_apontamento_validada = _parse_date_any(data_apontamento_iso)
                    if not data_apontamento_validada:
                        ui.notify('INFORME UMA DATA VÁLIDA.', color='negative')
                        return
                    if data_apontamento_validada > date.today():
                        ui.notify('NÃO É PERMITIDO APONTAMENTO EM DATA FUTURA.', color='negative')
                        return
                    payload = dict(
                        os_id=os_id,
                        atividade_id=atividade['id'],
                        descricao_servico=descricao_servico.value,
                        observacao=observacao.value,
                        data_apontamento=data_apontamento_iso,
                    )
                    if terceiro:
                        payload.update(dict(
                            empresa_terceira=empresa_terceira.value,
                            hora_inicio=_normalize_hhmm(hora_inicio.value, True),
                            hora_fim=_normalize_hhmm(hora_fim.value, True) or None,
                            duracao_min=_parse_hhmm(duracao.value) if duracao.value else None,
                            usar_escala=False,
                        ))
                    else:
                        payload.update(dict(
                            funcionario_id=funcionario_id.value,
                            hora_inicio=_normalize_hhmm(hora_inicio.value, True),
                            hora_fim=_normalize_hhmm(hora_fim.value, True) if (modo_hora.value == 'HORA FIM' and not dia_todo.value) else None,
                            duracao_min=_parse_hhmm(duracao.value) if (modo_hora.value == 'DURAÇÃO' and not dia_todo.value) else None,
                            usar_escala=bool(dia_todo.value),
                        ))
                    if editando:
                        db.atualizar_os_apontamento(apontamento['id'], **payload)
                        ui.notify('APONTAMENTO ATUALIZADO', type='positive')
                    else:
                        db.criar_os_apontamento(**payload)
                        ui.notify('APONTAMENTO SALVO', type='positive')
                    dialog.close(); refresh_current_os(reload_list=True, texto='ATUALIZANDO OS...')
                except Exception as ex:
                    ui.notify(str(ex), type='negative')

            with ui.row().classes('w-full justify-end gap-2 pt-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-blue-500 text-black font-bold')
        dialog.open()

    def confirmar_excluir_apontamento(ap):
        with ui.dialog() as dialog, ui.card().classes('w-[420px] max-w-[96vw] p-5 rounded-2xl gap-4'):
            ui.label('CONFIRMAR EXCLUSÃO').classes('text-lg font-bold text-red-700')
            ui.label('Deseja excluir este apontamento?').classes('text-sm text-slate-700')

            def excluir():
                try:
                    db.excluir_os_apontamento(ap['id'])
                    dialog.close()
                    refresh_current_os(reload_list=True, texto='ATUALIZANDO OS...')
                    ui.notify('APONTAMENTO EXCLUÍDO', type='positive')
                except Exception as ex:
                    ui.notify(str(ex), type='negative')

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=excluir).props('unelevated').classes('bg-red-600 text-white')
        dialog.open()

    def abrir_form_material(os_id, atividade):
        with ui.dialog() as dialog, ui.card().classes('w-[700px] max-w-[96vw] p-5 rounded-2xl gap-4'):
            ui.label('NOVO MATERIAL').classes('text-lg font-bold text-slate-800')
            descricao = ui.input('MATERIAL').props('outlined dense').classes('w-full')
            quantidade = ui.number('QUANTIDADE', value=1, min=0, step=0.01).props('outlined dense').classes('w-full')
            unidade = ui.input('UNIDADE').props('outlined dense').classes('w-full')
            custo_unitario = ui.number('CUSTO UNITÁRIO').props('outlined dense').classes('w-full')
            total_label = ui.label('TOTAL: R$ 0,00').classes('text-sm font-bold text-slate-700')
            observacao = ui.textarea('OBSERVAÇÃO').props('outlined autogrow').classes('w-full')

            def atualizar_total(*_):
                total_label.set_text(f'TOTAL: {_fmt_brl(float(quantidade.value or 0) * float(custo_unitario.value or 0))}')
            quantidade.on('update:model-value', atualizar_total)
            custo_unitario.on('update:model-value', atualizar_total)
            atualizar_total()

            def salvar():
                try:
                    db.criar_os_material(os_id=os_id, atividade_id=atividade['id'], descricao_material=descricao.value,
                                         quantidade=quantidade.value, custo_unitario=custo_unitario.value, unidade=unidade.value,
                                         observacao=observacao.value)
                    dialog.close()
                    refresh_current_os(reload_list=False, texto='ATUALIZANDO OS...')
                    ui.notify('MATERIAL SALVO', type='positive')
                except Exception as ex:
                    ui.notify(str(ex), type='negative')

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('SALVAR', on_click=salvar).props('unelevated').classes('bg-amber-500 text-black font-bold')
        dialog.open()

    def excluir_material(mat_id):
        db.excluir_os_material(mat_id)
        refresh_current_os(reload_list=False, texto='ATUALIZANDO OS...')
        ui.notify('MATERIAL EXCLUÍDO', type='positive')

    def confirmar_excluir_material(mat_id):
        with ui.dialog() as dialog, ui.card().classes('w-[420px] max-w-[96vw] p-5 rounded-2xl gap-4'):
            ui.label('CONFIRMAR EXCLUSÃO').classes('text-lg font-bold text-red-700')
            ui.label('Deseja excluir este material?').classes('text-sm text-slate-700')
            def confirmar():
                try:
                    excluir_material(mat_id)
                    dialog.close()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=confirmar).props('unelevated').classes('bg-red-600 text-white')
        dialog.open()

    def excluir_anexo_os(anexo_id):
        db.remover_os_anexo(anexo_id)
        refresh_current_os(reload_list=False, texto='ATUALIZANDO OS...')
        ui.notify('ANEXO EXCLUÍDO', type='positive')

    def confirmar_excluir_anexo_os(anexo_id):
        with ui.dialog() as dialog, ui.card().classes('w-[420px] max-w-[96vw] p-5 rounded-2xl gap-4'):
            ui.label('CONFIRMAR EXCLUSÃO').classes('text-lg font-bold text-red-700')
            ui.label('Deseja excluir este anexo da OS?').classes('text-sm text-slate-700')
            def confirmar():
                try:
                    excluir_anexo_os(anexo_id)
                    dialog.close()
                except Exception as ex:
                    ui.notify(str(ex), type='negative')
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('CANCELAR', on_click=dialog.close).props('flat')
                ui.button('EXCLUIR', on_click=confirmar).props('unelevated').classes('bg-red-600 text-white')
        dialog.open()

    busca.on('keydown.enter', lambda e: carregar_lista())
    busca.on('update:model-value', lambda e: carregar_lista() if not str(getattr(busca, 'value', '') or '').strip() else None)

    def _carga_inicial_os():
        _set_loading(True, 'CARREGANDO OS...')
        try:
            carregar_lista(force=True)
            if _get_os_id():
                render_detalhe(force=True)
        finally:
            _set_loading(False)

    ui.timer(0.12, _carga_inicial_os, once=True)


