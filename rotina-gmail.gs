/**
 * ROTINA DE ALERTAS DE VAGAS — roda na SUA conta Google, sem custo e sem copiar e colar.
 * Lê só os e-mails de alerta de vaga (LinkedIn, Indeed, Glassdoor, Infojobs, Catho, iNeed),
 * separa as vagas que combinam com você e as candidaturas, e entrega ao painel.
 * Não envia, não apaga e não altera nenhum e-mail. Veja o passo a passo no LEIA-ME.
 */
var CHAVE = "ESbi_HFN94isChnGu92Lqw";   // senha da rotina: o painel precisa da mesma chave. Não mostre a ninguém.

var REMETENTES = ["linkedin.com", "indeed.com", "glassdoor.com", "infojobs.com.br", "catho.com.br", "ineed.com.br", "ineedtrabalho.com.br"];

var ALTA = ["gerente administrativo financeiro", "gerente administrativo e financeiro", "gerente financeiro", "gerente de controladoria",
  "controller", "controladoria", "coordenador financeiro", "coordenador administrativo", "gestor financeiro", "gestor administrativo",
  "fp&a", "planejamento financeiro", "analista financeiro", "analista de planejamento", "tesouraria", "administrativo financeiro",
  "administrativo e financeiro", "financeiro e administrativo", "gerente de financas", "coordenador de financas", "analista de financas",
  "superintendente financeiro", "head de financas"];
var MEDIA = ["financeiro", "financeira", "administrativo", "contas a pagar", "contas a receber", "orcamento", "faturamento", "custos",
  "diretor financeiro", "tesoureiro", "business partner financeiro", "financas"];
var EXCLUIR = /\b(?:estagio|estagiario|aprendiz|trainee|junior|jr|assistente|auxiliar|vendedor|motorista|seguranca|vigilante|limpeza|operador)\b/;
var CONTABIL = /\b(?:contador|contadora|contabilista|(?:analista|coordenador|coordenadora|gerente|supervisor|supervisora|especialista|consultor|consultora|auditor|auditora|tributarista)\s+(?:de\s+|em\s+)?(?:contabil|contabilidade)|contabil\s*(?:pleno|senior|pl|sr)|auditoria)\b/;
var CARGO = /\b(?:analista|gerente|coordenador|coordenadora|supervisor|supervisora|gestor|gestora|diretor|diretora|controller|especialista|consultor|consultora|tesoureiro|tesoureira|head|business partner|superintendente|executivo|executiva)\b/;
var GRANDE_POA = ["porto alegre", "canoas", "sao leopoldo", "novo hamburgo", "gravatai", "cachoeirinha", "alvorada", "viamao", "esteio",
  "sapucaia do sul", "guaiba", "eldorado do sul", "campo bom", "sapiranga", "estancia velha", "ivoti", "dois irmaos", "nova santa rita"];
var FORA = /(?:,|\s-|\/)\s*(?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RO|RR|SC|SP|SE|TO)\b|sao paulo|minas gerais|belo horizonte|rio de janeiro|curitiba|florianopolis|brasilia|salvador|recife|fortaleza|goiania/;

