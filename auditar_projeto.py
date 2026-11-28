#!/usr/bin/env python3
"""PROJETO_AUDITORIA - confere se tudo que foi planejado está no lugar certo do GitHub e funcionando.

Uso (o GitHub Actions chama sozinho; você não precisa rodar):
  python auditar_projeto.py                  só confere e escreve docs/auditoria_projeto.json
  python auditar_projeto.py --corrigir       também corrige o que é seguro corrigir
  --pode-workflow   (só com o segredo PAT_REPO) deixa corrigir arquivos de .github/workflows
  --avisar          abre/fecha uma Issue no GitHub (você recebe e-mail) se sobrar pendência
  --sem-api         não consulta a API do GitHub (para testes)

Regras de segurança: nunca apaga nada. O que sobra vai para a pasta _lixeira/.
Nunca mexe em dados/, fontes.json, controle.json nem em docs/ (exceto o próprio relatório).
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BRT = dt.timezone(dt.timedelta(hours=-3))
AGORA = dt.datetime.now(BRT)
ARGS = sys.argv[1:]
CORRIGIR = "--corrigir" in ARGS
PODE_WF = "--pode-workflow" in ARGS
AVISAR = "--avisar" in ARGS
SEM_API = "--sem-api" in ARGS
RAIZ = Path(ARGS[ARGS.index("--raiz") + 1]) if "--raiz" in ARGS else Path(__file__).resolve().parent
LIXEIRA = RAIZ / "_lixeira"
SAIDA = RAIZ / "docs" / "auditoria_projeto.json"
WF = RAIZ / ".github" / "workflows"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
HORAS_MAX = 30          # depois disso o painel é considerado desatualizado

# ---------------------------------------------------------------- o que o projeto PRECISA ter
# marcas = textos que provam que é a versão nova (e não um arquivo antigo baixado por engano)
ARQUIVOS = {
    "buscar_vagas.py": {"marcas": ["DIAS_APRENDER", "CFO_RE", "def veredito"],
                        "nome": "robô de vagas (com CFO e aprendizado por fonte)"},
    "modelo.html": {"marcas": ["drawHoje", "Aprendizado por fonte", "auditoria_projeto.json"], "unico": "__DADOS__",
                    "nome": "painel (aba Hoje, aprendizado e auditoria do projeto)"},
    "buscar_headhunters.py": {"marcas": ["headhunters_sementes.json"], "nome": "busca de head hunters"},
    "headhunters_sementes.json": {"marcas": ["effetive"], "json": True, "nome": "lista inicial de head hunters"},
    "fontes.json": {"json": True, "meu": True, "nome": "suas fontes de vagas"},
}
WORKFLOWS = {
    "vagas.yml": {"marcas": ["buscar_vagas.py plano", "cron"], "nome": "Buscar vagas"},
    "headhunters.yml": {"marcas": ["buscar_headhunters.py"], "nome": "Buscar contatos de head hunters"},
    "auditoria.yml": {"marcas": ["auditar_projeto.py"], "nome": "Auditoria do projeto"},
}
CRON_VAGAS = "7 10,15,19 * * *"
ARQUIVOS_APP = ["icon-192.png", "icon-512.png", "manifest.webmanifest", "sw.js", "index.html"]
CONHECIDOS_RAIZ = {"controle.json", "rotina-gmail.gs", "auditar_projeto.py", ".gitignore", ".gitattributes", "license"}
DUP = re.compile(r"^(?P<base>.+?) \((?P<n>\d+)\)(?P<ext>\.[^.]+)?$")

V, C, MANUAL = [], [], []          # verificações, correções feitas, pendências manuais
PRECISA_DISPARAR = {"motivo": ""}


# ---------------------------------------------------------------- utilidades
def reg(grupo, titulo, status, detalhe="", corrigido=False, manual=None):
    V.append({"grupo": grupo, "titulo": titulo, "status": status, "detalhe": detalhe, "corrigido": corrigido})
    if manual and status != "ok":
        MANUAL.append({"titulo": titulo, "passo": manual})


def ler(p):
    try:
        return Path(p).read_text("utf-8", errors="replace")
    except OSError:
        return None


def conteudo_ok(p, regras):
    """(ok, motivo). Confere marcas, trecho único e JSON válido."""
    t = ler(p)
    if t is None:
        return False, "não existe"
    if not t.strip():
        return False, "está vazio"
    baixo = t.lower()
    falta = [m for m in regras.get("marcas", []) if m.lower() not in baixo]
    if falta:
        return False, "versão antiga ou errada (falta: " + ", ".join(falta) + ")"
    u = regras.get("unico")
    if u and t.count(u) != 1:
        return False, f"o trecho {u} aparece {t.count(u)} vez(es) e deveria aparecer 1"
    if regras.get("json"):
        try:
            json.loads(t)
        except ValueError as e:
            return False, f"JSON quebrado ({e})"
    return True, ""


def p_lixeira(p):
    LIXEIRA.mkdir(exist_ok=True)
    destino = LIXEIRA / f"{AGORA:%Y%m%d-%H%M%S}-{Path(p).name}"
    n = 1
    while destino.exists():
        destino = LIXEIRA / f"{AGORA:%Y%m%d-%H%M%S}-{n}-{Path(p).name}"
        n += 1
    return destino


def para_lixeira(p, por_que):
    if not CORRIGIR:
        return False
    d = p_lixeira(p)
    shutil.move(str(p), str(d))
    C.append({"o_que": f"Tirei “{Path(p).relative_to(RAIZ)}” do caminho", "como": f"{por_que}. Guardei em {d.relative_to(RAIZ)} (nada foi apagado)."})
    return True


def referencia(rel):
    return RAIZ / "referencia" / ("workflows/" + Path(rel).name if rel.startswith(".github") else rel)


def restaurar(rel, regras, por_que):
    """Troca o arquivo pela cópia boa de referencia/. Só se a cópia tiver as marcas certas."""
    if regras.get("meu"):
        return False
    ref = referencia(rel)
    ok, _ = conteudo_ok(ref, regras)
    if not ok or not CORRIGIR:
        return False
    destino = RAIZ / rel
    if rel.startswith(".github") and not PODE_WF:
        return False
    if destino.exists():
        d = p_lixeira(destino)
        shutil.move(str(destino), str(d))
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ref, destino)
    C.append({"o_que": f"Restaurei “{rel}”", "como": f"{por_que}. Usei a cópia boa de referencia/ (a antiga ficou em _lixeira/)."})
    return True


def gh(*a):
    if SEM_API or not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")) or not REPO:
        return None
    try:
        return subprocess.run(["gh", *a], capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError):
        return None


def api(caminho):
    """(dados, erro). erro = None, '404' ou texto."""
    r = gh("api", caminho)
    if r is None:
        return None, "sem-api"
    if r.returncode:
        return None, "404" if "404" in (r.stderr + r.stdout) else (r.stderr or r.stdout)[:200]
    try:
        return json.loads(r.stdout or "null"), None
    except ValueError:
        return None, "resposta inválida"


def horas_desde(texto):
    """Lê 'dd/mm/aaaa HH:MM' (horário de Brasília) e devolve horas até agora."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})[ T]+(\d{2}):(\d{2})", texto or "")
    if not m:
        return None
    d, mo, a, h, mi = map(int, m.groups())
    return (AGORA - dt.datetime(a, mo, d, h, mi, tzinfo=BRT)).total_seconds() / 3600


