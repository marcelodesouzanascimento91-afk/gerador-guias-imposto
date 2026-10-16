"""Testes básicos com os XMLs fictícios (rode: python -m pytest tests -q)."""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
EXEMPLOS = os.path.join(RAIZ, "exemplos", "xmls")


def _cfg(tmp):
    import app.config as C
    C.DADOS_DIR = os.path.join(tmp, "dados")
    import app.processador as P
    P.DADOS_DIR = C.DADOS_DIR
    cfg = C.Config(os.path.join(tmp, "config.ini"))
    cfg.set("empresa", "cnpjs_remetente", "99999999000191")
    cfg.set("encargos", "juros", "fixo")      # não depende da internet (SELIC)
    return cfg


def test_analise_identifica_impostos():
    from app.processador import analisar_pasta
    from datetime import date
    with tempfile.TemporaryDirectory() as tmp:
        res = analisar_pasta(_cfg(tmp), EXEMPLOS, date(2026, 10, 8), log=lambda *a: None)
        tipos = {(o.nNF, o.tipo) for o in res.obrigacoes if o.gera_guia}
        assert ("1001", "DIFAL") in tipos and ("1001", "FCP DIFAL") in tipos
        assert ("1002", "ICMS-ST") in tipos and ("1002", "FCP-ST") in tipos
        assert not any(n == "1008" for n, _ in tipos)          # nota cancelada
        assert not any(n == "1009" for n, _ in tipos)          # outro emitente
        difal_1001 = next(o for o in res.obrigacoes if o.nNF == "1001" and o.tipo == "DIFAL")
        assert round(difal_1001.valor, 2) == 800.00
        assert difal_1001.dias_atraso > 0 and difal_1001.multa > 0   # vencida em 22/09
        assert any(o.nivel == "ERRO" and o.nNF == "1007" for o in res.ocorrencias)  # DIFAL não destacado


def test_lote_xml_valido_e_pdfs():
    from app.processador import processar
    with tempfile.TemporaryDirectory() as tmp:
        saida = os.path.join(tmp, "saida")
        r = processar(_cfg(tmp), EXEMPLOS, saida, "lote", log=lambda *a: None)
        assert os.path.exists(r["excel"])
        pdfs = [f for f in os.listdir(r["pasta_guias"]) if f.endswith(".pdf")]
        assert len(pdfs) >= 8
        lotes = os.listdir(os.path.join(saida, "Lotes_XML"))
        assert lotes and all(g.situacao.startswith("LOTE GERADO") for g in r["guias"])
