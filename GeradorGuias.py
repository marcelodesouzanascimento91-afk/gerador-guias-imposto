"""Gerador de Guias de Impostos (GNRE: DIFAL, FCP, ICMS-ST) a partir de XMLs de NF-e.

Uso:
  GeradorGuias.exe                       -> abre a interface gráfica
  GeradorGuias.exe processar --xml PASTA_OU_ARQUIVOS... --saida PASTA [--pdf PASTA_PDFS]
                   [--modo portal|lote|ws|ambos|analise] [--pagamento dd/mm/aaaa]
  GeradorGuias.exe relatorio --de dd/mm/aaaa --ate dd/mm/aaaa --saida ARQUIVO.xlsx [--campo vencimento|data_emissao|gerado_em]
"""
import argparse
import sys
from datetime import date, datetime


def _data(s):
    return datetime.strptime(s, "%d/%m/%Y").date()


def cli(argv):
    from app.config import Config, DADOS_DIR
    p = argparse.ArgumentParser(prog="GeradorGuias")
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("processar", help="Lê os XMLs e gera guias, PDFs e relatório")
    a.add_argument("--xml", required=True, nargs="+", help="Pasta(s) e/ou arquivo(s) XML")
    a.add_argument("--pdf", default=None, help="Pasta onde salvar os PDFs das guias")
    a.add_argument("--saida", required=True)
    a.add_argument("--modo", default=None, choices=["lote", "ws", "ambos", "portal", "analise"])
    a.add_argument("--pagamento", type=_data, default=date.today())
    a.add_argument("--senha", default="")
    a.add_argument("--reemitir", action="store_true")
    r = sub.add_parser("relatorio", help="Exporta o histórico filtrado por data")
    r.add_argument("--de", type=_data, required=True)
    r.add_argument("--ate", type=_data, required=True)
    r.add_argument("--saida", required=True)
    r.add_argument("--campo", default="vencimento", choices=["vencimento", "data_emissao", "gerado_em", "data_pagamento"])
    r.add_argument("--tipo", default="")
    r.add_argument("--uf", default="")
    args = p.parse_args(argv)
    cfg = Config()
    if args.cmd == "processar":
        from app.processador import processar
        modo = args.modo or cfg.get("geral", "modo", "lote")
        def perguntar(titulo, msg):
            try:
                return input(f"{titulo} - {msg} ")
            except EOFError:
                return None
        processar(cfg, args.xml, args.saida, modo, args.pagamento, args.senha, print, args.reemitir,
                  pasta_pdf=args.pdf, perguntar=perguntar)
    elif args.cmd == "relatorio":
        from app.db import Banco
        from app.relatorio import exportar
        rows = Banco(DADOS_DIR).consultar(args.campo, args.de, args.ate, args.tipo, args.uf.upper())
        exportar(rows, args.saida, "Relatório de Guias de Impostos – Consulta por período", None,
                 f"{args.campo} de {args.de:%d/%m/%Y} até {args.ate:%d/%m/%Y}")
        print(f"{len(rows)} registro(s) exportado(s) para {args.saida}")
    else:
        p.print_help()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli(sys.argv[1:])
    else:
        from app.gui import main
        main()
