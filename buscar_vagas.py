#!/usr/bin/env python3
"""Busca diária de vagas: testa cada fonte de fontes.json e gera docs/index.html."""
import json, re, sys, time, datetime as dt
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

RAIZ = Path(__file__).parent
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
BRT = dt.timezone(dt.timedelta(hours=-3))
# Palavras-chave do perfil (edite à vontade)
ALTA = ["fp&a", "fpa", "controladoria", "controller", "gerente financeiro", "gestor financeiro",
        "coordenador financeiro", "coordenador administrativo", "gerente administrativo",
        "analista financeiro", "planejamento financeiro", "tesouraria",
        "administrativo financeiro", "administrativo e financeiro", "financeiro e administrativo"]
MEDIA = ["financeiro", "financeira", "administrativo", "contas a pagar", "contas a receber",
         "contábil", "contabil", "orçamento", "faturamento"]
EXCLUIR = ["estágio", "estagiário", "estagiario", "aprendiz", "vendedor", "motorista",
           "segurança", "vigilante", "limpeza", "operador"]
DATA_RE = re.compile(r"\d{2}/\d{2}/\d{4}|há \d+ (?:minutos?|horas?|dias?|semanas?|mês|meses)|hoje|ontem", re.I)


def nivel(titulo):
    t = titulo.lower()
    if any(x in t for x in EXCLUIR):
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
            r = pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(3500)
            return (r.status if r else 0), pg.content(), int((time.time() - t0) * 1000)
        finally:
            pg.close()
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    if "charset" not in r.headers.get("content-type", "").lower():
        r.encoding = "utf-8"
    return r.status_code, r.text, int((time.time() - t0) * 1000)


def extrair(html, base):
    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=True)
    achadas, vistos = [], set()
    for a in links:
        txt = " ".join(a.get_text(" ", strip=True).split())
        if not 8 <= len(txt) <= 140:
            continue
        n = nivel(txt)
        href = urljoin(base, a["href"])
        if not n or href in vistos or href.startswith(("mailto:", "javascript:", "#")):
            continue
        vistos.add(href)
        ctx = a.parent.get_text(" ", strip=True)[:400] if a.parent else txt
        m = DATA_RE.search(ctx)
        achadas.append({"titulo": txt, "empresa": "", "url": href, "nivel": n,
                        "data": m.group(0) if m else None})
    return len(links), achadas


def gupy():
    achadas, erro = {}, None
    hoje = dt.date.today().isoformat()
    for termo in ["analista financeiro", "fp&a", "controladoria", "coordenador financeiro",
                  "gerente financeiro", "administrativo financeiro", "controller"]:
        try:
            r = requests.get("https://portal.api.gupy.io/api/job",
                             params={"name": termo, "city": "Porto Alegre",
                                     "state": "Rio Grande do Sul", "limit": 50},
                             headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            for v in r.json().get("data", []):
                url, nome = v.get("jobUrl"), v.get("name", "")
                prazo = (v.get("applicationDeadline") or "")[:10]
                n = nivel(nome)
                if not url or not n or (prazo and prazo < hoje):
                    continue
                pub = (v.get("publishedDate") or "")[:10]
                achadas[url] = {"titulo": nome, "empresa": v.get("careerPageName", ""), "url": url,
                                "nivel": n, "data": pub[8:10] + "/" + pub[5:7] + "/" + pub[:4] if pub else None,
                                "prazo": prazo or None, "verificada": True}
        except Exception as e:  # noqa
            erro = str(e)[:120]
    return len(achadas), list(achadas.values()), erro


def link_ok(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15, stream=True)
        return r.status_code < 400
    except Exception:
        return False


def main():
    fontes = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else RAIZ / "fontes.json").read_text("utf-8"))
    agora = dt.datetime.now(BRT)
    carimbo, dia = agora.strftime("%d/%m/%Y %H:%M"), agora.strftime("%Y-%m-%d")
    hist_path = RAIZ / "dados" / "historico.json"
    hist = json.loads(hist_path.read_text("utf-8")) if hist_path.exists() else {"vagas": {}, "execucoes": []}

    resultado, vagas = [], {}
    with (sync_playwright() if sync_playwright else nullcontext()) as pw:
        browser = pw.chromium.launch() if pw else None
        for f in fontes:
            r = {"nome": f["nome"], "url": f.get("url", ""), "tipo": f["tipo"], "status": "", "detalhe": ""}
            try:
                if f["tipo"] == "alerta":
                    r["status"] = "Não lida (exige login ou bloqueia robôs)"
                    r["detalhe"] = "Use alerta por e-mail nesse site"
                elif f["tipo"] == "gupy":
                    n, achadas, erro = gupy()
                    r["status"] = "Falhou" if erro and not n else ("OK, com vagas compatíveis" if n else "Carregou, sem vaga compatível hoje")
                    r["detalhe"] = erro or f"{n} vagas via API"
                    for v in achadas:
                        v["fonte"] = f["nome"]; vagas[v["url"]] = v
                elif not f.get("url"):
                    r["status"] = "Sem link cadastrado"
                    r["detalhe"] = "Preencha o campo url em fontes.json"
                else:
                    status, html, ms = baixar(f["url"], browser)
                    n_links, achadas = extrair(html, f["url"])
                    r["detalhe"] = f"HTTP {status} · {n_links} links · {ms / 1000:.1f}s"
                    if status >= 400 or status == 0:
                        r["status"] = "Falhou"
                    elif n_links < 5:
                        r["status"] = "Carregou sem lista de vagas (JavaScript ou login)"
                    elif achadas:
                        r["status"] = "OK, com vagas compatíveis"
                    else:
                        r["status"] = "Carregou, sem vaga compatível hoje"
                    for v in achadas:
                        v["fonte"] = f["nome"]; vagas.setdefault(v["url"], v)
            except Exception as e:  # noqa
                r["status"] = "Falhou"
                r["detalhe"] = str(e)[:120]
            r["vagas"] = sum(1 for v in vagas.values() if v["fonte"] == f["nome"])
            resultado.append(r)
        if browser:
            browser.close()

    for v in list(vagas.values())[:80]:
        if "verificada" not in v:
            v["verificada"] = link_ok(v["url"])
    lista = []
    for url, v in vagas.items():
        h = hist["vagas"].setdefault(url, {"primeira": dia})
        h.update({"titulo": v["titulo"], "ultima": dia})
        v["nova"] = h["primeira"] == dia
        v["primeira"] = h["primeira"]
        lista.append(v)
    lista.sort(key=lambda v: (v["nivel"] != "Alta", not v["nova"]))
    hist["execucoes"] = (hist["execucoes"] + [{"quando": carimbo, "vagas": len(lista),
                         "fontes_ok": sum(1 for r in resultado if r["status"].startswith(("OK", "Carregou, sem")))}])[-60:]
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=1), "utf-8")

    dados = {"gerado": carimbo, "vagas": lista, "fontes": resultado, "execucoes": hist["execucoes"][-14:]}
    html = (RAIZ / "modelo.html").read_text("utf-8").replace(
        "__DADOS__", json.dumps(dados, ensure_ascii=False).replace("</", "<\\/"))
    (RAIZ / "docs" / "index.html").write_text(html, "utf-8")
    print(f"{len(lista)} vagas · {sum(1 for r in resultado if r['status'].startswith('OK'))} fontes com vagas · {carimbo}")


if __name__ == "__main__":
    main()
