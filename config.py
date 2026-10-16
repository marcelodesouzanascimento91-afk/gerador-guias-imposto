"""Configuração do Gerador de Guias (arquivo config.ini ao lado do executável)."""
import base64
import configparser
import os
import sys


def base_dir():
    """Pasta do executável (quando empacotado) ou do projeto (em desenvolvimento)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir():
    """Pasta dos recursos empacotados (xsd)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return base_dir()


CONFIG_PATH = os.path.join(base_dir(), "config.ini")
DADOS_DIR = os.path.join(base_dir(), "dados")

DEFAULTS = {
    "empresa": {
        # CNPJs (somente números, separados por vírgula) considerados "nós" (remetente).
        # Vazio = processa qualquer emitente.
        "cnpjs_remetente": "",
        "telefone": "",
    },
    "geral": {
        "pasta_xml": "",
        "pasta_saida": "",
        "pasta_pdf": "",
        "modo": "portal",            # portal | ws | lote | ambos | analise
        "ambiente": "2",             # 1 = Produção, 2 = Homologação
        "consolidar_apuracao": "sim",
        "padrao_nome_arquivo": "{vencimento} - NF {nf} - {tipo} {uf}",
        "formato_data_arquivo": "%Y-%m-%d",
        "ignorar_notas_canceladas": "sim",
    },
    "portal": {
        # Emissão pelo site do Portal GNRE (navegador)
        "email": "",
        "navegador": "edge",          # edge | chrome
        "url_processar": "https://www.gnre.pe.gov.br:444/gnre/v/lote/processar",
        "url_consultar": "https://www.gnre.pe.gov.br:444/gnre/v/lote/consultar",
        "tempo_maximo_min": "15",
        "intervalo_atualizar_seg": "15",
    },
    "certificado": {
        "arquivo_pfx": "",
        "senha": "",                 # só é gravada se 'lembrar_senha' = sim (ofuscada, não criptografada)
        "lembrar_senha": "nao",
        "verificar_ssl": "sim",
    },
    "webservice": {
        "producao_recepcao": "https://www.gnre.pe.gov.br/gnreWS/services/GnreLoteRecepcao",
        "producao_resultado": "https://www.gnre.pe.gov.br/gnreWS/services/GnreResultadoLote",
        "producao_configuf": "https://www.gnre.pe.gov.br/gnreWS/services/GnreConfigUF",
        "homologacao_recepcao": "https://www.testegnre.pe.gov.br/gnreWS/services/GnreLoteRecepcao",
        "homologacao_resultado": "https://www.testegnre.pe.gov.br/gnreWS/services/GnreResultadoLote",
        "homologacao_configuf": "https://www.testegnre.pe.gov.br/gnreWS/services/GnreConfigUF",
        "guias_por_lote": "50",
        "espera_inicial_seg": "35",
        "tentativas_consulta": "10",
        "intervalo_consulta_seg": "20",
        "timeout_seg": "60",
    },
    "receitas": {
        "difal": "100102",
        "difal_apuracao": "100110",
        "fcp": "100129",
        "fcp_apuracao": "100137",
        "st": "100099",
        "st_apuracao": "100048",
        "fcp_st": "100129",
        "fcp_st_apuracao": "100137",
        "tipo_doc_origem_padrao": "10",
    },
    "prazos": {
        # Dia do mês subsequente para recolhimento por apuração (contribuinte com IE substituto na UF destino)
        "dia_st_apuracao": "9",
        "dia_difal_apuracao": "15",
        # Se o vencimento cair em dia não útil: posterior | anterior | manter
        "ajuste_dia_nao_util": "posterior",
    },
    "encargos": {
        # Padrão (pode ser sobrescrito por UF na seção [encargos_uf])
        "multa_dia_pct": "0.33",
        "multa_teto_pct": "20",
        "juros": "selic",            # selic | fixo
        "juros_fixo_mes_pct": "1",
        "selic_mes_fallback_pct": "1.0",
    },
    "encargos_uf": {
        # Exemplo: "sp = 0.33;20;selic"  (multa_dia_pct;multa_teto_pct;juros)
    },
}


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.cp = configparser.ConfigParser(interpolation=None)
        self.cp.optionxform = str.lower
        for sec, vals in DEFAULTS.items():
            self.cp[sec] = dict(vals)
        if os.path.exists(path):
            self.cp.read(path, encoding="utf-8")
        else:
            self.save()
        os.makedirs(DADOS_DIR, exist_ok=True)

    def get(self, sec, key, fallback=""):
        return self.cp.get(sec, key, fallback=fallback).strip()

    def getint(self, sec, key, fallback=0):
        try:
            return int(self.get(sec, key, str(fallback)))
        except ValueError:
            return fallback

    def getfloat(self, sec, key, fallback=0.0):
        try:
            return float(self.get(sec, key, str(fallback)).replace(",", "."))
        except ValueError:
            return fallback

    def getbool(self, sec, key, fallback=False):
        v = self.get(sec, key, "sim" if fallback else "nao").lower()
        return v in ("1", "s", "sim", "true", "yes", "y")

    def set(self, sec, key, value):
        if not self.cp.has_section(sec):
            self.cp.add_section(sec)
        self.cp.set(sec, key, str(value))

    # Senha ofuscada (base64) – NÃO é criptografia. Por padrão não é gravada.
    def get_senha(self):
        v = self.get("certificado", "senha")
        if not v:
            return ""
        try:
            return base64.b64decode(v.encode()).decode("utf-8")
        except Exception:
            return v

    def set_senha(self, senha, lembrar):
        self.set("certificado", "lembrar_senha", "sim" if lembrar else "nao")
        self.set("certificado", "senha",
                 base64.b64encode(senha.encode("utf-8")).decode() if (lembrar and senha) else "")

    def cnpjs_remetente(self):
        raw = self.get("empresa", "cnpjs_remetente")
        return {"".join(ch for ch in c if ch.isdigit()) for c in raw.split(",") if c.strip()}

    def encargos_uf(self, uf):
        """Retorna (multa_dia_pct, multa_teto_pct, modo_juros) para a UF."""
        base = (self.getfloat("encargos", "multa_dia_pct", 0.33),
                self.getfloat("encargos", "multa_teto_pct", 20.0),
                self.get("encargos", "juros", "selic").lower())
        v = self.get("encargos_uf", uf.lower())
        if not v:
            return base
        try:
            p = [x.strip() for x in v.split(";")]
            return (float(p[0].replace(",", ".")), float(p[1].replace(",", ".")),
                    (p[2] if len(p) > 2 else base[2]).lower())
        except Exception:
            return base

    def url(self, servico):
        amb = "producao" if self.get("geral", "ambiente") == "1" else "homologacao"
        return self.get("webservice", f"{amb}_{servico}")

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("; Configuração do Gerador de Guias de Impostos\n")
            f.write("; Edite pela tela 'Configurações' do aplicativo ou diretamente aqui.\n\n")
            self.cp.write(f)
