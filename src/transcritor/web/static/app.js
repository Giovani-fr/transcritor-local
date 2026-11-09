/* Interface do transcritor-local (sem build). Fala com a API por fetch e recebe eventos por SSE. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const mostrar = (el, sim) => { el.hidden = !sim; };

  const estado = {
    hardware: {}, modelo: { fase: "verificando" }, trabalho: null, linhas: [],
    enviando: null,        // {nome, pct} durante o envio do arquivo para o programa
    visualizando: null,    // item do histórico aberto pelo usuário
    dispensado: null,      // id do resultado que o usuário fechou ("Transcrever outro")
    historico: [], horarios: true,
  };
  let desenhadas = 0, idDesenhado = null, primeiroSnapshot = true;

  /* ---------- utilidades ---------- */
  function duracao(s) {
    s = Math.round(s || 0);
    if (s < 60) return s + " s";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h) return h + " h " + String(m).padStart(2, "0") + " min";
    return m + " min" + (s % 60 ? " " + (s % 60) + " s" : "");
  }
  function restante(s) {
    if (s == null) return "calculando o tempo restante…";
    if (s < 10) return "quase pronto…";
    if (s < 60) return "faltam cerca de " + (Math.ceil(s / 5) * 5) + " segundos";
    const m = Math.ceil(s / 60);
    if (m < 60) return "faltam cerca de " + m + " min";
    return "faltam cerca de " + Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0") + " min";
  }
  function dataBonita(txt) {
    const m = /^(\d{4})-(\d\d)-(\d\d) (\d\d:\d\d)/.exec(txt || "");
    return m ? m[3] + "/" + m[2] + "/" + m[1] + " " + m[4] : "";
  }
  function aviso(msg) {
    const t = $("#aviso-rapido");
    t.textContent = msg; t.hidden = false;
    clearTimeout(aviso.t); aviso.t = setTimeout(() => { t.hidden = true; }, 2500);
  }
  function mbTexto(mb) { return mb >= 1000 ? (mb / 1024).toFixed(1).replace(".", ",") + " GB" : Math.round(mb) + " MB"; }
  function barra(el, frac) {
    const pct = Math.max(0, Math.min(100, Math.round(frac * 100)));
    el.firstElementChild.style.width = pct + "%";
    el.setAttribute("aria-valuenow", pct);
    return pct;
  }

  /* ---------- tema ---------- */
  $("#btn-tema").addEventListener("click", () => {
    const html = document.documentElement;
    const atual = html.dataset.tema || (matchMedia("(prefers-color-scheme: dark)").matches ? "escuro" : "claro");
    const novo = atual === "escuro" ? "claro" : "escuro";
    html.dataset.tema = novo;
    try { localStorage.setItem("tema", novo); } catch (e) { /* sem armazenamento */ }
  });

  /* ---------- texto na tela ---------- */
  function linhaEl(t, texto, fundo) {
    const d = document.createElement("span");
    d.className = "l";
    const h = document.createElement("span"); h.className = "hora"; h.textContent = t;
    d.append(h);
    if (fundo) { const f = document.createElement("span"); f.className = "fundo"; f.textContent = "(fundo)"; d.append(f); }
    d.append(document.createTextNode(texto));
    return d;
  }
  function pintarTexto(el, texto, horarios) {
    el.replaceChildren();
    if (!texto.trim()) { el.innerHTML = '<span class="vazio">Nenhum texto.</span>'; return; }
    if (horarios) {
      for (const l of texto.split("\n")) {
        const m = /^\[([\d:]+)\](?: \(fundo\))? ?(.*)$/.exec(l);
        if (m) el.append(linhaEl(m[1], m[2], l.includes("(fundo)")));
      }
    } else {
      for (const p of texto.split(/\n\n+/)) { const e = document.createElement("p"); e.textContent = p; el.append(e); }
    }
  }
  function desenharAoVivo() {
    const el = $("#texto-vivo");
    const id = estado.trabalho && estado.trabalho.id;
    if (id !== idDesenhado || estado.linhas.length < desenhadas) { el.replaceChildren(); desenhadas = 0; idDesenhado = id; }
    const colado = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
    for (; desenhadas < estado.linhas.length; desenhadas++) {
      const l = estado.linhas[desenhadas];
      el.append(linhaEl(l.t, l.texto, l.fundo));
    }
    if (!desenhadas && !el.firstChild) el.innerHTML = '<span class="vazio">O texto vai aparecendo aqui…</span>';
    else if (desenhadas && el.querySelector(".vazio")) { el.querySelector(".vazio").remove(); }
    if (colado) el.scrollTop = el.scrollHeight;
  }

  /* ---------- telas ---------- */
  function renderModelo() {
    const m = estado.modelo, fase = m.fase;
    const tela = fase !== "pronto";
    mostrar($("#tela-modelo"), tela);
    mostrar($("#painel-arquivo"), !tela);
    mostrar($("#historico"), !tela);
    for (const f of ["verificando", "faltando", "baixando", "carregando"]) mostrar($("#modelo-" + f), fase === f);
    mostrar($("#modelo-erro-fatal"), fase === "erro");
    if (fase === "faltando") {
      $("#modelo-tamanho").textContent = mbTexto(m.tamanho_mb || 1500);
      const e = $("#modelo-erro"); e.textContent = m.erro || ""; mostrar(e, !!m.erro);
    }
    if (fase === "baixando") {
      const pct = barra($("#barra-modelo"), m.progresso || 0);
      $("#modelo-progresso").textContent = pct + "% · " + mbTexto(m.baixado_mb || 0) + " de " + mbTexto(m.tamanho_mb || 0);
    }
    if (fase === "erro") $("#modelo-erro-msg").textContent = m.erro || "";
  }
  function renderBanner() {
    const b = $("#banner-hw"), h = estado.hardware;
    if (!h || !h.mensagem) { b.hidden = true; return; }
    b.hidden = false;
    b.className = "aviso" + (h.aviso ? "" : " ok");
    b.textContent = (h.aviso ? "" : "⚡ ") + h.mensagem;
  }
  function etapaArquivo() {
    if (estado.visualizando) return "fim";
    if (estado.enviando) return "andamento";
    const t = estado.trabalho;
    if (t && ["preparando", "transcrevendo", "parando"].includes(t.fase)) return "andamento";
    if (t && t.id !== estado.dispensado) return "fim";
    return "inicio";
  }
  function renderArquivo() {
    if (estado.modelo.fase !== "pronto") return;
    const etapa = etapaArquivo();
    mostrar($("#arq-inicio"), etapa === "inicio");
    mostrar($("#arq-andamento"), etapa === "andamento");
    mostrar($("#arq-fim"), etapa === "fim");
    if (etapa === "andamento") renderAndamento();
    if (etapa === "fim") renderFim();
  }
  function renderAndamento() {
    const t = estado.trabalho, env = estado.enviando;
    const btn = $("#btn-parar");
    if (env) {
      $("#and-nome").textContent = env.nome;
      barra($("#barra-job"), env.pct);
      $("#and-status").textContent = "Enviando o arquivo… " + Math.round(env.pct * 100) + "%";
      $("#and-restante").textContent = "";
      btn.disabled = true;
      return;
    }
    $("#and-nome").textContent = t.nome;
    const ind = t.fase === "preparando";
    $("#barra-job").classList.toggle("indeterminada", ind);
    if (!ind) barra($("#barra-job"), t.progresso || 0);
    else $("#barra-job").firstElementChild.style.width = "";
    let status = "Transcrevendo… " + Math.round((t.progresso || 0) * 100) + "%";
    let resto = restante(t.restante_s);
    if (t.fase === "preparando") { status = "Preparando o áudio…"; resto = ""; }
    if (t.fase === "parando") { status = "Parando…"; resto = "termina o trecho atual"; }
    $("#and-status").textContent = status;
    $("#and-restante").textContent = resto + (t.duracao_s && t.fase !== "preparando" ? " · áudio de " + duracao(t.duracao_s) : "");
    btn.disabled = t.fase === "parando";
    desenharAoVivo();
  }
  async function renderFim() {
    const v = estado.visualizando;
    const t = v ? { id: v.id, nome: v.nome, fase: v.status === "concluido" ? "concluido" : v.status, erro: "", aviso: "" } : estado.trabalho;
    if (!t) return;
    const meta = estado.historico.find((h) => h.id === t.id) || {};
    $("#fim-nome").textContent = t.nome;
    const selo = $("#fim-selo");
    const rotulos = { concluido: "Pronto ✓", parado: "Parado", erro: "Não deu certo", interrompido: "Interrompido" };
    selo.textContent = rotulos[t.fase] || "";
    selo.className = "selo" + (t.fase === "erro" ? " erro" : t.fase === "concluido" ? "" : " parado");
    const partes = [];
    if (meta.criado) partes.push(dataBonita(meta.criado));
    if (meta.duracao_s) partes.push("áudio de " + duracao(meta.duracao_s));
    if (meta.tempo_s && t.fase === "concluido") partes.push("transcrito em " + duracao(meta.tempo_s));
    if (meta.modelo) partes.push("modelo " + meta.modelo);
    $("#fim-meta").textContent = partes.join(" · ");
    const av = $("#fim-aviso"), er = $("#fim-erro");
    av.textContent = t.aviso || ""; mostrar(av, !!t.aviso);
    er.textContent = t.erro || ""; mostrar(er, !!t.erro);
    const temTexto = !!(meta.linhas || (!v && estado.linhas.length));
    mostrar($("#fim-texto-bloco"), temTexto);
    if (temTexto) await carregarTextoFinal(t.id);
  }
  let tokenTexto = 0;
  async function carregarTextoFinal(id) {
    const meu = ++tokenTexto;
    try {
      const r = await fetch("/api/historico/" + encodeURIComponent(id) + "/texto?horarios=" + estado.horarios);
      if (!r.ok || meu !== tokenTexto) return;
      pintarTexto($("#texto-final"), await r.text(), estado.horarios);
    } catch (e) { /* a conexão cai e volta sozinha */ }
  }
  function idAtual() { return estado.visualizando ? estado.visualizando.id : (estado.trabalho && estado.trabalho.id); }

  function render() { renderModelo(); renderBanner(); renderArquivo(); }

  /* ---------- histórico ---------- */
  const STATUS = { concluido: "Pronto", parado: "Parado", erro: "Com erro", interrompido: "Interrompido" };
  async function carregarHistorico() {
    try {
      const r = await fetch("/api/historico");
      estado.historico = await r.json();
    } catch (e) { return; }
    const ul = $("#lista-historico"); ul.replaceChildren();
    for (const h of estado.historico) {
      const li = document.createElement("li");
      const b = document.createElement("button"); b.type = "button"; b.className = "item";
      const nome = document.createElement("strong"); nome.textContent = h.nome;
      const info = document.createElement("span"); info.className = "miudo";
      info.textContent = [dataBonita(h.criado), h.duracao_s ? duracao(h.duracao_s) : "", STATUS[h.status] || ""].filter(Boolean).join(" · ");
      b.append(nome, info);
      b.addEventListener("click", () => {
        if (etapaArquivo() === "andamento") { aviso("Espere a transcrição terminar ou clique em Parar."); return; }
        estado.visualizando = h; render(); window.scrollTo({ top: 0, behavior: "smooth" });
      });
      li.append(b); ul.append(li);
    }
    mostrar($("#historico-vazio"), estado.historico.length === 0);
    if (estado.trabalho || estado.visualizando) renderArquivo();
  }

  /* ---------- eventos do servidor ---------- */
  function conectar() {
    const es = new EventSource("/api/eventos");
    es.onopen = () => mostrar($("#conexao"), false);
    es.onerror = () => mostrar($("#conexao"), true);
    es.addEventListener("snapshot", (e) => {
      const d = JSON.parse(e.data);
      Object.assign(estado, { hardware: d.hardware, modelo: d.modelo, trabalho: d.trabalho, linhas: d.linhas });
      // ao abrir a página, começa na tela inicial; um resultado antigo fica no histórico
      if (primeiroSnapshot && d.trabalho && !["preparando", "transcrevendo", "parando"].includes(d.trabalho.fase)) estado.dispensado = d.trabalho.id;
      primeiroSnapshot = false;
      render(); carregarHistorico();
    });
    es.addEventListener("estado", (e) => {
      const d = JSON.parse(e.data);
      const antes = estado.trabalho;
      Object.assign(estado, { hardware: d.hardware, modelo: d.modelo, trabalho: d.trabalho });
      if (d.trabalho && (!antes || antes.id !== d.trabalho.id)) { estado.linhas = []; estado.enviando = null; estado.visualizando = null; }
      render();
    });
    es.addEventListener("linha", (e) => { estado.linhas.push(JSON.parse(e.data)); if (etapaArquivo() === "andamento") desenharAoVivo(); });
    es.addEventListener("historico", carregarHistorico);
  }

  /* ---------- ações ---------- */
  async function post(url) { try { await fetch(url, { method: "POST" }); } catch (e) { /* ver aviso de conexão */ } }
  $("#btn-baixar").addEventListener("click", () => post("/api/modelo/baixar"));
  $("#btn-tentar-modelo").addEventListener("click", () => post("/api/modelo/baixar"));
  $("#btn-parar").addEventListener("click", () => { $("#btn-parar").disabled = true; post("/api/parar"); });
  $("#btn-outro").addEventListener("click", () => {
    estado.dispensado = estado.trabalho ? estado.trabalho.id : null; estado.visualizando = null; estado.horarios = true;
    $("#chk-horarios").checked = true; render();
  });
  $("#chk-horarios").addEventListener("change", (e) => { estado.horarios = e.target.checked; carregarTextoFinal(idAtual()); });
  $("#btn-copiar").addEventListener("click", async () => {
    try {
      const r = await fetch("/api/historico/" + encodeURIComponent(idAtual()) + "/texto?horarios=" + estado.horarios);
      await navigator.clipboard.writeText(await r.text());
      aviso("Texto copiado!");
    } catch (e) { aviso("Não consegui copiar. Selecione o texto e use Ctrl+C."); }
  });
  $("#btn-baixar-txt").addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = "/api/historico/" + encodeURIComponent(idAtual()) + "/texto?baixar=true&horarios=" + estado.horarios;
    document.body.append(a); a.click(); a.remove();
  });
  $("#btn-pasta").addEventListener("click", () => post("/api/historico/" + encodeURIComponent(idAtual()) + "/abrir-pasta"));

  /* envio do arquivo (XHR para mostrar o progresso do envio) */
  function enviar(arquivo) {
    if (!arquivo) return;
    estado.enviando = { nome: arquivo.name, pct: 0 }; estado.dispensado = null; estado.visualizando = null; render();
    const dados = new FormData();
    dados.append("arquivo", arquivo);
    dados.append("idioma", $("#idioma").value);
    try { localStorage.setItem("idioma", $("#idioma").value); } catch (e) { /* sem armazenamento */ }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/arquivo");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) { estado.enviando.pct = e.loaded / e.total; renderAndamento(); } };
    const falhou = (msg) => {
      estado.enviando = null; render();
      const er = $("#fim-erro");
      estado.visualizando = null;
      estado.trabalho = { id: "_erro", nome: arquivo.name, fase: "erro", erro: msg, aviso: "", progresso: 0 };
      estado.dispensado = null; render();
      er.textContent = msg;
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) { estado.enviando = null; render(); return; }
      let msg = "Não consegui enviar o arquivo. Tente de novo.";
      try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) { /* mantém a mensagem padrão */ }
      falhou(msg);
    };
    xhr.onerror = () => falhou("Não consegui enviar o arquivo. Confira se o programa ainda está aberto e tente de novo.");
    xhr.send(dados);
  }
  const entrada = $("#entrada-arquivo"), zona = $("#zona");
  $("#btn-escolher").addEventListener("click", () => entrada.click());
  entrada.addEventListener("change", () => { enviar(entrada.files[0]); entrada.value = ""; });
  for (const ev of ["dragenter", "dragover"]) zona.addEventListener(ev, (e) => { e.preventDefault(); zona.classList.add("sobre"); });
  for (const ev of ["dragleave", "drop"]) zona.addEventListener(ev, (e) => { e.preventDefault(); zona.classList.remove("sobre"); });
  zona.addEventListener("drop", (e) => enviar(e.dataTransfer.files[0]));
  // soltar o arquivo fora da zona não deve abrir o arquivo no navegador
  for (const ev of ["dragover", "drop"]) window.addEventListener(ev, (e) => e.preventDefault());

  try { const i = localStorage.getItem("idioma"); if (i) $("#idioma").value = i; } catch (e) { /* ok */ }
  conectar();
})();
