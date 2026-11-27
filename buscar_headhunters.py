#!/usr/bin/env python3
"""
Busca CONTATOS PROFISSIONAIS PÚBLICOS de head hunters / consultorias de recrutamento da Grande Porto Alegre.

O que faz
  1. Parte das consultorias que já estão no fontes.json e, se a internet permitir, descobre outras por busca.
  2. Abre só o site da própria consultoria (home, contato, equipe, trabalhe conosco) e lê o que está PUBLICADO ali:
     e-mails, telefones/WhatsApp (DDD 51) e nomes que aparecem junto de cargos como "Consultor" ou "Head Hunter".
  3. Grava docs/headhunters.json (o painel lê esse arquivo na aba "Head hunters") e dados/headhunters.csv.

Regras que o robô segue (de propósito)
  - NÃO acessa LinkedIn, Instagram, Facebook, WhatsApp, Indeed, Glassdoor, Catho etc. (proíbem robôs).
  - Respeita o robots.txt de cada site, espera entre as páginas e se identifica.
  - Só guarda contato que a empresa colocou no próprio site. Não adivinha e-mail e não busca contato de pessoa física.
  - Pouco volume: no máximo MAX_SITES sites e MAX_PAGINAS páginas por site.

Uso:  python buscar_headhunters.py            (usa fontes.json + busca)
      python buscar_headhunters.py --sem-busca (só as consultorias já cadastradas)
      python buscar_headhunters.py --sites https://exemplo.com.br,https://outro.com.br
"""
import csv, json, re, sys, time, html
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, unquote
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

RAIZ = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; ContatosRH-Bot/1.0; uso pessoal de recolocacao; contato publico)"
ESPERA = 1.5          # segundos entre requisições
TIMEOUT = 15
MAX_SITES = 80
MAX_PAGINAS = 6
LIMITE_TEMPO = 18 * 60  # segundos no total

# Sites que o robô nunca abre (proíbem robôs, são portais de vagas ou plataformas compartilhadas)
BLOQUEADOS = ("linkedin.", "facebook.", "instagram.", "wa.me", "whatsapp.", "indeed.", "glassdoor.", "catho.",
              "infojobs.", "vagas.com", "bne.com", "trabalhabrasil", "jobsora", "adzuna", "jooble", "gupy.io",
              "pandape", "solides", "recrutei", "selecty", "recrut.ai", "tunni", "senior.com", "google.", "youtube.",
              "wikipedia", "gov.br", "twitter.", "x.com", "tiktok.", "reclameaqui", "econodata", "cnpj", "telelistas",
              "apontador", "guiamais", "yelp", "tripadvisor", "mercadolivre", "olx.", "bing.", "duckduckgo.",
              "pinterest.", "medium.", "blogspot.", "wordpress.com", "jusbrasil")

REGIAO = ("porto alegre", "canoas", "novo hamburgo", "são leopoldo", "sao leopoldo", "gravataí", "gravatai",
          "cachoeirinha", "alvorada", "viamão", "viamao", "esteio", "sapucaia", "guaíba", "guaiba", "rio grande do sul", " rs")
CARGO = re.compile(r"head ?hunter|consultor(?:a)?\b|recrutador(?:a)?|s[óo]cio|s[óo]cia|diretor(?:a)?|gerente|talent|headhunter|"
                   r"executive search|selecionador", re.I)
LIGACAO = {"da", "de", "do", "dos", "das", "e"}
NAO_NOME = {"nossa", "nosso", "nossos", "nossas", "equipe", "time", "contato", "fale", "sobre", "empresa", "consultoria",
            "recursos", "humanos", "gestão", "gestao", "serviços", "servicos", "quem", "somos", "trabalhe", "conosco",
            "vagas", "cadastre", "saiba", "mais", "veja", "conheça", "conheca", "política", "politica", "privacidade",
            "recrutamento", "seleção", "selecao", "talentos", "carreira", "carreiras", "home", "início", "inicio",
            "porto", "alegre", "grupo", "soluções", "solucoes", "avaliação", "avaliacao", "desenvolvimento", "forte",
            "enviar", "currículo", "curriculo", "candidato", "candidatos", "cliente", "clientes", "parceiros", "blog"}
