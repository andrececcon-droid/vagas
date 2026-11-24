/**
 * ROTINA DE ALERTAS DE VAGAS v2 — roda na SUA conta Google, sem custo e sem copiar e colar.
 * Lê o CORPO dos e-mails de alerta (LinkedIn, Indeed, Glassdoor, Infojobs, Catho, iNeed), tira de cada e-mail
 * TODAS as vagas com o link direto da vaga, separa o que combina com você e as candidaturas, e entrega ao painel.
 * Só lê. Não envia, não apaga e não altera nenhum e-mail.
 */
var CHAVE = "ESbi_HFN94isChnGu92Lqw";   // senha da rotina: o painel precisa da mesma chave. Não mostre a ninguém.

var REMETENTES = ["linkedin.com", "indeed.com", "glassdoor.com", "infojobs.com.br", "catho.com.br", "ineed.com.br", "ineedtrabalho.com.br"];
var IGNORAR_REMETENTES = /newsletters-noreply@linkedin\.com|messages-noreply@linkedin\.com|invitations@linkedin\.com/i;
var IGNORAR_ASSUNTO = /vagas PJ|novas vagas|notícia|newsletter|convite|conexão|aniversário|quem viu seu perfil/i;

var ALTA = ["gerente administrativo financeiro", "gerente administrativo e financeiro", "gerente financeiro", "gerente de controladoria",
  "controller", "controladoria", "coordenador financeiro", "coordenador administrativo", "gestor financeiro", "gestor administrativo",
  "fp&a", "planejamento financeiro", "analista financeiro", "analista de planejamento", "tesouraria", "administrativo financeiro",
  "administrativo e financeiro", "financeiro e administrativo", "gerente de financas", "coordenador de financas", "analista de financas",
  "superintendente financeiro", "head de financas", "gerente de planejamento", "analista de orcamento"];
var MEDIA = ["financeiro", "financeira", "administrativo", "contas a pagar", "contas a receber", "orcamento", "faturamento", "custos",
  "diretor financeiro", "tesoureiro", "business partner financeiro", "financas"];
var EXCLUIR = /\b(?:estagio|estagiario|aprendiz|trainee|junior|jr|assistente|auxiliar|vendedor|motorista|seguranca|vigilante|limpeza|operador)\b/;
var CONTABIL = /\b(?:contador|contadora|contabilista|(?:analista|coordenador|coordenadora|gerente|supervisor|supervisora|especialista|consultor|consultora|auditor|auditora|tributarista)\s+(?:de\s+|em\s+)?(?:contabil|contabilidade)|contabil\s*(?:pleno|senior|pl|sr)|auditoria)\b/;
var CARGO = /\b(?:analista|gerente|coordenador|coordenadora|supervisor|supervisora|gestor|gestora|diretor|diretora|controller|especialista|consultor|consultora|tesoureiro|tesoureira|head|business partner|superintendente|executivo|executiva)\b/;
var GRANDE_POA = ["porto alegre", "canoas", "sao leopoldo", "novo hamburgo", "gravatai", "cachoeirinha", "alvorada", "viamao", "esteio",
  "sapucaia do sul", "guaiba", "eldorado do sul", "campo bom", "sapiranga", "estancia velha", "ivoti", "dois irmaos", "nova santa rita"];
var FORA = /(?:,|\s-|\/)\s*(?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RO|RR|SC|SP|SE|TO)\b|sao paulo|minas gerais|belo horizonte|rio de janeiro|curitiba|florianopolis|brasilia|salvador|recife|fortaleza|goiania/;

