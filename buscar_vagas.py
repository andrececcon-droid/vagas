#!/usr/bin/env python3
"""Busca diária de vagas: testa cada fonte de fontes.json e gera docs/index.html."""
import base64, json, os, random, re, sys, time, unicodedata, datetime as dt
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

RAIZ = Path(__file__).parent
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
BRT = dt.timezone(dt.timedelta(hours=-3))
# Cargos que o André procura (edite à vontade). ALTA = cargo-alvo; MEDIA = cargo parecido.
ALTA = ["gerente administrativo financeiro", "gerente administrativo e financeiro", "gerente financeiro",
        "gerente de controladoria", "controller", "controladoria", "coordenador financeiro",
        "coordenador administrativo", "gestor financeiro", "gestor administrativo", "fp&a",
        "planejamento financeiro", "analista financeiro", "analista de planejamento", "tesouraria",
        "administrativo financeiro", "administrativo e financeiro", "financeiro e administrativo",
        "gerente de finanças", "gerente de financas", "coordenador de finanças", "coordenador de financas",
        "head de finanças", "head de financas", "analista de finanças", "analista de financas",
        "superintendente financeiro", "superintendente administrativo"]
MEDIA = ["financeiro", "financeira", "administrativo", "contas a pagar", "contas a receber", "contábil",
         "contabil", "orçamento", "faturamento", "custos", "diretor financeiro", "diretor de finanças",
         "tesoureiro", "business partner financeiro", "finanças", "financas"]
NOTA_MIN = 65   # vaga com nota menor que isso não aparece no painel (fica na Auditoria, com o motivo)
# Cargos fora do nível dele (palavras inteiras)
EXCLUIR = ["estágio", "estagiário", "estagiario", "aprendiz", "trainee", "júnior", "junior", "jr", "jr.",
           "assistente", "auxiliar", "vendedor", "motorista", "segurança", "vigilante", "limpeza", "operador"]
EXCL_RE = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in EXCLUIR) + r")(?!\w)")

# Competências do currículo: procuradas na descrição de cada vaga
COMPETENCIAS = {
    "Fluxo de caixa": r"fluxo de caixa",
    "Conciliação": r"concilia",
    "Contas a pagar e receber": r"contas a (?:pagar|receber)",
    "Orçamento / forecast": r"orçament|budget|forecast",
    "FP&A / planejamento financeiro": r"fp&a|planejamento financeiro|planejamento estratégico",
    "Relatórios gerenciais e indicadores": r"relat[óo]rios gerenciais|informações gerenciais|\bdre\b|indicadores|\bkpis?\b",
    "Custos e margem": r"custos|margem|rentabilidade|precifica",
    "Faturamento e cobrança": r"faturamento|cobran[çc]a|notas fiscais",
    "Contratos e convênios": r"contratos|conv[êe]nios|licita",
    "ERP (Omie, Conta Azul, SAP...)": r"\berp\b|\bsap\b|omie|conta azul|totvs|protheus|bling|linx",
    "Liderança de equipes": r"lideran[çc]a|gest[ãa]o de equipes?|liderar|coordenar equipes?",
    "Fechamento contábil": r"fechamento (?:cont[áa]bil|mensal)|balancete",
    "Excel / BI / dados": r"\bexcel\b|power ?bi|business intelligence|an[áa]lise de dados|\bsql\b|python",
    "Compliance fiscal e trabalhista": r"compliance|obriga[çc][õo]es (?:fiscais|acess[óo]rias)|trabalhista",
    "Tesouraria e crédito": r"tesouraria|capta[çc][ãa]o|negocia[çc][õo]es banc[áa]rias|cr[ée]dito",
    "Fornecedores e compras": r"fornecedores|compras|suprimentos",
    "Gestão de pessoas (folha, admissões)": r"folha de pagamento|admiss[õo]es|demiss[õo]es|departamento pessoal",
    "Impostos e tributação": r"tribut|impostos|icms|pis/?cofins|irpj|csll|\bsped\b",
    "Consolidação / IFRS / BR GAAP": r"consolida[çc][ãa]o|\bifrs\b|br gaap|cpc",
    "Auditoria e controles internos": r"auditoria|controles internos|sox|riscos",
    "BI e dashboards": r"power ?bi|dashboards?|tableau|qlik",
    "Inglês": r"ingl[êe]s|english",
}
COMP_RE = {k: re.compile(v, re.I) for k, v in COMPETENCIAS.items()}
PERFIL = {"cargos": [], "competencias": [], "gerado": ""}   # vem de perfil.json (gerado pelo painel a partir do currículo)


def carregar_perfil():
    """Lê perfil.json (sem dados pessoais): cargos e competências do currículo. Os cargos entram na busca."""
    global PERFIL
    f = RAIZ / "perfil.json"
    if not f.exists():
        return
    try:
        d = json.loads(f.read_text("utf-8"))
        cargos = [sem_acento_min(c) for c in d.get("cargos", []) if isinstance(c, str) and 4 <= len(c) <= 60]
        PERFIL = {"cargos": cargos, "competencias": [c for c in d.get("competencias", []) if c in COMPETENCIAS],
                  "gerado": d.get("gerado", "")}
        for c in cargos:
            if c not in ALTA and CARGO_RE.search(c) and not EXCL_RE.search(c) and not CONTABIL_RE.search(c):
                ALTA.append(c)
    except Exception as e:  # noqa
        print("perfil.json inválido:", str(e)[:80])


def sem_acento_min(s):
    return " ".join(str(s).lower().split())

# Um título só vale como vaga se tiver nome de cargo e não for link de menu ("Solicite um orçamento"...)
# Vagas de contabilidade pura (contador, analista contábil...) ficam fora
CONTABIL_RE = re.compile(r"\b(?:contador(?:a)?|contabilista|"
                         r"(?:analista|coordenador|coordenadora|gerente|supervisor|supervisora|especialista|"
                         r"consultor|consultora|auditor|auditora|tributarista)\s+(?:de\s+|em\s+)?(?:cont[áa]bil|contabilidade)|"
                         r"cont[áa]bil\s*(?:pleno|s[êe]nior|pl|sr)\b|cont[áa]bil\s*/\s*fiscal)", re.I)
# Exigência de ser contador (registro no CRC) na descrição
CRC_RE = re.compile(r"\bCRC\b|conselho regional de contabilidade", re.I)
# Texto do cartão que vem junto com o título em alguns sites ("... Empresa confidencial Presencial CLT ...")
CORTE_TITULO = re.compile(r"\s+(?:Empresa confidencial|Vaga confidencial|Presencial|H[ií]brido|Home ?office|CLT\b|PJ\b|"
                          r"\d+ vagas?|Publicada|Ver vaga|A combinar|R\$ ?\d)", re.I)


def limpar_titulo(t):
    m = CORTE_TITULO.search(t)
    t = t[:m.start()] if m else t
    t = re.sub(r"^NOVA\s+BM VAGAS\s+Unidade\s+\w+\s+", "", t, flags=re.I)
    ach = achar_local_uf(t)
    if ach and ach[0] >= 8:
        t = t[:ach[0]]                       # a cidade que vem dentro do título não faz parte do cargo
    return t.strip(" →-–|:·")

CARGO_RE = re.compile(r"\b(?:analista|assistente|auxiliar|gerente|coordenador|coordenadora|supervisor|supervisora|"
                      r"gestor|gestora|diretor|diretora|controller|controlador|controladora|especialista|consultor|"
                      r"consultora|contador|contadora|tesoureiro|tesoureira|executivo|executiva|l[ií]der|head|"
                      r"business partner|encarregado|encarregada|t[ée]cnico|t[ée]cnica|comprador|compradora|"
                      r"planejador|planejadora|auditor|auditora|or[çc]amentista|faturista|analyst|manager|superintendente)\b", re.I)
NAO_VAGA = re.compile(r"solicit|pe[çc]a um|fa[çc]a (?:seu|um)|fale conosco|seja (?:um )?(?:nosso )?fornecedor|"
                      r"cadastro de fornecedor|[áa]rea do fornecedor|anuncie|cota[çc][ãa]o|portal d[oa]|acesse|segunda via|"
                      r"boleto|mensalidade|[áa]rea d[oa] (?:aluno|cliente)|ouvidoria|transpar[êe]ncia|informa[çc][õo]es", re.I)