function sa(s) {   // minúsculas e sem acento
  return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

function nivelDoTitulo(titulo) {
  var t = sa(titulo);
  if (EXCLUIR.test(t) || CONTABIL.test(t) || !CARGO.test(t)) return null;
  if (ALTA.some(function (x) { return t.indexOf(x) >= 0; })) return "Alta";
  if (MEDIA.some(function (x) { return t.indexOf(x) >= 0; })) return "Média";
  return null;
}

function acharLocal(texto) {
  var t = sa(texto);
  for (var i = 0; i < GRANDE_POA.length; i++) if (t.indexOf(GRANDE_POA[i]) >= 0) return { local: GRANDE_POA[i].replace(/\b\w/g, function (c) { return c.toUpperCase(); }) + " - RS", fora: false };
  if (/\brs\b|rio grande do sul/.test(t)) return { local: "RS", fora: false };
  var original = String(texto || "");
  if (FORA.test(original) || FORA.test(t)) return { local: "Fora do RS", fora: true };
  if (/remot|home.?office/.test(t)) return { local: "Remoto", fora: false };
  return { local: "", fora: false };
}

function limpar(s) {
  return String(s || "").replace(/[“”"]/g, "").replace(/\s+/g, " ").replace(/^(?:nova vaga|vaga|alerta de vaga)\s*[:\-–]\s*/i, "").trim();
}

function nomeFonte(de) {
  var d = sa(de);
  if (d.indexOf("linkedin") >= 0) return "Alerta (LinkedIn)";
  if (d.indexOf("indeed") >= 0) return "Alerta (Indeed)";
  if (d.indexOf("glassdoor") >= 0) return "Alerta (Glassdoor)";
  if (d.indexOf("infojobs") >= 0) return "Alerta (Infojobs)";
  if (d.indexOf("catho") >= 0) return "Alerta (Catho)";
  if (d.indexOf("ineed") >= 0) return "Alerta (iNeed)";
  return "Alerta de e-mail";
}

/** Lê um e-mail (assunto, começo do corpo, remetente, data, link) e diz se é vaga, candidatura ou nada. */
function analisar(assunto, corpo, de, data, url) {
  var a = limpar(assunto), c = String(corpo || ""), sA = sa(a), fonte = nomeFonte(de), m;
  var iso = Utilities_fmt(data);

  // 1) candidatura enviada
  if ((m = /sua candidatura foi enviada (?:para|a|à|ao)\s+(.+)/i.exec(a))) {
    var emp = limpar(m[1]).replace(/[.!]+$/, "");
    var linhas = c.split(/\n+/).map(limpar).filter(Boolean), tit = "";
    for (var i = 0; i < linhas.length && i < 12; i++) if (CARGO.test(sa(linhas[i])) && linhas[i].length < 90 && sa(linhas[i]).indexOf("candidatura") < 0) { tit = linhas[i]; break; }
    return { tipo: "candidatura", titulo: tit || "(vaga em " + emp + ")", empresa: emp, fonte: fonte, data: iso, status: "Enviado", url: url, obs: "lida do e-mail de confirmação" };
  }
  // 2) perfil dos inscritos para a vaga de X (LinkedIn): vaga à qual você se candidatou
  if ((m = /perfil dos inscritos para a vaga de\s+(.+?)(?:\s+(?:na|em)\s+(.+))?$/i.exec(a))) {
    return { tipo: "candidatura", titulo: limpar(m[1]), empresa: limpar(m[2] || ""), fonte: fonte, data: iso, status: "Enviado", url: url, obs: "e-mail de perfil dos inscritos" };
  }
  // 3) retorno de processo seletivo
  if (/retorno do seu processo seletivo|atualizac[a-z]* (?:sobre|da|do) (?:sua candidatura|processo)|processo seletivo|sua candidatura (?:para|na|no)/.test(sA)) {
    var st = /infelizmente|nao seguiremos|nao foi selecionad|encerr|outro(?:s)? candidato/.test(sa(c + " " + a)) ? "Recusa" : (/entrevista/.test(sa(c + " " + a)) ? "Entrevista" : "Retorno");
    var mm = /\s[-–:]\s(.+)$/.exec(a) || /(?: para a vaga de| para| na| no)\s+(.+)$/i.exec(a);
    return { tipo: "candidatura", titulo: mm ? limpar(mm[1]) : a, empresa: "", fonte: fonte, data: iso, status: st, url: url, obs: "retorno de processo: " + a.slice(0, 80) };
  }
  // 4) alerta de vaga: "Cargo na Empresa", "Cargo - Empresa - Cidade" ou só o cargo
  var tit2 = a, emp2 = "", loc2 = "";
  if ((m = /^(.+?)\s+na\s+(.+?)(?:\s*[-–:]\s*e mais.*|\s*[-–]\s*.*)?$/i.exec(a))) { tit2 = limpar(m[1]); emp2 = limpar(m[2]); }
  else if ((m = /^(.+?)\s[-–]\s(.+?)\s[-–]\s(.+)$/.exec(a))) { tit2 = limpar(m[1]); emp2 = limpar(m[2]); loc2 = limpar(m[3]); }
  else if ((m = /:\s*(.+?)\s+(?:na|em)\s+(.+)$/i.exec(a))) { tit2 = limpar(m[1]); emp2 = limpar(m[2]); }
  var nv = nivelDoTitulo(tit2);
  if (!nv) return { tipo: "descartado", titulo: tit2, motivo: "título não combina com o seu perfil" };
  var L = acharLocal(a + " " + loc2 + " " + c.slice(0, 500));
  if (L.fora) return { tipo: "descartado", titulo: tit2, motivo: "fora do RS" };
  return { tipo: "vaga", titulo: tit2, empresa: emp2, local: L.local, fonte: fonte, data: iso, url: url, nivel: nv, obs: "triada só pelo assunto do e-mail" };
}

function Utilities_fmt(d) {
  try { return Utilities.formatDate(d, "America/Sao_Paulo", "yyyy-MM-dd"); } catch (e) { return new Date(d).toISOString().slice(0, 10); }
}

function responder(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/** O painel chama este endereço: .../exec?key=SUA_CHAVE&dias=14 */
function doGet(e) {
  var p = (e && e.parameter) || {};
  if (p.key !== CHAVE) return responder({ erro: "chave incorreta" });
  var dias = Math.max(1, Math.min(Number(p.dias) || 14, 60));
  var q = "newer_than:" + dias + "d (" + REMETENTES.map(function (r) { return "from:" + r; }).join(" OR ") + ")";
  var threads = GmailApp.search(q, 0, 120), vagas = [], cands = [], vistos = {}, lidos = 0;
  threads.forEach(function (t) {
    try {
      var msgs = t.getMessages(), m = msgs[0];
      lidos++;
      var r = analisar(t.getFirstMessageSubject(), String(m.getPlainBody() || "").slice(0, 1500), m.getFrom(), m.getDate(), t.getPermalink());
      var k = sa((r.titulo || "") + "|" + (r.empresa || "") + "|" + r.tipo);
      if (vistos[k]) return;
      vistos[k] = 1;
      if (r.tipo === "vaga") vagas.push(r);
      else if (r.tipo === "candidatura") cands.push(r);
    } catch (err) { /* ignora e-mail ilegível */ }
  });
  return responder({ tipo: "alertas", v: 1, gerado: new Date().toISOString(), lidos: lidos, vagas: vagas, candidaturas: cands });
}

if (typeof module !== "undefined") module.exports = { analisar: analisar, doGet: doGet, nivelDoTitulo: nivelDoTitulo, CHAVE: CHAVE };
