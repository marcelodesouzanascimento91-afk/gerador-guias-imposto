"""Geração do PDF da GNRE (modelo oficial em 3 vias) com código de barras I2of5."""
import os
import re
from datetime import date

from reportlab.graphics.barcode.common import I2of5
from reportlab.lib.colors import black, HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

VIAS = ["1ª via – Banco", "2ª via – Contribuinte", "3ª via – Contribuinte / Acompanha a mercadoria"]
CINZA = HexColor("#555555")


def moeda(v):
    if v is None:
        return ""
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_doc(d):
    d = re.sub(r"\D", "", d or "")
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d


def fmt_linha(l):
    l = re.sub(r"\D", "", l or "")
    if len(l) == 48:
        return "  ".join(f"{l[i:i + 11]}-{l[i + 11]}" for i in range(0, 48, 12))
    return l


def br(d):
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    return d.strftime("%d/%m/%Y")


def nome_arquivo(g, cfg):
    padrao = cfg.get("geral", "padrao_nome_arquivo", "{vencimento} - NF {nf} - {tipo} {uf}")
    fmt = cfg.get("geral", "formato_data_arquivo", "%Y-%m-%d")
    venc = g.vencimento
    if g.data_pagamento and g.data_pagamento > g.vencimento:
        venc = g.data_pagamento  # guia vencida: nova data de pagamento (com encargos)
    if g.ret_data_limite:
        try:
            venc = date.fromisoformat(g.ret_data_limite[:10])
        except ValueError:
            pass
    qtd = len({o.nNF for o in g.obrigacoes})
    nf = g.nfs if qtd <= 3 else f"{g.nfs.split(', ')[0]} e outras {qtd - 1}"
    nome = padrao.format(vencimento=venc.strftime(fmt), nf=nf, tipo=g.tipo, uf=g.uf,
                         receita=g.receita, cliente=g.obrigacoes[0].cliente[:30])
    return re.sub(r'[\\/:*?"<>|]+', "-", nome).strip() + ".pdf"


def _campo(c, x, y, w, h, rotulo, valor, tam=8, negrito=False, alinhar="l"):
    c.setLineWidth(0.5)
    c.rect(x, y, w, h)
    c.setFont("Helvetica", 5.5)
    c.setFillColor(CINZA)
    c.drawString(x + 1.2 * mm, y + h - 2.3 * mm, rotulo)
    c.setFillColor(black)
    c.setFont("Helvetica-Bold" if negrito else "Helvetica", tam)
    valor = str(valor or "")
    max_chars = int((w - 2 * mm) / (tam * 0.5)) or 1
    if alinhar == "r":
        c.drawRightString(x + w - 1.2 * mm, y + 1.5 * mm, valor[:max_chars])
    else:
        c.drawString(x + 1.2 * mm, y + 1.5 * mm, valor[:max_chars])