DATA_RE = re.compile(r"publicada em \d{1,2} de [a-zç]+\.?|\d{2}/\d{2}/\d{4}|há \d+ (?:minutos?|horas?|dias?|semanas?|mês|meses)|hoje|ontem", re.I)

# Local da vaga
UF_FORA = "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RO|RR|SC|SP|SE|TO"
ESTADOS_FORA = ["minas gerais", "são paulo", "rio de janeiro", "santa catarina", "paraná", "bahia",
                "pernambuco", "ceará", "goiás", "distrito federal", "espírito santo", "mato grosso"]
GRANDE_POA = ["porto alegre", "canoas", "são leopoldo", "novo hamburgo", "gravataí", "cachoeirinha",
              "alvorada", "viamão", "esteio", "sapucaia do sul", "guaíba", "eldorado do sul",
              "campo bom", "sapiranga", "estância velha", "ivoti", "dois irmãos", "nova santa rita",
              "charqueadas", "taquara", "parobé", "glorinha"]
_W = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wáéíóúâêôãõç']+"
_CID = rf"((?:{_W}\s+(?:(?:do|de|da|dos|das)\s+)?){{0,2}}{_W})"
CIDADE_UF = re.compile(_CID + rf"\s*[-–/,]\s*({UF_FORA})\b")
CIDADE_RS = re.compile(_CID + r"\s*[-–/,]\s*RS\b")


NAO_CIDADE = {"pagar", "receber", "financeiro", "financeira", "administrativo", "administrativa", "analista", "gerente",
              "coordenador", "pleno", "senior", "sênior", "junior", "júnior", "contas", "compras", "fiscal", "custos",
              "controller", "vendas", "comercial", "faturamento", "cobrança", "cobranca", "tesouraria", "rh", "ti"}


def achar_local_uf(t):
    """Procura 'Cidade / UF' no texto. Devolve (início da cidade, 'Cidade - UF', 'fora' ou 'rs') ou None."""
    for rx, regiao in ((CIDADE_UF, "fora"), (CIDADE_RS, "rs")):
        for m in rx.finditer(t):
            palavras = m.group(1).split()
            while len(palavras) >= 3 and palavras[0].isupper() and not palavras[1].isupper():
                palavras.pop(0)                          # "RECEBER Belo Horizonte" -> "Belo Horizonte"
            while palavras and palavras[0].lower() in NAO_CIDADE:
                palavras.pop(0)                          # "Financeiro Caxias do Sul" -> "Caxias do Sul"
            if not palavras:
                continue                                 # "Contas a Pagar - AP": sigla do cargo, não é estado
            cidade = " ".join(palavras)
            uf = m.group(2) if regiao == "fora" else "RS"
            try:
                inicio = t.index(cidade, m.start(1))
            except ValueError:
                inicio = m.start(1)
            return inicio, f"{cidade} - {uf}", regiao
    return None


def detectar_local(ctx, titulo=""):
    """Devolve (texto do local, região): poa, rs, remoto, fora ou ? (não informado)."""
    txt = ctx.replace(titulo, " ") if titulo else ctx
    low = txt.lower()
    for c in GRANDE_POA:
        if re.search(r"\b" + re.escape(c) + r"\b", low):
            return c.title().replace(" Do ", " do "), "poa"
    ach = achar_local_uf(txt)
    if ach and ach[2] == "fora":
        return ach[1], "fora"
    for e in ESTADOS_FORA:
        if e in low:
            return e.title(), "fora"
    if ach and ach[2] == "rs":
        return ach[1], "rs"
    if re.search(r"remot|home.?office", (txt + " " + titulo).lower()):
        return "Remoto", "remoto"
    return "", "?"


def nivel(titulo):
    t = titulo.lower()
    if EXCL_RE.search(t) or CONTABIL_RE.search(t) or NAO_VAGA.search(t) or not CARGO_RE.search(t):
        return None
    if any(x in t for x in ALTA):
        return "Alta"
    if any(x in t for x in MEDIA):
        return "Média"
    return None


def baixar(url, browser):
    t0 = time.time()
    if browser:
        pg = browser.new_page(user_agent=UA)
        try:
            r = pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_timeout(3000)
            altura = 0
            for _ in range(4):                       # rola a página: muitos sites só carregam a lista ao rolar
                nova = pg.evaluate("window.scrollTo(0, document.body.scrollHeight); document.body.scrollHeight")
                pg.wait_for_timeout(900)
                if nova == altura:
                    break
                altura = nova
            return (r.status if r else 0), pg.content(), int((time.time() - t0) * 1000)
        finally:
            pg.close()
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    if "charset" not in r.headers.get("content-type", "").lower():
        r.encoding = "utf-8"
    return r.status_code, r.text, int((time.time() - t0) * 1000)


def contexto(a, txt):
    """Texto ao redor da vaga: sobe na página enquanto o bloco tiver só esta vaga."""
    node, melhor = a.parent, txt
    for _ in range(4):
        if node is None:
            break
        cands = sum(1 for x in node.find_all("a", href=True)
                    if nivel(limpar_titulo(" ".join(x.get_text(" ", strip=True).split()))))
        t = node.get_text(" ", strip=True)
        if cands > 1 or len(t) > 300:
            break
        melhor = t
        node = node.parent
    return melhor


LINK_VAGAS = re.compile(r"\b(vagas?|oportunidades|trabalhe conosco|carreiras?|empregos?)\b", re.I)
LINK_VAGAS2 = re.compile(r"\b(?:vagas?|oportunidades|trabalhe conosco)\b", re.I)
LINK_NAO = re.compile(r"anunci|divulg|public|empresa|cadastre sua|contrat|alerta", re.I)
HREF_NAO = re.compile(r"alerta|cadastr|curricul|newsletter|/vaga/|/vagas/\d|/job/|/jobs/\d", re.I)
LINK_EXATO = re.compile(r"(ver |buscar |todas as |nossas |encontre )?(vagas|oportunidades|trabalhe conosco|carreiras?|empregos?)"
                        r"( abertas| dispon[ií]veis)?", re.I)


def link_vagas(html, base):
    """Se o endereço for a página inicial do site, acha o link que leva à lista de vagas."""
    r = links_de_lista(html, base)
    return r[0] if r else None


def links_de_lista(html, base):
    """Todos os links que parecem levar à lista de vagas, do mais provável ao menos provável."""
    soup = BeautifulSoup(html, "html.parser")
    achados = []
    for a in soup.find_all("a", href=True):
        txt = " ".join(a.get_text(" ", strip=True).split())
        href = urljoin(base, a["href"])
        exato = bool(LINK_EXATO.fullmatch(txt))
        if not (3 <= len(txt) <= 40) or LINK_NAO.search(txt) or not (exato or LINK_VAGAS2.search(txt)):
            continue
        if href.startswith(("mailto:", "javascript:", "tel:")) or HREF_NAO.search(href):
            continue
        if not urlparse(href).path.strip("/") or href.split("#")[0] == base.split("#")[0]:
            continue  # raiz do site ou a própria página
        achados.append((0 if exato else 1, len(achados), href))
    vistos, saida = set(), []
    for _, _, h in sorted(achados):
        if h not in vistos:
            vistos.add(h); saida.append(h)
    return saida


GENERICO = re.compile(r"^(?:ver|veja|saiba|conhe[çc]a)\b.{0,20}|^candidat\w+.{0,20}|^detalhes?$|^mais (?:detalhes|informa[çc][õo]es)$|^inscreva.{0,15}$", re.I)


def titulo_do_cartao(a):
    """Quando o link diz só 'Ver vaga', o cargo está num título perto dele: sobe até 4 níveis e devolve (título, bloco)."""
    node = a.parent
    for _ in range(4):
        if node is None:
            break
        for h in node.find_all(["h1", "h2", "h3", "h4", "h5", "strong"]):
            t = " ".join(h.get_text(" ", strip=True).split())
            if 8 <= len(t) <= 140 and CARGO_RE.search(t):
                return t, node
        node = node.parent
    return "", None


