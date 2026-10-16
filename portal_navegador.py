"""Emissão das guias pelo site do Portal GNRE (www.gnre.pe.gov.br) usando o navegador do usuário.

Fluxo (mesmas telas que o usuário usaria manualmente):
  1. "Processar Lote"  (/gnre/v/lote/processar): envia o XML de lote gerado pelo programa
     e lê o Número do Recibo (protocolo).
  2. "Consultar Lote"  (/gnre/v/lote/consultar): consulta o protocolo, aguarda o processamento,
     seleciona todas as guias processadas e clica em "Emitir".
  3. Baixa o PDF oficial emitido pelo portal, salva na pasta escolhida e separa uma guia por arquivo.

O portal usa reCAPTCHA e, no primeiro uso de um e-mail, um código de validação enviado por e-mail.
O programa NÃO resolve o reCAPTCHA: se o portal mostrar um desafio, o usuário o resolve na janela
do navegador e o programa continua sozinho. O código de validação do e-mail é pedido numa caixa
de diálogo do programa.
"""
import base64
import os
import re
import shutil
import tempfile
import time

from .config import DADOS_DIR

URL_PROCESSAR = "https://www.gnre.pe.gov.br:444/gnre/v/lote/processar"
URL_CONSULTAR = "https://www.gnre.pe.gov.br:444/gnre/v/lote/consultar"


class ErroPortal(Exception):
    pass


