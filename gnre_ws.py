"""Montagem do lote GNRE v2.00, validação XSD e comunicação com o Portal GNRE (webservice).

Fluxo: GnreConfigUF (regras da UF/receita) -> GnreLoteRecepcao (envio) ->
GnreResultadoLote (consulta, com código de barras / linha digitável).
"""
import json
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import requests
from lxml import etree

from .config import resource_dir
from .regras import OPERACAO

NS_GNRE = "http://www.gnre.pe.gov.br"
NSG = {"g": NS_GNRE}


# ======================================================================== modelo
@dataclass
class Guia:
    id: int
    obrigacoes: list
    uf: str
    receita: str
    tipo: str
    modo: str
    principal: float
    fcp: float = 0.0                # FCP no mesmo item (tipo 12) quando a UF exige
    vencimento: date = None
    data_pagamento: date = None
    multa: float = 0.0
    juros: float = 0.0
    doc_origem: str = ""
    tipo_doc_origem: str = "10"
    referencia: tuple = None        # (periodo, mes, ano)
    campos_extras: list = field(default_factory=list)   # [(codigo, valor)]
    incluir_destinatario: bool = True
    detalhamento: str = ""
    produto: str = ""
    # retorno do portal
    situacao: str = "A GERAR"
    linha_digitavel: str = ""
    codigo_barras: str = ""
    nosso_numero: str = ""
    recibo: str = ""
    ret_multa: float = None
    ret_juros: float = None
    ret_atualizacao: float = None
    ret_total: float = None
    ret_data_limite: str = ""
    motivos: list = field(default_factory=list)
    avisos: list = field(default_factory=list)
    arquivo_pdf: str = ""
    reconsultar: bool = False
    regras_aplicadas: bool = False

    @property
    def nota(self):
        return self.obrigacoes[0].nota

    @property
    def total(self):
        if self.ret_total:
            return self.ret_total
        return round(self.principal + self.fcp + self.multa + self.juros, 2)

    @property
    def uma_nota(self):
        return len({o.chave for o in self.obrigacoes}) == 1

    @property
    def nfs(self):
        return ", ".join(sorted({o.nNF for o in self.obrigacoes}, key=lambda x: int(x) if x.isdigit() else 0))


def montar_guias(obrigacoes, cfg, consolidar_apuracao=True):
    """Agrupa obrigações em guias. Por operação: 1 guia por nota/tipo.
    Por apuração (consolidar): 1 guia por UF + receita + mês de referência."""
    guias, grupos = [], {}
    gid = int(time.time()) % 10**7 * 100
    for ob in obrigacoes:
        if not ob.gera_guia:
            continue
        if ob.modo != OPERACAO and consolidar_apuracao:
            ref = ob.data_referencia or ob.vencimento
            k = (ob.uf, ob.receita, ob.tipo, ref.year, ref.month, ob.recibo_pendente)
            if k in grupos:
                g = grupos[k]
                g.obrigacoes.append(ob)
                g.principal = round(g.principal + ob.valor, 2)
                g.multa = round(g.multa + ob.multa, 2)
                g.juros = round(g.juros + ob.juros, 2)
                continue
        gid += 1
        ref_d = ob.data_referencia or ob.vencimento
        g = Guia(id=gid, obrigacoes=[ob], uf=ob.uf, receita=ob.receita, tipo=ob.tipo, modo=ob.modo,
                 principal=ob.valor, vencimento=ob.vencimento, data_pagamento=ob.data_pagamento,
                 multa=ob.multa, juros=ob.juros, doc_origem=ob.nNF,
                 tipo_doc_origem=cfg.get("receitas", "tipo_doc_origem_padrao", "10"),
                 referencia=("0", f"{ref_d.month:02d}", str(ref_d.year)))
        if ob.modo != OPERACAO and consolidar_apuracao:
            grupos[(ob.uf, ob.receita, ob.tipo, ref_d.year, ref_d.month, ob.recibo_pendente)] = g
        guias.append(g)
    for g in guias:
        if len(g.obrigacoes) > 1:
            g.incluir_destinatario = False
            g.doc_origem = ""
        # Lote já enviado anteriormente e ainda não consultado: reaproveita recibo e identificador
        recs = {o.recibo_pendente for o in g.obrigacoes}
        ids = {o.id_guia_pendente for o in g.obrigacoes}
        if len(recs) == 1 and "" not in recs and len(ids) == 1 and "" not in ids:
            g.recibo, g.id, g.reconsultar = recs.pop(), int(ids.pop()), True
    # Guias pendentes que tinham sido fundidas (DIFAL + FCP na mesma GNRE) voltam a ser uma só
    finais, por_id = [], {}
    for g in guias:
        if g.reconsultar and g.id in por_id:
            base = por_id[g.id]
            if g.tipo == "FCP DIFAL" and base.tipo == "DIFAL":
                base.fcp += g.principal
            elif base.tipo == "FCP DIFAL" and g.tipo == "DIFAL":
                base.fcp, base.principal, base.tipo, base.receita = base.principal, g.principal, g.tipo, g.receita
            else:
                base.principal += g.principal
            base.multa += g.multa
            base.juros += g.juros
            base.obrigacoes += g.obrigacoes
            continue
        if g.reconsultar:
            por_id[g.id] = g
        finais.append(g)
    return finais