function sa(s) { return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
function limpar(s) { return String(s || "").replace(/[“”"]/g, "").replace(/\s+/g, " ").replace(/^[\s|•\-–]+|[\s|•\-–]+$/g, "").trim(); }

/** Diz se o título serve (e por que não, se não servir). */
function triarTitulo(titulo) {
  var t = sa(titulo);
  if (!CARGO.test(t)) return { nivel: null, motivo: "não parece um cargo" };
  if (CONTABIL.test(t)) return { nivel: null, motivo: "contabilidade pura (contador / contábil / auditoria)" };
  if (EXCLUIR.test(t)) return { nivel: null, motivo: "cargo abaixo do seu nível (assistente, júnior, estágio...)" };
  if (ALTA.some(function (x) { return t.indexOf(x) >= 0; })) return { nivel: "Alta" };
  if (MEDIA.some(function (x) { return t.indexOf(x) >= 0; })) return { nivel: "Média" };
  return { nivel: null, motivo: "cargo sem relação com finanças/administração" };
}
function nivelDoTitulo(t) { return triarTitulo(t).nivel; }

function acharLocal(texto) {
  var t = sa(texto), original = String(texto || "");
  for (var i = 0; i < GRANDE_POA.length; i++) if (t.indexOf(GRANDE_POA[i]) >= 0) return { local: GRANDE_POA[i].replace(/\b\w/g, function (c) { return c.toUpperCase(); }) + " - RS", fora: false };
  if (/\brs\b|rio grande do sul/.test(t)) return { local: "RS", fora: false };
  if (FORA.test(original) || FORA.test(t)) return { local: "Fora do RS", fora: true };
  if (/remot|home.?office/.test(t)) return { local: "Remoto", fora: false };
  return { local: "", fora: false };
}

function nomeFonte(de) {
  var d = sa(de);
  var m = { linkedin: "LinkedIn", indeed: "Indeed", glassdoor: "Glassdoor", infojobs: "Infojobs", catho: "Catho", ineed: "iNeed" };
  for (var k in m) if (d.indexOf(k) >= 0) return "Alerta (" + m[k] + ")";
  return "Alerta de e-mail";
}

function decodificar(s) {
  return String(s || "").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#(\d+);/g, function (_, n) { return String.fromCharCode(+n); });
}

/** HTML do e-mail -> texto com links no formato [texto](endereço); quebras de bloco viram " || ". */
function htmlParaMd(html) {
  var h = String(html || "").replace(/<(script|style)[\s\S]*?<\/\1>/gi, " ");
  h = h.replace(/<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, function (_, href, inner) {
    var t = inner.replace(/<\/(td|tr|div|p|li|h\d|table)>|<br\s*\/?>/gi, " || ").replace(/<[^>]+>/g, " ");
    return "[" + decodificar(t).replace(/\s+/g, " ") + "](" + decodificar(href) + ")";
  });
  h = h.replace(/<\/(td|tr|div|p|li|h\d|table)>|<br\s*\/?>/gi, "\n").replace(/<[^>]+>/g, " ");
  return decodificar(h).replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n");
}

function jobIdLinkedIn(txt) { var m = /linkedin\.com\/(?:comm\/)?jobs\/view\/(\d+)/.exec(txt); return m ? m[1] : ""; }

/** Extrai TODAS as vagas de um e-mail de alerta. Devolve [{titulo, empresa, local, url}]. */
function vagasDoCorpo(de, plain, md) {
  var d = sa(de), out = [], m;
  if (d.indexOf("linkedin") >= 0) {
    var blocos = String(plain || "").split(/\n-{20,}\n/);
    blocos.forEach(function (b) {
      var id = jobIdLinkedIn(b); if (!id) return;
      var linhas = b.split("\n").map(limpar).filter(function (l) {
        return l && !/^(seu alerta|visualizar vaga|candidate-se|esta empresa|ver todas|gerencie|http|\d+ (ex-alunos|conex)|recrutando|vaga com|você talvez)/i.test(l) && l.indexOf("linkedin.com") < 0;
      });
      if (linhas.length >= 2) out.push({ titulo: linhas[0], empresa: linhas[1], local: linhas[2] || "", url: "https://www.linkedin.com/jobs/view/" + id + "/" });
    });
  } else if (d.indexOf("indeed") >= 0) {
    var re = /https?:\/\/[^\s)\]]*indeed\.com\/(?:rc\/clk|pagead\/clk|viewjob)[^\s)\]]*/g, ult = 0, txt = String(plain || "");
    while ((m = re.exec(txt))) {
      var jk = /[?&]jk=([0-9a-f]+)/.exec(m[0]); if (!jk) { ult = re.lastIndex; continue; }
      var linhas2 = txt.slice(ult, m.index).split("\n").map(limpar).filter(function (l) {
        return l && !/^(alerta de vagas|\d+ vagas? abert|vagas \d|ver os resultados|recém publicada|ativamente|novo$)/i.test(l) && l.indexOf("http") < 0;
      });
      ult = re.lastIndex;
      if (!linhas2.length) continue;
      var cl = (linhas2[1] || "").split(/\s+-\s+/);
      out.push({ titulo: linhas2[0], empresa: limpar(cl[0]), local: limpar(cl.slice(1).join(" - ")), url: "https://br.indeed.com/viewjob?jk=" + jk[1] });
    }
  } else if (d.indexOf("glassdoor") >= 0) {
    var rg = /\[([^\]]+)\]\((https:\/\/www\.glassdoor\.com\.br\/partner\/jobListing\.htm[^)]*)\)/g, txt2 = String(md || plain || "");
    while ((m = rg.exec(txt2))) {
      var tok = m[1].split("||").map(function (t) { return t.replace(/^[\s|]+|[\s|]+$/g, ""); }).filter(function (t) { return /[A-Za-zÀ-ú]/.test(t); });
      if (tok.length < 2) continue;
      var id = /jobListingId=(\d+)/.exec(m[2]);
      out.push({ titulo: tok[1], empresa: tok[0].replace(/\s*\d[.,]\d\s*★.*$/, ""), local: tok[2] || "", url: id ? "https://www.glassdoor.com.br/job-listing/j.htm?jl=" + id[1] : m[2] });
    }
  } else {
    var ra = /\[([^\]]{8,140})\]\((https?:\/\/[^)\s]+)\)/g, txt3 = String(md || plain || "");
    while ((m = ra.exec(txt3))) {
      if (/unsubscribe|descadastr|cancelar|privacidade|preferenc|premium|facebook|instagram/i.test(m[2] + m[1])) continue;
      var t1 = limpar(m[1].split("||")[0]);
      if (nivelDoTitulo(t1)) out.push({ titulo: t1, empresa: "", local: "", url: m[2] });
    }
  }
  return out;
}

