"""Gera XMLs de NF-e 100% FICTÍCIOS (CNPJs/CPFs/IEs inventados, base 999...) para testar o programa.

Uso: python tests/gerar_xmls_ficticios.py [pasta_destino]
"""
import os, random, sys
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "exemplos", "xmls")
os.makedirs(out, exist_ok=True)
def dv_cnpj(b):
    def c(base, p):
        s=sum(int(a)*b for a,b in zip(base,p))%11; return "0" if s<2 else str(11-s)
    p1=[5,4,3,2,9,8,7,6,5,4,3,2]; d1=c(b,p1); d2=c(b+d1,[6]+p1); return b+d1+d2
def dv_cpf(b):
    d1=(sum(int(x)*w for x,w in zip(b,range(10,1,-1)))*10)%11%10
    d2=(sum(int(x)*w for x,w in zip(b+str(d1),range(11,1,-1)))*10)%11%10
    return b+str(d1)+str(d2)
def dv_chave(c43):
    p,s=2,0
    for d in reversed(c43):
        s+=int(d)*p; p=2 if p==9 else p+1
    dv=11-s%11; return c43+str(0 if dv>=10 else dv)
EMIT=dv_cnpj("999999990001"); OUTRO=dv_cnpj("999999980001")
CUF={"SP":"35","RJ":"33","MG":"31","BA":"29","AM":"13","PR":"41"}
def nfe(n, data, dest, itens, emit=EMIT, iest="", cstat="100", ind_final="1", ind_ie="9", inf=""):
    chave=dv_chave(f"35{data[2:4]}{data[5:7]}{emit}55001{n:09d}1{n:08d}")
    dets=""; tot={"vProd":0,"vICMS":0,"vST":0,"vFCPST":0,"vICMSUFDest":0,"vFCPUFDest":0,"vICMSDeson":0}
    for i,it in enumerate(itens,1):
        tot["vProd"]+=it["v"]
        icms=it.get("icms", f"<ICMS00><orig>0</orig><CST>00</CST><modBC>3</modBC><vBC>{it['v']:.2f}</vBC><pICMS>{it.get('pi',12):.2f}</pICMS><vICMS>{it['v']*it.get('pi',12)/100:.2f}</vICMS></ICMS00>")
        if "vICMSST" in it:
            st=it["vICMSST"]; fs=it.get("vFCPST",0); tot["vST"]+=st; tot["vFCPST"]+=fs
            icms=f"<ICMS10><orig>0</orig><CST>10</CST><modBC>3</modBC><vBC>{it['v']:.2f}</vBC><pICMS>12.00</pICMS><vICMS>{it['v']*.12:.2f}</vICMS><modBCST>4</modBCST><pMVAST>40.00</pMVAST><vBCST>{it['v']*1.4:.2f}</vBCST><pICMSST>18.00</pICMSST><vICMSST>{st:.2f}</vICMSST><vBCFCPST>{it['v']*1.4:.2f}</vBCFCPST><pFCPST>2.00</pFCPST><vFCPST>{fs:.2f}</vFCPST></ICMS10>"
        if "deson" in it:
            tot["vICMSDeson"]+=it["deson"]
            icms=f"<ICMS40><orig>0</orig><CST>40</CST><vICMSDeson>{it['deson']:.2f}</vICMSDeson><motDesICMS>7</motDesICMS></ICMS40>"
        tot["vICMS"]+=it["v"]*it.get("pi",12)/100 if "deson" not in it else 0
        ufd=""
        if "dest_aliq" in it:
            v=it["v"]; pd,pi,pf=it["dest_aliq"],it.get("pi",12),it.get("pfcp",0)
            d=round(v*(pd-pi)/100,2); f=round(v*pf/100,2)
            d=it.get("difal_forcado",d)
            tot["vICMSUFDest"]+=d; tot["vFCPUFDest"]+=f
            ufd=f"<ICMSUFDest><vBCUFDest>{v:.2f}</vBCUFDest><vBCFCPUFDest>{v:.2f}</vBCFCPUFDest><pFCPUFDest>{pf:.4f}</pFCPUFDest><pICMSUFDest>{pd:.4f}</pICMSUFDest><pICMSInter>{pi:.2f}</pICMSInter><pICMSInterPart>100.0000</pICMSInterPart><vFCPUFDest>{f:.2f}</vFCPUFDest><vICMSUFDest>{d:.2f}</vICMSUFDest><vICMSUFRemet>0.00</vICMSUFRemet></ICMSUFDest>"
        dets+=f"""<det nItem="{i}"><prod><cProd>P{i:03d}</cProd><cEAN>SEM GTIN</cEAN><xProd>PRODUTO FICTICIO {i}</xProd><NCM>39269090</NCM><CFOP>{it.get('cfop','6108')}</CFOP><uCom>UN</uCom><qCom>10.0000</qCom><vUnCom>{it['v']/10:.4f}</vUnCom><vProd>{it['v']:.2f}</vProd><cEANTrib>SEM GTIN</cEANTrib><uTrib>UN</uTrib><qTrib>10.0000</qTrib><vUnTrib>{it['v']/10:.4f}</vUnTrib><indTot>1</indTot></prod><imposto><ICMS>{icms}</ICMS>{ufd}</imposto></det>"""
    vNF=tot["vProd"]+tot["vST"]+tot["vFCPST"]
    doc=f"<CPF>{dest['doc']}</CPF>" if len(dest['doc'])==11 else f"<CNPJ>{dest['doc']}</CNPJ>"
    ie=f"<IE>{dest['ie']}</IE>" if dest.get('ie') else ""
    isuf=f"<ISUF>{dest['isuf']}</ISUF>" if dest.get('isuf') else ""
    xml=f"""<?xml version="1.0" encoding="UTF-8"?><nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe><infNFe Id="NFe{chave}" versao="4.00"><ide><cUF>35</cUF><cNF>{n:08d}</cNF><natOp>VENDA DE MERCADORIA</natOp><mod>55</mod><serie>1</serie><nNF>{n}</nNF><dhEmi>{data}T10:00:00-03:00</dhEmi><dhSaiEnt>{data}T11:00:00-03:00</dhSaiEnt><tpNF>1</tpNF><idDest>{'1' if dest['uf']=='SP' else '2'}</idDest><cMunFG>3550308</cMunFG><tpImp>1</tpImp><tpEmis>1</tpEmis><cDV>{chave[-1]}</cDV><tpAmb>1</tpAmb><finNFe>1</finNFe><indFinal>{ind_final}</indFinal><indPres>2</indPres><procEmi>0</procEmi><verProc>1.0</verProc></ide><emit><CNPJ>{emit}</CNPJ><xNome>EMPRESA FICTICIA COMERCIO LTDA</xNome><enderEmit><xLgr>RUA EXEMPLO</xLgr><nro>1000</nro><xBairro>BAIRRO TESTE</xBairro><cMun>3550308</cMun><xMun>SAO PAULO</xMun><UF>SP</UF><CEP>01000000</CEP><cPais>1058</cPais><xPais>BRASIL</xPais><fone>1100000000</fone></enderEmit><IE>999999999999</IE>{f'<IEST>{iest}</IEST>' if iest else ''}<CRT>3</CRT></emit><dest>{doc}<xNome>{dest['nome']}</xNome><enderDest><xLgr>RUA FICTICIA</xLgr><nro>10</nro><xBairro>CENTRO</xBairro><cMun>{dest['cmun']}</cMun><xMun>{dest['xmun']}</xMun><UF>{dest['uf']}</UF><CEP>00000000</CEP><cPais>1058</cPais></enderDest><indIEDest>{ind_ie}</indIEDest>{ie}{isuf}</dest>{dets}<total><ICMSTot><vBC>0.00</vBC><vICMS>{tot['vICMS']:.2f}</vICMS><vICMSDeson>{tot['vICMSDeson']:.2f}</vICMSDeson><vFCPUFDest>{tot['vFCPUFDest']:.2f}</vFCPUFDest><vICMSUFDest>{tot['vICMSUFDest']:.2f}</vICMSUFDest><vICMSUFRemet>0.00</vICMSUFRemet><vFCP>0.00</vFCP><vBCST>0.00</vBCST><vST>{tot['vST']:.2f}</vST><vFCPST>{tot['vFCPST']:.2f}</vFCPST><vFCPSTRet>0.00</vFCPSTRet><vProd>{tot['vProd']:.2f}</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc><vII>0.00</vII><vIPI>0.00</vIPI><vIPIDevol>0.00</vIPIDevol><vPIS>0.00</vPIS><vCOFINS>0.00</vCOFINS><vOutro>0.00</vOutro><vNF>{vNF:.2f}</vNF></ICMSTot></total><transp><modFrete>0</modFrete></transp><infAdic><infCpl>{inf}</infCpl></infAdic></infNFe></NFe><protNFe versao="4.00"><infProt><tpAmb>1</tpAmb><verAplic>SP_NFE</verAplic><chNFe>{chave}</chNFe><dhRecbto>{data}T10:01:00-03:00</dhRecbto><nProt>999{n:012d}</nProt><cStat>{cstat}</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe></nfeProc>"""
    open(os.path.join(out, f"{chave}-procNFe.xml"),"w",encoding="utf-8").write(xml)
    return chave