# ---------------------------------------------------------------- 1) arquivos obrigatórios
def checar_arquivos():
    g = "Arquivos do projeto"
    for nome, regras in ARQUIVOS.items():
        p = RAIZ / nome
        ok, motivo = conteudo_ok(p, regras)
        if ok:
            reg(g, f"{nome}: {regras['nome']}", "ok", "no lugar certo e na versão certa")
            continue
        corrigiu = restaurar(nome, regras, motivo)
        if corrigiu:
            PRECISA_DISPARAR["motivo"] = PRECISA_DISPARAR["motivo"] or f"{nome} foi corrigido"
            reg(g, f"{nome}: {regras['nome']}", "ok", f"Estava com problema ({motivo}); corrigi automaticamente", True)
        else:
            reg(g, f"{nome}: {regras['nome']}", "erro", motivo,
                manual=f"Suba o arquivo “{nome}” do pacote de hoje na raiz do repositório (Add file → Upload files) e confirme em Commit changes.")


# ---------------------------------------------------------------- 2) arquivos fora do lugar
def checar_fora_do_lugar():
    g = "Arquivos fora do lugar"
    achou = False
    arquivos = [p for p in RAIZ.iterdir() if p.is_file()]
    for p in sorted(arquivos):
        m = DUP.match(p.name)
        if m:
            achou = True
            canon = RAIZ / (m["base"] + (m["ext"] or ""))
            regras = ARQUIVOS.get(canon.name) or {}
            wf = WORKFLOWS.get(canon.name)
            # cópia “(1)” de um workflow: o lugar certo é .github/workflows
            if wf and canon.suffix == ".yml":
                destino = WF / canon.name
                if destino.exists():
                    if para_lixeira(p, "era uma cópia repetida do workflow, que já está no lugar certo"):
                        reg(g, p.name, "ok", "cópia repetida de workflow: tirei do caminho", True)
                    else:
                        reg(g, p.name, "erro", "cópia repetida de workflow na raiz", manual=f"Apague “{p.name}” da raiz (abra o arquivo → ⋯ → Delete file).")
                else:
                    reg(g, p.name, "erro", "workflow fora de .github/workflows", manual=_passo_workflow(canon.name))
                continue
            if canon.exists():
                novo_ok = regras and conteudo_ok(p, regras)[0]
                velho_ok = (not regras) or conteudo_ok(canon, regras)[0]
                if CORRIGIR and novo_ok and not velho_ok:       # o “(1)” é que é o certo: troca
                    para_lixeira(canon, f"a cópia “{p.name}” era a versão certa")
                    shutil.move(str(p), str(canon))
                    C.append({"o_que": f"Troquei {canon.name} pela cópia “{p.name}”", "como": "a cópia “(1)” tinha a versão nova; renomeei"})
                    PRECISA_DISPARAR["motivo"] = PRECISA_DISPARAR["motivo"] or f"{canon.name} foi trocado"
                    reg(g, p.name, "ok", "era a versão nova: renomeei", True)
                elif para_lixeira(p, "era uma cópia repetida (nome com número entre parênteses)"):
                    reg(g, p.name, "ok", "cópia repetida: tirei do caminho", True)
                else:
                    reg(g, p.name, "erro", "cópia repetida na raiz", manual=f"Apague “{p.name}” (abra o arquivo → ⋯ → Delete file).")
            elif canon.name in ARQUIVOS:
                if CORRIGIR and conteudo_ok(p, regras)[0]:
                    shutil.move(str(p), str(canon))
                    C.append({"o_que": f"Renomeei “{p.name}” para “{canon.name}”", "como": "o arquivo certo tinha ido com número no nome"})
                    reg(g, p.name, "ok", "renomeei para o nome certo", True)
                else:
                    reg(g, p.name, "erro", "nome com número e não existe o arquivo sem número", manual=f"Renomeie “{p.name}” para “{canon.name}” (abra → ✏️ → mude o nome).")
            else:
                reg(g, p.name, "aviso", "parece cópia repetida de um arquivo que eu não conheço; deixei como está")
            continue
        if p.suffix == ".yml" and p.name in WORKFLOWS:
            achou = True
            if (WF / p.name).exists() and para_lixeira(p, "workflow na raiz não roda; o certo está em .github/workflows"):
                reg(g, p.name, "ok", "workflow na raiz (não roda ali): tirei do caminho", True)
            elif (WF / p.name).exists():
                reg(g, p.name, "erro", "workflow na raiz (não roda ali)", manual=f"Apague “{p.name}” da raiz; o certo já está em .github/workflows.")
            else:
                reg(g, p.name, "erro", "workflow na raiz: não aparece no Actions", manual=_passo_workflow(p.name))
        elif p.name in ARQUIVOS_APP:
            achou = True
            if (RAIZ / "docs" / p.name).exists():
                if para_lixeira(p, "o painel só usa a versão que fica em docs/"):
                    reg(g, p.name, "ok", "arquivo do aplicativo solto na raiz: tirei do caminho", True)
                else:
                    reg(g, p.name, "erro", "arquivo do aplicativo solto na raiz", manual=f"Apague “{p.name}” da raiz (o certo fica em docs/).")
            elif CORRIGIR:
                (RAIZ / "docs").mkdir(exist_ok=True)
                shutil.move(str(p), str(RAIZ / "docs" / p.name))
                C.append({"o_que": f"Movi “{p.name}” para docs/", "como": "o arquivo do aplicativo estava solto na raiz"})
                reg(g, p.name, "ok", "movi para docs/", True)
            else:
                reg(g, p.name, "erro", "arquivo do aplicativo fora de docs/", manual=f"“{p.name}” precisa ficar dentro da pasta docs/.")
    if not achou:
        reg(g, "Nada solto ou repetido na raiz", "ok", "a raiz está limpa")
    sobra = sorted(p.name for p in RAIZ.iterdir() if p.is_file() and p.name not in ARQUIVOS and p.name not in CONHECIDOS_RAIZ
                   and not DUP.match(p.name) and not p.name.lower().startswith(("leia-me", "readme", "aviso", "parcial"))
                   and p.name not in ARQUIVOS_APP and p.name not in WORKFLOWS)
    if sobra:
        reg(g, "Outros arquivos na raiz", "aviso", "não reconheço: " + ", ".join(sobra[:12]) + ". Não mexi.")