# ======================================================================== utilidades
def _limpa(txt, maxlen):
    txt = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    txt = re.sub(r"[\r\n\t]+", " ", txt)
    txt = re.sub(r"[<>&\"']", "", txt)
    return re.sub(r"\s+", " ", txt).strip()[:maxlen].strip()


def _num(txt):
    return "".join(ch for ch in (txt or "") if ch.isdigit())


def _v(x):
    return f"{x:.2f}"


def _sub(parent, tag, text=None, **attrs):
    e = etree.SubElement(parent, f"{{{NS_GNRE}}}{tag}", **attrs)
    if text is not None:
        e.text = str(text)
    return e


# ======================================================================== XML do lote
def dados_gnre_xml(lote_guias, cfg, regras_uf=None):
    """Gera <TLote_GNRE versao="2.00"> com as guias. regras_uf: {(uf, receita): dict} do ConfigUF."""
    regras_uf = regras_uf or {}
    root = etree.Element(f"{{{NS_GNRE}}}TLote_GNRE", nsmap={None: NS_GNRE}, versao="2.00")
    guias_el = _sub(root, "guias")
    for g in lote_guias:
        n = g.nota
        r = regras_uf.get((g.uf, g.receita), {})
        d = _sub(guias_el, "TDadosGNRE", versao="2.00")
        _sub(d, "ufFavorecida", g.uf)
        _sub(d, "tipoGnre", "0")
        ce = _sub(d, "contribuinteEmitente")
        ide = _sub(ce, "identificacao")
        # IE do emitente na UF favorecida (substituto) quando houver; senão CNPJ
        if n.emit_iest and g.modo != OPERACAO:
            _sub(ide, "IE", _num(n.emit_iest))
        else:
            _sub(ide, "CNPJ" if len(_num(n.emit_cnpj)) == 14 else "CPF", _num(n.emit_cnpj))
        _sub(ce, "razaoSocial", _limpa(n.emit_nome, 60))
        _sub(ce, "endereco", _limpa(f"{n.emit_lgr}, {n.emit_nro} - {n.emit_bairro}", 60))
        if n.emit_cmun:
            _sub(ce, "municipio", _num(n.emit_cmun)[-5:])
        _sub(ce, "uf", n.emit_uf)
        if len(_num(n.emit_cep)) == 8:
            _sub(ce, "cep", _num(n.emit_cep))
        fone = _num(cfg.get("empresa", "telefone") or n.emit_fone)
        if 6 <= len(fone) <= 11:
            _sub(ce, "telefone", fone)

        itens = _sub(d, "itensGNRE")
        it = _sub(itens, "item")
        _sub(it, "receita", g.receita)
        if g.detalhamento:
            _sub(it, "detalhamentoReceita", g.detalhamento)
        if g.doc_origem and r.get("exigeDocumentoOrigem", "S") != "N":
            if g.tipo_doc_origem in ("22", "24") and g.uma_nota:
                _sub(it, "documentoOrigem", n.chave, tipo=g.tipo_doc_origem)  # chave de acesso
            else:
                _sub(it, "documentoOrigem", _num(g.doc_origem)[:18], tipo=g.tipo_doc_origem)
        if g.produto:
            _sub(it, "produto", g.produto)
        if r.get("exigePeriodoReferencia", "S") != "N" and g.referencia:
            ref = _sub(it, "referencia")
            if r.get("exigePeriodoApuracao", "S") != "N":
                _sub(ref, "periodo", g.referencia[0])
            _sub(ref, "mes", g.referencia[1])
            _sub(ref, "ano", g.referencia[2])
        if r.get("exigeDataVencimento", "S") != "N" and g.vencimento:
            _sub(it, "dataVencimento", g.vencimento.isoformat())
        _sub(it, "valor", _v(g.principal), tipo="11")
        if g.fcp:
            _sub(it, "valor", _v(g.fcp), tipo="12")
        if r.get("valorExigido") in ("T", "A"):
            _sub(it, "valor", _v(g.principal), tipo="21")
            if g.fcp:
                _sub(it, "valor", _v(g.fcp), tipo="22")
        if g.incluir_destinatario and r.get("exigeContribuinteDestinatario", "S") != "N" and n.dest_doc:
            cd = _sub(it, "contribuinteDestinatario")
            idd = _sub(cd, "identificacao")
            ie_dest = _num(n.dest_ie)
            if ie_dest and n.dest_indIEDest == "1":
                _sub(idd, "IE", ie_dest)
            else:
                _sub(idd, "CNPJ" if n.dest_tipo_doc == "CNPJ" else "CPF", _num(n.dest_doc))
            _sub(cd, "razaoSocial", _limpa(n.dest_nome, 60))
            if n.dest_cmun:
                _sub(cd, "municipio", _num(n.dest_cmun)[-5:])
        if g.campos_extras:
            cx = _sub(it, "camposExtras")
            for cod, val in g.campos_extras[:3]:
                c = _sub(cx, "campoExtra")
                _sub(c, "codigo", cod)
                _sub(c, "valor", _limpa(str(val), 100))
        _sub(d, "valorGNRE", _v(g.principal + g.fcp))
        if g.data_pagamento:
            _sub(d, "dataPagamento", max(g.data_pagamento, date.today()).isoformat())
        _sub(d, "identificadorGuia", str(g.id)[-10:])
    return root


