"""Leitura de XMLs de NF-e (nfeProc / NFe) e eventos de cancelamento."""
import os
from dataclasses import dataclass, field
from datetime import datetime, date

from lxml import etree

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}


def _t(node, path, default=""):
    if node is None:
        return default
    r = node.find(path, NS)
    return r.text.strip() if r is not None and r.text else default


def _f(node, path):
    v = _t(node, path, "0")
    try:
        return float(v)
    except ValueError:
        return 0.0


def _data(txt):
    if not txt:
        return None
    try:
        return datetime.fromisoformat(txt[:19]).date()
    except ValueError:
        try:
            return datetime.strptime(txt[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


@dataclass
class Item:
    nItem: str
    cProd: str
    xProd: str
    ncm: str
    cfop: str
    vProd: float
    cst: str            # CST ou CSOSN
    orig: str
    vICMS: float
    vBCST: float
    vICMSST: float
    vFCPST: float
    vICMSDeson: float
    motDesICMS: str
    # Grupo ICMSUFDest (DIFAL EC 87/2015)
    vBCUFDest: float
    pFCPUFDest: float
    pICMSUFDest: float
    pICMSInter: float
    vFCPUFDest: float
    vICMSUFDest: float
    vICMSUFRemet: float
    tem_icmsufdest: bool


@dataclass
class NFe:
    arquivo: str
    chave: str = ""
    nNF: str = ""
    serie: str = ""
    modelo: str = ""
    tpNF: str = ""          # 0 entrada, 1 saída
    idDest: str = ""        # 1 interna, 2 interestadual, 3 exterior
    indFinal: str = ""      # 1 consumidor final
    finNFe: str = ""
    natOp: str = ""
    dhEmi: date = None
    dhSaiEnt: date = None
    tpAmb: str = ""
    # emitente
    emit_cnpj: str = ""
    emit_nome: str = ""
    emit_ie: str = ""
    emit_iest: str = ""
    emit_crt: str = ""
    emit_lgr: str = ""
    emit_nro: str = ""
    emit_bairro: str = ""
    emit_cmun: str = ""
    emit_xmun: str = ""
    emit_uf: str = ""
    emit_cep: str = ""
    emit_fone: str = ""
    # destinatário
    dest_doc: str = ""
    dest_tipo_doc: str = ""   # CNPJ | CPF | EXT
    dest_nome: str = ""
    dest_ie: str = ""
    dest_indIEDest: str = ""
    dest_isuf: str = ""
    dest_cmun: str = ""
    dest_xmun: str = ""
    dest_uf: str = ""
    # totais
    vNF: float = 0.0
    vProd: float = 0.0
    vICMS: float = 0.0
    vST: float = 0.0
    vFCPST: float = 0.0
    vFCP: float = 0.0
    vICMSDeson: float = 0.0
    vICMSUFDest: float = 0.0
    vFCPUFDest: float = 0.0
    vICMSUFRemet: float = 0.0
    infCpl: str = ""
    # protocolo
    cStat: str = ""
    nProt: str = ""
    autorizada: bool = False
    cancelada: bool = False
    itens: list = field(default_factory=list)
    erros_leitura: list = field(default_factory=list)

    @property
    def data_saida(self):
        return self.dhSaiEnt or self.dhEmi


def parse_nfe(path):
    """Retorna NFe, ('EVENTO', chave, tpEvento, cStat) ou None se não for NF-e."""
    try:
        parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True)
        root = etree.parse(path, parser).getroot()
    except Exception as e:  # XML corrompido
        n = NFe(arquivo=path)
        n.erros_leitura.append(f"XML inválido: {e}")
        return n

    tag = etree.QName(root).localname
    if tag in ("procEventoNFe", "evento", "retEnvEvento"):
        ev = root.find(".//n:infEvento", NS)
        ret = root.find(".//n:retEvento/n:infEvento", NS)
        return ("EVENTO", _t(ev, "n:chNFe"), _t(ev, "n:tpEvento"), _t(ret, "n:cStat"))

    inf = root.find(".//n:infNFe", NS)
    if inf is None:
        return None

    n = NFe(arquivo=path)
    n.chave = (inf.get("Id") or "").replace("NFe", "")
    ide = inf.find("n:ide", NS)
    n.nNF = _t(ide, "n:nNF")
    n.serie = _t(ide, "n:serie")
    n.modelo = _t(ide, "n:mod")
    n.tpNF = _t(ide, "n:tpNF")
    n.idDest = _t(ide, "n:idDest")
    n.indFinal = _t(ide, "n:indFinal")
    n.finNFe = _t(ide, "n:finNFe")
    n.natOp = _t(ide, "n:natOp")
    n.tpAmb = _t(ide, "n:tpAmb")
    n.dhEmi = _data(_t(ide, "n:dhEmi") or _t(ide, "n:dEmi"))
    n.dhSaiEnt = _data(_t(ide, "n:dhSaiEnt") or _t(ide, "n:dSaiEnt"))

    em = inf.find("n:emit", NS)
    n.emit_cnpj = _t(em, "n:CNPJ") or _t(em, "n:CPF")
    n.emit_nome = _t(em, "n:xNome")
    n.emit_ie = _t(em, "n:IE")
    n.emit_iest = _t(em, "n:IEST")
    n.emit_crt = _t(em, "n:CRT")
    ee = em.find("n:enderEmit", NS) if em is not None else None
    n.emit_lgr = _t(ee, "n:xLgr")
    n.emit_nro = _t(ee, "n:nro")
    n.emit_bairro = _t(ee, "n:xBairro")
    n.emit_cmun = _t(ee, "n:cMun")
    n.emit_xmun = _t(ee, "n:xMun")
    n.emit_uf = _t(ee, "n:UF")
    n.emit_cep = _t(ee, "n:CEP")
    n.emit_fone = _t(ee, "n:fone")

    de = inf.find("n:dest", NS)
    if de is not None:
        if _t(de, "n:CNPJ"):
            n.dest_doc, n.dest_tipo_doc = _t(de, "n:CNPJ"), "CNPJ"
        elif _t(de, "n:CPF"):
            n.dest_doc, n.dest_tipo_doc = _t(de, "n:CPF"), "CPF"
        else:
            n.dest_doc, n.dest_tipo_doc = _t(de, "n:idEstrangeiro"), "EXT"
        n.dest_nome = _t(de, "n:xNome")
        n.dest_ie = _t(de, "n:IE")
        n.dest_indIEDest = _t(de, "n:indIEDest")
        n.dest_isuf = _t(de, "n:ISUF")
        ed = de.find("n:enderDest", NS)
        n.dest_cmun = _t(ed, "n:cMun")
        n.dest_xmun = _t(ed, "n:xMun")
        n.dest_uf = _t(ed, "n:UF")

    tot = inf.find("n:total/n:ICMSTot", NS)
    n.vNF = _f(tot, "n:vNF")
    n.vProd = _f(tot, "n:vProd")
    n.vICMS = _f(tot, "n:vICMS")
    n.vST = _f(tot, "n:vST")
    n.vFCPST = _f(tot, "n:vFCPST")
    n.vFCP = _f(tot, "n:vFCP")
    n.vICMSDeson = _f(tot, "n:vICMSDeson")
    n.vICMSUFDest = _f(tot, "n:vICMSUFDest")
    n.vFCPUFDest = _f(tot, "n:vFCPUFDest")
    n.vICMSUFRemet = _f(tot, "n:vICMSUFRemet")
    n.infCpl = _t(inf, "n:infAdic/n:infCpl")

    for det in inf.findall("n:det", NS):
        prod = det.find("n:prod", NS)
        imp = det.find("n:imposto", NS)
        icms_grp = None
        icms = imp.find("n:ICMS", NS) if imp is not None else None
        if icms is not None and len(icms):
            icms_grp = icms[0]
        ufd = imp.find("n:ICMSUFDest", NS) if imp is not None else None
        n.itens.append(Item(
            nItem=det.get("nItem", ""),
            cProd=_t(prod, "n:cProd"), xProd=_t(prod, "n:xProd"), ncm=_t(prod, "n:NCM"),
            cfop=_t(prod, "n:CFOP"), vProd=_f(prod, "n:vProd"),
            cst=_t(icms_grp, "n:CST") or _t(icms_grp, "n:CSOSN"), orig=_t(icms_grp, "n:orig"),
            vICMS=_f(icms_grp, "n:vICMS"), vBCST=_f(icms_grp, "n:vBCST"),
            vICMSST=_f(icms_grp, "n:vICMSST"), vFCPST=_f(icms_grp, "n:vFCPST"),
            vICMSDeson=_f(icms_grp, "n:vICMSDeson"), motDesICMS=_t(icms_grp, "n:motDesICMS"),
            vBCUFDest=_f(ufd, "n:vBCUFDest"), pFCPUFDest=_f(ufd, "n:pFCPUFDest"),
            pICMSUFDest=_f(ufd, "n:pICMSUFDest"), pICMSInter=_f(ufd, "n:pICMSInter"),
            vFCPUFDest=_f(ufd, "n:vFCPUFDest"), vICMSUFDest=_f(ufd, "n:vICMSUFDest"),
            vICMSUFRemet=_f(ufd, "n:vICMSUFRemet"), tem_icmsufdest=ufd is not None,
        ))

    prot = root.find(".//n:protNFe/n:infProt", NS)
    n.cStat = _t(prot, "n:cStat")
    n.nProt = _t(prot, "n:nProt")
    n.autorizada = n.cStat in ("100", "150")
    if n.cStat in ("101", "151", "135", "155"):
        n.cancelada = True
    return n


def listar_xmls(pasta, recursivo=True):
    arquivos = []
    if recursivo:
        for raiz, _, nomes in os.walk(pasta):
            arquivos += [os.path.join(raiz, x) for x in nomes if x.lower().endswith(".xml")]
    else:
        arquivos = [os.path.join(pasta, x) for x in os.listdir(pasta) if x.lower().endswith(".xml")]
    return sorted(arquivos)


def expandir_fontes(fontes, recursivo=True):
    """Aceita uma pasta, um arquivo ou uma lista misturando arquivos XML e pastas."""
    if isinstance(fontes, str):
        fontes = [fontes]
    arquivos = []
    for f in fontes:
        if os.path.isdir(f):
            arquivos += listar_xmls(f, recursivo)
        elif os.path.isfile(f) and f.lower().endswith(".xml"):
            arquivos.append(f)
    vistos, unicos = set(), []
    for a in arquivos:
        k = os.path.normcase(os.path.abspath(a))
        if k not in vistos:
            vistos.add(k)
            unicos.append(a)
    return unicos


def carregar_pasta(fontes, recursivo=True):
    """Lê os XMLs (pasta(s) e/ou arquivos avulsos); aplica eventos de cancelamento. Retorna (notas, ignorados)."""
    notas, eventos, ignorados = {}, [], []
    for arq in expandir_fontes(fontes, recursivo):
        r = parse_nfe(arq)
        if r is None:
            ignorados.append(arq)
        elif isinstance(r, tuple):
            eventos.append(r)
        else:
            chave = r.chave or arq
            if chave in notas:
                notas[chave].erros_leitura.append(f"XML duplicado também em: {os.path.basename(arq)}")
                continue
            notas[chave] = r
    for _, chave, tp, cstat in eventos:
        if tp == "110111" and chave in notas:
            if cstat in ("135", "136", "155"):
                notas[chave].cancelada = True
            else:
                notas[chave].erros_leitura.append(
                    f"Evento de cancelamento sem retorno de homologação (cStat '{cstat}') – nota mantida como ativa.")
    return list(notas.values()), ignorados