def _passo_workflow(nome):
    return (f"Crie “{nome}” no lugar certo: Add file → Create new file, digite .github/workflows/{nome} no nome e cole o conteúdo "
            f"de referencia/workflows/{nome}. (Dica: para eu corrigir isso sozinho no futuro, crie o segredo PAT_REPO; o LEIA-ME explica.)")


# ---------------------------------------------------------------- 3) workflows
def checar_workflows():
    g = "Workflows (o robô no Actions)"
    try:
        import yaml
    except ImportError:
        yaml = None
    for nome, regras in WORKFLOWS.items():
        rel = f".github/workflows/{nome}"
        ok, motivo = conteudo_ok(RAIZ / rel, regras)
        if ok and yaml is not None:
            try:
                doc = yaml.safe_load((RAIZ / rel).read_text("utf-8"))
                if not isinstance(doc, dict) or "jobs" not in doc:
                    ok, motivo = False, "YAML sem a parte jobs"
            except yaml.YAMLError as e:
                ok, motivo = False, f"YAML com erro ({str(e)[:80]})"
        if ok:
            reg(g, f"{nome}: {regras['nome']}", "ok", "está em .github/workflows e com o conteúdo certo")
            continue
        if restaurar(rel, regras, motivo):
            PRECISA_DISPARAR["motivo"] = PRECISA_DISPARAR["motivo"] or f"{nome} foi restaurado"
            reg(g, f"{nome}: {regras['nome']}", "ok", f"Estava com problema ({motivo}); restaurei", True)
        elif nome == "auditoria.yml" and not (RAIZ / rel).exists():
            reg(g, f"{nome}: {regras['nome']}", "aviso", "ainda não existe; a auditoria automática precisa dele", manual=_passo_workflow(nome))
        else:
            reg(g, f"{nome}: {regras['nome']}", "erro", motivo, manual=_passo_workflow(nome))
    t = ler(WF / "vagas.yml") or ""
    if t and CRON_VAGAS not in t:
        reg(g, "Horários do robô (vagas.yml)", "aviso", f"o horário combinado era “{CRON_VAGAS}” (07h07, 12h07 e 16h07) e não está no arquivo")
    elif t:
        reg(g, "Horários do robô (vagas.yml)", "ok", "3 rodadas por dia: 07h07, 12h07 e 16h07")


