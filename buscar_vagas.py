#!/usr/bin/env python3
"""Busca diária de vagas: testa cada fonte de fontes.json e gera docs/index.html."""
import json, re, sys, time, datetime as dt
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
        "administrativo financeiro", "administrativo e financeiro", "financeiro e administrativo"]
MEDIA = ["financeiro", "financeira", "administrativo", "contas a pagar", "contas a receber", "contábil",
         "contabil", "orçamento", "faturamento", "custos", "diretor financeiro"]
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
}
COMP_RE = {k: re.compile(v, re.I) for k, v in COMPETENCIAS.items()}

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
                          r"\d+ vagas?|Publicada|Ver vaga)", re.I)


def limpar_titulo(t):
    m = CORTE_TITULO.search(t)
    return (t[:m.start()] if m else t).strip(" →-–|:·")

CARGO_RE = re.compile(r"\b(?:analista|assistente|auxiliar|gerente|coordenador|coordenadora|supervisor|supervisora|"
                      r"gestor|gestora|diretor|diretora|controller|controlador|controladora|especialista|consultor|"
                      r"consultora|contador|contadora|tesoureiro|tesoureira|executivo|executiva|l[ií]der|head|"
                      r"business partner|encarregado|encarregada|t[ée]cnico|t[ée]cnica|comprador|compradora|"
                      r"planejador|planejadora|auditor|auditora|or[çc]amentista|faturista|analyst|manager)\b", re.I)
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


def detectar_local(ctx, titulo=""):
    """Devolve (texto do local, região): poa, rs, remoto, fora ou ? (não informado)."""
    txt = ctx.replace(titulo, " ") if titulo else ctx
    low = txt.lower()
    for c in GRANDE_POA:
        if re.search(r"\b" + re.escape(c) + r"\b", low):
            return c.title().replace(" Do ", " do "), "poa"
    m = CIDADE_UF.search(txt)
    if m:
        return f"{re.sub(r'^(?:Pleno|S[êe]nior|J[úu]nior)\s+', '', m.group(1))} - {m.group(2)}", "fora"
    for e in ESTADOS_FORA:
        if e in low:
            return e.title(), "fora"
    m = CIDADE_RS.search(txt)
    if m:
        return f"{m.group(1)} - RS", "rs"
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
            pg.wait_for_timeout(3500)
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
    return min(achados)[2] if achados else None


def extrair(html, base):
    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=True)
    achadas, vistos = [], set()
    for a in links:
        bruto = " ".join(a.get_text(" ", strip=True).split())
        if not 8 <= len(bruto) <= 260:
            continue
        txt = limpar_titulo(bruto)
        if not 8 <= len(txt) <= 140:
            continue
        n = nivel(txt)
        href = urljoin(base, a["href"])
        if not n or href in vistos or href.startswith(("mailto:", "javascript:", "#")):
            continue
        vistos.add(href)
        ctx = contexto(a, txt)
        m = DATA_RE.search(ctx)
        local, regiao = detectar_local(ctx + " " + bruto, txt)
        achadas.append({"titulo": txt, "empresa": "", "url": href, "nivel": n,
                        "data": m.group(0) if m else None, "local": local, "regiao": regiao})
    return len(links), achadas


GUPY_APIS = [("https://portal.gupy.io/api/job-search/jobs", "jobName"),  # endereço atual (desde out/2026)
             ("https://employability-portal.gupy.io/api/v1/jobs", "jobName"),
             ("https://portal.api.gupy.io/api/v1/jobs", "name")]


def gupy():
    """Busca no portal público do Gupy. Tenta os endereços conhecidos até um responder."""
    achadas, erros, ok = {}, [], None
    hoje = dt.date.today().isoformat()
    for termo in ["gerente administrativo financeiro", "gerente financeiro", "controller", "coordenador financeiro",
                  "coordenador administrativo", "analista financeiro", "controladoria", "fp&a",
                  "planejamento financeiro", "tesouraria", "administrativo financeiro", "gestor financeiro"]:
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
                            "url": url, "nivel": n,
                            "data": pub[8:10] + "/" + pub[5:7] + "/" + pub[:4] if pub else None,
                            "prazo": prazo or None, "local": loc, "regiao": reg, "verificada": True,
                            "descricao": (v.get("description") or "")[:3000]}
    erro = None if ok else ("; ".join(sorted(set(erros)))[:220] or "sem resposta")
    return len(achadas), list(achadas.values()), erro, (ok[0].split("//")[1] if ok else "")