_schema = None


def validar_lote(root):
    """Valida contra o XSD oficial (lote_gnre_v2.00). Retorna lista de erros."""
    global _schema
    if _schema is None:
        _schema = etree.XMLSchema(etree.parse(os.path.join(resource_dir(), "xsd", "lote_gnre_v2_validacao.xsd")))
    if _schema.validate(root):
        return []
    return [f"Linha {e.line}: {e.message}" for e in _schema.error_log]


def aplicar_regras_uf(g, regra, avisos_cb=None):
    """Ajusta a guia conforme a configuração da UF (ConfigUF)."""
    if not regra or g.regras_aplicadas:
        return
    g.regras_aplicadas = True
    tipos = regra.get("tiposDocumentosOrigem", [])
    if tipos and g.tipo_doc_origem not in tipos:
        preferidos = [t for t in tipos if t in ("10", "22", "24")]
        g.tipo_doc_origem = (preferidos or tipos)[0]
    if regra.get("exigeDetalhamentoReceita") == "S" and regra.get("detalhamentos"):
        g.detalhamento = regra["detalhamentos"][0][0]
        g.avisos.append(f"Detalhamento de receita definido automaticamente: {regra['detalhamentos'][0][1]}")
    if regra.get("exigeProduto") == "S" and regra.get("produtos"):
        g.produto = regra["produtos"][0][0]
        g.avisos.append(f"Produto definido automaticamente: {regra['produtos'][0][1]} – confira.")
    if regra.get("exigeValorFecp") == "S" and g.tipo == "DIFAL":
        g.avisos.append("UF exige valor FCP na mesma guia do DIFAL.")
    n = g.nota
    for ca in regra.get("camposAdicionais", []):
        titulo = unicodedata.normalize("NFKD", ca["titulo"].lower()).encode("ascii", "ignore").decode()
        val = None
        if re.search(r"\bchave\b", titulo):
            val = n.chave
        elif ca["tipo"] == "D" and re.search(r"\bemiss", titulo):
            val = (n.dhEmi or date.today()).isoformat()
        elif ca["tipo"] == "D" and re.search(r"\bsaida\b", titulo):
            val = (n.data_saida or date.today()).isoformat()
        elif re.search(r"\b(numero|n\.?o|no)\b.*\b(nf-?e?|nota|documento fiscal)\b|^\s*nf-?e?\s*$", titulo):
            val = n.nNF
        if val is not None and len(g.obrigacoes) == 1:
            g.campos_extras.append((ca["codigo"], val))
        elif ca.get("obrigatorio") == "S":
            g.avisos.append(f"Campo extra obrigatório não preenchido automaticamente: {ca['titulo']} (cód. {ca['codigo']})")


