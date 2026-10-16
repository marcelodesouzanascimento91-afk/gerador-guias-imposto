# Gerador de Guias GNRE (DIFAL · FCP · ICMS-ST)

Aplicativo desktop para Windows (Python + Tkinter) que lê XMLs de **NF-e de saída**, identifica os impostos estaduais que o **remetente** precisa recolher **por guia, fora da nota fiscal**, e gera as guias **GNRE** com PDF e relatório em Excel.

![Tela principal](docs/tela_principal.png)

> **Dados fictícios:** todas as notas, empresas, CNPJs, CPFs e inscrições estaduais deste repositório são inventados. Os documentos usam a base `999...` e foram gerados por `tests/gerar_xmls_ficticios.py`.

## O que ele faz

| Etapa | Detalhe |
|---|---|
| **Importa XMLs** | Arquivos avulsos ou uma pasta inteira (com subpastas). Aceita `nfeProc`, `NFe` e eventos de cancelamento. |
| **Identifica o imposto** | DIFAL (EC 87/2015 / LC 190/2022) – receita 100102 · FCP – 100129 · ICMS-ST interestadual – 100099 · FCP-ST · por apuração quando há IE de substituto (IEST): 100110 / 100137 / 100048 |
| **Confere o XML** | DV da chave e do CNPJ, DIFAL não destacado, DIFAL/FCP divergente do cálculo, alíquota interestadual incoerente (4/7/12%), nota sem protocolo, cancelada, de homologação ou duplicada, ISUF ausente |
| **Vencimentos e atrasos** | Por operação: vence na saída. Por apuração: dia 9 (ST) ou 15 (DIFAL) do mês seguinte, ajustado para dia útil (feriados nacionais). Vencidas aparecem **em vermelho**, com estimativa de multa (0,33%/dia, até 20%) e juros SELIC (API do Banco Central). |
| **Emite a guia** | **Site do Portal GNRE** (Edge/Chrome via Selenium: Processar Lote → Consultar Lote → Emitir → baixa o PDF oficial) · **Webservice GNRE v2.00** com certificado A1 (ConfigUF, recepção e resultado do lote) · **Arquivo de lote** XML validado no XSD oficial, para envio manual |
| **PDF** | Um arquivo por guia, com o nome `AAAA-MM-DD - NF 123 - DIFAL RJ.pdf` (vencimento + nota). |
| **Relatório Excel** | Nota, cliente, tipo de imposto, valor, vencimento, multa, juros, situação e linha digitável, com abas de resumo por UF e de ocorrências. Filtro por data inicial e final. |
| **Histórico** | SQLite local, para evitar emissão em duplicidade e consultar por período. |

<details>
<summary>Exemplo de guia (modo arquivo de lote: demonstrativo sem código de barras)</summary>

![Exemplo de guia](docs/exemplo_guia.png)
</details>

## Como rodar

```bash
git clone https://github.com/<usuario>/gerador-guias-gnre.git
cd gerador-guias-gnre
python -m venv .venv && .venv\Scripts\activate      # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python GeradorGuias.py                               # abre a interface
```

Para testar sem dados reais, clique em **Importar em lote** e escolha a pasta `exemplos/xmls`. Em **Configurações**, informe o CNPJ fictício `99999999000191` e escolha o modo **"Só arquivo de lote"**.

### Linha de comando

```bash
python GeradorGuias.py processar --xml exemplos/xmls --saida saida --modo lote
python GeradorGuias.py relatorio --de 01/10/2026 --ate 31/10/2026 --saida saida/outubro.xlsx
```

Modos de emissão: `portal` (site, padrão), `ws` (webservice), `ambos` (webservice + lote se falhar), `lote` e `analise`.

### Gerar o .exe

No Windows, rode `build_exe.bat` (PyInstaller). Os executáveis saem em `dist/`.

### Testes

```bash
pip install -r requirements-dev.txt
python tests/gerar_xmls_ficticios.py     # (re)gera os XMLs fictícios em exemplos/xmls
python -m pytest tests -q
```

## Estrutura

```
GeradorGuias.py          entrada (interface gráfica ou linha de comando)
app/
  nfe_parser.py          leitura dos XMLs de NF-e e eventos
  regras.py              regras fiscais (DIFAL, FCP, ST, SUFRAMA) e validações
  prazos.py              dias úteis, vencimentos, multa e juros (SELIC/BCB)
  gnre_ws.py             leiaute GNRE 2.00, validação XSD, cliente do webservice
  portal_navegador.py    automação do site do Portal GNRE (Selenium)
  pdf_guia.py            PDF da guia (3 vias, código de barras I2of5)
  relatorio.py           Excel (openpyxl)
  db.py                  histórico em SQLite
  processador.py         orquestração
  gui.py                 interface Tkinter
xsd/                     schemas oficiais GNRE v2.00
exemplos/xmls/           NF-e FICTÍCIAS para teste
tests/                   gerador de XMLs fictícios e testes
docs/MANUAL.txt          manual do usuário
```

## Configuração e privacidade

- Na primeira execução, o programa cria `config.ini` e a pasta `dados/` ao lado do executável. Os dois estão no `.gitignore` e **não devem ser versionados**, porque contêm CNPJ, e-mail, caminho do certificado e histórico de guias.
- O modelo de configuração está em `config.exemplo.ini`.
- Nunca faça commit de certificados (`.pfx`/`.p12`), de XMLs reais de clientes nem de PDFs ou relatórios gerados.

## Limitações e aviso legal

- O cálculo usa os valores **destacados no XML**. O programa confere e avisa sobre divergências, mas **não substitui a análise fiscal**. Regras específicas de cada UF (prazos, encargos, campos extras, Simples Nacional, protocolos de ST) devem ser validadas pelo setor fiscal ou pela contabilidade.
- Multa e juros são **estimativas**. Vale o valor calculado pelo Portal GNRE ou pela SEFAZ.
- A automação do site depende das telas atuais do portal e pode parar de funcionar se elas mudarem. O programa **não resolve reCAPTCHA**: quando o site pede, o usuário resolve na janela do navegador.
- O webservice exige certificado A1 (e-CNPJ) e CNPJ habilitado no Portal GNRE. Teste antes em **homologação**.
- Projeto independente, sem vínculo com a SEFAZ-PE, com o Portal GNRE ou com qualquer órgão público.
- Os XSDs são os schemas públicos do Manual de Integração GNRE. A referência de implementação foi o projeto [nfephp-org/sped-gnre](https://github.com/nfephp-org/sped-gnre).

## Licença

[MIT](LICENSE)