RJ=dict(doc=dv_cpf("999000001"),nome="CLIENTE FICTICIO PESSOA FISICA A",uf="RJ",cmun="3304557",xmun="RIO DE JANEIRO")
nfe(1001,"2026-09-22",RJ,[dict(v=8500,pi=12,dest_aliq=20,pfcp=2),dict(v=1500,pi=12,dest_aliq=20,pfcp=2)])
MG=dict(doc=dv_cnpj("999999970001"),nome="CLIENTE FICTICIO CONTRIBUINTE MG LTDA",uf="MG",cmun="3106200",xmun="BELO HORIZONTE",ie="9999999999999")
nfe(1002,"2026-10-08",MG,[dict(v=20000,vICMSST=2640,vFCPST=560)],ind_final="0",ind_ie="1")
BA=dict(doc=dv_cpf("999000002"),nome="CLIENTE FICTICIO PESSOA FISICA B",uf="BA",cmun="2927408",xmun="SALVADOR")
nfe(1003,"2026-10-05",BA,[dict(v=12000,pi=7,dest_aliq=20.5,pfcp=2)],iest="999999999")
nfe(1004,"2026-10-06",BA,[dict(v=5000,pi=7,dest_aliq=20.5,pfcp=2)],iest="999999999")
AM=dict(doc=dv_cnpj("999999960001"),nome="CLIENTE FICTICIO ZONA FRANCA LTDA",uf="AM",cmun="1302603",xmun="MANAUS",ie="999999999",isuf="999999999")
nfe(1005,"2026-10-07",AM,[dict(v=30000,deson=2100,cfop='6109')],ind_final="0",ind_ie="1")
SPd=dict(doc=dv_cnpj("999999950001"),nome="CLIENTE FICTICIO INTERNO SP LTDA",uf="SP",cmun="3509502",xmun="CAMPINAS",ie="999999999998")
nfe(1006,"2026-10-07",SPd,[dict(v=10000,vICMSST=1200,cfop='5401')],ind_final="0",ind_ie="1")
PR=dict(doc=dv_cpf("999000003"),nome="CLIENTE FICTICIO PESSOA FISICA C",uf="PR",cmun="4106902",xmun="CURITIBA")
nfe(1007,"2026-10-01",PR,[dict(v=4000,pi=12)])  # sem ICMSUFDest -> erro
c=nfe(1008,"2026-10-02",RJ,[dict(v=3000,pi=12,dest_aliq=20,pfcp=2)])
open(os.path.join(out,"evento_canc_1008.xml"),"w").write(f'<?xml version="1.0"?><procEventoNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00"><evento versao="1.00"><infEvento Id="ID110111{c}01"><cOrgao>35</cOrgao><tpAmb>1</tpAmb><CNPJ>{EMIT}</CNPJ><chNFe>{c}</chNFe><dhEvento>2026-10-02T12:00:00-03:00</dhEvento><tpEvento>110111</tpEvento><nSeqEvento>1</nSeqEvento><verEvento>1.00</verEvento><detEvento versao="1.00"><descEvento>Cancelamento</descEvento><nProt>1</nProt><xJust>erro de digitacao no pedido</xJust></detEvento></infEvento></evento><retEvento versao="1.00"><infEvento><tpAmb>1</tpAmb><cStat>135</cStat><chNFe>{c}</chNFe></infEvento></retEvento></procEventoNFe>')
nfe(1009,"2026-10-03",RJ,[dict(v=2000,pi=12,dest_aliq=20,pfcp=2)],emit=OUTRO)  # outro emitente
nfe(1010,"2026-10-08",RJ,[dict(v=6000,pi=12,dest_aliq=20,pfcp=2,difal_forcado=400.00)])  # DIFAL divergente
open(os.path.join(out,"nao_e_nfe.xml"),"w").write("<?xml version='1.0'?><outro/>")
print("XMLs fictícios gerados em", os.path.abspath(out), "| CNPJ emitente fictício:", EMIT)
