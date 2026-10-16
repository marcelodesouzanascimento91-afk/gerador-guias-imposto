"""Calendário (dias úteis bancários), vencimentos e estimativa de multa/juros."""
import json
import os
from datetime import date, timedelta

import requests


# ---------------------------------------------------------------- calendário
def _pascoa(ano):
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes = (h + l_ - 7 * m + 114) // 31
    dia = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_bancarios(ano):
    p = _pascoa(ano)
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)]
    s = {date(ano, m, d) for m, d in fixos}
    s |= {p - timedelta(days=48), p - timedelta(days=47),  # carnaval
          p - timedelta(days=2),                           # sexta-feira santa
          p + timedelta(days=60)}                          # corpus christi
    return s


def dia_util(d):
    return d.weekday() < 5 and d not in feriados_bancarios(d.year)


def ajustar_dia_util(d, modo="posterior"):
    if modo == "manter":
        return d
    passo = timedelta(days=1 if modo == "posterior" else -1)
    while not dia_util(d):
        d += passo
    return d


def proximo_dia_util(d):
    return ajustar_dia_util(d, "posterior")


def dia_do_mes_seguinte(ref, dia):
    ano, mes = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
    import calendar
    dia = min(dia, calendar.monthrange(ano, mes)[1])
    return date(ano, mes, dia)


# ---------------------------------------------------------------- SELIC
class Selic:
    """Taxa SELIC acumulada no mês (BCB/SGS série 4390), com cache local."""
    URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.4390/dados?formato=json&dataInicial={ini}&dataFinal={fim}"

    def __init__(self, cache_path, fallback_pct=1.0, log=None):
        self.cache_path = cache_path
        self.fallback = fallback_pct
        self.log = log or (lambda *_: None)
        self.taxas = {}
        if os.path.exists(cache_path):
            try:
                self.taxas = json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                self.taxas = {}
        self._baixado = False

    def _baixar(self):
        if self._baixado:
            return
        self._baixado = True
        hoje = date.today()
        try:
            url = self.URL.format(ini=f"01/01/{hoje.year - 6}", fim=hoje.strftime("%d/%m/%Y"))
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            for item in r.json():
                d, m, a = item["data"].split("/")
                self.taxas[f"{a}-{m}"] = float(item["valor"])
            json.dump(self.taxas, open(self.cache_path, "w", encoding="utf-8"))
        except Exception as e:
            self.log(f"Aviso: não foi possível atualizar a SELIC no Banco Central ({e}). "
                     f"Usando cache/valor padrão de {self.fallback}% a.m.")

    def mes(self, ano, mes):
        k = f"{ano:04d}-{mes:02d}"
        if k not in self.taxas:
            self._baixar()
        return self.taxas.get(k, self.fallback)


# ---------------------------------------------------------------- encargos
def calcular_encargos(principal, vencimento, pagamento, multa_dia_pct, multa_teto_pct,
                      modo_juros, selic=None, juros_fixo_pct=1.0):
    """Estimativa de multa de mora e juros (padrão: regra federal/maioria das UFs).

    Multa: multa_dia_pct ao dia, limitada a multa_teto_pct.
    Juros (selic): SELIC acumulada do mês seguinte ao vencimento até o mês anterior ao
    pagamento + 1% no mês do pagamento. (fixo): juros_fixo_pct por mês ou fração.
    O valor oficial é o calculado pelo Portal GNRE/SEFAZ na emissão.
    """
    dias = (pagamento - vencimento).days
    if dias <= 0 or principal <= 0:
        return {"dias_atraso": 0, "multa": 0.0, "juros": 0.0, "perc_juros": 0.0}
    perc_multa = min(dias * multa_dia_pct, multa_teto_pct)
    multa = round(principal * perc_multa / 100, 2)
    meses = (pagamento.year - vencimento.year) * 12 + (pagamento.month - vencimento.month)
    perc_juros = 0.0
    if modo_juros == "fixo":
        perc_juros = juros_fixo_pct * max(1, meses)
    elif meses >= 1:
        a, m = vencimento.year, vencimento.month
        for _ in range(meses - 1):
            m += 1
            if m > 12:
                a, m = a + 1, 1
            perc_juros += selic.mes(a, m) if selic else 1.0
        perc_juros += 1.0
    juros = round(principal * perc_juros / 100, 2)
    return {"dias_atraso": dias, "multa": multa, "juros": juros,
            "perc_multa": perc_multa, "perc_juros": round(perc_juros, 4)}