def juntar_fcp(guias, regras_uf):
    """Se a UF exige FCP no mesmo item do DIFAL (exigeValorFecp=S), funde as guias."""
    resultado, por_nota = [], {}
    for g in guias:
        if g.tipo == "DIFAL":
            por_nota[(g.obrigacoes[0].chave, g.uf)] = g
    for g in guias:
        if g.tipo == "FCP DIFAL" and len(g.obrigacoes) == 1 and not g.reconsultar:
            dif = por_nota.get((g.obrigacoes[0].chave, g.uf))
            if dif and not dif.fcp and not dif.reconsultar and \
                    regras_uf.get((dif.uf, dif.receita), {}).get("exigeValorFecp") == "S":
                dif.fcp = g.principal
                dif.multa += g.multa
                dif.juros += g.juros
                dif.obrigacoes += g.obrigacoes
                continue
        resultado.append(g)
    return resultado


# ======================================================================== cliente webservice
class CertificadoA1:
    """Converte o .pfx em PEMs temporários para uso pelo requests (apagados ao final)."""

    def __init__(self, pfx_path, senha):
        from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
        with open(pfx_path, "rb") as f:
            key, cert, extras = pkcs12.load_key_and_certificates(f.read(), senha.encode() if senha else None)
        self.validade = cert.not_valid_after_utc.date() if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.date()
        self.titular = cert.subject.rfc4514_string()
        self._dir = tempfile.mkdtemp(prefix="gnre_")
        self.cert_file = os.path.join(self._dir, "c.pem")
        self.key_file = os.path.join(self._dir, "k.pem")
        with open(self.cert_file, "wb") as f:
            f.write(cert.public_bytes(Encoding.PEM))
            for c in extras or []:
                f.write(c.public_bytes(Encoding.PEM))
        with open(self.key_file, "wb") as f:
            f.write(key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))

    def limpar(self):
        for p in (self.cert_file, self.key_file):
            try:
                with open(p, "wb") as f:
                    f.write(b"\0" * 4096)
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(self._dir)
        except OSError:
            pass