# ---------------------------------------------------------------- 4) o painel (Pages)
def checar_painel():
    g = "Painel publicado"
    idx = RAIZ / "docs" / "index.html"
    t = ler(idx)
    if t is None:
        reg(g, "docs/index.html", "erro", "o painel ainda não foi gerado", manual="Rode Actions → Buscar vagas → Run workflow. O painel nasce no fim da rodada.")
        PRECISA_DISPARAR["motivo"] = PRECISA_DISPARAR["motivo"] or "o painel não existe"
    elif "__DADOS__" in t:
        reg(g, "docs/index.html", "erro", "o painel está sem dados (modelo cru)", manual="Rode Actions → Buscar vagas.")
        PRECISA_DISPARAR["motivo"] = PRECISA_DISPARAR["motivo"] or "o painel está sem dados"
    else:
        reg(g, "docs/index.html", "ok", f"painel gerado ({len(t) // 1024} KB)")
    falt = [a for a in ARQUIVOS_APP[:4] if not (RAIZ / "docs" / a).exists()]
    if falt:
        reg(g, "Arquivos do aplicativo em docs/", "aviso", "faltam: " + ", ".join(falt) + " (o robô recria na próxima rodada)")
        PRECISA_DISPARAR["motivo"] = PRECISA_DISPARAR["motivo"] or "faltam arquivos do aplicativo em docs/"
    else:
        reg(g, "Arquivos do aplicativo em docs/", "ok", "ícones, manifest e sw.js no lugar")
    hist = _json(RAIZ / "dados" / "historico.json") or {}
    ex = hist.get("execucoes") or []
    if ex:
        h = horas_desde(ex[-1].get("quando", ""))
        if h is None:
            reg(g, "Última rodada", "aviso", "não consegui ler a data da última rodada")
        elif h > HORAS_MAX:
            reg(g, "Última rodada", "erro", f"foi há {h:.0f} horas ({ex[-1]['quando']}); era para rodar 3 vezes por dia",
                manual="Abra Actions → Buscar vagas e veja o último passo vermelho (se houver). Rode Run workflow e me mande o texto do erro.")
        else:
            reg(g, "Última rodada", "ok", f"há {h:.0f} h ({ex[-1]['quando']}), {ex[-1].get('vagas', 0)} vaga(s)")
    else:
        reg(g, "Última rodada", "aviso", "o histórico ainda não tem rodadas")
    d, e = api(f"repos/{REPO}/pages") if REPO else (None, "sem-api")
    if e == "404":
        reg(g, "GitHub Pages ligado", "erro", "o Pages não está ligado: o endereço do painel não abre",
            manual="Settings → Pages → Source: Deploy from a branch → Branch: main, pasta /docs → Save.")
    elif d:
        reg(g, "GitHub Pages ligado", "ok", "publicando " + str((d.get("source") or {}).get("path", "")) + " da branch " + str((d.get("source") or {}).get("branch", "")))