/** Lê um e-mail inteiro e devolve { vagas:[...], candidatura:{...}|null, descartes:[...] }. */
function analisar(assunto, plain, html, de, data, emailUrl) {
  var a = limpar(assunto), sA = sa(a), fonte = nomeFonte(de), iso = dataIso(data), md = htmlParaMd(html), corpo = String(plain || "") + "\n" + md, m;
  var res = { vagas: [], candidatura: null, descartes: [] };

  // candidaturas e retornos
  if ((m = /candidatura foi enviada (?:para|a|à|ao)\s+(?:a empresa\s+)?(.+)$/i.exec(a))) {
    var emp = limpar(m[1]).replace(/[.!]+$/, ""), tit = "", url = "";
    var t = /inscrever para a vaga de\s+([^\[!|\n]+?)\s*(?:\[|!|\||\n|$)/i.exec(corpo); if (t) tit = limpar(t[1]);
    var u = /(https?:\/\/www\.infojobs\.com\.br\/vaga-de-[^)\]\s]+(?:\s[^)\]\s?]+)*)/i.exec(corpo); if (u) url = u[1].split("?")[0].replace(/ /g, "%20");
    var id = jobIdLinkedIn(corpo); if (id) url = "https://www.linkedin.com/jobs/view/" + id + "/";
    if (/^a empresa$/i.test(emp) || !emp) emp = "";
    res.candidatura = { titulo: tit || "(vaga " + (emp ? "na " + emp : "sem título no e-mail") + ")", empresa: emp, fonte: fonte, data: iso, status: "Enviado", url: url || emailUrl, email_url: emailUrl, obs: "e-mail de confirmação" };
    return res;
  }
  if ((m = /^sua candidatura a\s+(.+?)\s+na\s+(.+)$/i.exec(a))) {
    res.candidatura = { titulo: limpar(m[1]), empresa: limpar(m[2]), fonte: fonte, data: iso, status: "Retorno", url: emailUrl, email_url: emailUrl, obs: "atualização da candidatura: abra o e-mail para ver o resultado" };
    return res;
  }
  if ((m = /sua candidatura foi (?:vista|visualizada) (?:pel[ao]s?|por)\s+(.+)$/i.exec(a))) {
    res.candidatura = { titulo: "(vaga na " + limpar(m[1]) + ")", empresa: limpar(m[1]), fonte: fonte, data: iso, status: "Retorno", url: emailUrl, email_url: emailUrl, obs: "o recrutador viu sua candidatura" };
    return res;
  }
  if ((m = /perfil dos inscritos para a vaga de\s+(.+?)(?:\s+(?:na|em)\s+(.+))?$/i.exec(a))) {
    res.candidatura = { titulo: limpar(m[1]), empresa: limpar(m[2] || ""), fonte: fonte, data: iso, status: "Enviado", url: emailUrl, email_url: emailUrl, obs: "e-mail de perfil dos inscritos" };
    return res;
  }
  if (/retorno do seu processo seletivo|atualiza[a-z]* (?:sobre|da|do) (?:sua candidatura|processo)|processo seletivo/.test(sA) && !/vaga de|na empresa/.test(sA)) {
    var st = /infelizmente|nao seguiremos|nao foi selecionad|outros? candidatos?/.test(sa(corpo + " " + a)) ? "Recusa" : (/entrevista/.test(sa(corpo + " " + a)) ? "Entrevista" : "Retorno");
    var mm = /\s[-–:]\s(.+)$/.exec(a);
    res.candidatura = { titulo: mm ? limpar(mm[1]) : a, empresa: "", fonte: fonte, data: iso, status: st, url: emailUrl, email_url: emailUrl, obs: "retorno de processo: " + a.slice(0, 80) };
    return res;
  }

  // alertas de vaga: primeiro o corpo (várias vagas, link direto)
  var lista = vagasDoCorpo(de, plain, md);
  if (!lista.length) {  // sem corpo legível: usa o assunto e o link do e-mail
    var tit2 = a, emp2 = "";
    if ((m = /^(?:vaga de\s+)?(.+?)\s+na empresa\s+(.+?)(?:\s+-\s+recrutando.*)?$/i.exec(a))) { tit2 = limpar(m[1]); emp2 = limpar(m[2]); }
    else if ((m = /cargo de\s+(.+?)\s+da empresa\s+(.+?)(?:\s+-\s+.*)?$/i.exec(a))) { tit2 = limpar(m[1]); emp2 = limpar(m[2]); }
    else if ((m = /à vaga de\s+(.+?)\s+na\s+(.+)$/i.exec(a))) { tit2 = limpar(m[1]); emp2 = limpar(m[2]); }
    else if ((m = /^(.+?)\s+em\s+(.+?)\.\s+\d+ vagas?/i.exec(a))) { tit2 = limpar(m[1]); emp2 = limpar(m[2]); }
    lista = [{ titulo: tit2, empresa: emp2, local: "", url: emailUrl, doEmail: true }];
  }
  lista.forEach(function (v) {
    var tr = triarTitulo(v.titulo);
    if (!tr.nivel) { res.descartes.push({ titulo: v.titulo, empresa: v.empresa, motivo: tr.motivo, fonte: fonte, url: v.url, email_url: emailUrl }); return; }
    var L = acharLocal(v.titulo + " " + v.local + " " + a);
    if (L.fora) { res.descartes.push({ titulo: v.titulo, empresa: v.empresa, motivo: "fora do RS (" + v.local + ")", fonte: fonte, url: v.url, email_url: emailUrl }); return; }
    res.vagas.push({ titulo: v.titulo, empresa: v.empresa, local: v.local || L.local, fonte: fonte, data: iso, url: v.url, email_url: emailUrl, nivel: tr.nivel,
      obs: v.doEmail ? "só o assunto do e-mail foi lido (abra o e-mail para ver a vaga)" : "vaga lida do corpo do e-mail, com link direto" });
  });
  return res;
}