def _via(c, g, cfg, topo, via, demonstrativo):
    n = g.nota
    L, R = 10 * mm, 200 * mm
    W = R - L
    h = 6.2 * mm
    y = topo - 8 * mm
    # Cabeçalho
    c.setLineWidth(0.8)
    c.rect(L, y, W * 0.62, 8 * mm)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(L + 2 * mm, y + 4.2 * mm, "GNRE")
    c.setFont("Helvetica", 6.5)
    c.drawString(L + 14 * mm, y + 4.6 * mm, "Guia Nacional de Recolhimento")
    c.drawString(L + 14 * mm, y + 1.8 * mm, "de Tributos Estaduais")
    c.setFont("Helvetica-Oblique", 6)
    c.drawRightString(L + W * 0.62 - 2 * mm, y + 1.8 * mm, via)
    _campo(c, L + W * 0.62, y, W * 0.16, 8 * mm, "UF Favorecida", g.uf, 11, True)
    _campo(c, L + W * 0.78, y, W * 0.22, 8 * mm, "Código da Receita", g.receita, 11, True)

    # Bloco esquerdo – emitente
    lw, rx, rw = W * 0.62, L + W * 0.62, W * 0.38
    y0 = y
    c.setFont("Helvetica-Bold", 6)
    linhas_esq = [
        ("Razão Social (Contribuinte Emitente)", n.emit_nome, 1.0),
        ("CNPJ/CPF/Inscrição Estadual", f"{fmt_doc(n.emit_cnpj)}   IE: {n.emit_iest or n.emit_ie}", 1.0),
        ("Endereço", f"{n.emit_lgr}, {n.emit_nro} - {n.emit_bairro}", 1.0),
    ]
    yy = y0
    for rot, val, _ in linhas_esq:
        yy -= h
        _campo(c, L, yy, lw, h, rot, val)
    yy -= h
    _campo(c, L, yy, lw * 0.5, h, "Município", n.emit_xmun)
    _campo(c, L + lw * 0.5, yy, lw * 0.12, h, "UF", n.emit_uf)
    _campo(c, L + lw * 0.62, yy, lw * 0.18, h, "CEP", n.emit_cep)
    _campo(c, L + lw * 0.80, yy, lw * 0.20, h, "Telefone", cfg.get("empresa", "telefone") or n.emit_fone)
    # Destinatário
    if g.incluir_destinatario:
        dest_doc = f"{fmt_doc(n.dest_doc)}" + (f"   IE: {n.dest_ie}" if n.dest_ie else "")
        dest_mun = f"{n.dest_xmun} / {n.dest_uf}"
        dest_nome = n.dest_nome
    else:
        dest_doc, dest_mun, dest_nome = "Diversos (guia consolidada)", "", f"{len({o.chave for o in g.obrigacoes})} notas fiscais"
    yy -= h
    _campo(c, L, yy, lw, h, "Destinatário – Razão Social", dest_nome)
    yy -= h
    _campo(c, L, yy, lw * 0.55, h, "Destinatário – CNPJ/CPF/Inscrição Estadual", dest_doc)
    _campo(c, L + lw * 0.55, yy, lw * 0.45, h, "Município do Destinatário", dest_mun)
    # Informações complementares
    info_h = 4 * h
    yy -= info_h
    c.rect(L, yy, lw, info_h)
    c.setFont("Helvetica", 5.5)
    c.setFillColor(CINZA)
    c.drawString(L + 1.2 * mm, yy + info_h - 2.3 * mm, "Informações Complementares")
    c.setFillColor(black)
    c.setFont("Helvetica", 6.6)
    rotulo = g.tipo + (" + FCP" if g.fcp else "")
    info = [f"{rotulo} – NF-e nº {g.nfs}" + (f" série {g.obrigacoes[0].serie}" if g.uma_nota else "")]
    if g.fcp:
        info.append(f"ICMS {moeda(g.principal)} + Fundo de Combate à Pobreza {moeda(g.fcp)}")
    if g.uma_nota:
        info.append(f"Chave: {n.chave}")
    else:
        info.append(f"Período de referência {g.referencia[1]}/{g.referencia[2]} – apuração consolidada")
    info.append(f"Modalidade: {g.modo}")
    if g.data_pagamento and g.data_pagamento > g.vencimento:
        info.append(f"Venc. original {br(g.vencimento)} – pagamento em {br(g.data_pagamento)} com encargos")
    if g.recibo:
        info.append(f"Recibo do lote: {g.recibo}")
    for i, t in enumerate(info[:6]):
        c.drawString(L + 1.5 * mm, yy + info_h - 5.2 * mm - i * 2.9 * mm, t[:105])

    # Bloco direito – valores
    tot_multa = g.ret_multa if g.ret_multa is not None else g.multa
    tot_juros = g.ret_juros if g.ret_juros is not None else g.juros
    venc_exib = g.ret_data_limite or (g.data_pagamento if g.data_pagamento and g.data_pagamento > g.vencimento else g.vencimento)
    direita = [
        ("Nº de Controle", g.nosso_numero or "—"),
        ("Data de Vencimento", br(venc_exib)),
        ("Nº Documento de Origem", g.doc_origem or "—"),
        ("Período de Referência / Parcela", f"{g.referencia[1]}/{g.referencia[2]}" if g.referencia else ""),
        ("Valor Principal", moeda(g.principal + g.fcp)),
        ("Atualização Monetária", moeda(g.ret_atualizacao or 0)),
        ("Juros", moeda(tot_juros)),
        ("Multa", moeda(tot_multa)),
        ("Total a Recolher", moeda(g.total)),
    ]
    yy2 = y0
    hd = (y0 - yy) / len(direita)
    for i, (rot, val) in enumerate(direita):
        yy2 -= hd
        _campo(c, rx, yy2, rw, hd, rot, val, 9 if i == 8 else 8, i in (1, 8), "r")

    # Linha digitável e código de barras
    yb = yy - 4.5 * mm
    c.setFont("Helvetica-Bold", 9.5)
    if g.linha_digitavel:
        c.drawString(L, yb, fmt_linha(g.linha_digitavel))
    c.setFont("Helvetica", 6)
    c.drawRightString(R, yb, "Autenticação mecânica")
    if g.codigo_barras and len(re.sub(r"\D", "", g.codigo_barras)) == 44:
        bc = I2of5(re.sub(r"\D", "", g.codigo_barras), barWidth=0.29 * mm, ratio=3, barHeight=12 * mm,
                   checksum=0, bearers=0, quiet=0)
        bc.drawOn(c, L, yb - 14 * mm)
    if demonstrativo:
        c.saveState()
        c.setFillColor(HexColor("#C62828"))
        c.setFont("Helvetica-Bold", 8)
        c.drawString(L, yb - 6 * mm, "DEMONSTRATIVO – SEM CÓDIGO DE BARRAS. NÃO É DOCUMENTO DE ARRECADAÇÃO.")
        c.setFont("Helvetica", 7)
        c.drawString(L, yb - 10 * mm, "Emita a guia no Portal GNRE (www.gnre.pe.gov.br) enviando o arquivo de lote XML gerado junto.")
        c.restoreState()


def gerar_pdf(g, cfg, pasta):
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, nome_arquivo(g, cfg))
    base, ext = os.path.splitext(caminho)
    k = 2
    while os.path.exists(caminho) and not g.arquivo_pdf == caminho:
        caminho = f"{base} ({k}){ext}"
        k += 1
    demonstrativo = not g.codigo_barras
    c = canvas.Canvas(caminho, pagesize=A4)
    c.setTitle(f"GNRE {g.tipo} {g.uf} - NF {g.nfs}")
    c.setAuthor("Gerador de Guias")
    altura_via = 95 * mm
    topo = A4[1] - 6 * mm
    for i, via in enumerate(VIAS):
        _via(c, g, cfg, topo - i * altura_via, via, demonstrativo)
        if i < 2:
            c.setDash(2, 2)
            c.setLineWidth(0.3)
            yc = topo - (i + 1) * altura_via + 2.5 * mm
            c.line(5 * mm, yc, A4[0] - 5 * mm, yc)
            c.setDash()
    c.showPage()
    c.save()
    g.arquivo_pdf = caminho
    return caminho