def _json(p):
    try:
        return json.loads(Path(p).read_text("utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 5) o que está rodando de verdade
def checar_execucao():
    g = "Execução no GitHub Actions"
    if not REPO or gh("--version") is None:
        reg(g, "Consulta ao Actions", "aviso", "só roda dentro do GitHub (aqui não há acesso à API); esta parte fica para a próxima auditoria")
        return
    reg_wf, e = api(f"repos/{REPO}/actions/workflows?per_page=100")
    if e:
        reg(g, "Workflows registrados", "aviso", f"não consegui consultar o Actions ({e})")
        return
    nomes = {w["name"]: w for w in reg_wf.get("workflows", [])}
    for arq, regras in WORKFLOWS.items():
        if regras["nome"] not in nomes:
            if (WF / arq).exists():
                reg(g, f"“{regras['nome']}” aparece no Actions?", "aviso", "o arquivo existe mas o GitHub ainda não registrou (pode levar alguns minutos)")
            continue
        reg(g, f"“{regras['nome']}” aparece no Actions?", "ok", "sim, registrado")
    for arq, regras in WORKFLOWS.items():
        w = nomes.get(regras["nome"])
        if not w or arq == "auditoria.yml":
            continue
        runs, e = api(f"repos/{REPO}/actions/workflows/{w['id']}/runs?per_page=20")
        runs = (runs or {}).get("workflow_runs", [])
        if not runs:
            reg(g, f"{regras['nome']}: já rodou?", "aviso", "ainda não rodou nenhuma vez", manual=f"Actions → {regras['nome']} → Run workflow.")
            if arq == "vagas.yml":
                PRECISA_DISPARAR["motivo"] = PRECISA_DISPARAR["motivo"] or "o robô de vagas nunca rodou"
            continue
        if any(r["status"] in ("in_progress", "queued") for r in runs):
            reg(g, f"{regras['nome']}: rodando agora", "ok", "tem uma rodada em andamento")
            continue
        ult = runs[0]
        ok24 = [r for r in runs if r["conclusion"] == "success" and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))).total_seconds() < 86400]
        if ult["conclusion"] == "success":
            reg(g, f"{regras['nome']}: última rodada", "ok", f"deu certo; {len(ok24)} rodada(s) bem-sucedida(s) nas últimas 24 h")
        else:
            passos = _passos_com_erro(ult["id"])
            reg(g, f"{regras['nome']}: última rodada", "erro", f"terminou com “{ult['conclusion']}”" + (f"; passo com erro: {passos}" if passos else ""),
                manual=f"Abra {ult['html_url']} , clique no passo vermelho e me mande o texto do erro.")