class ClienteGNRE:
    def __init__(self, cfg, certificado, cache_dir, log=print):
        self.cfg = cfg
        self.cert = certificado
        self.log = log
        self.amb = cfg.get("geral", "ambiente", "2")
        self.host = "www.gnre.pe.gov.br" if self.amb == "1" else "www.testegnre.pe.gov.br"
        self.timeout = cfg.getint("webservice", "timeout_seg", 60)
        self.verify = cfg.getbool("certificado", "verificar_ssl", True)
        self.cache_path = os.path.join(cache_dir, f"configuf_amb{self.amb}.json")
        try:
            self.cache = json.load(open(self.cache_path, encoding="utf-8"))
        except Exception:
            self.cache = {}

    # ------------------------------------------------------------ SOAP
    def _post(self, url, action, envelope):
        headers = {"Content-Type": f'application/soap+xml;charset=utf-8;action="{action}"'}
        r = requests.post(url, data=envelope, headers=headers, timeout=self.timeout,
                          cert=(self.cert.cert_file, self.cert.key_file), verify=self.verify)
        if r.status_code >= 400 and b"Envelope" not in r.content:
            raise RuntimeError(f"HTTP {r.status_code} em {url}: {r.text[:300]}")
        body = etree.fromstring(r.content)
        fault = body.find(".//{http://www.w3.org/2003/05/soap-envelope}Fault")
        if fault is not None:
            raise RuntimeError("SOAP Fault: " + " ".join(fault.itertext())[:500])
        return body

    def _envelope(self, cabec_ns, dados_ns, versao, conteudo, prefixed=False):
        soap = "http://www.w3.org/2003/05/soap-envelope"
        env = etree.Element(f"{{{soap}}}Envelope", nsmap={"soap12": soap})
        h = etree.SubElement(env, f"{{{soap}}}Header")
        cab = etree.SubElement(h, f"{{{cabec_ns}}}gnreCabecMsg", nsmap={None: cabec_ns})
        etree.SubElement(cab, f"{{{cabec_ns}}}versaoDados").text = versao
        b = etree.SubElement(env, f"{{{soap}}}Body")
        dm = etree.SubElement(b, f"{{{dados_ns}}}gnreDadosMsg", nsmap={None: dados_ns})
        dm.append(conteudo)
        return etree.tostring(env, xml_declaration=True, encoding="UTF-8")

    # ------------------------------------------------------------ ConfigUF
    def config_uf(self, uf, receita):
        k = f"{uf}-{receita}"
        if k in self.cache and self.cache[k].get("_data") == date.today().isoformat():
            return self.cache[k]
        ns = f"http://{self.host}/webservice/GnreConfigUF"
        c = etree.Element(f"{{{NS_GNRE}}}TConsultaConfigUf", nsmap={None: NS_GNRE})
        etree.SubElement(c, f"{{{NS_GNRE}}}ambiente").text = self.amb
        etree.SubElement(c, f"{{{NS_GNRE}}}uf").text = uf
        etree.SubElement(c, f"{{{NS_GNRE}}}receita").text = receita
        try:
            resp = self._post(self.cfg.url("configuf"), ns, self._envelope(ns, ns, "1.00", c))
        except Exception as e:
            self.log(f"  ConfigUF {uf}/{receita} indisponível ({e}); usando cache/padrão.")
            return self.cache.get(k, {})
        regra = parse_config_uf(resp)
        regra["_data"] = date.today().isoformat()
        self.cache[k] = regra
        json.dump(self.cache, open(self.cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return regra

    # ------------------------------------------------------------ Lote
    def enviar_lote(self, lote_root):
        ns = f"http://{self.host}/webservice/GnreLoteRecepcao"
        env = self._envelope("http://www.gnre.pe.gov.br/wsdl/processar", ns, "2.00", lote_root)
        if len(env) > 590_000:
            raise RuntimeError("Lote excede 600 KB; reduza 'guias_por_lote'.")
        resp = self._post(self.cfg.url("recepcao"), ns, env)
        cod = _txt(resp, ".//g:situacaoRecepcao/g:codigo")
        desc = _txt(resp, ".//g:situacaoRecepcao/g:descricao")
        recibo = _txt(resp, ".//g:recibo/g:numero")
        tempo = _txt(resp, ".//g:recibo/g:tempoEstimadoProc")
        if cod != "100" or not recibo:
            raise RuntimeError(f"Lote rejeitado na recepção: {cod} - {desc}")
        return recibo, int(tempo or 0)

    def consultar_lote(self, recibo):
        ns = f"http://{self.host}/webservice/GnreResultadoLote"
        c = etree.Element(f"{{{NS_GNRE}}}TConsLote_GNRE", nsmap={None: NS_GNRE})
        etree.SubElement(c, f"{{{NS_GNRE}}}ambiente").text = self.amb
        etree.SubElement(c, f"{{{NS_GNRE}}}numeroRecibo").text = recibo
        etree.SubElement(c, f"{{{NS_GNRE}}}incluirPDFGuias").text = "S"
        env = self._envelope("http://www.gnre.pe.gov.br/wsdl/consultar", ns, "2.00", c)
        try:
            resp = self._post(self.cfg.url("resultado"), ns, env)
            if _txt(resp, ".//g:situacaoProcess/g:codigo") not in ("501", "504", "303"):
                return resp
        except RuntimeError:
            pass
        # Versão do portal que não aceita incluirPDFGuias: repete no leiaute 1.00 sem o campo
        c.remove(c[-1])
        env = self._envelope("http://www.gnre.pe.gov.br/wsdl/consultar", ns, "1.00", c)
        return self._post(self.cfg.url("resultado"), ns, env)

    def preparar(self, guias):
        """Baixa as regras da UF (ConfigUF), aplica-as e funde FCP+DIFAL quando exigido."""
        self.regras = getattr(self, "regras", {})
        for g in guias:
            if (g.uf, g.receita) not in self.regras:
                self.regras[(g.uf, g.receita)] = self.config_uf(g.uf, g.receita)
            aplicar_regras_uf(g, self.regras[(g.uf, g.receita)])
        return juntar_fcp(guias, self.regras)

    def _aguardar_resultado(self, recibo, lote, espera_inicial, pasta_xml_lote):
        tentativas = self.cfg.getint("webservice", "tentativas_consulta", 10)
        intervalo = self.cfg.getint("webservice", "intervalo_consulta_seg", 20)
        time.sleep(espera_inicial)
        resp = None
        for _ in range(tentativas):
            try:
                resp = self.consultar_lote(recibo)
            except Exception as e:
                self.log(f"  Consulta falhou ({e}); nova tentativa...")
                time.sleep(intervalo)
                continue
            if _txt(resp, ".//g:situacaoProcess/g:codigo") in ("400", "401"):
                time.sleep(intervalo)
                continue
            break
        if resp is not None and pasta_xml_lote:
            with open(os.path.join(pasta_xml_lote, f"lote_retorno_{recibo}.xml"), "wb") as f:
                f.write(etree.tostring(resp, pretty_print=True, encoding="UTF-8"))
        if resp is None or _txt(resp, ".//g:situacaoProcess/g:codigo") in ("400", "401"):
            for g in lote:
                g.situacao = "PENDENTE CONSULTA"
                g.motivos = [f"Lote ainda em processamento no portal (recibo {recibo}). "
                             "Processe novamente mais tarde: o app consultará este recibo sem reenviar."]
            self.log(f"  Recibo {recibo} ainda em processamento – ficará pendente de consulta.")
            return {}
        return aplicar_resultado(resp, lote, reconsulta=any(g.reconsultar for g in lote))

    def processar(self, guias, pasta_xml_lote=None):
        """Envia as guias (já preparadas) em lotes e preenche o retorno. Retorna {'_lote': pdf} se o portal devolver PDF."""
        por_lote = max(1, min(self.cfg.getint("webservice", "guias_por_lote", 50), 100))
        espera = self.cfg.getint("webservice", "espera_inicial_seg", 35)
        regras = getattr(self, "regras", {})
        pdfs = {}
        # 1) Recibos pendentes de execuções anteriores: só consulta
        pend = {}
        for g in guias:
            if g.reconsultar and g.recibo:
                pend.setdefault(g.recibo, []).append(g)
        for recibo, lote in pend.items():
            self.log(f"  Consultando recibo pendente {recibo}...")
            pdfs.update(self._aguardar_resultado(recibo, lote, 0, pasta_xml_lote))
        # 2) Novas guias: valida cada uma no XSD (uma inválida não bloqueia as demais) e envia
        novas = []
        for g in guias:
            if g.reconsultar:
                continue
            erros = validar_lote(dados_gnre_xml([g], self.cfg, regras))
            if erros:
                g.situacao, g.motivos = "ERRO XSD", erros[:5]
                self.log(f"  NF {g.nfs} {g.tipo}/{g.uf} não enviada – erro de schema: {erros[0]}")
            else:
                novas.append(g)
        for i in range(0, len(novas), por_lote):
            lote = novas[i:i + por_lote]
            root = dados_gnre_xml(lote, self.cfg, regras)
            if pasta_xml_lote:
                with open(os.path.join(pasta_xml_lote, f"lote_envio_{lote[0].id}.xml"), "wb") as f:
                    f.write(etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True))
            try:
                recibo, tempo = self.enviar_lote(root)
            except Exception as e:
                for g in lote:
                    g.situacao, g.motivos = "ERRO ENVIO", [str(e)]
                self.log(f"  Erro no envio: {e}")
                continue
            self.log(f"  Lote enviado. Recibo {recibo}. Aguardando processamento...")
            for g in lote:
                g.recibo = recibo
            pdfs.update(self._aguardar_resultado(recibo, lote, max(espera, tempo / 1000), pasta_xml_lote))
        return guias, pdfs


def _txt(node, path, default=""):
    r = node.find(path, NSG)
    return r.text.strip() if r is not None and r.text else default


def _float(node, path):
    t = _txt(node, path)
    try:
        return float(t) if t else None
    except ValueError:
        return None


def parse_config_uf(resp):
    cfg = resp.find(".//g:TConfigUf", NSG)
    if cfg is None:
        return {}
    rec = cfg.find(".//g:receitas/g:receita", NSG)
    out = {"situacao": _txt(cfg, "g:situacaoConsulta/g:codigo")}
    if rec is None:
        return out
    for tag in ("exigeContribuinteEmitente", "exigeDetalhamentoReceita", "exigeProduto",
                "exigePeriodoReferencia", "exigePeriodoApuracao", "exigeParcela", "valorExigido",
                "exigeDocumentoOrigem", "exigeContribuinteDestinatario", "exigeDataVencimento",
                "exigeDataPagamento", "exigeConvenio", "exigeCamposAdicionais", "exigeValorFecp"):
        out[tag] = _txt(rec, f"g:{tag}")
    out["tiposDocumentosOrigem"] = [_txt(t, "g:codigo") for t in rec.findall(".//g:tipoDocumentoOrigem", NSG)]
    out["detalhamentos"] = [(_txt(t, "g:codigo"), _txt(t, "g:descricao"))
                            for t in rec.findall(".//g:detalhamentoReceita", NSG)]
    out["produtos"] = [(_txt(t, "g:codigo"), _txt(t, "g:descricao")) for t in rec.findall(".//g:produto", NSG)]
    out["camposAdicionais"] = [{
        "codigo": _txt(c, "g:codigo"), "tipo": _txt(c, "g:tipo"), "titulo": _txt(c, "g:titulo"),
        "obrigatorio": _txt(c, "g:obrigatorio")} for c in rec.findall(".//g:campoAdicional", NSG)]
    return out


def aplicar_resultado(resp, lote, reconsulta=False):
    """Lê TResultLote_GNRE e preenche as guias (casando por identificadorGuia)."""
    import base64
    por_id = {str(g.id)[-10:]: g for g in lote}
    pdfs = {}
    cod = _txt(resp, ".//g:situacaoProcess/g:codigo")
    desc = _txt(resp, ".//g:situacaoProcess/g:descricao")
    resultado = resp.findall(".//g:resultado/g:guia", NSG)
    if not resultado:
        for g in lote:
            g.situacao, g.motivos = "ERRO PORTAL", [f"{cod} - {desc}"]
            if cod == "404":   # erro no processamento: pode ser reenviado
                g.recibo = ""
        return pdfs
    for i, el in enumerate(resultado):
        ident = _txt(el, ".//g:identificadorGuia")
        g = por_id.get(ident) or (None if reconsulta else (lote[i] if i < len(lote) else None))
        if g is None:
            continue
        sit = _txt(el, "g:situacaoGuia")
        g.linha_digitavel = _txt(el, ".//g:linhaDigitavel")
        g.codigo_barras = _txt(el, ".//g:codigoBarras")
        g.nosso_numero = _txt(el, ".//g:nossoNumero")
        g.ret_data_limite = _txt(el, ".//g:dataLimitePagamento")
        vals = {v.get("tipo"): float(v.text) for v in el.findall(".//g:valor", NSG) if v.text}
        if vals:
            g.ret_multa = vals.get("31", 0) + vals.get("32", 0)
            g.ret_juros = vals.get("41", 0) + vals.get("42", 0)
            g.ret_atualizacao = vals.get("51", 0) + vals.get("52", 0)
        g.ret_total = _float(el, ".//g:valorGNRE")
        g.motivos = [f"{_txt(m, 'g:codigo')} - {_txt(m, 'g:descricao')} {_txt(m, 'g:campo')}".strip()
                     for m in el.findall(".//g:motivosRejeicao/g:motivo", NSG)]
        g.situacao = {"0": "EMITIDA", "1": "REJEITADA PORTAL", "2": "REJEITADA UF",
                      "3": "ERRO COMUNICAÇÃO UF"}.get(sit, f"SITUAÇÃO {sit}")
    vistos = set()
    for el in resultado:
        vistos.add(_txt(el, ".//g:identificadorGuia"))
    for g in lote:
        if g.situacao == "A GERAR" and str(g.id)[-10:] not in vistos and reconsulta:
            g.situacao, g.motivos = "ERRO PORTAL", [f"Guia não encontrada no retorno do recibo {g.recibo}"]
    pdf_b64 = _txt(resp, ".//g:pdfGuias")
    if pdf_b64:
        try:
            pdfs["_lote"] = base64.b64decode(pdf_b64)
        except Exception:
            pass
    return pdfs
