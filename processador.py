"""Orquestra: leitura dos XMLs -> análise -> guias (webservice ou lote) -> PDFs -> histórico -> Excel."""
import json
import os
import re
from datetime import date, datetime

from lxml import etree

from . import gnre_ws
from .config import DADOS_DIR
from .db import Banco
from .nfe_parser import carregar_pasta
from .pdf_guia import gerar_pdf
from .prazos import Selic
from .regras import Analisador, Ocorrencia
from .relatorio import exportar


def descrever_fontes(fontes):
    if isinstance(fontes, str):
        return fontes
    arqs = [f for f in fontes if not os.path.isdir(f)]
    pastas = [f for f in fontes if os.path.isdir(f)]
    partes = []
    if pastas:
        partes.append("pasta(s) " + "; ".join(pastas))
    if arqs:
        partes.append(f"{len(arqs)} arquivo(s) selecionado(s)")
    return " + ".join(partes) or "(nenhum)"


def analisar_pasta(cfg, pasta_xml, data_pagamento, log=print):
    log(f"Lendo XMLs: {descrever_fontes(pasta_xml)}")
    if not cfg.cnpjs_remetente():
        log("ATENÇÃO: CNPJ da empresa remetente não configurado (aba Configurações) – "
            "todas as notas de saída serão analisadas, de qualquer emitente.")
    notas, ignorados = carregar_pasta(pasta_xml)
    log(f"  {len(notas)} NF-e encontradas; {len(ignorados)} arquivos XML ignorados (não são NF-e).")
    selic = Selic(os.path.join(DADOS_DIR, "selic.json"), cfg.getfloat("encargos", "selic_mes_fallback_pct", 1.0), log)
    res = Analisador(cfg, selic).analisar(notas, data_pagamento)
    log(f"  {res.notas_consideradas} notas de saída do(s) remetente(s) analisadas; "
        f"{sum(1 for o in res.obrigacoes if o.gera_guia)} obrigações a recolher por guia; "
        f"{len(res.ocorrencias)} ocorrências.")
    return res


def _regras_cache(cfg):
    amb = cfg.get("geral", "ambiente", "2")
    p = os.path.join(DADOS_DIR, f"configuf_amb{amb}.json")
    try:
        cache = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    return {tuple(k.split("-", 1)): v for k, v in cache.items()}


def gerar_lote_arquivo(guias, cfg, pasta, log):
    regras = _regras_cache(cfg)
    for g in guias:
        gnre_ws.aplicar_regras_uf(g, regras.get((g.uf, g.receita)))
    guias = gnre_ws.juntar_fcp(guias, regras)
    validas = []
    for g in guias:
        erros = gnre_ws.validar_lote(gnre_ws.dados_gnre_xml([g], cfg, regras))
        if erros:
            g.situacao, g.motivos = "ERRO XSD", erros[:5]
            log(f"  ATENÇÃO: NF {g.nfs} {g.tipo}/{g.uf} fora do lote – erro de schema: {erros[0]}")
        else:
            validas.append(g)
    por_lote = max(1, min(cfg.getint("webservice", "guias_por_lote", 50), 100))
    arquivos = []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for i in range(0, len(validas), por_lote):
        lote = validas[i:i + por_lote]
        root = gnre_ws.dados_gnre_xml(lote, cfg, regras)
        nome = os.path.join(pasta, f"Lote_GNRE_{stamp}_{i // por_lote + 1:02d}.xml")
        with open(nome, "wb") as f:
            f.write(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))
        arquivos.append((nome, lote))
        for g in lote:
            g.situacao = "LOTE GERADO – EMITIR NO PORTAL"
        log(f"  Arquivo de lote gerado: {os.path.basename(nome)} ({len(lote)} guias)")
    if not regras:
        log("  Dica: rode uma vez no modo webservice (com certificado) para baixar as regras de cada UF "
            "(campos extras, tipo de documento). Elas ficam em cache e melhoram o arquivo de lote.")
    return guias, arquivos