def verificar(url):
    """Testa se o link abre e devolve também o texto da página (descrição da vaga)."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        ctype = r.headers.get("content-type", "").lower()
        if r.status_code >= 400:
            return False, ""
        if "html" not in ctype:
            return True, ""
        if "charset" not in ctype:
            r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        return True, " ".join(soup.get_text(" ", strip=True).split())[:6000]
    except Exception:
        return False, ""


def competencias(texto):
    return [k for k, rx in COMP_RE.items() if rx.search(texto)]


def avaliar(v):
    """Cruza a vaga com as competências do currículo e ajusta a compatibilidade."""
    desc = v.get("descricao", "")
    v["comp"] = competencias(v.get("titulo", "") + " " + desc)
    v["lida"] = len(desc) > 300
    v.setdefault("verificada", None)
    # "Alta" só para cargos-alvo (lista ALTA). As competências nunca promovem um cargo parecido.
    if v["nivel"] == "Alta" and v["lida"] and not v["comp"]:
        v["nivel"] = "Média"
    v["descartar"] = ""
    if CRC_RE.search(desc):
        v["descartar"] = "Exige ser contador (registro no CRC)"
        return
    if v["nivel"] == "Média" and v["lida"] and len(v["comp"]) < 2:
        v["descartar"] = f"Pouca aderência: cita {len(v['comp'])} das suas competências"
    elif v["nivel"] == "Média" and not v["lida"] and v.get("grupo") == "Empresa":
        v["descartar"] = "Cargo parecido, mas a página não tem descrição para conferir"


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
            status, html, ms, usada = baixar_com_variantes(f["url"], browser)
            n_links, achadas = extrair(html, usada)
            seguiu = " · endereço alternativo: " + usada[:60] if usada != f["url"] else ""
            if (not achadas and n_links >= 5 and 0 < status < 400
                    and not urlparse(usada).path.strip("/")):   # só a partir da página inicial
                prox = link_vagas(html, usada)
                if prox:
                    try:
                        st2, html2, _ = baixar(prox, browser)
                        if 0 < st2 < 400:
                            n_links, achadas = extrair(html2, prox)
                            seguiu += " · seguiu " + prox[:70]
                    except Exception:  # noqa
                        pass
            r["detalhe"] = f"HTTP {status} · {n_links} links · {ms / 1000:.1f}s{seguiu}"
            if status >= 400 or status == 0:
                r["status"] = "Falhou"
            elif n_links < 5:
                r["status"] = "Carregou sem lista de vagas (JavaScript ou login)"
            elif achadas:
                r["status"] = "OK, com vagas compatíveis"
            else:
                r["status"] = "Carregou, sem vaga compatível hoje"
    except Exception as e:  # noqa
        r["status"] = "Falhou"
        r["detalhe"] = str(e)[:120]
    for v in achadas:
        v["fonte"], v["grupo"] = f["nome"], f.get("grupo", "")
    return r, achadas


def coletar(fontes, indices):
    """Roda um lote de fontes (os índices dados) e já filtra e avalia as vagas dele."""
    resultado, vagas = [], {}
    hp = RAIZ / "dados" / "historico.json"
    hist_fontes = json.loads(hp.read_text("utf-8")).get("fontes", {}) if hp.exists() else {}
    with (sync_playwright() if sync_playwright else nullcontext()) as pw:
        browser = pw.chromium.launch() if pw else None
        for i in indices:
            r, achadas = processar_fonte(fontes[i], browser, hist_fontes)
            r["ordem"] = i
            for v in achadas:
                v["ordem"] = i
                vagas.setdefault(v["url"], v)
            resultado.append(r)
        if browser:
            browser.close()

    fora = [{"titulo": v["titulo"], "motivo": "Fora do RS: " + v.get("local", ""), "fonte": v["fonte"], "url": v["url"]}
            for v in vagas.values() if v.get("regiao") == "fora"]
    vagas = {u: v for u, v in vagas.items() if v.get("regiao") != "fora"}
    for v in list(vagas.values())[:250]:
        if "verificada" not in v:
            v["verificada"], v["descricao"] = verificar(v["url"])
    for v in vagas.values():
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
        v.pop("descricao", None)
    return resultado, list(vagas.values()), fora


def montar(parciais, fontes):
    """Junta os lotes, atualiza o histórico e gera o painel (docs/index.html)."""
    agora = dt.datetime.now(BRT)
    carimbo, dia = agora.strftime("%d/%m/%Y %H:%M"), agora.strftime("%Y-%m-%d")
    hist_path = RAIZ / "dados" / "historico.json"
    hist = json.loads(hist_path.read_text("utf-8")) if hist_path.exists() else {"vagas": {}, "execucoes": []}

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

    lista = []
    for url, v in vagas.items():
        h = hist["vagas"].setdefault(url, {"primeira": dia})
        h.update({"titulo": v["titulo"], "ultima": dia})
        v["nova"] = h["primeira"] == dia
        v["primeira"] = h["primeira"]
        lista.append(v)
    lista.sort(key=lambda v: (v["nivel"] != "Alta", -len(v.get("comp", [])), not v["nova"]))
    hist["execucoes"] = (hist["execucoes"] + [{"quando": carimbo, "vagas": len(lista),
                         "fontes_ok": sum(1 for r in resultado if r["status"].startswith(("OK", "Carregou, sem")))}])[-60:]
    hist_path.parent.mkdir(exist_ok=True)
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=1), "utf-8")

    dados = {"gerado": carimbo, "vagas": lista, "fontes": resultado, "execucoes": hist["execucoes"][-14:], "fora": fora[:60]}
    html = (RAIZ / "modelo.html").read_text("utf-8").replace(
        "__DADOS__", json.dumps(dados, ensure_ascii=False).replace("</", "<\\/"))
    (RAIZ / "docs").mkdir(exist_ok=True)
    (RAIZ / "docs" / "index.html").write_text(html, "utf-8")
    print(f"{len(lista)} vagas · {sum(1 for r in resultado if r['status'].startswith('OK'))} fontes com vagas · "
          f"{len(parciais)} lotes · {carimbo}")


def opcao(args, nome, padrao=None):
    return args[args.index(nome) + 1] if nome in args else padrao


def main():
    """Modos: plano | coletar | montar (usados pelo GitHub) ou, sem modo, tudo de uma vez."""
    args = sys.argv[1:]
    modo = args[0] if args and args[0] in ("plano", "coletar", "montar") else "tudo"
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
