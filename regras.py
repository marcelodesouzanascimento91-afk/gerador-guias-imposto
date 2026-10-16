"""Análise fiscal das NF-e: identifica impostos a recolher por guia (fora da nota) e inconsistências."""
from dataclasses import dataclass, field
from datetime import date

from .prazos import ajustar_dia_util, dia_do_mes_seguinte, calcular_encargos

UF_SUFRAMA = {"AM", "AC", "RO", "RR", "AP"}
UF_SUL_SUDESTE_EXC_ES = {"SP", "RJ", "MG", "PR", "SC", "RS"}

# Modos
OPERACAO = "GNRE por operação"
APURACAO = "Apuração mensal (IE substituto na UF)"
INFORMATIVO = "Informativo"


@dataclass
class Obrigacao:
    chave: str
    nNF: str
    serie: str
    data_emissao: date
    cliente: str
    cliente_doc: str
    cliente_tipo_doc: str
    cliente_ie: str
    cliente_cmun: str
    uf: str                 # UF favorecida
    tipo: str               # DIFAL, FCP DIFAL, ICMS-ST, FCP-ST, SUFRAMA, ...
    receita: str
    modo: str
    valor: float
    vencimento: date
    vencimento_original: date
    data_pagamento: date = None
    dias_atraso: int = 0
    multa: float = 0.0
    juros: float = 0.0
    situacao: str = "A GERAR"
    observacao: str = ""
    nota: object = None     # NFe
    gera_guia: bool = True
    data_referencia: date = None   # data da saída (base do vencimento e do mês de referência)
    recibo_pendente: str = ""      # recibo de lote já enviado aguardando consulta
    id_guia_pendente: str = ""

    @property
    def total(self):
        return round(self.valor + self.multa + self.juros, 2)


@dataclass
class Ocorrencia:
    chave: str
    nNF: str
    arquivo: str
    nivel: str      # ERRO | AVISO | INFO
    mensagem: str


@dataclass
class ResultadoAnalise:
    obrigacoes: list = field(default_factory=list)
    ocorrencias: list = field(default_factory=list)
    notas_lidas: int = 0
    notas_consideradas: int = 0


# ------------------------------------------------------------- validadores
def dv_chave_ok(chave):
    if len(chave) != 44 or not chave.isdigit():
        return False
    pesos, soma = 2, 0
    for d in reversed(chave[:43]):
        soma += int(d) * pesos
        pesos = 2 if pesos == 9 else pesos + 1
    dv = 11 - soma % 11
    return (0 if dv >= 10 else dv) == int(chave[43])


def cnpj_ok(c):
    c = "".join(ch for ch in c if ch.isdigit())
    if len(c) != 14 or c == c[0] * 14:
        return False
    def calc(base, pesos):
        s = sum(int(a) * b for a, b in zip(base, pesos)) % 11
        return "0" if s < 2 else str(11 - s)
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = calc(c[:12], p1)
    d2 = calc(c[:12] + d1, [6] + p1)
    return c[-2:] == d1 + d2


def aliquota_interestadual_esperada(uf_orig, uf_dest, origem_mercadoria):
    if origem_mercadoria in ("1", "2", "3", "8"):
        return 4.0
    if uf_orig in UF_SUL_SUDESTE_EXC_ES and uf_dest not in UF_SUL_SUDESTE_EXC_ES:
        return 7.0
    return 12.0