class PortalGNRE:
    def __init__(self, cfg, log=print, perguntar=None):
        self.cfg = cfg
        self.log = log
        self.perguntar = perguntar or (lambda titulo, msg: None)
        self.driver = None
        self.url_processar = cfg.get("portal", "url_processar") or URL_PROCESSAR
        self.url_consultar = cfg.get("portal", "url_consultar") or URL_CONSULTAR
        self.timeout = cfg.getint("portal", "tempo_maximo_min", 15) * 60
        self.download_dir = tempfile.mkdtemp(prefix="gnre_portal_")

    # ------------------------------------------------------------------ navegador
    def abrir(self):
        from selenium import webdriver
        preferido = (self.cfg.get("portal", "navegador") or "edge").lower()
        ordem = ["edge", "chrome"] if preferido != "chrome" else ["chrome", "edge"]
        prefs = {
            "download.default_directory": self.download_dir,
            "download.prompt_for_download": False,
            "plugins.always_open_pdf_externally": True,
            "profile.default_content_settings.popups": 0,
        }
        erros = []
        for nav in ordem:
            try:
                perfil = os.path.join(DADOS_DIR, f"navegador_{nav}")
                os.makedirs(perfil, exist_ok=True)
                if nav == "edge":
                    op = webdriver.EdgeOptions()
                else:
                    op = webdriver.ChromeOptions()
                op.add_argument(f"--user-data-dir={perfil}")
                op.add_argument("--start-maximized")
                op.add_experimental_option("prefs", prefs)
                binario = self.cfg.get("portal", "binario_navegador")
                if binario:
                    op.binary_location = binario
                if self.cfg.getbool("portal", "oculto", False):
                    op.add_argument("--headless=new")
                    op.add_argument("--no-sandbox")
                    op.add_argument("--disable-dev-shm-usage")
                drv = self.cfg.get("portal", "driver")
                if nav == "edge":
                    from selenium.webdriver.edge.service import Service
                    self.driver = webdriver.Edge(options=op, service=Service(drv) if drv else None)
                else:
                    from selenium.webdriver.chrome.service import Service
                    self.driver = webdriver.Chrome(options=op, service=Service(drv) if drv else None)
                self.driver.set_page_load_timeout(90)
                self.log(f"  Navegador aberto: {'Microsoft Edge' if nav == 'edge' else 'Google Chrome'}")
                return
            except Exception as e:  # navegador não instalado / driver indisponível
                erros.append(f"{nav}: {str(e).splitlines()[0][:150]}")
        raise ErroPortal("Não foi possível abrir o Edge nem o Chrome. " + " | ".join(erros))

    def reabrir(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None
        time.sleep(2)
        self.abrir()

    def executar(self, fn, *args):
        """Executa uma etapa; se o navegador cair (sessão perdida), reabre e tenta mais uma vez."""
        try:
            return fn(*args)
        except ErroPortal:
            raise
        except Exception as e:
            msg = str(e).lower()
            if "invalid session" in msg or "disconnected" in msg or "no such window" in msg or "not reachable" in msg:
                self.log("  O navegador foi fechado/perdeu a conexão – reabrindo e repetindo a etapa...")
                self.reabrir()
                return fn(*args)
            raise ErroPortal(str(e).splitlines()[0][:300])

    def fechar(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        shutil.rmtree(self.download_dir, ignore_errors=True)

    # ------------------------------------------------------------------ utilidades
    def _js(self, script, *args):
        return self.driver.execute_script(script, *args)

    def _dialogos_visiveis(self):
        """[(titulo, texto)] dos diálogos jQuery-UI abertos."""
        return self._js("""
            return [...document.querySelectorAll('.ui-dialog')]
              .filter(d => d.offsetParent !== null && getComputedStyle(d).display !== 'none'
                           && !d.querySelector('#gLoading'))
              .map(d => [ (d.querySelector('.ui-dialog-title')||{}).innerText || '',
                          (d.querySelector('.ui-dialog-content')||{}).innerText || '',
                          !!d.querySelector('#numero'),
                          !!d.querySelector('.ui-icon32-error, .ui-icon32-alert') ]);
        """) or []

    def _clicar_botao_dialogo(self, texto):
        return self._js("""
            const t = arguments[0];
            const b = [...document.querySelectorAll('.ui-dialog .ui-dialog-buttonpane button')]
               .find(b => b.offsetParent !== null && b.innerText.trim() === t);
            if (b) { b.click(); return true; } return false;
        """, texto)

    def _desafio_captcha_visivel(self):
        return bool(self._js("""
            return [...document.querySelectorAll('iframe')].some(f =>
               /recaptcha\\/api2\\/bframe|recaptcha\\/enterprise\\/bframe/.test(f.src) &&
               f.getBoundingClientRect().height > 100 &&
               getComputedStyle(f.parentElement.parentElement).visibility !== 'hidden');
        """))

    def _aguardar(self, cond, descricao, limite=None):
        fim = time.time() + (limite or self.timeout)
        avisou = False
        while time.time() < fim:
            r = cond()
            if r:
                return r
            if not avisou and self._desafio_captcha_visivel():
                self.log("  >>> O portal pediu uma verificação reCAPTCHA. Resolva-a na janela do navegador; "
                         "o programa continua automaticamente.")
                avisou = True
            time.sleep(1)
        raise ErroPortal(f"Tempo esgotado aguardando: {descricao}")

    # ------------------------------------------------------------------ 1) envio do lote
    def enviar_lote(self, arquivo_xml, email):
        from selenium.webdriver.common.by import By
        d = self.driver
        nome = os.path.basename(arquivo_xml)
        if not re.match(r"^[a-zA-Z0-9\-_. ]+\.(xml|XML)$", nome):
            novo = os.path.join(os.path.dirname(arquivo_xml), re.sub(r"[^a-zA-Z0-9\-_. ]", "_", nome))
            shutil.copy(arquivo_xml, novo)
            arquivo_xml = novo
        self.log(f"  Abrindo 'Processar Lote' no portal e enviando {os.path.basename(arquivo_xml)}...")
        d.get(self.url_processar)
        self._aguardar(lambda: d.find_elements(By.ID, "arquivo"), "página Processar Lote", 90)
        time.sleep(1.5)
        em = d.find_element(By.ID, "email")
        em.clear()
        em.send_keys(email)
        self._js("var s=document.getElementById('versao'); if(s){s.value='2.00';}")
        d.find_element(By.ID, "arquivo").send_keys(os.path.abspath(arquivo_xml))
        self._aguardar(lambda: self._js("var e=document.querySelector('.NFI-filename'); return e && e.value;"),
                       "seleção do arquivo", 20)
        d.find_element(By.ID, "processar").click()
        pediu_codigo = False

        def estado():
            dlgs = self._dialogos_visiveis()
            for titulo, texto, _, _ in dlgs:
                m = re.search(r"Recibo:\s*(\d+)", texto)
                if m:
                    return ("ok", m.group(1), texto)
            for titulo, texto, tem_numero, eh_erro in dlgs:
                if eh_erro and not tem_numero:
                    return ("erro", None, f"{titulo}: {texto}")
            if any(t[2] for t in dlgs):
                return ("codigo", None, "")
            return None

        while True:
            st = self._aguardar(estado, "recibo do lote")
            if st[0] == "ok":
                self.log(f"  Lote recebido pelo portal. Recibo nº {st[1]}.")
                self._clicar_botao_dialogo("Fechar")
                return st[1]
            if st[0] == "codigo":
                if pediu_codigo:
                    time.sleep(2)
                    continue
                pediu_codigo = True
                cod = self.perguntar("Validação do e-mail no Portal GNRE",
                                     f"O Portal GNRE enviou um número de validação para {email}.\n"
                                     "Digite o número recebido:")
                if not cod:
                    raise ErroPortal("Validação de e-mail cancelada pelo usuário.")
                campo = d.find_element(By.ID, "numero")
                campo.clear()
                campo.send_keys(cod.strip())
                self._clicar_botao_dialogo("Validar")
                time.sleep(2)
                continue
            if pediu_codigo and "Validar Email" in st[2]:
                # código errado: fecha a mensagem e pede de novo
                self._clicar_botao_dialogo("Ok") or self._clicar_botao_dialogo("OK") or self._clicar_botao_dialogo("Fechar")
                self.log("  Código de validação recusado pelo portal: " + re.sub(r"\s+", " ", st[2])[:200])
                pediu_codigo = False
                time.sleep(1)
                continue
            raise ErroPortal("Portal recusou o lote: " + re.sub(r"\s+", " ", st[2]).strip()[:500])

    # ------------------------------------------------------------------ 2) consulta + emissão
    def consultar_e_emitir(self, protocolo, email):
        from selenium.webdriver.common.by import By
        d = self.driver
        self.log(f"  Consultando o recibo {protocolo} em 'Consultar Lote'...")
        # Abre a consulta numa aba nova (a página de envio fica para trás e é fechada)
        anterior = d.current_window_handle
        d.switch_to.new_window("tab")
        try:
            novo = d.current_window_handle
            d.switch_to.window(anterior)
            d.close()
            d.switch_to.window(novo)
        except Exception:
            pass
        d.get(self.url_consultar)
        self._aguardar(lambda: d.find_elements(By.ID, "protocolo"), "página Consultar Lote", 90)
        time.sleep(1.5)
        d.find_element(By.ID, "protocolo").send_keys(protocolo)
        self._js("document.getElementById('origemPortal').click();")
        em = d.find_element(By.ID, "email")
        em.clear()
        em.send_keys(email)
        espera_proc = self.cfg.getint("portal", "intervalo_atualizar_seg", 15)
        d.find_element(By.ID, "consultar").click()

        def resultado_aberto():
            vis = self._js("var r=document.getElementById('resultado');"
                           "return r && r.offsetParent!==null ? (document.getElementById('situacao')||{}).innerText : null;")
            if vis:
                return vis
            for titulo, texto, _, eh_erro in self._dialogos_visiveis():
                if eh_erro:
                    raise ErroPortal("Consulta do lote: " + re.sub(r"\s+", " ", texto)[:400])
            return None

        while True:
            situacao = self._aguardar(resultado_aberto, "resultado da consulta do lote")
            if re.search(r"Aguardando|Em processamento|pendência de tempo", situacao, re.I):
                self.log(f"  Situação: {situacao.strip()[:80]} – nova consulta em {espera_proc}s")
                time.sleep(espera_proc)
                self._js("document.getElementById('btnAtualizar') && document.getElementById('btnAtualizar').click();")
                time.sleep(2)
                continue
            break
        self.log(f"  Situação do lote: {situacao.strip()}")
        linhas = self._js("""
            return [...document.querySelectorAll('#tblGuias > tbody > tr')].map(tr => {
                const td = tr.querySelectorAll('td');
                return { seq: (td[1]||{}).innerText, uf: (td[2]||{}).innerText,
                         dados: (td[3]||{}).innerText, situacao: (td[4]||{}).innerText,
                         emitivel: !!tr.querySelector('input[type=checkbox][name^="guias"]') };
            });
        """) or []
        if not any(l["emitivel"] for l in linhas):
            return None, linhas
        self._js("document.querySelectorAll('input[type=checkbox][name^=\"guias\"]').forEach(c => c.checked = true);")
        self._js("document.getElementById('btnEmitir').click();")
        self.log("  Emitindo as guias no portal...")
        src = self._aguardar(lambda: self._js(
            "var f=document.getElementById('ifGnre'); return f && f.src && f.src.indexOf('emitir')>=0 ? f.src : null;"),
            "emissão das guias (botão Emitir)")
        pdf = self._baixar(src)
        return pdf, linhas

    def _baixar(self, url):
        """Baixa o documento emitido com a sessão do navegador. Se vier HTML, imprime em PDF."""
        time.sleep(2)
        b64 = self.driver.execute_async_script("""
            const done = arguments[arguments.length-1];
            fetch(arguments[0], {credentials: 'include'}).then(r => r.arrayBuffer()).then(buf => {
                let s = ''; const b = new Uint8Array(buf);
                for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
                done(btoa(s));
            }).catch(e => done('ERRO:' + e));
        """, url)
        if b64 and not b64.startswith("ERRO:"):
            dados = base64.b64decode(b64)
            if dados[:4] == b"%PDF":
                return dados
        # Conteúdo em HTML (ou bloqueado): abre a guia e gera o PDF pelo próprio navegador
        atual = self.driver.current_window_handle
        self.driver.switch_to.new_window("tab")
        try:
            self.driver.get(url)
            time.sleep(4)
            arquivos = [f for f in os.listdir(self.download_dir) if f.lower().endswith(".pdf")]
            if arquivos:  # o navegador baixou o PDF
                return open(os.path.join(self.download_dir, arquivos[-1]), "rb").read()
            r = self.driver.execute_cdp_cmd("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True})
            return base64.b64decode(r["data"])
        finally:
            self.driver.close()
            self.driver.switch_to.window(atual)


# ---------------------------------------------------------------------- PDF: separar por guia
LINHA_RE = re.compile(r"(\d{11}\s*-?\s*\d)\s+(\d{11}\s*-?\s*\d)\s+(\d{11}\s*-?\s*\d)\s+(\d{11}\s*-?\s*\d)")


def separar_pdf(pdf_bytes, guias, pasta, cfg, log=print):
    """Divide o PDF do portal em um arquivo por guia (na ordem do lote) e grava com o nome padrão."""
    import io
    from pypdf import PdfReader, PdfWriter
    from .pdf_guia import nome_arquivo
    leitor = PdfReader(io.BytesIO(pdf_bytes))
    paginas = len(leitor.pages)
    n = len(guias)
    if n == 0:
        return
    textos = []
    for p in leitor.pages:
        try:
            textos.append(p.extract_text() or "")
        except Exception:
            textos.append("")
    # Associa páginas às guias: 1) por texto (nº do documento/chave/valor); 2) divisão uniforme
    grupos = {i: [] for i in range(n)}
    atual = None
    for ip, txt in enumerate(textos):
        so_num = re.sub(r"\D", "", txt)
        achou = None
        for ig, g in enumerate(guias):
            chave = g.obrigacoes[0].chave if g.uma_nota else ""
            valor = f"{g.principal + g.fcp:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            if (chave and chave in so_num) or (g.doc_origem and re.search(rf"\b{re.escape(g.doc_origem)}\b", txt)
                                                and valor in txt and g.uf in txt):
                achou = ig
                break
        if achou is None and atual is not None:
            achou = atual
        if achou is None:
            break
        grupos[achou].append(ip)
        atual = achou
    usados = sum(len(v) for v in grupos.values())
    if usados != paginas or any(not v for v in grupos.values()):
        if paginas % n == 0:
            por = paginas // n
            grupos = {i: list(range(i * por, (i + 1) * por)) for i in range(n)}
        else:
            log("  Não foi possível separar o PDF do portal por guia; cada guia aponta para o arquivo completo.")
            return
    os.makedirs(pasta, exist_ok=True)
    for ig, g in enumerate(guias):
        w = PdfWriter()
        for ip in grupos[ig]:
            w.add_page(leitor.pages[ip])
        texto = " ".join(textos[ip] for ip in grupos[ig])
        m = LINHA_RE.search(texto)
        if m and not g.linha_digitavel:
            g.linha_digitavel = re.sub(r"\D", "", "".join(m.groups()))
        caminho = os.path.join(pasta, nome_arquivo(g, cfg))
        base, ext = os.path.splitext(caminho)
        k = 2
        while os.path.exists(caminho):
            caminho = f"{base} ({k}){ext}"
            k += 1
        with open(caminho, "wb") as f:
            w.write(f)
        g.arquivo_pdf = caminho