def _passos_com_erro(run_id):
    d, e = api(f"repos/{REPO}/actions/runs/{run_id}/jobs?per_page=50")
    if not d:
        return ""
    out = []
    for j in d.get("jobs", []):
        for s in j.get("steps", []):
            if s.get("conclusion") == "failure":
                out.append(f"{j['name']} › {s['name']}")
    return "; ".join(out[:4])


# ---------------------------------------------------------------- 6) o que foi planejado, na prática
def checar_pratica():
    g = "O planejado, na prática"
    fontes = _json(RAIZ / "fontes.json") or []
    hist = _json(RAIZ / "dados" / "historico.json") or {}
    vistas = set((hist.get("fontes") or {}).keys())
    nomes = [f.get("nome") for f in fontes if isinstance(f, dict) and f.get("nome")]
    if nomes and vistas:
        nunca = [n for n in nomes if n not in vistas]
        if nunca:
            reg(g, "Todas as suas fontes foram visitadas?", "aviso", f"{len(nunca)} de {len(nomes)} nunca apareceram no histórico (ex.: {', '.join(nunca[:4])}). Podem ser fontes novas, que entram na próxima rodada.")
        else:
            reg(g, "Todas as suas fontes foram visitadas?", "ok", f"as {len(nomes)} fontes do fontes.json já foram visitadas pelo robô")
    elif nomes:
        reg(g, "Todas as suas fontes foram visitadas?", "aviso", "o histórico ainda está vazio (o robô ainda não rodou)")
    ap = _json(RAIZ / "dados" / "aprendizado.json")
    if ap is None:
        reg(g, "Aprendizado por fonte (5 dias)", "aviso", "dados/aprendizado.json ainda não existe: ele nasce na primeira rodada do robô novo")
    else:
        dias = max([len((m or {}).get("dias", [])) for m in ap.values()] or [0])
        reg(g, "Aprendizado por fonte (5 dias)", "ok", f"funcionando: {len(ap)} fonte(s) na memória; dia {min(dias, 5)} de 5 de observação" + (" (já pode haver sugestões de exclusão)" if dias >= 5 else ""))
    hh = _json(RAIZ / "docs" / "headhunters.json")
    if hh is None:
        reg(g, "Head hunters", "aviso", "docs/headhunters.json ainda não existe", manual="Actions → Buscar contatos de head hunters → Run workflow.")
    else:
        n = len(hh.get("contatos", hh)) if isinstance(hh, (dict, list)) else 0
        reg(g, "Head hunters", "ok", f"lista gerada ({n} item(ns))")


