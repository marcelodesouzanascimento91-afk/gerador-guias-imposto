"""Interface gráfica simplificada (Tkinter).

  [IMPORTAR XML]        | Tabela: NF / Cliente / Tipo de imposto / Valor /     | [GERAR GUIA PARA PAGAMENTO]
  [Importar lote/pasta] |         Vencimento / Multa+Juros / Total / Situação  |
  [Limpar dados]        | (vencidas em vermelho, no prazo em preto)            | [GERAR RELATÓRIO EXCEL]
  Data de pagamento     |                                                      |  De: __/__/____  Até: __/__/____
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import processador
from .config import Config, DADOS_DIR
from .db import Banco
from .relatorio import exportar

APP_NOME = "Gerador de Guias de Impostos – GNRE / DIFAL / ST"

COR_IMPORTAR = "#D9D9D9"
COR_VERDE = "#E2EFDA"
COR_VERDE_ATIVO = "#C6E0B4"
COR_BORDA_VERDE = "#70AD47"
VERMELHO = "#C00000"
CINZA = "#7F7F7F"
AZUL = "#1F3864"
FONTE = "Segoe UI" if sys.platform.startswith("win") else "DejaVu Sans"


def abrir(caminho):
    try:
        if sys.platform.startswith("win"):
            os.startfile(caminho)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", caminho])
        else:
            subprocess.Popen(["xdg-open", caminho])
    except Exception:
        pass


def parse_br(txt):
    txt = (txt or "").strip()
    if not txt:
        return None
    for f in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, f).date()
        except ValueError:
            pass
    raise ValueError(f"Data inválida: {txt} (use dd/mm/aaaa)")


def moeda(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def botao(pai, texto, comando, bg, fonte=11, borda=None, **kw):
    b = tk.Button(pai, text=texto, command=comando, bg=bg, activebackground=COR_VERDE_ATIVO if bg == COR_VERDE else "#BFBFBF",
                  relief="flat", bd=0, highlightthickness=1, highlightbackground=borda or bg,
                  font=(FONTE, fonte), cursor="hand2", wraplength=220, justify="center", **kw)
    return b


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = Config()
        self.title(APP_NOME)
        self.geometry("1320x720")
        if sys.platform.startswith("win"):
            try:
                self.state("zoomed")
            except tk.TclError:
                pass
        self.minsize(1050, 600)
        self.configure(bg="white")
        self.fila = queue.Queue()
        self.analise = None
        self.fontes = []
        self.ocupado = False
        self.linhas = []       # obrigações exibidas na tabela (mesma ordem dos iids)
        estilo = ttk.Style(self)
        try:
            estilo.theme_use("vista" if sys.platform.startswith("win") else "clam")
        except tk.TclError:
            pass
        estilo.configure("Treeview", font=(FONTE, 10), rowheight=24)
        estilo.configure("Treeview.Heading", font=(FONTE, 10, "bold"))
        self._montar()
        self.after(150, self._drenar_fila)

    # ================================================================== LAYOUT
    def _montar(self):
        raiz = tk.Frame(self, bg="white")
        raiz.pack(fill="both", expand=True, padx=12, pady=10)
        raiz.columnconfigure(1, weight=1)
        raiz.rowconfigure(0, weight=1)

        # ---------------- coluna esquerda: importar
        esq = tk.Frame(raiz, bg="white", width=200)
        esq.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        tk.Frame(esq, bg="white", height=90).pack()
        self.bt_importar = botao(esq, "IMPORTAR\nXML", self.importar_arquivos, COR_IMPORTAR, 20, width=9, height=3)
        self.bt_importar.pack(fill="x")
        tk.Label(esq, text="Selecione um ou vários XMLs\n(Ctrl / Shift para vários)", bg="white", fg=CINZA,
                 font=(FONTE, 8)).pack(pady=(2, 8))
        self.bt_lote = botao(esq, "Importar em lote\n(pasta inteira)", self.importar_pasta, COR_IMPORTAR, 11, height=2)
        self.bt_lote.pack(fill="x")
        tk.Frame(esq, bg="white", height=14).pack()
        self.bt_limpar = botao(esq, "Limpar dados", self.limpar, "#F2F2F2", 9, borda="#BFBFBF")
        self.bt_limpar.pack(fill="x", padx=30)

        tk.Frame(esq, bg="white", height=26).pack()
        tk.Label(esq, text="Data de pagamento:", bg="white", font=(FONTE, 9)).pack(anchor="w")
        self.var_pag = tk.StringVar(value=date.today().strftime("%d/%m/%Y"))
        ent = ttk.Entry(esq, textvariable=self.var_pag, width=12, justify="center")
        ent.pack(anchor="w", pady=2)
        ent.bind("<Return>", lambda e: self.recalcular())
        ttk.Button(esq, text="Recalcular multa", command=self.recalcular).pack(anchor="w")
        tk.Label(esq, text="Multa e juros são estimados até\nesta data (vale o valor do portal).",
                 bg="white", fg=CINZA, font=(FONTE, 8), justify="left").pack(anchor="w", pady=(2, 0))

        # ---------------- centro: tabela
        centro = tk.Frame(raiz, bg="#D9D9D9", padx=1, pady=1)
        centro.grid(row=0, column=1, sticky="nsew")
        cab = tk.Frame(centro, bg="#D9D9D9")
        cab.pack(fill="x")
        tk.Label(cab, text="NF / CLIENTE / TIPO DE IMPOSTO / VALOR", bg="#D9D9D9", font=(FONTE, 12, "bold")).pack(pady=(6, 0))
        self.lbl_info = tk.Label(cab, text="Importe os XMLs para listar os impostos a pagar.", bg="#D9D9D9",
                                 fg="#404040", font=(FONTE, 9))
        self.lbl_info.pack(pady=(0, 6))
        tab = tk.Frame(centro, bg="white")
        tab.pack(fill="both", expand=True)
        cols = ("nf", "cliente", "tipo", "uf", "valor", "venc", "multa", "total", "situacao")
        tit = ("NF", "Cliente", "Tipo de imposto", "UF", "Valor", "Vencimento", "Multa + juros", "Total", "Situação")
        larg = (55, 170, 90, 35, 92, 82, 92, 95, 150)
        self.tv = ttk.Treeview(tab, columns=cols, show="headings", selectmode="extended")
        for c, t, w in zip(cols, tit, larg):
            self.tv.heading(c, text=t, command=lambda c=c: self._ordenar(c))
            self.tv.column(c, width=w, minwidth=40, anchor="e" if c in ("valor", "multa", "total") else
                           ("center" if c in ("nf", "uf", "venc") else "w"), stretch=(c == "cliente"))
        self.tv.tag_configure("vencida", foreground=VERMELHO)
        self.tv.tag_configure("prazo", foreground="black")
        self.tv.tag_configure("emitida", foreground="#375623")
        self.tv.tag_configure("info", foreground=CINZA)
        sb = ttk.Scrollbar(tab, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv.bind("<Double-1>", self._detalhe)
        rod = tk.Frame(centro, bg="#D9D9D9")
        rod.pack(fill="x")
        self.lbl_total = tk.Label(rod, text="", bg="#D9D9D9", font=(FONTE, 10, "bold"), fg=AZUL)
        self.lbl_total.pack(side="right", padx=8, pady=4)
        leg = tk.Frame(rod, bg="#D9D9D9")
        leg.pack(side="left", padx=8)
        for txt, cor in (("■ Vencida", VERMELHO), ("■ No prazo", "black"), ("■ Guia emitida", "#375623"),
                         ("■ Só conferência (sem guia)", CINZA)):
            tk.Label(leg, text=txt, fg=cor, bg="#D9D9D9", font=(FONTE, 8)).pack(side="left", padx=4)

        # ---------------- coluna direita: gerar guia / relatório
        dir_ = tk.Frame(raiz, bg="white", width=240)
        dir_.grid(row=0, column=2, sticky="ns", padx=(12, 0))
        topo_dir = tk.Frame(dir_, bg="white")
        topo_dir.pack(fill="x")
        ttk.Button(topo_dir, text="⚙ Configurações", command=self.abrir_config).pack(anchor="e")
        self.bt_gerar = botao(dir_, "GERAR GUIA PARA\nPAGAMENTO", self.gerar, COR_VERDE, 13, borda=COR_BORDA_VERDE,
                              width=16, height=4)
        self.bt_gerar.pack(fill="x", pady=(8, 2))
        self.lbl_modo = tk.Label(dir_, text="", bg="white", fg=CINZA, font=(FONTE, 8), justify="center", wraplength=230)
        self.lbl_modo.pack()
        self._atualizar_lbl_modo()
        tk.Label(dir_, text="Gera as linhas selecionadas\n(ou todas, se nenhuma estiver selecionada)",
                 bg="white", fg=CINZA, font=(FONTE, 8)).pack(pady=(0, 4))
        self.bt_pasta_pdf = ttk.Button(dir_, text="Abrir pasta das guias", command=self._abrir_pasta_pdf)
        self.bt_pasta_pdf.pack()

        tk.Frame(dir_, bg="white").pack(fill="both", expand=True)

        rel = tk.Frame(dir_, bg=COR_VERDE, highlightthickness=1, highlightbackground=COR_BORDA_VERDE, padx=10, pady=8)
        rel.pack(fill="x", pady=(0, 4))
        tk.Label(rel, text="GERAR RELATÓRIO\nEXCEL", bg=COR_VERDE, font=(FONTE, 13)).pack(pady=(0, 6))
        hoje = date.today()
        self.r_de = tk.StringVar(value=hoje.replace(day=1).strftime("%d/%m/%Y"))
        prox = (hoje.replace(day=28) + timedelta(days=4)).replace(day=1)
        self.r_ate = tk.StringVar(value=(prox - timedelta(days=1)).strftime("%d/%m/%Y"))
        self.r_campo = tk.StringVar(value="Vencimento")
        lin = tk.Frame(rel, bg=COR_VERDE)
        lin.pack(fill="x")
        tk.Label(lin, text="Data inicial:", bg=COR_VERDE, font=(FONTE, 9)).grid(row=0, column=0, sticky="w")
        ttk.Entry(lin, textvariable=self.r_de, width=12, justify="center").grid(row=0, column=1, pady=2, padx=4)
        tk.Label(lin, text="Data final:", bg=COR_VERDE, font=(FONTE, 9)).grid(row=1, column=0, sticky="w")
        ttk.Entry(lin, textvariable=self.r_ate, width=12, justify="center").grid(row=1, column=1, pady=2, padx=4)
        tk.Label(lin, text="Filtrar por:", bg=COR_VERDE, font=(FONTE, 9)).grid(row=2, column=0, sticky="w")
        ttk.Combobox(lin, textvariable=self.r_campo, width=13, state="readonly",
                     values=["Vencimento", "Emissão da NF", "Geração da guia"]).grid(row=2, column=1, pady=2, padx=4)
        at = tk.Frame(rel, bg=COR_VERDE)
        at.pack(pady=4)
        for txt, fn in (("Este mês", self._p_mes), ("Mês anterior", self._p_mes_ant)):
            ttk.Button(at, text=txt, command=fn, width=11).pack(side="left", padx=1)
        self.bt_excel = botao(rel, "Gerar Excel", self.gerar_excel, "white", 11, borda=COR_BORDA_VERDE)
        self.bt_excel.pack(fill="x", pady=(4, 0), ipady=4)

        # ---------------- rodapé: andamento
        baixo = tk.Frame(self, bg="white")
        baixo.pack(fill="x", padx=12, pady=(0, 8))
        self.prog = ttk.Progressbar(baixo, mode="indeterminate", length=160)
        self.prog.pack(side="right")
        self.lbl_status = tk.Label(baixo, text="Pronto.", bg="white", anchor="w", font=(FONTE, 9))
        self.lbl_status.pack(side="left", fill="x", expand=True)
        self.var_log = tk.BooleanVar(value=False)
        ttk.Checkbutton(baixo, text="Mostrar detalhes", variable=self.var_log, command=self._toggle_log).pack(side="right", padx=8)
        self.fr_log = tk.Frame(self, bg="white")
        self.txt = tk.Text(self.fr_log, height=8, wrap="word", font=("Consolas", 9))
        sb2 = ttk.Scrollbar(self.fr_log, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb2.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        self.txt.tag_configure("erro", foreground=VERMELHO)
        self.txt.tag_configure("aviso", foreground="#9C5700")

    def _toggle_log(self):
        if self.var_log.get():
            self.fr_log.pack(fill="x", padx=12, pady=(0, 8))
        else:
            self.fr_log.pack_forget()

    def _atualizar_lbl_modo(self):
        modo = self.cfg.get("geral", "modo", "portal")
        txt = {"portal": "Abre o site do Portal GNRE, emite e salva o PDF",
               "ws": "Emite pelo webservice GNRE (certificado A1)",
               "ambos": "Webservice; se falhar, gera arquivo de lote",
               "lote": "Gera arquivo de lote + PDF demonstrativo"}.get(modo, modo)
        amb = "PRODUÇÃO" if self.cfg.get("geral", "ambiente") == "1" else "homologação"
        self.lbl_modo.configure(text=txt + (f" ({amb})" if modo in ("ws", "ambos") else ""))

    # ================================================================== infraestrutura
    def log(self, msg):
        self.fila.put(("log", msg))

    def status(self, msg):
        self.fila.put(("status", msg))

    def _drenar_fila(self):
        try:
            while True:
                tipo, val = self.fila.get_nowait()
                if tipo == "log":
                    s = val.strip()
                    tag = "erro" if s.startswith(("ERRO", "[ERRO]")) else (
                        "aviso" if s.startswith(("[AVISO]", "ATENÇÃO", "Aviso", ">>>")) else "")
                    self.txt.insert("end", val + "\n", tag)
                    self.txt.see("end")
                    if s and not s.startswith("["):
                        self.lbl_status.configure(text=s[:160], fg=VERMELHO if tag == "erro" else "black")
                    if ">>>" in s:  # reCAPTCHA etc.: chama atenção
                        self.lbl_status.configure(text=s.replace(">>>", "").strip()[:160], fg="#C55A11")
                elif tipo == "status":
                    self.lbl_status.configure(text=val, fg="black")
                elif tipo == "fim":
                    val()
        except queue.Empty:
            pass
        self.after(150, self._drenar_fila)

    def _rodar(self, alvo, ao_fim, texto="Processando..."):
        if self.ocupado:
            return
        self.ocupado = True
        for b in (self.bt_importar, self.bt_lote, self.bt_limpar, self.bt_gerar, self.bt_excel):
            b.configure(state="disabled")
        self.lbl_status.configure(text=texto, fg="black")
        self.prog.start(12)

        def run():
            res = None
            try:
                res = alvo()
            except Exception as e:
                import traceback
                self.log(f"ERRO: {e}")
                self.log(traceback.format_exc())
            def fim():
                self.ocupado = False
                self.prog.stop()
                for b in (self.bt_importar, self.bt_lote, self.bt_limpar, self.bt_gerar, self.bt_excel):
                    b.configure(state="normal")
                ao_fim(res)
            self.fila.put(("fim", fim))
        threading.Thread(target=run, daemon=True).start()

    def _perguntar(self, titulo, msg):
        """Pergunta ao usuário a partir da thread de trabalho (bloqueia até a resposta)."""
        ev, res = threading.Event(), {}
        def ask():
            res["v"] = simpledialog.askstring(titulo, msg, parent=self)
            ev.set()
        self.fila.put(("fim", ask))
        ev.wait()
        return res.get("v")

    def _data_pagamento(self):
        try:
            dp = parse_br(self.var_pag.get()) or date.today()
        except ValueError as e:
            messagebox.showwarning(APP_NOME, str(e))
            return None
        if dp < date.today():
            messagebox.showwarning(APP_NOME, "A data de pagamento não pode ser anterior a hoje.")
            return None
        return dp

    # ================================================================== IMPORTAR
    def importar_arquivos(self):
        ini = self.cfg.get("geral", "ultima_pasta_xml") or os.path.expanduser("~")
        arqs = filedialog.askopenfilenames(title="Importar XML de NF-e (selecione um ou vários)", initialdir=ini,
                                           filetypes=[("XML de NF-e", "*.xml *.XML"), ("Todos os arquivos", "*.*")])
        if arqs:
            self.cfg.set("geral", "ultima_pasta_xml", os.path.dirname(arqs[0]))
            self._adicionar([os.path.normpath(a) for a in arqs])

    def importar_pasta(self):
        ini = self.cfg.get("geral", "ultima_pasta_xml") or os.path.expanduser("~")
        p = filedialog.askdirectory(title="Importar em lote: pasta com os XMLs (inclui subpastas)", initialdir=ini)
        if p:
            self.cfg.set("geral", "ultima_pasta_xml", p)
            self._adicionar([os.path.normpath(p)])

    def _adicionar(self, novos):
        for n in novos:
            if n not in self.fontes:
                self.fontes.append(n)
        self.cfg.save()
        self.analisar()

    def limpar(self):
        if self.linhas and not messagebox.askyesno(APP_NOME, "Limpar os XMLs importados e a lista da tela?\n\n"
                                                   "(O histórico das guias já emitidas é mantido.)"):
            return
        self.fontes, self.analise, self.linhas = [], None, []
        self.tv.delete(*self.tv.get_children())
        self.txt.delete("1.0", "end")
        self.lbl_total.configure(text="")
        self.lbl_info.configure(text="Importe os XMLs para listar os impostos a pagar.")
        self.lbl_status.configure(text="Dados limpos.", fg="black")

    def analisar(self):
        dp = self._data_pagamento()
        if not dp or not self.fontes:
            return
        self.cfg = Config()
        fontes = list(self.fontes)
        self.txt.delete("1.0", "end")
        self._rodar(lambda: processador.analisar_pasta(self.cfg, fontes, dp, self.log), self._preencher,
                    "Lendo os XMLs e calculando os impostos...")

    def recalcular(self):
        if self.fontes:
            self.analisar()

    # ================================================================== TABELA
    def _preencher(self, res):
        if not res:
            return
        self.analise = res
        self.tv.delete(*self.tv.get_children())
        banco = Banco(DADOS_DIR)
        # guias primeiro (ordenadas por vencimento), informativos no fim
        self.linhas = sorted(res.obrigacoes, key=lambda o: (not o.gera_guia, o.vencimento, o.nNF.zfill(9)))
        tot, tot_enc, venc, n_guias = 0.0, 0.0, 0, 0
        for i, ob in enumerate(self.linhas):
            ex = banco.existente(ob.chave, ob.tipo) if ob.gera_guia else None
            emitida = bool(ex and (ex.get("situacao") or "").startswith("EMITIDA"))
            if not ob.gera_guia:
                tag, sit = "info", "Só conferência (sem guia)"
            elif emitida:
                tag, sit = "emitida", "Guia emitida"
                ob.situacao = ex["situacao"]
            elif ob.dias_atraso > 0:
                tag, sit = "vencida", f"VENCIDA há {ob.dias_atraso} dia(s)"
                venc += 1
            else:
                tag, sit = "prazo", "No prazo"
            if ex and not emitida and ob.gera_guia:
                st = (ex.get("situacao") or "")
                if st.startswith("LOTE GERADO"):
                    sit += " – lote gerado"
                elif st.startswith(("REJEITADA", "ERRO", "PENDENTE")):
                    sit += " – " + st.split("(")[0].strip().lower()
            enc = ob.multa + ob.juros
            self.tv.insert("", "end", iid=str(i), tags=(tag,), values=(
                ob.nNF, ob.cliente, ob.tipo, ob.uf, moeda(ob.valor), ob.vencimento.strftime("%d/%m/%Y"),
                moeda(enc) if enc else "—", moeda(ob.total) if ob.gera_guia else "—", sit))
            if ob.gera_guia and not emitida:
                tot += ob.total
                tot_enc += enc
                n_guias += 1
        erros = sum(1 for o in res.ocorrencias if o.nivel == "ERRO")
        self.lbl_total.configure(text=f"A pagar: {n_guias} guia(s)  |  Total {moeda(tot)}"
                                      + (f"  (multa/juros {moeda(tot_enc)})" if tot_enc else ""))
        info = f"{res.notas_consideradas} nota(s) analisada(s)"
        if venc:
            info += f"  •  {venc} vencida(s)"
        if erros:
            info += f"  •  {erros} erro(s) nos XMLs (veja 'Mostrar detalhes' ou o relatório)"
        self.lbl_info.configure(text=info, fg=VERMELHO if (venc or erros) else "#404040")
        for o in res.ocorrencias:
            self.log(f"[{o.nivel}] NF {o.nNF}: {o.mensagem}")
        self.lbl_status.configure(text=f"{len(self.fontes)} origem(ns) importada(s). Selecione as linhas e clique em "
                                       "GERAR GUIA PARA PAGAMENTO.", fg="black")
        if erros and not self.var_log.get():
            self.var_log.set(True)
            self._toggle_log()

    def _ordenar(self, col):
        itens = [(self.tv.set(k, col), k) for k in self.tv.get_children("")]
        def chave(v):
            t = v[0]
            if col in ("valor", "multa", "total"):
                try:
                    return float(t.replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
                except ValueError:
                    return 0
            if col == "venc":
                try:
                    return datetime.strptime(t, "%d/%m/%Y")
                except ValueError:
                    return datetime.min
            if col == "nf":
                return int(t) if t.isdigit() else 0
            return t
        rev = getattr(self, "_ord_rev", {}).get(col, False)
        itens.sort(key=chave, reverse=rev)
        for i, (_, k) in enumerate(itens):
            self.tv.move(k, "", i)
        self._ord_rev = {col: not rev}

    def _detalhe(self, _e):
        sel = self.tv.selection()
        if not sel:
            return
        ob = self.linhas[int(sel[0])]
        txt = (f"NF {ob.nNF} série {ob.serie}\nCliente: {ob.cliente} ({ob.cliente_doc})\n"
               f"Imposto: {ob.tipo}  |  UF favorecida: {ob.uf}  |  Receita: {ob.receita or '—'}\n"
               f"Modalidade: {ob.modo}\nValor: {moeda(ob.valor)}\n"
               f"Vencimento: {ob.vencimento:%d/%m/%Y}" +
               (f"  (vencida há {ob.dias_atraso} dia(s))\nMulta est.: {moeda(ob.multa)}  Juros est.: {moeda(ob.juros)}\n"
                f"Total na data de pagamento: {moeda(ob.total)}" if ob.dias_atraso else "") +
               f"\n\nChave: {ob.chave}\n\n{ob.observacao}")
        oc = [o for o in (self.analise.ocorrencias if self.analise else []) if o.chave == ob.chave]
        if oc:
            txt += "\n\nOcorrências:\n" + "\n".join(f"• [{o.nivel}] {o.mensagem}" for o in oc)
        messagebox.showinfo(f"NF {ob.nNF} – {ob.tipo}", txt)

    # ================================================================== GERAR GUIAS
    def _abrir_pasta_pdf(self):
        p = self.cfg.get("geral", "pasta_pdf")
        if p and os.path.isdir(p):
            abrir(p)
        else:
            messagebox.showinfo(APP_NOME, "Nenhuma guia gerada ainda.")

    def gerar(self):
        if not self.analise:
            messagebox.showinfo(APP_NOME, "Clique em IMPORTAR XML primeiro.")
            return
        dp = self._data_pagamento()
        if not dp:
            return
        sel = self.tv.selection() or self.tv.get_children()
        escolhidas = [self.linhas[int(i)] for i in sel]
        escolhidas = [o for o in escolhidas if o.gera_guia and self.tv.set(str(self.linhas.index(o)), "situacao") != "Guia emitida"]
        if not escolhidas:
            messagebox.showinfo(APP_NOME, "Não há guias a gerar nas linhas selecionadas "
                                          "(já emitidas ou apenas de conferência).")
            return
        total = sum(o.total for o in escolhidas)
        vencidas = sum(1 for o in escolhidas if o.dias_atraso > 0)
        modo = self.cfg.get("geral", "modo", "portal")
        if modo not in ("portal", "ws", "ambos", "lote"):
            modo = "portal"
        destino_txt = {"portal": "O programa vai abrir o site do Portal GNRE no navegador, emitir as guias e salvar os PDFs.",
                       "ws": "As guias serão emitidas pelo webservice GNRE (certificado A1).",
                       "ambos": "As guias serão emitidas pelo webservice GNRE; se falhar, será gerado o arquivo de lote.",
                       "lote": "Será gerado o arquivo de lote para o portal e um PDF demonstrativo."}[modo]
        if not messagebox.askyesno(APP_NOME, f"Gerar {len(escolhidas)} guia(s) – total {moeda(total)}"
                                   + (f"\n{vencidas} vencida(s): pagamento em {dp:%d/%m/%Y} com multa/juros." if vencidas else "")
                                   + f"\n\n{destino_txt}\n\nNa próxima tela escolha a pasta onde salvar os PDFs."):
            return
        pasta = filedialog.askdirectory(title="Salvar os PDFs das guias em",
                                        initialdir=self.cfg.get("geral", "pasta_pdf") or os.path.expanduser("~"))
        if not pasta:
            return
        pasta = os.path.normpath(pasta)
        senha = ""
        if modo in ("ws", "ambos"):
            senha = self.cfg.get_senha() or simpledialog.askstring(APP_NOME, "Senha do certificado digital A1:",
                                                                   show="*", parent=self)
            if senha is None:
                return
            if self.cfg.get("geral", "ambiente") == "1" and not messagebox.askyesno(
                    APP_NOME, "Ambiente de PRODUÇÃO: as guias serão emitidas de verdade. Continuar?"):
                return
        self.cfg.set("geral", "pasta_pdf", pasta)
        self.cfg.set("geral", "pasta_saida", pasta)
        self.cfg.save()
        sel_chaves = {(o.chave, o.tipo) for o in escolhidas}
        analise, cfg, log, fontes = self.analise, self.cfg, self.log, list(self.fontes)
        perguntar = self._perguntar

        def trabalho():
            from .prazos import Selic
            from .regras import Analisador
            an = Analisador(cfg, Selic(os.path.join(DADOS_DIR, "selic.json"),
                                       cfg.getfloat("encargos", "selic_mes_fallback_pct", 1.0), log))
            for ob in analise.obrigacoes:
                if ob.gera_guia:
                    ob.situacao = "A GERAR"
                an.aplicar_pagamento(ob, dp)
            return processador.processar(cfg, fontes, pasta, modo, dp, senha, log, False, analise,
                                         sel_chaves, pasta, perguntar)

        def fim(r):
            if not r:
                return
            self._preencher(self.analise)
            ok = sum(1 for g in r["guias"] if g.situacao.startswith(("EMITIDA", "LOTE GERADO")))
            erro = [g for g in r["guias"] if g.situacao.startswith(("ERRO", "REJEITADA", "PENDENTE"))]
            msg = f"{ok} guia(s) gerada(s) e salva(s) em:\n{r['pasta_guias']}"
            if erro:
                msg += f"\n\n{len(erro)} guia(s) não emitida(s):\n" + "\n".join(
                    f"• NF {g.nfs} {g.tipo}/{g.uf}: {(g.motivos or [g.situacao])[0][:120]}" for g in erro[:6])
            if messagebox.askyesno(APP_NOME, msg + "\n\nAbrir a pasta das guias?"):
                abrir(r["pasta_guias"])
        self._rodar(trabalho, fim, "Gerando as guias...")

    # ================================================================== RELATÓRIO EXCEL
    def _p_mes(self):
        h = date.today()
        prox = (h.replace(day=28) + timedelta(days=4)).replace(day=1)
        self.r_de.set(h.replace(day=1).strftime("%d/%m/%Y"))
        self.r_ate.set((prox - timedelta(days=1)).strftime("%d/%m/%Y"))

    def _p_mes_ant(self):
        fim = date.today().replace(day=1) - timedelta(days=1)
        self.r_de.set(fim.replace(day=1).strftime("%d/%m/%Y"))
        self.r_ate.set(fim.strftime("%d/%m/%Y"))

    def gerar_excel(self):
        try:
            de, ate = parse_br(self.r_de.get()), parse_br(self.r_ate.get())
        except ValueError as e:
            messagebox.showwarning(APP_NOME, str(e))
            return
        if not de or not ate or de > ate:
            messagebox.showwarning(APP_NOME, "Informe a data inicial e a data final (inicial ≤ final).")
            return
        campo = {"Vencimento": "vencimento", "Emissão da NF": "data_emissao", "Geração da guia": "gerado_em"}[self.r_campo.get()]
        # histórico (guias já processadas) + o que está na tela agora
        rows = {(r["chave"], r["tipo"]): r for r in Banco(DADOS_DIR).consultar(campo, de, ate)}
        for ob in self.linhas:
            d = {"vencimento": ob.vencimento, "data_emissao": ob.data_emissao, "gerado_em": date.today()}[campo]
            if not d or not (de <= d <= ate) or (ob.chave, ob.tipo) in rows:
                continue
            rows[(ob.chave, ob.tipo)] = {
                "nf": ob.nNF, "serie": ob.serie, "data_emissao": ob.data_emissao, "cliente": ob.cliente,
                "cliente_doc": ob.cliente_doc, "uf": ob.uf, "tipo": ob.tipo, "receita": ob.receita, "modo": ob.modo,
                "valor": ob.valor, "multa": ob.multa, "juros": ob.juros, "vencimento": ob.vencimento,
                "vencimento_original": ob.vencimento_original, "data_pagamento": ob.data_pagamento,
                "dias_atraso": ob.dias_atraso,
                "situacao": "VENCIDA" if ob.dias_atraso > 0 and ob.gera_guia else ob.situacao,
                "observacao": ob.observacao, "chave": ob.chave}
        linhas = sorted(rows.values(), key=lambda x: (str(x.get("vencimento") or ""), str(x.get("nf")).zfill(9)))
        if not linhas:
            messagebox.showinfo(APP_NOME, f"Nenhum imposto com {self.r_campo.get().lower()} entre "
                                          f"{de:%d/%m/%Y} e {ate:%d/%m/%Y}.")
            return
        p = filedialog.asksaveasfilename(
            title="Salvar relatório Excel", defaultextension=".xlsx",
            initialfile=f"Relatorio_Guias_{de:%Y-%m-%d}_a_{ate:%Y-%m-%d}.xlsx",
            initialdir=self.cfg.get("geral", "pasta_pdf") or os.path.expanduser("~"),
            filetypes=[("Excel", "*.xlsx")])
        if not p:
            return
        oc = None
        if self.analise:
            chaves = {r["chave"] for r in linhas}
            oc = [o for o in self.analise.ocorrencias if o.chave in chaves]
        exportar(linhas, p, "Relatório de Guias de Impostos", oc,
                 f"{self.r_campo.get()} de {de:%d/%m/%Y} até {ate:%d/%m/%Y}")
        self.lbl_status.configure(text=f"Relatório salvo: {p} ({len(linhas)} linha(s))", fg="black")
        abrir(p)

    # ================================================================== CONFIGURAÇÕES
    def abrir_config(self):
        j = tk.Toplevel(self)
        j.title("Configurações")
        j.transient(self)
        j.grab_set()
        f = ttk.Frame(j, padding=12)
        f.pack(fill="both", expand=True)
        self.cv = {}
        ttk.Label(f, text="Como gerar as guias:").grid(row=0, column=0, sticky="w", pady=2)
        self.v_modo = tk.StringVar(value=self.cfg.get("geral", "modo", "portal"))
        mf = ttk.Frame(f)
        mf.grid(row=0, column=1, columnspan=2, sticky="w")
        for txt, val in (("Site do Portal GNRE (navegador)", "portal"), ("Webservice (certificado A1)", "ws"),
                         ("Webservice + lote", "ambos"), ("Só arquivo de lote", "lote")):
            ttk.Radiobutton(mf, text=txt, variable=self.v_modo, value=val).pack(side="left", padx=3)
        campos = [
            ("empresa", "cnpjs_remetente", "CNPJ(s) da empresa remetente (separados por vírgula)", 50),
            ("empresa", "telefone", "Telefone para a guia (DDD + número)", 20),
            ("portal", "email", "E-mail usado no site do Portal GNRE", 40),
            ("portal", "navegador", "Navegador para o site (edge / chrome)", 10),
            ("certificado", "arquivo_pfx", "Certificado digital A1 (.pfx) – só para webservice", 50),
            ("geral", "ambiente", "Ambiente do webservice (1 = Produção, 2 = Homologação)", 5),
            ("geral", "padrao_nome_arquivo", "Nome do PDF ({vencimento} {nf} {tipo} {uf} {receita} {cliente})", 50),
            ("geral", "formato_data_arquivo", "Formato da data no nome do PDF (%Y-%m-%d ou %d-%m-%Y)", 15),
            ("prazos", "dia_st_apuracao", "Dia de vencimento ST por apuração (mês seguinte)", 5),
            ("prazos", "dia_difal_apuracao", "Dia de vencimento DIFAL por apuração (mês seguinte)", 5),
            ("encargos", "multa_dia_pct", "Multa de mora (% ao dia)", 8),
            ("encargos", "multa_teto_pct", "Teto da multa de mora (%)", 8),
            ("encargos", "juros", "Juros (selic / fixo)", 8),
        ]
        for i, (sec, key, rot, w) in enumerate(campos, 1):
            ttk.Label(f, text=rot + ":").grid(row=i, column=0, sticky="w", pady=2)
            v = tk.StringVar(value=self.cfg.get(sec, key))
            self.cv[(sec, key)] = v
            ttk.Entry(f, textvariable=v, width=w).grid(row=i, column=1, sticky="w", padx=6)
            if key == "arquivo_pfx":
                ttk.Button(f, text="Procurar...", command=self._pfx).grid(row=i, column=2, sticky="w")
        i = len(campos) + 1
        self.v_senha = tk.StringVar(value=self.cfg.get_senha())
        self.v_lembrar = tk.BooleanVar(value=self.cfg.getbool("certificado", "lembrar_senha"))
        ttk.Label(f, text="Senha do certificado:").grid(row=i, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.v_senha, show="*", width=20).grid(row=i, column=1, sticky="w", padx=6)
        ttk.Checkbutton(f, text="Lembrar senha (apenas ofuscada)", variable=self.v_lembrar).grid(row=i, column=2, sticky="w")
        bt = ttk.Frame(f)
        bt.grid(row=i + 1, column=0, columnspan=3, sticky="w", pady=10)
        ttk.Button(bt, text="Salvar", command=lambda: self._salvar_cfg(j)).pack(side="left")
        ttk.Button(bt, text="Testar certificado", command=self._testar_cert).pack(side="left", padx=6)
        ttk.Button(bt, text="Abrir config.ini (avançado)", command=lambda: abrir(self.cfg.path)).pack(side="left")
        ttk.Button(bt, text="Cancelar", command=j.destroy).pack(side="left", padx=6)

    def _pfx(self):
        p = filedialog.askopenfilename(filetypes=[("Certificado A1", "*.pfx *.p12")])
        if p:
            self.cv[("certificado", "arquivo_pfx")].set(os.path.normpath(p))

    def _salvar_cfg(self, janela=None):
        for (sec, key), v in self.cv.items():
            self.cfg.set(sec, key, v.get().strip())
        self.cfg.set("geral", "modo", self.v_modo.get())
        self.cfg.set_senha(self.v_senha.get(), self.v_lembrar.get())
        self.cfg.save()
        self._atualizar_lbl_modo()
        if janela:
            janela.destroy()
        if self.fontes:
            self.analisar()

    def _testar_cert(self):
        from .gnre_ws import CertificadoA1
        try:
            c = CertificadoA1(self.cv[("certificado", "arquivo_pfx")].get(), self.v_senha.get())
            c.limpar()
            msg = f"Certificado OK.\n\n{c.titular}\n\nVálido até {c.validade:%d/%m/%Y}"
            if c.validade < date.today():
                msg += "\n\nATENÇÃO: certificado VENCIDO."
            messagebox.showinfo(APP_NOME, msg)
        except Exception as e:
            messagebox.showerror(APP_NOME, f"Não foi possível abrir o certificado:\n{e}")


def main():
    app = App()
    if not app.cfg.cnpjs_remetente():
        app.after(600, lambda: messagebox.showinfo(
            APP_NOME, "Primeiro uso: informe o CNPJ da empresa e o e-mail do Portal GNRE no botão Configurações (canto superior direito)."))
    app.mainloop()