def emitir_pelo_portal(cfg, arquivos, pasta_pdf, log, perguntar=None):
    """Envia cada arquivo de lote no site do Portal GNRE (navegador), emite e salva os PDFs oficiais."""
    from .portal_navegador import PortalGNRE, separar_pdf, ErroPortal
    email = cfg.get("portal", "email")
    if not email and perguntar:
        email = perguntar("E-mail para o Portal GNRE",
                          "Informe o e-mail usado no Portal GNRE (recebe o protocolo e o código de validação):")
        if email:
            cfg.set("portal", "email", email.strip())
            cfg.save()
    if not email:
        raise ErroPortal("Informe o e-mail do Portal GNRE na aba Configurações.")
    portal = PortalGNRE(cfg, log, perguntar)
    os.makedirs(pasta_pdf, exist_ok=True)
    try:
        portal.abrir()
        for arquivo, lote in arquivos:
            try:
                protocolo = portal.executar(portal.enviar_lote, arquivo, email)
                for g in lote:
                    g.recibo = protocolo
                pdf, linhas = portal.executar(portal.consultar_e_emitir, protocolo, email)
            except ErroPortal as e:
                log(f"  ERRO no portal ({os.path.basename(arquivo)}): {e}")
                for g in lote:
                    if not g.recibo:
                        g.situacao, g.motivos = "ERRO PORTAL (SITE)", [str(e)]
                    else:
                        g.situacao, g.motivos = "PENDENTE CONSULTA", [f"Recibo {g.recibo}: {e}"]
                continue
            # situação de cada guia (sequencial = ordem no arquivo de lote)
            emitidas = []
            for i, g in enumerate(lote):
                lin = linhas[i] if i < len(linhas) else {}
                if lin.get("emitivel"):
                    g.situacao = "EMITIDA (PORTAL)"
                    emitidas.append(g)
                else:
                    g.situacao = "REJEITADA PORTAL"
                    g.motivos = [re.sub(r"\s+", " ", lin.get("situacao", "sem retorno"))[:400]]
            if pdf:
                completo = os.path.join(pasta_pdf, f"GNRE_Portal_Recibo_{lote[0].recibo}.pdf")
                with open(completo, "wb") as f:
                    f.write(pdf)
                log(f"  PDF oficial do portal salvo: {completo}")
                for g in emitidas:
                    g.arquivo_pdf = completo
                separar_pdf(pdf, emitidas, pasta_pdf, cfg, log)
                log(f"  {len(emitidas)} guia(s) emitida(s) e salvas em {pasta_pdf}")
    finally:
        portal.fechar()