# ---------------------------------------------------------------- resultado
def salvar():
    cont = {k: sum(1 for v in V if v["status"] == k) for k in ("ok", "aviso", "erro")}
    r = {"gerado": AGORA.strftime("%d/%m/%Y %H:%M"), "modo_corrigir": CORRIGIR,
         "resumo": dict(cont, corrigidos=len(C)), "verificacoes": V, "correcoes": C, "pendencias": MANUAL}
    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(r, ensure_ascii=False, indent=1), "utf-8")
    return r


def avisar(r):
    if not AVISAR or gh("--version") is None:
        return
    titulo_base = "🔧 Auditoria do projeto"
    lista = gh("issue", "list", "--state", "open", "--search", f"{titulo_base} in:title", "--json", "number,title")
    abertas = []
    try:
        abertas = [i for i in json.loads(lista.stdout or "[]") if i["title"].startswith(titulo_base)] if lista and not lista.returncode else []
    except ValueError:
        pass
    erros = [v for v in V if v["status"] == "erro"]
    if not erros:
        for i in abertas:
            gh("issue", "close", str(i["number"]), "--comment", "Tudo certo na última auditoria. Fechei sozinho.")
        return
    if abertas:
        return                      # já existe um aviso aberto: não enche sua caixa de e-mail
    corpo = ["A auditoria achou coisas que eu **não consigo corrigir sozinho**:", ""]
    corpo += [f"{i}. **{m['titulo']}**: {m['passo']}" for i, m in enumerate(MANUAL, 1)] or ["(veja o painel → Auditoria)"]
    if C:
        corpo += ["", "O que eu já corrigi sozinho:"] + [f"- {c['o_que']}" for c in C]
    (RAIZ / "auditoria-aviso.md").write_text("\n".join(corpo), "utf-8")
    gh("issue", "create", "--title", f"{titulo_base}: {len(erros)} pendência(s) para você", "--body-file", str(RAIZ / "auditoria-aviso.md"))


def disparar():
    if not CORRIGIR or not PRECISA_DISPARAR["motivo"] or not (WF / "vagas.yml").exists():
        return
    if any(v["status"] == "erro" and v["grupo"] == "Workflows (o robô no Actions)" for v in V):
        return
    if any("rodando agora" in v["titulo"] for v in V):
        return
    ult, _e = api(f"repos/{REPO}/actions/workflows/vagas.yml/runs?per_page=1")        # freio: nunca em laço
    runs = (ult or {}).get("workflow_runs") or []
    if runs and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00"))).total_seconds() < 6 * 3600:
        return
    r = gh("workflow", "run", "vagas.yml")
    if r is not None and r.returncode == 0:
        C.append({"o_que": "Mandei o robô rodar de novo", "como": PRECISA_DISPARAR["motivo"] + ": rodei “Buscar vagas” para o painel refletir a correção."})


def main():
    checar_arquivos()
    checar_workflows()
    checar_fora_do_lugar()
    checar_painel()
    checar_execucao()
    checar_pratica()
    disparar()
    r = salvar()
    s = r["resumo"]
    print(f"Auditoria {r['gerado']}: {s['ok']} ok · {s['aviso']} aviso(s) · {s['erro']} erro(s) · {s['corrigidos']} correção(ões) automática(s)")
    for v in V:
        if v["status"] != "ok" or v["corrigido"]:
            print(f"  [{v['status'].upper():5}] {v['grupo']} › {v['titulo']}: {v['detalhe']}" + (" (corrigido)" if v["corrigido"] else ""))
    for c in C:
        print(f"  [FEITO] {c['o_que']}: {c['como']}")
    avisar(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