RUIDO_EMAIL = ("example.", "sentry", "wixpress", "domain.", "seudominio", "email.com", "seuemail", "@2x", ".png", ".jpg",
               ".jpeg", ".gif", ".webp", ".svg", "u003e", "noreply", "no-reply", "privacidade", "lgpd", "dpo@", "abuse@")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
FONE_RE = re.compile(r"(?<!\d)(?:\+?55[\s.\-]?)?\(?(\d{2})\)?[\s.\-]?(9?\d{4})[\s.\-]?(\d{4})(?!\d)")
CAMINHOS = ("contato", "fale-conosco", "contact", "faleconosco", "sobre", "quem-somos", "equipe", "nosso-time",
            "trabalhe-conosco", "cadastre-seu-curriculo", "curriculo")
LINK_RE = re.compile(r"contato|fale|contact|atendimento|sobre|quem.?somos|equipe|time|trabalhe|curr[ií]culo|cadastr|nossa.?empresa", re.I)

_sessao = requests.Session()
_sessao.headers.update({"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
_robots = {}
_ultimo = [0.0]


def dominio(u):
    return urlparse(u).netloc.lower().replace("www.", "")


def bloqueado(u):
    host = urlparse(u).netloc.lower().replace("www.", "")
    if host in ("x.com", "wa.me", "t.co"):
        return True
    return any(b in host for b in BLOQUEADOS if b not in ("x.com", "wa.me"))


def pode(u):
    """Respeita o robots.txt."""
    p = urlparse(u)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            r = _sessao.get(base + "/robots.txt", timeout=TIMEOUT)
            if r.status_code == 200 and "<html" not in r.text[:200].lower():
                rp.parse(r.text.splitlines())
            else:
                rp.parse([])
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, u)


def baixar(u):
    if bloqueado(u) or not pode(u):
        return None, "bloqueado pelo robots.txt ou lista de sites proibidos"
    falta = ESPERA - (time.time() - _ultimo[0])
    if falta > 0:
        time.sleep(falta)
    try:
        r = _sessao.get(u, timeout=TIMEOUT)
        _ultimo[0] = time.time()
    except Exception as e:
        _ultimo[0] = time.time()
        return None, "erro: " + type(e).__name__
    if r.status_code != 200 or "text/html" not in r.headers.get("content-type", "text/html"):
        return None, f"HTTP {r.status_code}"
    try:
        return r.content.decode("utf-8"), "ok"
    except UnicodeDecodeError:
        return r.text, "ok"


def decodifica_cf(hexa):
    """E-mails protegidos pelo Cloudflare (data-cfemail)."""
    try:
        k = int(hexa[:2], 16)
        return "".join(chr(int(hexa[i:i + 2], 16) ^ k) for i in range(2, len(hexa), 2))
    except Exception:
        return ""


def limpa_email(e):
    e = html.unescape(e).strip(" .,;:<>()[]\"'").lower()
    if any(x in e for x in RUIDO_EMAIL) or e.count("@") != 1:
        return ""
    if len(e) > 70:
        return ""
    return e


def fone_fmt(m):
    ddd, a, b = m.group(1), m.group(2), m.group(3)
    if ddd != "51":          # só Grande Porto Alegre
        return ""
    return f"(51) {a}-{b}"


def parece_nome(s):
    s = s.strip(" -–|:,•")
    if not (5 <= len(s) <= 42) or re.search(r"\d|@|http", s):
        return ""
    p = s.split()
    if not (2 <= len(p) <= 5):
        return ""
    for w in p:
        if w.lower() in LIGACAO:
            continue
        if not re.match(r"^[A-ZÀ-Ú][a-zà-ú'\-]+$", w) or w.lower() in NAO_NOME:
            return ""
    if CARGO.search(s):
        return ""
    return s


def extrai_pessoas(soup):
    """Nome seguido (ou junto) de um cargo, como aparece em páginas de equipe."""
    textos = [t.strip() for t in soup.stripped_strings if t.strip()]
    achados = []
    for i, t in enumerate(textos):
        m = re.match(r"^(.{5,42}?)\s*[\-–|,:•]\s*(.{3,60})$", t)
        if m and parece_nome(m.group(1)) and CARGO.search(m.group(2)):
            achados.append((parece_nome(m.group(1)), m.group(2).strip()))
        elif parece_nome(t) and i + 1 < len(textos) and len(textos[i + 1]) < 70 and CARGO.search(textos[i + 1]):
            achados.append((parece_nome(t), textos[i + 1].strip()))
    vistos, saida = set(), []
    for n, c in achados:
        if n.lower() not in vistos:
            vistos.add(n.lower())
            saida.append({"nome": n, "cargo": c})
    return saida[:15]


def le_pagina(url, texto):
    soup = BeautifulSoup(texto, "html.parser")
    emails, fones, whats = set(), set(), set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.lower().startswith("mailto:"):
            e = limpa_email(unquote(h[7:].split("?")[0]))
            if e:
                emails.add(e)
        elif h.lower().startswith("tel:"):
            m = FONE_RE.search(re.sub(r"[^\d+]", "", unquote(h[4:])))
            if m and fone_fmt(m):
                fones.add(fone_fmt(m))
        elif "wa.me/" in h or "api.whatsapp.com" in h or "wa.link" in h:
            m = FONE_RE.search(re.sub(r"[^\d+]", "", h.split("?")[0] + (parse_qs(urlparse(h).query).get("phone", [""])[0])))
            if m and fone_fmt(m):
                whats.add(fone_fmt(m))
    for el in soup.find_all(attrs={"data-cfemail": True}):
        e = limpa_email(decodifica_cf(el["data-cfemail"]))
        if e:
            emails.add(e)
    corpo = soup.get_text(" ", strip=True)
    for e in EMAIL_RE.findall(corpo):
        e = limpa_email(e)
        if e:
            emails.add(e)
    for m in FONE_RE.finditer(corpo):
        f = fone_fmt(m)
        if f:
            fones.add(f)
    titulo = (soup.title.get_text(strip=True) if soup.title else "")[:120]
    nome_site = ""
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        nome_site = og["content"].strip()
    return soup, emails, fones, whats, corpo, titulo, nome_site


def links_uteis(soup, base, dom):
    achados, vistos = [], set()
    for a in soup.find_all("a", href=True):
        u = urljoin(base, a["href"]).split("#")[0]
        if dominio(u) != dom or u in vistos or bloqueado(u):
            continue
        if LINK_RE.search(a.get_text(" ", strip=True)) or LINK_RE.search(urlparse(u).path):
            vistos.add(u)
            achados.append(u)
    return achados


def raiz_do(dom):
    p = dom.split(":")[0].split(".")
    if len(p) >= 3 and p[-2] in ("com", "org", "net", "ind", "adm", "eti"):
        return p[-3]
    return p[-2] if len(p) >= 2 else p[0]


def varre_site(url, nome_hint=""):
    """Abre a home e as páginas de contato/equipe do mesmo site."""
    dom = dominio(url)
    rel = {"site": url, "dominio": dom, "empresa": nome_hint, "status": "", "paginas": 0, "emails": 0, "telefones": 0}
    fila, vistas = [url], set()
    home_soup = None
    emails, fones, whats, pessoas, regiao_ok, nome_site, paginas_ok = set(), set(), set(), [], False, "", []
    while fila and rel["paginas"] < MAX_PAGINAS:
        u = fila.pop(0)
        if u in vistas:
            continue
        vistas.add(u)
        txt, st = baixar(u)
        if txt is None:
            if rel["paginas"] == 0:
                rel["status"] = st
            continue
        rel["paginas"] += 1
        soup, e, f, w, corpo, titulo, ns = le_pagina(u, txt)
        emails |= e
        fones |= f
        whats |= w
        nome_site = nome_site or ns or titulo.split("|")[0].split(" - ")[0].strip()
        pessoas += extrai_pessoas(soup)
        low = " " + corpo.lower() + " "
        regiao_ok = regiao_ok or any(r in low for r in REGIAO)
        if e or f or w:
            paginas_ok.append(u)
        if home_soup is None:
            home_soup = soup
            fila += links_uteis(soup, u, dom)[:5]
            fila += [urljoin(u, "/" + c) for c in CAMINHOS[:3]]
    if not rel["status"]:
        rel["status"] = "ok" if rel["paginas"] else "não abriu"
    raiz = raiz_do(dom)
    emails = {e for e in emails if raiz in e.split("@")[1] or any(k in e for k in ("curriculo", "vagas", "rh@", "contato", "selecao"))}
    rel["emails"], rel["telefones"] = len(emails), len(fones | whats)
    if not (emails or fones or whats or pessoas):
        rel["status"] = rel["status"] + " · sem contato público encontrado"
        return rel, []
    nome_emp = nome_hint or nome_site or dom
    base = {"empresa": nome_emp, "site": url, "regiao_confirmada": bool(regiao_ok or fones),
            "pagina_contato": (paginas_ok[0] if paginas_ok else url)}
    saida = []
    mails = sorted(emails)
    for p in pessoas:
        pri = p["nome"].split()[0].lower()
        pr = re.sub(r"[^a-z]", "", pri.encode("ascii", "ignore").decode())
        pm = [m for m in mails if pr and m.split("@")[0].startswith(pr)]
        saida.append(dict(base, tipo="pessoa", nome=p["nome"], cargo=p["cargo"], emails=pm, telefones=[], whatsapp=[]))
    geral = [m for m in mails if not any(m in s["emails"] for s in saida)]
    saida.append(dict(base, tipo="empresa", nome="", cargo="Contato geral / currículos", emails=geral,
                      telefones=sorted(fones), whatsapp=sorted(whats)))
    return rel, [s for s in saida if s["emails"] or s["telefones"] or s["whatsapp"] or s["tipo"] == "pessoa"]


def sementes(args):
    sites = {}
    if "--sites" in args:
        for u in args[args.index("--sites") + 1].split(","):
            sites[dominio(u)] = (u.strip(), "")
        return sites
    ex = RAIZ / "headhunters_sementes.json"
    if ex.exists():
        try:
            for u in json.loads(ex.read_text("utf-8")).get("sites", []):
                sites.setdefault(dominio(u), (u, ""))
        except Exception:
            pass
    f = RAIZ / "fontes.json"
    if f.exists():
        for x in json.loads(f.read_text("utf-8")):
            u = x.get("url", "")
            if x.get("grupo") in ("Recrutamento", "Favorita", "Outra fonte") and u.startswith("http") and not bloqueado(u):
                p = urlparse(u)
                sites.setdefault(dominio(u), (f"{p.scheme}://{p.netloc}/", x.get("nome", "").split("(")[0].split("–")[0].strip()))
    return sites


CONSULTAS = ["head hunter Porto Alegre", "recrutamento executivo Porto Alegre consultoria", "consultoria recrutamento seleção Porto Alegre",
             "executive search Porto Alegre", "recrutamento e seleção Canoas", "recrutamento e seleção Novo Hamburgo",
             "recrutamento e seleção São Leopoldo", "recrutamento e seleção Gravataí", "headhunter financeiro Porto Alegre",
             "consultoria RH Porto Alegre vagas gerência"]


def descobre():
    """Procura outras consultorias (melhor esforço; se a busca falhar, segue só com as cadastradas)."""
    achados, erros = {}, 0
    consultas = list(CONSULTAS)
    ex = RAIZ / "headhunters_sementes.json"
    if ex.exists():
        try:
            consultas += json.loads(ex.read_text("utf-8")).get("nomes", [])
        except Exception:
            pass
    for q in consultas:
        try:
            r = _sessao.post("https://html.duckduckgo.com/html/", data={"q": q}, timeout=TIMEOUT)
            time.sleep(ESPERA * 2)
            if r.status_code != 200:
                erros += 1
                continue
            for a in BeautifulSoup(r.text, "html.parser").select("a.result__a"):
                u = a.get("href", "")
                if "uddg=" in u:
                    u = unquote(parse_qs(urlparse(u).query).get("uddg", [""])[0])
                if u.startswith("http") and not bloqueado(u):
                    p = urlparse(u)
                    achados.setdefault(dominio(u), (f"{p.scheme}://{p.netloc}/", a.get_text(" ", strip=True)[:60]))
        except Exception:
            erros += 1
    return achados, erros


def main():
    args = sys.argv[1:]
    t0 = time.time()
    sites = sementes(args)
    busca = {"consultas": 0, "erros": 0, "novos": 0}
    if "--sem-busca" not in args and "--sites" not in args:
        novos, erros = descobre()
        busca = {"consultas": len(CONSULTAS) + len(json.loads((RAIZ / "headhunters_sementes.json").read_text("utf-8")).get("nomes", [])) if (RAIZ / "headhunters_sementes.json").exists() else len(CONSULTAS), "erros": erros, "novos": len([d for d in novos if d not in sites])}
        for d, v in novos.items():
            sites.setdefault(d, v)
    relatorio, contatos = [], []
    for i, (d, (u, nome)) in enumerate(list(sites.items())[:MAX_SITES]):
        if time.time() - t0 > LIMITE_TEMPO:
            relatorio.append({"site": u, "dominio": d, "empresa": nome, "status": "não visitado (limite de tempo)", "paginas": 0, "emails": 0, "telefones": 0})
            continue
        rel, achou = varre_site(u, nome)
        relatorio.append(rel)
        contatos += achou
        print(f"[{i + 1}/{min(len(sites), MAX_SITES)}] {d}: {rel['status']} · {rel['paginas']} pág · {rel['emails']} e-mails · {rel['telefones']} telefones")
    # junta por e-mail/telefone para não repetir
    vistos, final = set(), []
    for c in contatos:
        chave = (c["tipo"], c["nome"].lower(), ",".join(c["emails"]), ",".join(c["telefones"] + c["whatsapp"]), c["site"])
        if chave not in vistos:
            vistos.add(chave)
            final.append(c)
    saida = {"gerado": time.strftime("%Y-%m-%d %H:%M"), "aviso": "Contatos publicados pelas próprias consultorias em seus sites. Use só para apresentação profissional e respeite pedidos de descadastro.",
             "busca": busca, "sites_visitados": len(relatorio), "contatos": final, "relatorio": relatorio}
    (RAIZ / "docs").mkdir(exist_ok=True)
    (RAIZ / "dados").mkdir(exist_ok=True)
    (RAIZ / "docs" / "headhunters.json").write_text(json.dumps(saida, ensure_ascii=False, indent=1), "utf-8")
    with open(RAIZ / "dados" / "headhunters.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["empresa", "tipo", "nome", "cargo", "emails", "telefones", "whatsapp", "site", "pagina_contato", "regiao_confirmada"])
        for c in final:
            w.writerow([c["empresa"], c["tipo"], c["nome"], c["cargo"], " | ".join(c["emails"]), " | ".join(c["telefones"]),
                        " | ".join(c["whatsapp"]), c["site"], c["pagina_contato"], "sim" if c["regiao_confirmada"] else "não"])
    print(f"\n{len(final)} contatos em {len(relatorio)} sites · busca: {busca}")


if __name__ == "__main__":
    main()