function dataIso(d) {
  try { return Utilities.formatDate(new Date(d), "America/Sao_Paulo", "yyyy-MM-dd"); } catch (e) { return new Date(d).toISOString().slice(0, 10); }
}
function responder(obj) { return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON); }

var ORDEM = { Entrevista: 4, Oferta: 5, Retorno: 3, Recusa: 3, Enviado: 1 };

/** O painel chama este endereço: .../exec?key=SUA_CHAVE&dias=14 */
function doGet(e) {
  var p = (e && e.parameter) || {};
  if (p.key !== CHAVE) return responder({ erro: "chave incorreta" });
  var dias = Math.max(1, Math.min(Number(p.dias) || 14, 60));
  var q = "newer_than:" + dias + "d (" + REMETENTES.map(function (r) { return "from:" + r; }).join(" OR ") + ") -from:newsletters-noreply@linkedin.com";
  var threads = GmailApp.search(q, 0, 100), conta = "";
  try { conta = Session.getEffectiveUser().getEmail(); } catch (err) { conta = ""; }
  var vagas = [], cands = {}, descartes = [], vistos = {}, lidos = 0, ignorados = 0, porFonte = {}, emails = [];
  threads.forEach(function (t) {
    try {
      var m = t.getMessages()[0], de = m.getFrom(), assunto = t.getFirstMessageSubject();
      if (IGNORAR_REMETENTES.test(de) || IGNORAR_ASSUNTO.test(assunto)) { ignorados++; return; }
      lidos++;
      var link = "https://mail.google.com/mail/" + (conta ? "?authuser=" + encodeURIComponent(conta) : "") + "#all/" + t.getId();
      var r = analisar(assunto, m.getPlainBody(), m.getBody(), de, m.getDate(), link);
      var f = nomeFonte(de); porFonte[f] = (porFonte[f] || 0) + 1;
      emails.push({ assunto: assunto.slice(0, 90), fonte: f, vagas: r.vagas.length, descartes: r.descartes.length, candidatura: !!r.candidatura, email_url: link });
      r.vagas.forEach(function (v) { var k = v.url; if (!vistos[k]) { vistos[k] = 1; vagas.push(v); } });
      r.descartes.forEach(function (d) { if (descartes.length < 300) descartes.push(d); });
      if (r.candidatura) {
        var c = r.candidatura, k2 = sa(c.empresa || c.titulo), ant = cands[k2];
        if (!ant) cands[k2] = c;
        else {
          if ((ORDEM[c.status] || 0) > (ORDEM[ant.status] || 0)) { ant.status = c.status; ant.obs = c.obs; }
          if (/^\(vaga/.test(ant.titulo) && !/^\(vaga/.test(c.titulo)) { ant.titulo = c.titulo; ant.url = c.url; }
        }
      }
    } catch (err) { /* ignora e-mail ilegível */ }
  });
  var candidaturas = Object.keys(cands).map(function (k) { return cands[k]; });
  return responder({ tipo: "alertas", v: 2, gerado: new Date().toISOString(), lidos: lidos, vagas: vagas, candidaturas: candidaturas,
    auditoria: { lidos: lidos, ignorados: ignorados, por_fonte: porFonte, vagas_extraidas: vagas.length, descartes: descartes, emails: emails.slice(0, 80), dias: dias } });
}

if (typeof module !== "undefined") module.exports = { analisar: analisar, doGet: doGet, vagasDoCorpo: vagasDoCorpo, triarTitulo: triarTitulo, htmlParaMd: htmlParaMd, CHAVE: CHAVE };
