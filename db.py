"""Histórico das guias em SQLite (base do módulo de relatórios)."""
import os
import sqlite3
from datetime import datetime

CAMPOS = [
    ("chave", "TEXT"), ("nf", "TEXT"), ("serie", "TEXT"), ("data_emissao", "TEXT"),
    ("cliente", "TEXT"), ("cliente_doc", "TEXT"), ("uf", "TEXT"), ("tipo", "TEXT"),
    ("receita", "TEXT"), ("modo", "TEXT"), ("valor", "REAL"), ("multa", "REAL"), ("juros", "REAL"),
    ("total", "REAL"), ("vencimento", "TEXT"), ("vencimento_original", "TEXT"),
    ("data_pagamento", "TEXT"), ("dias_atraso", "INTEGER"), ("situacao", "TEXT"),
    ("linha_digitavel", "TEXT"), ("codigo_barras", "TEXT"), ("nosso_numero", "TEXT"),
    ("recibo", "TEXT"), ("id_guia", "TEXT"), ("arquivo_pdf", "TEXT"), ("observacao", "TEXT"),
    ("gerado_em", "TEXT"), ("atualizado_em", "TEXT"),
]


class Banco:
    def __init__(self, pasta):
        os.makedirs(pasta, exist_ok=True)
        self.con = sqlite3.connect(os.path.join(pasta, "guias.db"), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        cols = ", ".join(f"{c} {t}" for c, t in CAMPOS)
        self.con.execute(f"CREATE TABLE IF NOT EXISTS guias (id INTEGER PRIMARY KEY AUTOINCREMENT, {cols}, "
                         "UNIQUE(chave, tipo))")
        self.con.execute("CREATE TABLE IF NOT EXISTS ocorrencias (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                         "chave TEXT, nf TEXT, arquivo TEXT, nivel TEXT, mensagem TEXT, registrado_em TEXT)")
        existentes = {r[1] for r in self.con.execute("PRAGMA table_info(guias)")}
        for c, t in CAMPOS:
            if c not in existentes:
                self.con.execute(f"ALTER TABLE guias ADD COLUMN {c} {t}")
        self.con.commit()

    def existente(self, chave, tipo):
        r = self.con.execute("SELECT * FROM guias WHERE chave=? AND tipo=?", (chave, tipo)).fetchone()
        return dict(r) if r else None

    def salvar(self, ob, extra=None):
        extra = extra or {}
        agora = datetime.now().isoformat(timespec="seconds")
        dados = {
            "chave": ob.chave, "nf": ob.nNF, "serie": ob.serie,
            "data_emissao": ob.data_emissao.isoformat() if ob.data_emissao else "",
            "cliente": ob.cliente, "cliente_doc": ob.cliente_doc, "uf": ob.uf, "tipo": ob.tipo,
            "receita": ob.receita, "modo": ob.modo, "valor": ob.valor, "multa": ob.multa,
            "juros": ob.juros, "total": ob.total, "vencimento": ob.vencimento.isoformat(),
            "vencimento_original": ob.vencimento_original.isoformat(),
            "data_pagamento": ob.data_pagamento.isoformat() if ob.data_pagamento else "",
            "dias_atraso": ob.dias_atraso, "situacao": ob.situacao, "observacao": ob.observacao,
            "atualizado_em": agora,
        }
        dados.update(extra)
        atual = self.existente(ob.chave, ob.tipo)
        if atual:
            # Não sobrescreve código de barras de guia já emitida, a menos que venha um novo
            for k in ("linha_digitavel", "codigo_barras", "nosso_numero", "recibo", "id_guia", "arquivo_pdf"):
                if not dados.get(k):
                    dados[k] = atual.get(k) or ""
            sets = ", ".join(f"{k}=?" for k in dados)
            self.con.execute(f"UPDATE guias SET {sets} WHERE id=?", list(dados.values()) + [atual["id"]])
        else:
            dados["gerado_em"] = agora
            ks = ", ".join(dados)
            self.con.execute(f"INSERT INTO guias ({ks}) VALUES ({', '.join('?' * len(dados))})",
                             list(dados.values()))
        self.con.commit()

    def salvar_ocorrencias(self, ocorrencias):
        agora = datetime.now().isoformat(timespec="seconds")
        self.con.executemany(
            "INSERT INTO ocorrencias (chave, nf, arquivo, nivel, mensagem, registrado_em) VALUES (?,?,?,?,?,?)",
            [(o.chave, o.nNF, o.arquivo, o.nivel, o.mensagem, agora) for o in ocorrencias])
        self.con.commit()

    def consultar(self, campo_data="vencimento", de=None, ate=None, tipo="", uf="", situacao="", texto=""):
        if campo_data not in ("vencimento", "data_emissao", "gerado_em", "data_pagamento"):
            campo_data = "vencimento"
        sql, p = "SELECT * FROM guias WHERE 1=1", []
        if de:
            sql += f" AND substr({campo_data},1,10) >= ?"
            p.append(de.isoformat())
        if ate:
            sql += f" AND substr({campo_data},1,10) <= ?"
            p.append(ate.isoformat())
        if tipo:
            sql += " AND tipo = ?"
            p.append(tipo)
        if uf:
            sql += " AND uf = ?"
            p.append(uf)
        if situacao:
            sql += " AND situacao = ?"
            p.append(situacao)
        if texto:
            sql += " AND (cliente LIKE ? OR nf LIKE ? OR chave LIKE ?)"
            p += [f"%{texto}%"] * 3
        sql += f" ORDER BY {campo_data}, nf"
        return [dict(r) for r in self.con.execute(sql, p).fetchall()]

    def valores_distintos(self, campo):
        if campo not in ("tipo", "uf", "situacao"):
            return []
        return [r[0] for r in self.con.execute(f"SELECT DISTINCT {campo} FROM guias ORDER BY 1") if r[0]]