def motivo_titulo(t):
    """Por que um título que parece vaga foi descartado (None se nem parece vaga: menu, rodapé...)."""
    low = t.lower()
    if CONTABIL_RE.search(low):
        return "Contabilidade pura (contador / analista contábil)"
    if EXCL_RE.search(low):
        return "Cargo abaixo do seu nível (" + EXCL_RE.search(low).group(0) + ")"
    if NAO_VAGA.search(low):
        return None
    if CARGO_RE.search(low):
        return "Cargo sem relação com finanças/administração"
    return None


def proxima_pagina(html, base):
    """Acha o link 'Próxima' / '>' / '2' da paginação (None se não houver)."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        txt = " ".join(a.get_text(" ", strip=True).split()).lower()
        rel = " ".join(a.get("rel") or []).lower()
        lab = (a.get("aria-label") or "").lower()
        if "next" in rel or re.fullmatch(r"(pr[óo]xima?( p[áa]gina)?|seguinte|next|›|»|>)", txt) or re.search(r"pr[óo]xima|next", lab):
            href = urljoin(base, a["href"])
            if not href.startswith(("javascript:", "mailto:", "#")) and href.split("#")[0] != base.split("#")[0]:
                return href
    return None


def extrair(html, base):
    """Devolve (nº de links, vagas achadas, {'candidatos': n, 'descartes': [...]})."""
    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=True)
    achadas, vistos, descartes, candidatos = [], set(), [], 0
    for a in links:
        bruto = " ".join(a.get_text(" ", strip=True).split())
        bloco = None
        if GENERICO.match(bruto) and len(bruto) <= 40:
            tc, bloco = titulo_do_cartao(a)
            if not tc:
                continue
            bruto = tc
        if not 8 <= len(bruto) <= 260:
            continue
        txt = limpar_titulo(bruto)
        if not 8 <= len(txt) <= 140:
            continue
        n = nivel(txt)
        href = urljoin(base, a["href"])
        if not n:
            mot = motivo_titulo(txt)
            if mot:
                candidatos += 1
                if len(descartes) < 80:
                    descartes.append({"titulo": txt, "motivo": mot, "url": href})
            continue
        candidatos += 1
        if href in vistos or href.startswith(("mailto:", "javascript:", "#")):
            continue
        if not urlparse(href).path.strip("/") or href.split("#")[0].rstrip("/") == base.split("#")[0].rstrip("/"):
            if len(descartes) < 80:
                descartes.append({"titulo": txt, "motivo": "O link leva à página inicial, e não à vaga", "url": href})
            continue
        vistos.add(href)
        ctx = bloco.get_text(" ", strip=True) if bloco is not None else contexto(a, txt)
        m = DATA_RE.search(ctx)
        local, regiao = detectar_local(ctx + " " + bruto, "")
        achadas.append({"titulo": txt, "empresa": "", "url": href, "nivel": n,
                        "data": m.group(0) if m else None, "data_iso": normalizar_data(m.group(0)) if m else None,
                        "local": local, "regiao": regiao})
    return len(links), achadas, {"candidatos": candidatos, "descartes": descartes}


GUPY_APIS = [("https://portal.gupy.io/api/job-search/jobs", "jobName"),  # endereço atual (desde out/2026)
             ("https://employability-portal.gupy.io/api/v1/jobs", "jobName"),
             ("https://portal.api.gupy.io/api/v1/jobs", "name")]


def gupy():
    """Busca no portal público do Gupy. Tenta os endereços conhecidos até um responder."""
    achadas, erros, ok = {}, [], None
    hoje = dt.date.today().isoformat()
    for termo in ["gerente administrativo financeiro", "gerente financeiro", "controller", "coordenador financeiro",
                  "coordenador administrativo", "analista financeiro", "controladoria", "fp&a",
                  "planejamento financeiro", "tesouraria", "administrativo financeiro", "gestor financeiro",
                  "gerente de finanças", "finanças"] + [c for c in PERFIL["cargos"][:6]]:
        dados = []
        for off in range(0, 600, 100):
            pagina = None
            for base, campo in ([ok] if ok else GUPY_APIS):
                try:
                    r = requests.get(base, params={campo: termo, "city": "Porto Alegre",
                                                   "state": "Rio Grande do Sul",
                                                   "limit": 100, "offset": off},
                                     headers={"User-Agent": UA}, timeout=30)
                    if r.status_code == 200:
                        pagina, ok = r.json().get("data", []), (base, campo)
                        break
                    erros.append(f"{base.split('//')[1].split('/')[0]}: HTTP {r.status_code}")
                except Exception as e:  # noqa
                    erros.append(str(e)[:60])
            if not pagina:
                break
            dados += pagina
            if len(pagina) < 100:
                break
        for v in dados:
            url, nome = v.get("jobUrl") or v.get("url"), v.get("name") or v.get("title") or ""
            prazo = (v.get("applicationDeadline") or "")[:10]
            n = nivel(nome)
            if not url or not n or (prazo and prazo < hoje) or "internship" in (v.get("type") or ""):
                continue
            cid, uf = v.get("city") or "", v.get("state") or ""
            if v.get("workplaceType") == "remote":
                loc, reg = "Remoto", "remoto"
            else:
                loc = f"{cid} - {'RS' if 'rio grande do sul' in uf.lower() else uf}".strip(" -")
                reg = "poa" if cid.lower() in GRANDE_POA else (
                    "rs" if "rio grande do sul" in uf.lower() or uf.upper() == "RS" else ("fora" if uf else "?"))
            pub = (v.get("publishedDate") or "")[:10]
            achadas[url] = {"titulo": nome, "empresa": v.get("careerPageName") or v.get("companyName") or "",
                            "url": url, "nivel": n, "data_iso": pub or None,
                            "data": pub[8:10] + "/" + pub[5:7] + "/" + pub[:4] if pub else None,
                            "prazo": prazo or None, "local": loc, "regiao": reg, "verificada": True,
                            "descricao": (v.get("description") or "")[:3000]}
    erro = None if ok else ("; ".join(sorted(set(erros)))[:220] or "sem resposta")
    return len(achadas), list(achadas.values()), erro, (ok[0].split("//")[1] if ok else "")


# ---------- datas, dados estruturados (JobPosting) e utilitários ----------
MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
DATA_DESC = re.compile(r"(?:publicad[ao]|data de publica[çc][ãa]o|postad[ao]|divulgad[ao])\s*(?:em|:)?\s*([0-9a-zç/\. \-]{4,25})", re.I)


def hoje_brt():
    return dt.datetime.now(BRT).date()


def normalizar_data(txt, ref=None):
    """Converte '10/11/2026', '10 de nov.', 'há 3 dias', 'hoje' ou ISO em AAAA-MM-DD (ou None)."""
    if not txt or not isinstance(txt, str):
        return None
    ref = ref or hoje_brt()
    t = txt.lower().strip()
    try:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
        if m:
            return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t)
        if m:
            ano = int(m[3]) + (2000 if int(m[3]) < 100 else 0)
            return dt.date(ano, int(m[2]), int(m[1])).isoformat()
        m = re.search(r"(\d{1,2}) de ([a-zç]+)\.?(?: de (\d{4}))?", t)
        if m and MESES.get(m[2][:3]):
            d = dt.date(int(m[3]) if m[3] else ref.year, MESES[m[2][:3]], int(m[1]))
            if not m[3] and d > ref + dt.timedelta(days=1):
                d = d.replace(year=d.year - 1)
            return d.isoformat()
        if "hoje" in t:
            return ref.isoformat()
        if "ontem" in t:
            return (ref - dt.timedelta(days=1)).isoformat()
        m = re.search(r"h[áa] (\d+) (minutos?|horas?|dias?|semanas?|m[êe]s|meses)", t)
        if m:
            n, u = int(m[1]), m[2]
            dias = n if u.startswith("dia") else n * 7 if u.startswith("sem") else n * 30 if u.startswith("m") and not u.startswith("min") else 0
            return (ref - dt.timedelta(days=dias)).isoformat()
    except (ValueError, OverflowError):
        return None
    return None


def jsonld_vagas(html):
    """Lê o dado estruturado 'JobPosting' (o mesmo que o Google usa) quando a página tem."""
    achadas = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        pilha = []
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                pilha.append(json.loads(s.string or s.get_text() or ""))
            except Exception:  # noqa
                continue
        while pilha:
            d = pilha.pop()
            if isinstance(d, list):
                pilha += d
            elif isinstance(d, dict):
                tipo = d.get("@type")
                if "JobPosting" in (tipo if isinstance(tipo, list) else [tipo]):
                    achadas.append(d)
                for k in ("@graph", "itemListElement", "item"):
                    if d.get(k):
                        pilha.append(d[k])
    except Exception:  # noqa
        pass
    return achadas


def info_estruturada(html):
    for d in jsonld_vagas(html):
        org = d.get("hiringOrganization")
        empresa = org.get("name", "") if isinstance(org, dict) else (org if isinstance(org, str) else "")
        loc = d.get("jobLocation")
        loc = loc[0] if isinstance(loc, list) and loc else loc
        end = (loc or {}).get("address", {}) if isinstance(loc, dict) else {}
        cidade, uf = ("", "") if not isinstance(end, dict) else (end.get("addressLocality", ""), end.get("addressRegion", ""))
        desc = BeautifulSoup(d.get("description") or "", "html.parser").get_text(" ", strip=True)
        return {"titulo": d.get("title") or "", "empresa": empresa or "", "cidade": cidade or "", "uf": uf or "",
                "remoto": str(d.get("jobLocationType", "")).upper() == "TELECOMMUTE",
                "data_iso": normalizar_data(d.get("datePosted")), "validade": normalizar_data(d.get("validThrough")),
                "descricao": desc}
    return None


def aplicar_info(v, info):
    """Completa a vaga com empresa, data, local e descrição vindos do dado estruturado."""
    if not info:
        return
    if info["empresa"] and not v.get("empresa"):
        v["empresa"] = info["empresa"]
    if info["data_iso"] and not v.get("data_iso"):
        v["data_iso"] = info["data_iso"]
    if len(info["descricao"]) > len(v.get("descricao", "")):
        v["descricao"] = info["descricao"][:6000]
    if info["validade"] and info["validade"] < hoje_brt().isoformat():
        v["encerrada"] = True
    if v.get("regiao") in ("?", "", None):
        cid, uf = info["cidade"], info["uf"]
        uf_s = "RS" if "rio grande do sul" in uf.lower() else uf.strip()
        if info["remoto"]:
            v["local"], v["regiao"] = "Remoto", "remoto"
        elif uf_s.upper() == "RS" or (not uf_s and cid.lower() in GRANDE_POA):
            v["local"], v["regiao"] = detectar_local(f"{cid} - RS") if cid else ("RS", "rs")
        elif uf_s:
            v["local"], v["regiao"] = f"{cid} - {uf_s}".strip(" -"), "fora"


def sem_txt(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip()


def chave_dup(v):
    emp = sem_txt(v.get("empresa", ""))
    return f"{sem_txt(v['titulo'])}|{emp}|{sem_txt(v.get('local', ''))}" if emp else ""



def _ler(html, final):
    info = info_estruturada(html)
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    return True, " ".join(soup.get_text(" ", strip=True).split())[:6000], info, final


def verificar(url, browser=None, fb=None):
    """Abre o link da vaga e devolve (abre?, texto, dado estruturado JobPosting, endereço final).
    Se o site bloquear robô (403) ou entregar página vazia, tenta de novo com o navegador (limite fb['max'] por lote)."""
    ruim = False
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        ctype = r.headers.get("content-type", "").lower()
        if r.status_code < 400 and "html" not in ctype:
            return True, "", None, r.url
        if r.status_code < 400:
            if "charset" not in ctype:
                r.encoding = "utf-8"
            res = _ler(r.text, r.url)
            if len(res[1]) >= 200 or res[2]:
                return res
        ruim = True
    except Exception:
        ruim = True
    if ruim and browser and fb is not None and fb["n"] < fb["max"]:
        fb["n"] += 1
        try:
            st, html, _ = baixar(url, browser)
            if 0 < st < 400:
                return _ler(html, url)
        except Exception:
            pass
    return False, "", None, ""


def auditar_link(v, final, info, texto):
    """Confere se o link leva mesmo à página da vaga (e não à página inicial, ao login ou a uma lista)."""
    if not v.get("verificada"):
        v["link_status"] = "nao_abre"
        return
    caminho = urlparse(final or v["url"]).path.strip("/").lower()
    if not caminho:
        v["link_status"] = "geral"                      # o link caiu na página inicial do site
    elif re.search(r"login|signin|entrar|acesso|auth|cadastr", caminho):
        v["link_status"] = "login"
    else:
        palavras = [w for w in sem_txt(v["titulo"]).split() if len(w) > 3][:3]
        corpo = sem_txt((texto or "")[:6000])
        achou_info = bool(info and info.get("titulo") and sem_txt(info["titulo"]).split()[:2] == sem_txt(v["titulo"]).split()[:2])
        v["link_status"] = "ok" if (achou_info or (palavras and all(w in corpo for w in palavras))) else "incerto"


def competencias(texto):
    return [k for k, rx in COMP_RE.items() if rx.search(texto)]


def termo_cargo(t, lista):
    return next((x for x in lista if x in t), "")


def pontuar(v):
    """Nota 0–100 = cargo (40) + competências (35) + local (15) + atualidade (10). Regra fixa, sem 'achismo'."""
    alvo = v.get("cargo_alvo")
    c_cargo = 40 if alvo else 22
    c_comp = min(35, round(35 * len(v.get("comp", [])) / 6)) if v.get("lida") else 12
    if v.get("lida") and PERFIL["competencias"] and v.get("comp"):
        cob = len(set(v["comp"]) & set(PERFIL["competencias"])) / len(v["comp"])
        c_comp = round(35 * (0.5 * min(1, len(v["comp"]) / 6) + 0.5 * cob))
    c_local = {"poa": 15, "rs": 11, "remoto": 10}.get(v.get("regiao"), 9)
    c_data = 4
    if v.get("data_iso"):
        try:
            d = (hoje_brt() - dt.date.fromisoformat(v["data_iso"])).days
            c_data = 10 if d <= 7 else 7 if d <= 30 else 4 if d <= 60 else 0
        except ValueError:
            pass
    v["nota"] = c_cargo + c_comp + c_local + c_data
    v["nota_det"] = {"cargo": c_cargo, "competencias": c_comp, "local": c_local, "atualidade": c_data}
    return v["nota"]


def avaliar(v):
    """Cruza a vaga com as competências do currículo, calcula a nota e explica o porquê."""
    desc = v.get("descricao", "")
    v["comp"] = competencias(v.get("titulo", "") + " " + desc)
    v["lida"] = len(desc) > 300
    if PERFIL["competencias"] and v["comp"]:
        tem = [c for c in v["comp"] if c in PERFIL["competencias"]]
        v["cv"] = {"pede": len(v["comp"]), "tem": tem, "falta": [c for c in v["comp"] if c not in tem]}
    v.setdefault("verificada", None)
    if not v.get("data_iso") and desc:
        m = DATA_DESC.search(desc[:4000])
        if m:
            v["data_iso"] = normalizar_data(m.group(1))
    t = v.get("titulo", "").lower()
    v["cargo_alvo"] = v["nivel"] == "Alta" and not (v["lida"] and not v["comp"])
    nota = pontuar(v)
    v["nivel"] = "Alta" if nota >= 80 else "Média"
    base = termo_cargo(t, ALTA) or termo_cargo(t, MEDIA)
    d = v["nota_det"]
    v["porque"] = (("cargo-alvo" if v["cargo_alvo"] else "cargo parecido") + f" “{base}” ({d['cargo']}/40)"
                   + (f"; descrição cita {len(v['comp'])} competências suas ({d['competencias']}/35)" if v["lida"]
                      else "; só o título foi lido, sem descrição (competências 12/35)")
                   + f"; local {d['local']}/15; atualidade {d['atualidade']}/10")
    v["descartar"] = ""
    if v.get("link_status") == "geral":
        v["descartar"] = "O link leva à página inicial do site, e não à vaga"
    elif v.get("encerrada"):
        v["descartar"] = "Vaga encerrada (prazo de inscrição vencido)"
    elif CRC_RE.search(desc):
        v["descartar"] = "Exige ser contador (registro no CRC)"
    elif nota < NOTA_MIN:
        v["descartar"] = f"Nota {nota} abaixo do mínimo {NOTA_MIN}"


def variantes(url):
    """Endereços alternativos para quando o principal não abre: com ou sem www e http."""
    u = urlparse(url)
    alt = u.netloc[4:] if u.netloc.startswith("www.") else "www." + u.netloc
    vs = [u._replace(netloc=alt).geturl()]
    if u.scheme == "https":
        vs += [u._replace(scheme="http").geturl(), u._replace(scheme="http", netloc=alt).geturl()]
    return vs


def baixar_com_variantes(url, browser):
    try:
        return baixar(url, browser) + (url,)
    except Exception as e0:
        if "Timeout" in str(e0):
            raise
        for alt in variantes(url):
            try:
                return baixar(alt, browser) + (alt,)
            except Exception:  # noqa
                continue
        raise e0


def pausada(f, hist_fontes, hoje):
    """Fonte (não favorita) que falhou 3 rodadas seguidas fica pausada e só é testada de novo após 7 dias."""
    if f.get("grupo") == "Favorita" or f["tipo"] != "pagina":
        return None
    h = hist_fontes.get(f["nome"], {})
    if h.get("falhas", 0) < 3:
        return None
    dias = (hoje - dt.date.fromisoformat(h.get("tentou", hoje.isoformat()))).days
    return h.get("tentou", ""), max(7 - dias, 0) if dias < 7 else None

CAMINHOS = ["vagas", "oportunidades", "trabalhe-conosco", "carreiras", "vagas-de-emprego", "empregos"]


def titulo_pagina(html):
    try:
        t = BeautifulSoup(html, "html.parser").title
        return " ".join(t.get_text(" ", strip=True).split())[:90] if t else ""
    except Exception:  # noqa
        return ""


def amostra_links(html, base, n=6):
    """Primeiros links com texto de cargo que o robô viu na página (para a auditoria)."""
    out = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        t = " ".join(a.get_text(" ", strip=True).split())
        if 8 <= len(t) <= 120 and CARGO_RE.search(t.lower()):
            out.append({"t": t[:80], "u": urljoin(base, a["href"])[:160]})
            if len(out) >= n:
                break
    return out


def explorar(f, browser, r):
    """Abre a fonte e, se for a página inicial, procura a lista de vagas (até 3 links, 2 saltos e caminhos comuns).
    Registra a trilha percorrida em r['trilha'] para a auditoria."""
    trilha, visitadas, achadas = [], set(), []
    estado = {"melhor": None}

    def visitar(url, tipo):
        status, html, ms, usada = baixar_com_variantes(url, browser)
        visitadas.add(usada.split("#")[0])
        n_links, ach, pr = extrair(html, usada)
        trilha.append({"passo": tipo, "url": usada, "status": status, "links": n_links, "candidatos": pr["candidatos"],
                       "aceitos": len(ach), "titulo": titulo_pagina(html), "amostra": amostra_links(html, usada), "ms": ms})
        return status, html, usada, n_links, ach, pr

    status, html, usada, n_links, achadas, pr = visitar(f["url"], "página cadastrada")
    primeiro = {"status": status, "links": n_links}
    base_html, base_url = html, usada
    if 0 < status < 400 and pr["candidatos"] == 0:
        # 1) links "Vagas / Oportunidades / Trabalhe conosco" da própria página, com até 2 saltos
        fila = [(u, 1) for u in links_de_lista(html, usada)[:3]]
        while fila and len(trilha) < 7:
            u, nivel_ = fila.pop(0)
            if u.split("#")[0] in visitadas:
                continue
            try:
                st2, h2, u2, nl2, ach2, pr2 = visitar(u, "seguiu link de vagas")
            except Exception:  # noqa
                continue
            if 0 < st2 < 400 and pr2["candidatos"] > 0:
                status, html, usada, n_links, achadas, pr = st2, h2, u2, nl2, ach2, pr2
                break
            if 0 < st2 < 400 and nivel_ < 2:
                fila += [(x, 2) for x in links_de_lista(h2, u2)[:2]]
    if pr["candidatos"] == 0 and 0 < status < 400 and not urlparse(f["url"]).path.strip("/"):
        # 2) caminhos comuns (/vagas, /oportunidades...), só com requisição simples
        raiz = urlparse(base_url)
        for c in CAMINHOS:
            u = f"{raiz.scheme}://{raiz.netloc}/{c}"
            if u in visitadas:
                continue
            try:
                rr = requests.get(u, headers={"User-Agent": UA}, timeout=10)
                if rr.status_code >= 400 or "html" not in rr.headers.get("content-type", "").lower():
                    continue
                visitadas.add(u)
                nl, ach, p2 = extrair(rr.text, rr.url)
                trilha.append({"passo": "tentou caminho comum", "url": rr.url, "status": rr.status_code, "links": nl,
                               "candidatos": p2["candidatos"], "aceitos": len(ach), "titulo": titulo_pagina(rr.text),
                               "amostra": amostra_links(rr.text, rr.url), "ms": 0})
                if p2["candidatos"] > 0:
                    status, html, usada, n_links, achadas, pr = rr.status_code, rr.text, rr.url, nl, ach, p2
                    break
            except Exception:  # noqa
                continue
    # paginação da lista encontrada
    paginas, vistas_pag = 1, {usada}
    while paginas < 4 and achadas:
        prox = proxima_pagina(html, usada)
        if not prox or prox in vistas_pag:
            break
        try:
            st3, html3, _ = baixar(prox, browser)
        except Exception:  # noqa
            break
        if not 0 < st3 < 400:
            break
        vistas_pag.add(prox)
        n3, ach3, pr3 = extrair(html3, prox)
        trilha.append({"passo": f"página {paginas + 1} da lista", "url": prox, "status": st3, "links": n3,
                       "candidatos": pr3["candidatos"], "aceitos": len(ach3), "titulo": titulo_pagina(html3),
                       "amostra": amostra_links(html3, prox), "ms": 0})
        novos = [x for x in ach3 if x["url"] not in {y["url"] for y in achadas}]
        n_links += n3
        pr["candidatos"] += pr3["candidatos"]
        pr["descartes"] += pr3["descartes"][:20]
        paginas += 1
        if not novos:
            break
        achadas += novos
        html, usada = html3, prox
    chegou = any(t["candidatos"] > 0 for t in trilha)
    r["trilha"] = trilha
    r["prof"] = {"links": sum(t["links"] for t in trilha), "candidatos": sum(t["candidatos"] for t in trilha),
                 "paginas": paginas, "aceitos": len(achadas), "visitadas": len(trilha), "chegou_lista": chegou}
    r["descartes_titulo"] = pr["descartes"][:60]
    r["detalhe"] = f"HTTP {primeiro['status']} · {len(trilha)} página(s) visitada(s) · lista: " + (usada[:70] if chegou else "não encontrada")
    if primeiro["status"] >= 400 or primeiro["status"] == 0:
        r["status"] = "Falhou"
    elif achadas:
        r["status"] = "OK, com vagas compatíveis"
    elif chegou:
        r["status"] = "Carregou, sem vaga compatível hoje"
    elif primeiro["links"] < 5 and len(trilha) == 1:
        r["status"] = "Carregou sem lista de vagas (JavaScript ou login)"
    elif not urlparse(usada).path.strip("/") or len(trilha) > 1:
        r["status"] = "Não achou a lista de vagas (ficou na página inicial)"
    else:
        r["status"] = "Abriu a página, mas sem nenhuma vaga listada"
    return achadas


def processar_fonte(f, browser, hist_fontes=None):
    """Abre uma fonte e devolve (resultado da fonte, vagas candidatas)."""
    r = {"nome": f["nome"], "url": f.get("url", ""), "tipo": f["tipo"], "grupo": f.get("grupo", ""),
         "status": "", "detalhe": ""}
    achadas = []
    pz = pausada(f, hist_fontes or {}, dt.datetime.now(BRT).date())
    if pz and pz[1] is not None:
        r["status"] = "Pausada: falhou 3 rodadas seguidas"
        r["detalhe"] = f"Última tentativa em {pz[0]}. Testa de novo em {pz[1]} dia(s)."
        r["pausada"] = True
        return r, achadas
    try:
        if f["tipo"] == "alerta":
            r["status"] = "Não lida (exige login ou bloqueia robôs)"
            r["detalhe"] = f.get("nota") or "Use alerta por e-mail nesse site"
        elif f["tipo"] == "gupy":
            n, achadas, erro, info = gupy()
            r["status"] = "Falhou" if erro and not n else ("OK, com vagas compatíveis" if n else "Carregou, sem vaga compatível hoje")
            r["detalhe"] = erro or f"{n} vagas via {info}"
        elif not f.get("url"):
            r["status"] = "Sem link cadastrado"
            r["detalhe"] = "Preencha o campo url em fontes.json"
        else:
            achadas = explorar(f, browser, r)
    except Exception as e:  # noqa
        r["status"] = "Falhou"
        r["detalhe"] = str(e).split("\n")[0][:120]
        r.setdefault("trilha", [{"passo": "página cadastrada", "url": f.get("url", ""), "status": 0, "links": 0, "candidatos": 0,
                                 "aceitos": 0, "titulo": "", "amostra": [], "ms": 0, "erro": r["detalhe"]}])
        r.setdefault("prof", {"links": 0, "candidatos": 0, "paginas": 0, "aceitos": 0, "visitadas": 1, "chegou_lista": False})
    for v in achadas:
        v["fonte"], v["grupo"] = f["nome"], f.get("grupo", "")
    return r, achadas


def separar_fora(vagas, fora):
    for u, v in list(vagas.items()):
        if v.get("regiao") == "fora":
            fora.append({"titulo": v["titulo"], "motivo": "Fora do RS: " + v.get("local", ""), "fonte": v["fonte"], "url": u})
            del vagas[u]


def coletar(fontes, indices):
    """Roda um lote de fontes (os índices dados), abre o link de cada vaga e já filtra, pontua e audita."""
    resultado, vagas = [], {}
    hp = RAIZ / "dados" / "historico.json"
    hist_fontes = json.loads(hp.read_text("utf-8")).get("fontes", {}) if hp.exists() else {}
    fora, fb = [], {"n": 0, "max": 40}
    with (sync_playwright() if sync_playwright else nullcontext()) as pw:
        browser = None
        if pw:
            try:
                browser = pw.chromium.launch()
            except Exception as e:  # noqa
                print("Navegador indisponível:", str(e)[:80])
        for i in indices:
            r, achadas = processar_fonte(fontes[i], browser, hist_fontes)
            r["ordem"] = i
            for v in achadas:
                v["ordem"] = i
                vagas.setdefault(v["url"], v)
            resultado.append(r)
        separar_fora(vagas, fora)
        for v in list(vagas.values())[:250]:
            if "verificada" not in v:
                v["verificada"], v["descricao"], info, final = verificar(v["url"], browser, fb)
                aplicar_info(v, info)
                auditar_link(v, final, info, v["descricao"])
        if browser:
            browser.close()
    separar_fora(vagas, fora)          # o dado estruturado pode revelar que a vaga é de fora do RS
    for v in vagas.values():
        if "link_status" not in v:       # Gupy vem da API oficial; o resto acima de 250 não foi testado
            v["link_status"] = "ok" if v.get("verificada") else "nao_testado"
        avaliar(v)
    fora += [{"titulo": v["titulo"], "motivo": v["descartar"], "fonte": v["fonte"], "url": v["url"]}
             for v in vagas.values() if v.get("descartar")]
    vagas = {u: v for u, v in vagas.items() if not v.get("descartar")}
    for r in resultado:
        r["vagas"] = sum(1 for v in vagas.values() if v["fonte"] == r["nome"])
        if r["status"].startswith("OK") and r["vagas"] == 0:
            meus = [x for x in fora if x["fonte"] == r["nome"]]
            if not meus:
                r["status"] = "Carregou, vagas já listadas por outra fonte"
            elif all(x["motivo"].startswith("Fora do RS") for x in meus):
                r["status"] = "Carregou, só vagas fora do RS"
            else:
                r["status"] = "Carregou, vagas descartadas pelos filtros"
    for v in vagas.values():
        v["descricao"] = v.get("descricao", "")[:2500]
    return resultado, list(vagas.values()), fora


def escrever_aviso(novas, total, carimbo):
    """Cria o texto do aviso (o GitHub transforma em e-mail) com as vagas Alta novas; sem novas, apaga."""
    arq, tit = RAIZ / "aviso.md", RAIZ / "aviso-titulo.txt"
    if not novas:
        for a in (arq, tit):
            a.unlink(missing_ok=True)
        return
    repo = os.environ.get("GITHUB_REPOSITORY", "andrececcon-droid/vagas")
    painel = f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/"
    linhas = []
    for i, v in enumerate(novas, 1):
        quem = " · ".join(x for x in (v.get("empresa") or v["fonte"], v.get("local", "")) if x)
        comp = ", ".join(v.get("comp", [])[:4])
        linhas.append(f"{i}. **{v['titulo']}** · {quem}\n   {v.get('porque', '')}" + (f"\n   Suas competências: {comp}" if comp else "")
                      + f"\n   [Abrir a vaga]({v['url']}) · fonte: {v['fonte']}")
    resto = f"\n\n…e mais {total - len(novas)} no painel." if total > len(novas) else ""
    arq.write_text("\n\n".join(linhas) + resto + f"\n\n[Abrir o painel]({painel})\n", "utf-8")
    tit.write_text(f"🔔 {total} vaga(s) nova(s) compatível(is) — {carimbo}", "utf-8")


ICONE_192 = "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAIAAADdvvtQAAADkklEQVR42u3cwXUTQRBFUcxxCiYVlqwJgqUJii1BsLWXpEIUBGAOoBl1T9Wv+5Y+tjTTev1/ydLMw9PzyzvgKO8tAQgEAoFAIBBAIBAIBAKBAAKBQCAQCAQQCAQCgUAggEAgEAgEAoFAAIFAIBAIBAIIBAKBQCAQQCAQCAQCgQACgUAgEAgEAgEEAoFAIBAIIBA28bjzyX59+7TuwT98fe219BmrIYHQR6Cl22LphhY/EggRAgmhsFlQAqGbQO3eLjVi/9qmJVD9Fus17KswJAqkxWJWNTCBKndEWH9dKZAQyljPzBmo5kbPi5+LBRJCASvpXRgI1KEvIvvreoG0WPc1TK6wOps+NX5KCCSEWq+eIRr9BVq3jSp0x7pjqBDeEggEAoFSWyy7vyQQggTKC6H4+JFAyBLIPxXbrdKUBNrfYsEfX6gw5AqUMUpPGJ8lEEIFMko3WplZCbSnxYaMz6UFEkJd1sQMBAJV6pdR/VVaIC3WYjUmVti6kJgWP9UFEkL118EQjWiBFm2+FV2zqL+Kx7AEAoEKBMbA8bmNQJNH6frnrsIwQKDio/TM8VkCYZJA0yahLuc7PYHOt8/Y918EwjCBCo7Sk8dnCYR5Ak0YpXudowQ63kTDx+euAmWHULuzk0AH40T8EAhTBUptsY7nJYGOtJL+ai9QXgg1PSMJhKkCrdiy/9NNK/qrb6BKIBBoYwgZn6MEyhilW5+FBMJsgXaO0sZnCQQCXRRC4kcCgUAgUMcW018SCAQK2tAx3yaQQP9uMR9fTBGoy7ZO+jKTBLphlMZbHi1BOz5++fH2hz+/f77kYB6enl9kRov++qM3l5skgbpGzt9/eZtGgTNQ5RH1wLHdZM/JvyLQ6Oy5xKFMgWqG0K1Hdd6ADQ5JoMDs2ekQgZLt2eBQrEDVWiz1en4JFB4/q0MoWaA6mz74plgSCASqvfVvOoZ1XbPokSUQCAQCpbZY/I2tJRAIVDUGJtxXXwKBQCXD4Ngzrvsi2KJHlkAgEAgU1mJnnmtF16xrRgkEAhULofPPct/AWHqFhgQqyr1e9dXX94wTaHUI3fHxz7/2G64Ok0CxObTn2sLAS5vzOPBVnm1XphIoTaPN18YTKMQkd+dASwzRIBAIBAKBQACBQCAQCAQCCAQCgUAgEEAgEAgEAoEAAoFAIBAIBAIBBAKBQCAQCCAQCAQCgUAAgUAgEAgEAggEAoFAaM9vfbEgtrZOKiYAAAAASUVORK5CYII="
ICONE_512 = "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"


def escrever_app(docs):
    """Arquivos para instalar o painel como aplicativo no celular."""
    docs.mkdir(exist_ok=True)
    (docs / "manifest.webmanifest").write_text(json.dumps({
        "name": "Dashboard de Vagas", "short_name": "Vagas", "start_url": ".", "scope": ".", "display": "standalone",
        "background_color": "#14171a", "theme_color": "#1565c0",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}, ensure_ascii=False), "utf-8")
    (docs / "sw.js").write_text('self.addEventListener("install",function(){self.skipWaiting()});'
                                'self.addEventListener("activate",function(e){e.waitUntil(clients.claim())});'
                                'self.addEventListener("fetch",function(){});\n', "utf-8")
    (docs / "icon-192.png").write_bytes(base64.b64decode(ICONE_192))
    (docs / "icon-512.png").write_bytes(base64.b64decode(ICONE_512))



def _norm(t):
    import unicodedata
    t = unicodedata.normalize("NFD", (t or "").lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def teste_controle(lista, descartes, resultado):
    """Confere vagas reais conhecidas (controle.json) contra o que o robô viu nesta rodada."""
    p = RAIZ / "controle.json"
    if not p.exists():
        return None
    try:
        itens = json.loads(p.read_text("utf-8")).get("vagas", [])
    except Exception:
        return None
    saida = []
    for it in itens:
        emp, cargo = _norm(it["empresa"]).strip(), [w for w in _norm(it["cargo"]).split() if len(w) > 3]
        achou, estado, onde = None, "Não vista", ""
        for v in lista:
            txt = _norm(v.get("titulo", "") + " " + v.get("empresa", ""))
            if emp in txt and sum(w in txt for w in cargo) >= max(1, len(cargo) - 1):
                achou, estado, onde = v, "No painel", v.get("fonte", ""); break
        if not achou:
            for d in descartes:
                txt = _norm(d.get("titulo", "") + " " + d.get("fonte", ""))
                if emp in txt and sum(w in txt for w in cargo) >= 1:
                    estado, onde = "Vista, mas descartada: " + d.get("motivo", ""), d.get("fonte", ""); break
        saida.append({"empresa": it["empresa"], "cargo": it["cargo"], "onde_existe": it.get("onde", ""),
                      "estado": estado, "fonte": onde, "url": achou["url"] if achou else ""})
    vistas = sum(1 for s in saida if s["estado"] != "Não vista")
    lidos = sum(1 for r in resultado if r.get("grupo") == "Agregador" and (r.get("prof") or {}).get("chegou_lista"))
    return {"total": len(saida), "vistas": vistas, "no_painel": sum(1 for s in saida if s["estado"] == "No painel"),
            "agregadores_lidos": lidos, "agregadores_total": sum(1 for r in resultado if r.get("grupo") == "Agregador"),
            "itens": saida, "data": json.loads(p.read_text("utf-8")).get("data", "")}


def montar(parciais, fontes):
    """Junta os lotes, atualiza o histórico e gera o painel (docs/index.html)."""
    agora = dt.datetime.now(BRT)
    carimbo, dia = agora.strftime("%d/%m/%Y %H:%M"), agora.strftime("%Y-%m-%d")
    hist_path = RAIZ / "dados" / "historico.json"
    hist = json.loads(hist_path.read_text("utf-8")) if hist_path.exists() else {"vagas": {}, "execucoes": []}
    primeira_rodada = not hist.get("vagas")

    por_ordem = {r["ordem"]: r for p in parciais for r in p["resultado"]}
    vagas, fora_vistas, fora = {}, set(), []
    for p in sorted(parciais, key=lambda p: p["lote"]):
        for v in p["vagas"]:
            antigo = vagas.get(v["url"])
            if antigo is None or v.get("ordem", 10 ** 9) < antigo.get("ordem", 10 ** 9):
                vagas[v["url"]] = v
        for x in p["fora"]:
            if x["url"] not in fora_vistas and x["url"] not in vagas:
                fora_vistas.add(x["url"]); fora.append(x)

    for u in [u for u in vagas if "jobsora" in u.lower()]:
        del vagas[u]          # Jobsora não é usado (pedido do André)
    # a mesma vaga (mesmo cargo, empresa e local) vinda de várias fontes vira um cartão só
    unicos, grupos = {}, {}
    for url, v in sorted(vagas.items(), key=lambda kv: kv[1].get("ordem", 10 ** 9)):
        k = chave_dup(v)
        if k and k in grupos:
            g = grupos[k]
            g.setdefault("tambem", []).append({"fonte": v["fonte"], "url": url})
            if v.get("data_iso") and not g.get("data_iso"):
                g["data_iso"] = v["data_iso"]
            continue
        unicos[url] = v
        if k:
            grupos[k] = v
    vagas = unicos

    resultado = []
    for i, f in enumerate(fontes):
        r = por_ordem.get(i) or {"nome": f["nome"], "url": f.get("url", ""), "tipo": f["tipo"],
                                 "grupo": f.get("grupo", ""), "status": "Não rodou nesta rodada",
                                 "detalhe": "O lote desta fonte deu erro (veja a aba Actions)", "vagas": 0}
        r["vagas"] = sum(1 for v in vagas.values() if v["fonte"] == r["nome"])
        if r["status"].startswith("OK") and r["vagas"] == 0:
            r["status"] = "Carregou, vagas já listadas por outra fonte"
        resultado.append(r)

    hf = hist.setdefault("fontes", {})
    for r in resultado:
        if r.get("pausada") or r["status"].startswith("Não rodou") or r["tipo"] != "pagina":
            continue
        h = hf.setdefault(r["nome"], {"falhas": 0, "tentou": dia})
        h["tentou"] = dia
        h["falhas"] = h["falhas"] + 1 if r["status"] == "Falhou" else 0
        h["rodadas"] = h.get("rodadas", 0) + 1
        h["vagas_total"] = h.get("vagas_total", 0) + r["vagas"]

    lista, novas_alta = [], []
    for url, v in vagas.items():
        h = hist["vagas"].setdefault(url, {"primeira": dia})
        h.update({"titulo": v["titulo"], "ultima": dia})
        v["nova"] = h["primeira"] == dia
        v["primeira"] = h["primeira"]
        if v["nivel"] == "Alta" and not h.get("avisada"):
            if primeira_rodada:
                h["avisada"] = True       # na 1ª rodada não manda aviso de tudo de uma vez
            else:
                novas_alta.append(v)
        lista.append(v)
    lista.sort(key=lambda v: (v["nivel"] != "Alta", -len(v.get("comp", [])), not v["nova"]))
    for v in novas_alta[:20]:
        hist["vagas"][v["url"]]["avisada"] = True
    escrever_aviso(novas_alta[:20], len(novas_alta), carimbo)

    contar = lambda *pref: sum(1 for r in resultado if r["status"].startswith(pref))
    saude = {"total": len(resultado), "ok": contar("OK"), "sem_vaga": contar("Carregou, "),
             "sem_leitura": contar("Carregou sem lista", "Não achou", "Abriu a página"), "falhou": contar("Falhou"), "pausadas": contar("Pausada"),
             "nao_lidas": contar("Não lida", "Não rodou", "Sem link")}
    limpeza = [{"nome": r["nome"], "url": r["url"], "grupo": r["grupo"], "rodadas": hf[r["nome"]]["rodadas"]}
               for r in resultado if r["tipo"] == "pagina" and r.get("grupo") != "Favorita" and r["nome"] in hf
               and hf[r["nome"]].get("rodadas", 0) >= 15 and hf[r["nome"]].get("vagas_total", 0) == 0
               and not r["status"].startswith(("Pausada", "Falhou"))]

    aud = {}
    for v in lista:
        a = aud.setdefault(v["fonte"], {"fonte": v["fonte"], "total": 0, "ok": 0, "incerto": 0, "login": 0, "nao_abre": 0,
                                        "nao_testado": 0, "geral": 0, "exemplos": []})
        a["total"] += 1
        a[v.get("link_status", "nao_testado")] = a.get(v.get("link_status", "nao_testado"), 0) + 1
        if v.get("link_status") in ("incerto", "login", "nao_abre") and len(a["exemplos"]) < 3:
            a["exemplos"].append({"titulo": v["titulo"], "url": v["url"], "status": v["link_status"]})
    for x in fora:
        if x["motivo"].startswith("O link leva"):
            a = aud.setdefault(x["fonte"], {"fonte": x["fonte"], "total": 0, "ok": 0, "incerto": 0, "login": 0, "nao_abre": 0,
                                            "nao_testado": 0, "geral": 0, "exemplos": []})
            a["geral"] += 1
            a["total"] += 1
            if len(a["exemplos"]) < 3:
                a["exemplos"].append({"titulo": x["titulo"], "url": x["url"], "status": "geral"})
    auditoria = sorted((a for a in aud.values() if a["total"] - a["ok"] - a["nao_testado"] > 0),
                       key=lambda a: -(a["total"] - a["ok"] - a["nao_testado"]))

    hist["execucoes"] = (hist["execucoes"] + [{"quando": carimbo, "vagas": len(lista),
                         "fontes_ok": sum(1 for r in resultado if r["status"].startswith(("OK", "Carregou, sem")))}])[-60:]
    hist_path.parent.mkdir(exist_ok=True)
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=1), "utf-8")

    # ---- auditoria geral: o que a busca REALMENTE fez
    desc_todos = [dict(x, fonte=r["nome"]) for r in resultado for x in r.get("descartes_titulo", [])]
    todos_desc, _vd = [], set()
    for x in [dict(x) for x in fora] + desc_todos:
        k = (x["titulo"], x["motivo"])
        if k not in _vd:
            _vd.add(k); todos_desc.append(x)
    n = len(lista)
    cont = lambda k: sum(1 for v in lista if v.get("link_status") == k)
    qual = {"vagas": n, "lidas": sum(1 for v in lista if v.get("lida")), "link_ok": cont("ok"), "link_incerto": cont("incerto"),
            "link_login": cont("login"), "link_nao_abre": cont("nao_abre"), "link_nao_testado": cont("nao_testado"),
            "geral_descartadas": sum(1 for x in fora if x["motivo"].startswith("O link leva")),
            "abertas": sum(1 for v in lista if v.get("verificada"))}
    trilhas = [{"nome": r["nome"], "grupo": r["grupo"], "status": r["status"], "cadastrada": r.get("url", ""), "vagas": r["vagas"],
                "trilha": [dict(t, amostra=t["amostra"][:4]) for t in r["trilha"][:6]]} for r in resultado if r.get("trilha")]
    prof = [{"nome": r["nome"], "grupo": r["grupo"], "status": r["status"], "vagas": r["vagas"], **r["prof"]}
            for r in resultado if r.get("prof")]
    funil = {"fontes": len(resultado), "lidas": sum(1 for r in resultado if r.get("prof")),
             "links": sum(p["links"] for p in prof), "candidatos": sum(p["candidatos"] for p in prof),
             "aceitos": sum(p["aceitos"] for p in prof), "paginas": sum(p["paginas"] for p in prof),
             "chegou_lista": sum(1 for p in prof if p.get("chegou_lista")),
             "fora_rs": sum(1 for x in fora if x["motivo"].startswith("Fora do RS")),
             "outros_descartes": sum(1 for x in fora if not x["motivo"].startswith("Fora do RS")),
             "no_painel": n}
    suspeitas = []
    for r in resultado:
        if r.get("grupo") == "Favorita" and r["status"].startswith(("Falhou", "Carregou sem lista", "Pausada")):
            suspeitas.append(f"Favorita “{r['nome']}”: {r['status']} — não dá para dizer que não há vaga lá")
        p = r.get("prof")
        if p and not p.get("chegou_lista") and r["status"].startswith(("Não achou", "Abriu a página")) and r.get("grupo") == "Favorita":
            suspeitas.append(f"Favorita “{r['nome']}”: o robô não chegou à lista de vagas ({len(r['trilha'])} página(s) visitada(s)) — cadastre o link direto da lista em fontes.json")
        if p and p["links"] >= 40 and p["candidatos"] == 0 and p.get("chegou_lista") is not False:
            suspeitas.append(f"“{r['nome']}”: {p['links']} links lidos, nenhum parece cargo — confira se a página lista vagas")
    if qual["link_incerto"]:
        suspeitas.append(f"{qual['link_incerto']} vaga(s) com link “incerto” (abriu, mas não confirmou o cargo na página)")
    amostra = random.sample(lista, min(8, len(lista)))
    audit_geral = {
        "qualidade": qual, "funil": funil, "trilhas": trilhas, "profundidade": sorted(prof, key=lambda x: -x["candidatos"])[:80],
        "descartes": todos_desc[:500], "total_descartes": len(todos_desc), "suspeitas": suspeitas[:30],
        "amostra": [{"titulo": v["titulo"], "empresa": v.get("empresa", ""), "fonte": v["fonte"], "url": v["url"],
                     "link_status": v.get("link_status"), "lida": v.get("lida"), "nota": v.get("nota"),
                     "aberta": bool(v.get("verificada"))} for v in amostra],
        "controle": teste_controle(lista, todos_desc, resultado),
        "regras": {"nota_min": NOTA_MIN, "nota_alta": 80, "excluidos": EXCLUIR, "contabil": "contador, analista/coordenador contábil, exige CRC",
                   "local": "só Rio Grande do Sul (e remoto)", "paginas_max": 4, "rolagens_max": 4,
                   "navegador_reserva_max": 40, "abre_link_max": 250}}

    dados = {"gerado": carimbo, "vagas": lista, "fontes": resultado, "execucoes": hist["execucoes"][-14:],
             "fora": fora[:60], "saude": saude, "limpeza": limpeza, "auditoria_links": auditoria, "auditoria": audit_geral,
             "competencias": COMPETENCIAS, "perfil": PERFIL if PERFIL["competencias"] or PERFIL["cargos"] else None}
    html = (RAIZ / "modelo.html").read_text("utf-8").replace(
        "__DADOS__", json.dumps(dados, ensure_ascii=False).replace("</", "<\\/"))
    (RAIZ / "docs").mkdir(exist_ok=True)
    (RAIZ / "docs" / "index.html").write_text(html, "utf-8")
    escrever_app(RAIZ / "docs")
    print(f"{len(lista)} vagas ({len(novas_alta)} Alta novas) · {saude['ok']} fontes com vagas · "
          f"{len(parciais)} lotes · {carimbo}")


def opcao(args, nome, padrao=None):
    return args[args.index(nome) + 1] if nome in args else padrao


def main():
    """Modos: plano | coletar | montar (usados pelo GitHub) ou, sem modo, tudo de uma vez."""
    args = sys.argv[1:]
    modo = args[0] if args and args[0] in ("plano", "coletar", "montar") else "tudo"
    carregar_perfil()
    caminho = opcao(args, "--fontes") or (args[0] if modo == "tudo" and args else str(RAIZ / "fontes.json"))
    fontes = json.loads(Path(caminho).read_text("utf-8"))
    if modo == "plano":
        lotes = max(1, -(-len(fontes) // int(opcao(args, "--tamanho", "30"))))
        print("lotes=" + json.dumps(list(range(lotes))))
        print(f"total={lotes}")
    elif modo == "coletar":
        lote, total = int(opcao(args, "--lote")), int(opcao(args, "--total"))
        res, vagas, fora = coletar(fontes, [i for i in range(len(fontes)) if i % total == lote])
        saida = opcao(args, "--saida", f"parcial-{lote}.json")
        Path(saida).write_text(json.dumps({"lote": lote, "resultado": res, "vagas": vagas, "fora": fora},
                                          ensure_ascii=False), "utf-8")
        print(f"lote {lote}/{total}: {len(res)} fontes · {len(vagas)} vagas")
    elif modo == "montar":
        pasta = Path(opcao(args, "--entrada", "parciais"))
        montar([json.loads(x.read_text("utf-8")) for x in sorted(pasta.glob("parcial-*.json"))], fontes)
    else:
        res, vagas, fora = coletar(fontes, list(range(len(fontes))))
        montar([{"lote": 0, "resultado": res, "vagas": vagas, "fora": fora}], fontes)


if __name__ == "__main__":
    main()