def processar(cfg, pasta_xml, pasta_saida, modo, data_pagamento=None, senha="", log=print,
              reemitir=False, resultado_analise=None, filtro_chaves=None, pasta_pdf=None, perguntar=None):
    """modo: 'ws' (webservice), 'lote' (arquivo para upload), 'ambos' (ws com fallback p/ lote), 'analise'."""
    data_pagamento = data_pagamento or date.today()
    res = resultado_analise or analisar_pasta(cfg, pasta_xml, data_pagamento, log)
    banco = Banco(DADOS_DIR)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta_guias = pasta_pdf or os.path.join(pasta_saida, "Guias")
    pasta_lotes = os.path.join(pasta_saida, "Lotes_XML")
    os.makedirs(pasta_saida, exist_ok=True)

    obrig = [o for o in res.obrigacoes if not filtro_chaves or (o.chave, o.tipo) in filtro_chaves]
    ocorrencias = list(res.ocorrencias)   # cópia: não acumula entre execuções sobre a mesma análise
    a_gerar, nao_salvar = [], set()
    bloqueio = ("EMITIDA", "LOTE GERADO")
    for ob in obrig:
        ob.recibo_pendente = ob.id_guia_pendente = ""
        if not ob.gera_guia:
            continue
        ex = banco.existente(ob.chave, ob.tipo)
        sit = (ex or {}).get("situacao") or ""
        if ex and sit.startswith(bloqueio) and not reemitir:
            ob.situacao = sit
            ob.observacao = (ob.observacao.split(" | Já processada")[0] + " | " if ob.observacao else "") + \
                f"Já processada em {(ex.get('atualizado_em') or '')[:10]} – marque 'Reemitir' para gerar de novo " \
                f"({os.path.basename(ex.get('arquivo_pdf') or '')})"
            nao_salvar.add((ob.chave, ob.tipo))
            continue
        if ex and sit.startswith("PENDENTE CONSULTA") and ex.get("recibo") and ex.get("id_guia") and not reemitir:
            ob.recibo_pendente, ob.id_guia_pendente = ex["recibo"], ex["id_guia"]
        a_gerar.append(ob)

    guias = gnre_ws.montar_guias(a_gerar, cfg, cfg.getbool("geral", "consolidar_apuracao", True))
    log(f"{len(guias)} guia(s) a emitir.")
    if nao_salvar:
        log(f"{len(nao_salvar)} obrigação(ões) já processada(s) anteriormente foram ignoradas.")
    extras = {}

    if modo != "analise" and guias:
        os.makedirs(pasta_lotes, exist_ok=True)
        pendentes = [g for g in guias if not g.reconsultar]
        if modo in ("ws", "ambos"):
            pfx = cfg.get("certificado", "arquivo_pfx")
            cert = None
            try:
                if not pfx or not os.path.exists(pfx):
                    raise RuntimeError("Certificado A1 (.pfx) não configurado ou não encontrado.")
                cert = gnre_ws.CertificadoA1(pfx, senha or cfg.get_senha())
                log(f"Certificado: {cert.titular} (válido até {cert.validade:%d/%m/%Y})")
                if cert.validade < date.today():
                    raise RuntimeError("Certificado digital VENCIDO.")
                cli = gnre_ws.ClienteGNRE(cfg, cert, DADOS_DIR, log)
                guias = cli.preparar(guias)
                log("Enviando ao Portal GNRE (" + ("PRODUÇÃO" if cfg.get("geral", "ambiente") == "1" else "HOMOLOGAÇÃO") + ")...")
                guias, pdfs = cli.processar(guias, pasta_lotes)
                if pdfs.get("_lote"):
                    p = os.path.join(pasta_guias, f"Portal_GNRE_lote_{stamp}.pdf")
                    os.makedirs(pasta_guias, exist_ok=True)
                    open(p, "wb").write(pdfs["_lote"])
                    log(f"  PDF oficial do portal salvo: {os.path.basename(p)}")
            except Exception as e:
                log(f"ERRO no webservice: {e}")
                for g in guias:
                    if g.situacao == "A GERAR":
                        g.situacao, g.motivos = "ERRO ENVIO", [str(e)]
            finally:
                if cert:
                    cert.limpar()
            # Só vão para o arquivo de lote as guias que NÃO chegaram ao portal (sem recibo).
            # Rejeições de regra e lotes em processamento não são reenviados.
            pendentes = [g for g in guias if not g.recibo and g.situacao.startswith(("ERRO ENVIO", "ERRO PORTAL", "A GERAR"))]
            if modo == "ws":
                pendentes = []
            elif pendentes:
                log(f"{len(pendentes)} guia(s) não transmitida(s) pelo webservice: gerando arquivo de lote.")
        if modo == "lote":
            for g in guias:
                if g.reconsultar:
                    g.situacao = "PENDENTE CONSULTA"
                    g.motivos = [f"Recibo {g.recibo} aguardando consulta – processe no modo webservice."]
        if modo == "portal":
            for g in guias:
                if g.reconsultar:
                    g.reconsultar = False   # no site, reenvia como lote novo
            ids = {g.id for g in guias}
            guias, arquivos = gerar_lote_arquivo(guias, cfg, pasta_lotes, log)
            if arquivos:
                log("Abrindo o site do Portal GNRE no navegador...")
                try:
                    emitir_pelo_portal(cfg, arquivos, pasta_guias, log, perguntar)
                except Exception as e:
                    log(f"ERRO ao usar o site do portal: {e}")
                    log("  Os arquivos de lote foram gerados em Lotes_XML para envio manual, "
                        "ou processe de novo para tentar outra vez pelo site.")
                    for g in guias:
                        if g.situacao.startswith("LOTE GERADO"):
                            g.situacao, g.motivos = "PENDENTE PORTAL", [str(e)[:300]]
            pendentes = []
        if modo in ("lote", "ambos") and pendentes:
            for g in pendentes:
                g.situacao = "A GERAR"
                g.motivos = []
                g.linha_digitavel = g.codigo_barras = ""
            ids = {g.id for g in pendentes}
            pendentes, _ = gerar_lote_arquivo(pendentes, cfg, pasta_lotes, log)
            guias = [g for g in guias if g.id not in ids] + pendentes

        # PDFs
        for g in guias:
            if g.situacao.startswith(("ERRO", "REJEITADA", "PENDENTE")):
                for m in g.motivos:
                    log(f"  NF {g.nfs} {g.tipo}/{g.uf}: {m}")
            try:
                if not g.situacao.startswith(("REJEITADA", "ERRO", "PENDENTE")) and \
                        not (g.situacao == "EMITIDA (PORTAL)" and g.arquivo_pdf):
                    gerar_pdf(g, cfg, pasta_guias)
            except Exception as e:
                log(f"  Falha ao gerar PDF da NF {g.nfs}: {e}")
            for av in g.avisos:
                ocorrencias.append(Ocorrencia(g.obrigacoes[0].chave, g.nfs, "", "AVISO", f"{g.tipo}/{g.uf}: {av}"))
            for m in g.motivos:
                ocorrencias.append(Ocorrencia(g.obrigacoes[0].chave, g.nfs, "",
                                              "AVISO" if g.situacao.startswith("PENDENTE") else "ERRO",
                                              f"Portal GNRE {g.tipo}/{g.uf}: {m}"))
            for ob in g.obrigacoes:
                ob.situacao = g.situacao
                if g.ret_multa is not None and len(g.obrigacoes) == 1:
                    ob.multa, ob.juros = g.ret_multa, (g.ret_juros or 0) + (g.ret_atualizacao or 0)
                extras[(ob.chave, ob.tipo)] = {
                    "linha_digitavel": g.linha_digitavel, "codigo_barras": g.codigo_barras,
                    "nosso_numero": g.nosso_numero, "recibo": g.recibo, "id_guia": str(g.id),
                    "arquivo_pdf": g.arquivo_pdf}
        log(f"PDFs gravados em: {pasta_guias}")

    # Histórico e Excel
    if modo != "analise":
        for ob in obrig:
            if (ob.chave, ob.tipo) not in nao_salvar:
                banco.salvar(ob, extras.get((ob.chave, ob.tipo)))
        banco.salvar_ocorrencias(ocorrencias)
    linhas = []
    for ob in obrig:
        e = extras.get((ob.chave, ob.tipo), {})
        linhas.append({
            "nf": ob.nNF, "serie": ob.serie, "data_emissao": ob.data_emissao, "cliente": ob.cliente,
            "cliente_doc": ob.cliente_doc, "uf": ob.uf, "tipo": ob.tipo, "receita": ob.receita, "modo": ob.modo,
            "valor": ob.valor, "multa": ob.multa, "juros": ob.juros, "vencimento": ob.vencimento,
            "vencimento_original": ob.vencimento_original, "data_pagamento": ob.data_pagamento,
            "dias_atraso": ob.dias_atraso, "situacao": ob.situacao, "observacao": ob.observacao,
            "chave": ob.chave, **e})
    linhas.sort(key=lambda x: (x["vencimento"], str(x["nf"]).zfill(9)))
    xlsx = os.path.join(pasta_saida, f"Relatorio_Guias_{stamp}.xlsx")
    exportar(linhas, xlsx, "Relatório de Guias de Impostos – Processamento", ocorrencias,
             f"Pagamento previsto {data_pagamento:%d/%m/%Y} | XMLs: {descrever_fontes(pasta_xml)}")
    log(f"Relatório Excel: {xlsx}")
    return {"guias": guias, "obrigacoes": obrig, "ocorrencias": ocorrencias, "excel": xlsx,
            "pasta_guias": pasta_guias}