# ------------------------------------------------------------- análise
class Analisador:
    def __init__(self, cfg, selic=None):
        self.cfg = cfg
        self.selic = selic

    def _vencimento(self, nota, modo, tipo):
        base = nota.data_saida or date.today()
        if modo == OPERACAO:
            return base, base  # recolhimento antes/na saída da mercadoria
        dia = self.cfg.getint("prazos", "dia_st_apuracao" if "ST" in tipo else "dia_difal_apuracao", 9)
        orig = dia_do_mes_seguinte(base, dia)
        return ajustar_dia_util(orig, self.cfg.get("prazos", "ajuste_dia_nao_util", "posterior")), orig

    def _obr(self, nota, tipo, receita, modo, valor, obs="", gera=True):
        venc, venc_orig = self._vencimento(nota, modo, tipo)
        return Obrigacao(
            chave=nota.chave, nNF=nota.nNF, serie=nota.serie, data_emissao=nota.dhEmi,
            cliente=nota.dest_nome, cliente_doc=nota.dest_doc, cliente_tipo_doc=nota.dest_tipo_doc,
            cliente_ie=nota.dest_ie, cliente_cmun=nota.dest_cmun, uf=nota.dest_uf,
            tipo=tipo, receita=receita, modo=modo, valor=round(valor, 2),
            vencimento=venc, vencimento_original=venc_orig, observacao=obs, nota=nota, gera_guia=gera,
            data_referencia=nota.data_saida or date.today(),
            situacao="A GERAR" if gera else "INFORMATIVO")

    def aplicar_pagamento(self, ob, data_pagamento):
        """Atualiza data de pagamento e estima encargos se vencida."""
        ob.data_pagamento = data_pagamento
        if not ob.gera_guia:
            return
        multa_dia, teto, modo_j = self.cfg.encargos_uf(ob.uf)
        enc = calcular_encargos(ob.valor, ob.vencimento, data_pagamento, multa_dia, teto, modo_j,
                                self.selic, self.cfg.getfloat("encargos", "juros_fixo_mes_pct", 1.0))
        ob.dias_atraso, ob.multa, ob.juros = enc["dias_atraso"], enc["multa"], enc["juros"]
        ob.observacao = ob.observacao.split(" | Vencida há")[0]
        if ob.observacao.startswith("Vencida há"):
            ob.observacao = ""
        if ob.dias_atraso > 0:
            ob.situacao = "VENCIDA"
            ob.observacao = (ob.observacao + " | " if ob.observacao else "") + \
                f"Vencida há {ob.dias_atraso} dia(s): multa est. {enc['perc_multa']:.2f}% + juros est. {enc['perc_juros']:.2f}%"

    def analisar(self, notas, data_pagamento=None):
        data_pagamento = data_pagamento or date.today()
        res = ResultadoAnalise(notas_lidas=len(notas))
        nossos = self.cfg.cnpjs_remetente()
        rc = lambda k: self.cfg.get("receitas", k)
        ignorar_canc = self.cfg.getbool("geral", "ignorar_notas_canceladas", True)

        for n in notas:
            oc = lambda nivel, msg, n=n: res.ocorrencias.append(Ocorrencia(n.chave, n.nNF, n.arquivo, nivel, msg))
            for e in n.erros_leitura:
                oc("ERRO", e)
            if not n.chave:
                continue
            if nossos and n.emit_cnpj not in nossos:
                continue  # não somos o remetente
            if n.tpNF == "0":
                continue  # nota de entrada
            if n.cancelada:
                oc("INFO" if ignorar_canc else "AVISO", "NF-e cancelada – nenhuma guia gerada.")
                if ignorar_canc:
                    continue
            res.notas_consideradas += 1

            # ------- validações gerais
            if not dv_chave_ok(n.chave):
                oc("ERRO", "Chave de acesso com dígito verificador inválido.")
            if n.emit_cnpj and len(n.emit_cnpj) == 14 and not cnpj_ok(n.emit_cnpj):
                oc("ERRO", f"CNPJ do emitente inválido: {n.emit_cnpj}")
            if not n.cStat:
                oc("AVISO", "XML sem protocolo de autorização (use o arquivo nfeProc autorizado). "
                            "Guias calculadas mesmo assim – confirme a autorização na SEFAZ.")
            elif not n.autorizada and not n.cancelada:
                oc("ERRO", f"NF-e não autorizada (cStat {n.cStat}) – nenhuma guia gerada.")
                continue
            if n.tpAmb == "2":
                if self.cfg.get("geral", "ambiente", "2") == "1":
                    oc("ERRO", "NF-e de HOMOLOGAÇÃO (sem valor fiscal) com GNRE em PRODUÇÃO – nenhuma guia gerada.")
                    continue
                oc("AVISO", "NF-e emitida em ambiente de HOMOLOGAÇÃO (sem valor fiscal).")
            if n.finNFe == "4":
                oc("INFO", "NF-e de devolução – verifique se há ressarcimento/restituição de ICMS-ST ou DIFAL.")
            if n.dest_tipo_doc == "EXT" or n.idDest == "3":
                continue  # exportação: sem GNRE

            interestadual = n.dest_uf and n.emit_uf and n.dest_uf != n.emit_uf
            inscrito = bool(n.emit_iest)  # IE de substituto tributário na UF de destino
            modo = APURACAO if inscrito else OPERACAO

            # ------- DIFAL (EC 87/2015 / LC 190/2022)
            nao_contrib = n.dest_indIEDest == "9"
            cons_final = n.indFinal == "1"
            difal = sum(i.vICMSUFDest for i in n.itens) or n.vICMSUFDest
            fcp = sum(i.vFCPUFDest for i in n.itens) or n.vFCPUFDest
            tem_grupo = any(i.tem_icmsufdest for i in n.itens)

            if interestadual and cons_final and nao_contrib:
                if not tem_grupo and difal == 0:
                    oc("ERRO", "Venda interestadual a consumidor final NÃO contribuinte sem o grupo ICMSUFDest: "
                               "DIFAL não destacado na NF-e (verifique a emissão / carta de correção não supre).")
                if n.emit_crt in ("1", "4"):
                    oc("INFO", "Emitente do Simples Nacional/MEI: confirme a exigência do DIFAL na UF "
                               f"{n.dest_uf} (há decisões judiciais e regras específicas).")
                for it in n.itens:
                    if not it.tem_icmsufdest or it.vBCUFDest <= 0:
                        continue
                    esperado = round(it.vBCUFDest * max(it.pICMSUFDest - it.pICMSInter, 0) / 100, 2)
                    if abs(esperado - it.vICMSUFDest) > 0.05:
                        oc("AVISO", f"Item {it.nItem}: DIFAL destacado R$ {it.vICMSUFDest:.2f} difere do "
                                    f"calculado R$ {esperado:.2f} (BC {it.vBCUFDest:.2f} × "
                                    f"({it.pICMSUFDest}% − {it.pICMSInter}%)). Algumas UFs usam base dupla.")
                    esp_fcp = round(it.vBCUFDest * it.pFCPUFDest / 100, 2)
                    if abs(esp_fcp - it.vFCPUFDest) > 0.05:
                        oc("AVISO", f"Item {it.nItem}: FCP destino R$ {it.vFCPUFDest:.2f} difere do calculado R$ {esp_fcp:.2f}.")
                    aliq = aliquota_interestadual_esperada(n.emit_uf, n.dest_uf, it.orig)
                    if it.pICMSInter and abs(it.pICMSInter - aliq) > 0.001:
                        oc("AVISO", f"Item {it.nItem}: alíquota interestadual {it.pICMSInter}% "
                                    f"(esperado {aliq}% para origem {it.orig} {n.emit_uf}→{n.dest_uf}).")
                if difal > 0:
                    res.obrigacoes.append(self._obr(
                        n, "DIFAL", rc("difal_apuracao") if inscrito else rc("difal"), modo, difal,
                        "ICMS diferencial de alíquota devido à UF de destino (EC 87/2015)"))
                if fcp > 0:
                    res.obrigacoes.append(self._obr(
                        n, "FCP DIFAL", rc("fcp_apuracao") if inscrito else rc("fcp"), modo, fcp,
                        "Fundo de Combate à Pobreza da UF de destino"))
            elif interestadual and difal > 0 and not nao_contrib:
                oc("AVISO", "Grupo ICMSUFDest preenchido, mas o destinatário não é 'não contribuinte' (indIEDest="
                            f"{n.dest_indIEDest}). Pelo contribuinte, o DIFAL é recolhido pelo destinatário.")

            # ------- ICMS-ST e FCP-ST
            vst = sum(i.vICMSST for i in n.itens) or n.vST
            vfcpst = sum(i.vFCPST for i in n.itens) or n.vFCPST
            if vst > 0 or vfcpst > 0:
                if interestadual:
                    if vst > 0:
                        res.obrigacoes.append(self._obr(
                            n, "ICMS-ST", rc("st_apuracao") if inscrito else rc("st"), modo, vst,
                            "ICMS retido por substituição tributária – UF de destino"))
                    if vfcpst > 0:
                        res.obrigacoes.append(self._obr(
                            n, "FCP-ST", rc("fcp_st_apuracao") if inscrito else rc("fcp_st"), modo, vfcpst,
                            "FCP retido por substituição tributária – UF de destino"))
                else:
                    res.obrigacoes.append(self._obr(
                        n, "ST INTERNA", "", INFORMATIVO, vst + vfcpst,
                        f"ST em operação interna ({n.emit_uf}): recolher na apuração/guia estadual própria, não por GNRE.",
                        gera=False))
            if n.vST > 0 and abs(n.vST - sum(i.vICMSST for i in n.itens)) > 0.05:
                oc("AVISO", f"Total vST (R$ {n.vST:.2f}) difere da soma dos itens.")

            # ------- SUFRAMA / Zona Franca e ALC
            deson = sum(i.vICMSDeson for i in n.itens if i.motDesICMS == "7") or (
                n.vICMSDeson if n.dest_isuf else 0)
            if n.dest_uf in UF_SUFRAMA and (n.dest_isuf or deson):
                if not n.dest_isuf:
                    oc("ERRO", "ICMS desonerado (motivo 7 – SUFRAMA) sem inscrição SUFRAMA (ISUF) do destinatário.")
                res.obrigacoes.append(self._obr(
                    n, "SUFRAMA", "", INFORMATIVO, deson,
                    "Operação com benefício SUFRAMA: emitir/confirmar PIN-e e acompanhar o internamento. "
                    "Valor = ICMS desonerado (não é guia GNRE).", gera=False))
            elif n.dest_isuf and n.dest_uf not in UF_SUFRAMA:
                oc("AVISO", f"Destinatário com ISUF mas UF {n.dest_uf} fora da área SUFRAMA.")

            if "GNRE" in n.infCpl.upper():
                oc("INFO", "Informações complementares citam GNRE – confira se a guia já foi recolhida.")

        for ob in res.obrigacoes:
            self.aplicar_pagamento(ob, data_pagamento)
        return res
