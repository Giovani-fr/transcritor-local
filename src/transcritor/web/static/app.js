/* Interface do transcritor-local (sem build). Fala com a API por fetch e recebe eventos por SSE. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const mostrar = (el, sim) => { el.hidden = !sim; };
  const ATIVAS = ["preparando", "baixando", "transcrevendo", "parando"];

  const estado = {
    hardware: {}, modelo: { fase: "verificando" }, trabalho: null, linhas: [],
    aba: "arquivo",
    enviando: null,        // {nome, pct} durante o envio do arquivo para o programa
    visualizando: null,    // item do histórico aberto pelo usuário
    dispensado: null,      // id do resultado que o usuário fechou ("Fazer outra transcrição")
    historico: [], horarios: true,
    video: null,           // vídeo consultado na aba YouTube
    hwAberto: false,
  };
  let desenhadas = 0, idDesenhado = null, primeiroSnapshot = true;
  try { estado.hwAberto = !localStorage.getItem("hwVisto"); } catch (e) { estado.hwAberto = true; }

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
  const IDIOMAS = { pt: "português", "pt-BR": "português", en: "inglês", es: "espanhol", fr: "francês", de: "alemão", it: "italiano" };
  const nomeIdioma = (c) => IDIOMAS[c] || IDIOMAS[(c || "").split("-")[0]] || c;
  const MODOS = { rapido: "modo Rápido", preciso: "modo Preciso" };
  function el(tag, props, ...filhos) {
    const e = document.createElement(tag);
    Object.assign(e, props || {});
    e.append(...filhos);
    return e;
  }
  async function copiar(texto, ok) {
    try { await navigator.clipboard.writeText(texto); aviso(ok || "Copiado!"); }
    catch (e) { aviso("Não consegui copiar. Selecione o texto e use Ctrl+C."); }
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
    const d = el("span", { className: "l" }, el("span", { className: "hora", textContent: t }));
    if (fundo) d.append(el("span", { className: "fundo", textContent: "(fundo)" }));
    d.append(document.createTextNode(texto));
    return d;
  }
  function pintarTexto(alvo, texto, horarios) {
    alvo.replaceChildren();
    if (!texto.trim()) { alvo.append(el("span", { className: "vazio", textContent: "Nenhum texto." })); return; }
    if (horarios) {
      for (const l of texto.split("\n")) {
        const m = /^\[([\d:]+)\](?: \(fundo\))? ?(.*)$/.exec(l);
        if (m) alvo.append(linhaEl(m[1], m[2], l.includes("(fundo)")));
      }
    } else {
      for (const p of texto.split(/\n\n+/)) alvo.append(el("p", { textContent: p }));
    }
  }
  function desenharAoVivo() {
    const alvo = $("#texto-vivo");
    const id = estado.trabalho && estado.trabalho.id;
    if (id !== idDesenhado || estado.linhas.length < desenhadas) { alvo.replaceChildren(); desenhadas = 0; idDesenhado = id; }
    const colado = alvo.scrollTop + alvo.clientHeight >= alvo.scrollHeight - 40;
    const vazio = alvo.querySelector(".vazio");
    if (estado.linhas.length && vazio) vazio.remove();
    for (; desenhadas < estado.linhas.length; desenhadas++) {
      const l = estado.linhas[desenhadas];
      alvo.append(linhaEl(l.t, l.texto, l.fundo));
    }
    if (!estado.linhas.length && !alvo.firstChild) alvo.append(el("span", { className: "vazio", textContent: "O texto vai aparecendo aqui…" }));
    if (colado) alvo.scrollTop = alvo.scrollHeight;
  }

  /* ---------- ajuda para atualizar o yt-dlp ---------- */
  function criarAjuda(ajuda) {
    const cmd = el("code", { textContent: ajuda.comando });
    const btn = el("button", { className: "btn btn-sec", type: "button", textContent: "Copiar comando" });
    btn.addEventListener("click", () => copiar(ajuda.comando, "Comando copiado!"));
    return el("div", { className: "ajuda" },
      el("p", {}, el("strong", { textContent: "Como atualizar o yt-dlp" }),
        document.createTextNode(" — o programa que baixa o áudio" + (ajuda.versao ? " (versão instalada: " + ajuda.versao + ")" : "") +
          ". Atualizar costuma resolver.")),
      el("ol", {},
        el("li", { textContent: "Feche a janela preta do programa." }),
        el("li", {}, document.createTextNode("Aperte a tecla Windows, digite "), el("strong", { textContent: "cmd" }), document.createTextNode(" e tecle Enter.")),
        el("li", { textContent: "Cole o comando abaixo e tecle Enter:" })),
      el("div", { className: "comando" }, cmd, btn),
      el("p", {}, document.createTextNode("Depois abra o "), el("strong", { textContent: "iniciar.bat" }), document.createTextNode(" de novo e tente outra vez.")));
  }

  /* ---------- telas ---------- */
  function renderModelo() {
    const m = estado.modelo, fase = m.fase;
    const tela = fase !== "pronto";
    mostrar($("#tela-modelo"), tela);
    mostrar($("#painel"), !tela);
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
    $("#hw-msg").textContent = (h.aviso ? "" : "⚡ ") + h.mensagem;
    const tem = !!h.detalhe, aberto = tem && estado.hwAberto;
    mostrar($("#hw-mais"), tem);
    $("#hw-mais").textContent = aberto ? "Mostrar menos" : "Saiba mais";
    $("#hw-mais").setAttribute("aria-expanded", String(aberto));
    $("#hw-detalhe").textContent = h.detalhe || "";
    mostrar($("#hw-detalhe"), aberto);
    if (tem) { try { localStorage.setItem("hwVisto", "1"); } catch (e) { /* ok */ } }   // nas próximas vezes abre recolhido
  }
  $("#hw-mais").addEventListener("click", () => { estado.hwAberto = !estado.hwAberto; renderBanner(); });

  function renderAbas() {
    for (const b of document.querySelectorAll(".aba[data-aba]")) {
      const ativa = b.dataset.aba === estado.aba;
      b.classList.toggle("ativa", ativa);
      if (ativa) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    }
  }
  function trabalhoAtivo() { return !!(estado.enviando || (estado.trabalho && ATIVAS.includes(estado.trabalho.fase))); }
  for (const b of document.querySelectorAll(".aba[data-aba]")) {
    b.addEventListener("click", () => {
      if (trabalhoAtivo()) { aviso("Tem uma transcrição em andamento. Espere terminar ou clique em Parar."); return; }
      estado.aba = b.dataset.aba; estado.visualizando = null; estado.dispensado = estado.trabalho ? estado.trabalho.id : null;
      render();
    });
  }

  function etapaPainel() {
    if (estado.visualizando) return "fim";
    if (estado.enviando) return "andamento";
    const t = estado.trabalho;
    if (t && ATIVAS.includes(t.fase)) return "andamento";
    if (t && t.id !== estado.dispensado) return "fim";
    return "inicio";
  }
  function renderPainel() {
    if (estado.modelo.fase !== "pronto") return;
    const etapa = etapaPainel();
    mostrar($("#inicio-arquivo"), etapa === "inicio" && estado.aba === "arquivo");
    mostrar($("#inicio-youtube"), etapa === "inicio" && estado.aba === "youtube");
    mostrar($("#arq-andamento"), etapa === "andamento");
    mostrar($("#arq-fim"), etapa === "fim");
    if (etapa === "andamento") renderAndamento();
    if (etapa === "fim") renderFim();
  }
  function renderAndamento() {
    const t = estado.trabalho, env = estado.enviando;
    const btn = $("#btn-parar");
    if (env) {
      $("#and-tipo").textContent = "Arquivo";
      $("#and-nome").textContent = env.nome;
      $("#barra-job").classList.remove("indeterminada");
      barra($("#barra-job"), env.pct);
      $("#and-status").textContent = "Enviando o arquivo… " + Math.round(env.pct * 100) + "%";
      $("#and-restante").textContent = "";
      btn.disabled = true;
      return;
    }
    const yt = t.tipo === "youtube";
    $("#and-tipo").textContent = yt ? "YouTube · " + MODOS[t.modo] : "Arquivo";
    $("#and-nome").textContent = t.nome;
    const ind = t.fase === "preparando", bj = $("#barra-job"), enchimento = bj.firstElementChild;
    const eraIndeterminada = bj.classList.contains("indeterminada");
    bj.classList.toggle("indeterminada", ind);
    if (ind) enchimento.style.width = "";
    else if (eraIndeterminada) {           // sai da barra "indeterminada" sem animar de 35% para 0%
      enchimento.style.transition = "none"; barra(bj, t.progresso || 0); void enchimento.offsetWidth; enchimento.style.transition = "";
    } else barra(bj, t.progresso || 0);
    const pct = Math.round((t.progresso || 0) * 100);
    let status = "Transcrevendo… " + pct + "%", resto = restante(t.restante_s);
    if (t.fase === "baixando") status = "Baixando o áudio do vídeo… " + pct + "%";
    if (t.fase === "preparando") { status = yt && t.modo === "rapido" ? "Buscando as legendas…" : "Preparando o áudio…"; resto = ""; }
    if (t.fase === "parando") { status = "Parando…"; resto = "termina o trecho atual"; }
    $("#and-status").textContent = status;
    const extra = t.duracao_s && t.fase !== "preparando" ? " · " + (yt ? "vídeo" : "áudio") + " de " + duracao(t.duracao_s) : "";
    $("#and-restante").textContent = resto + extra;
    btn.disabled = t.fase === "parando";
    desenharAoVivo();
  }
  async function renderFim() {
    const v = estado.visualizando;
    const t = v ? { id: v.id, nome: v.nome, fase: v.status, erro: "", aviso: "", tipo: v.tipo, url: v.url, modo: v.modo,
                    canal: v.canal, legenda: v.legenda, audio_guardado: v.audio_guardado, codigo_erro: "", ajuda: null }
                : estado.trabalho;
    if (!t) return;
    const meta = estado.historico.find((h) => h.id === t.id) || {};
    const yt = t.tipo === "youtube";
    $("#fim-nome").textContent = t.nome;
    const selo = $("#fim-selo");
    const rotulos = { concluido: "Pronto ✓", parado: "Parado", erro: "Não deu certo", interrompido: "Interrompido" };
    selo.textContent = rotulos[t.fase] || "";
    selo.className = "selo" + (t.fase === "erro" ? " erro" : t.fase === "concluido" ? "" : " parado");
    const partes = [];
    if (meta.criado) partes.push(dataBonita(meta.criado));
    if (yt && (t.canal || meta.canal)) partes.push(t.canal || meta.canal);
    if (yt) {
      const m = t.modo || meta.modo, leg = t.legenda || meta.legenda;
      partes.push(MODOS[m] + (m === "rapido" && leg ? " · legenda em " + nomeIdioma(leg.split(" ")[0]) + (leg.includes("automática") ? " (automática)" : "") : ""));
    }
    if (meta.duracao_s) partes.push((yt ? "vídeo" : "áudio") + " de " + duracao(meta.duracao_s));
    if (meta.tempo_s && t.fase === "concluido" && !(yt && (t.modo || meta.modo) === "rapido")) partes.push("transcrito em " + duracao(meta.tempo_s));
    if (meta.modelo && !(yt && (t.modo || meta.modo) === "rapido")) partes.push("modelo " + meta.modelo.replace(/-/g, "‑"));
    if (t.audio_guardado || meta.audio_guardado) partes.push("áudio guardado na pasta");
    $("#fim-meta").textContent = partes.join(" · ");
    const link = $("#fim-link");
    link.hidden = !(yt && (t.url || meta.url));
    if (!link.hidden) link.href = t.url || meta.url;
    const av = $("#fim-aviso"), er = $("#fim-erro");
    av.textContent = t.aviso || ""; mostrar(av, !!t.aviso);
    er.textContent = t.erro || ""; mostrar(er, !!t.erro);
    const ajuda = $("#fim-ajuda");
    ajuda.replaceChildren();
    if (t.ajuda && t.ajuda.tipo === "atualizar_ytdlp") ajuda.append(criarAjuda(t.ajuda));
    mostrar(ajuda, ajuda.childElementCount > 0);
    mostrar($("#fim-acao-preciso"), t.codigo_erro === "sem_legenda" && !!t.url);
    const temTexto = !!(meta.linhas || (!v && estado.linhas.length));
    mostrar($("#fim-texto-bloco"), temTexto);
    $("#btn-pasta").hidden = !temTexto;
    $("#btn-outro").textContent = yt ? "Transcrever outro vídeo" : "Transcrever outro arquivo";
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

  function render() { renderModelo(); renderBanner(); renderAbas(); renderPainel(); }

  /* ---------- histórico ---------- */
  const STATUS = { concluido: "Pronto", parado: "Parado", erro: "Com erro", interrompido: "Interrompido" };
  async function carregarHistorico() {
    try {
      const r = await fetch("/api/historico");
      estado.historico = await r.json();
    } catch (e) { return; }
    const ul = $("#lista-historico"); ul.replaceChildren();
    for (const h of estado.historico) {
      const info = [dataBonita(h.criado), h.tipo === "youtube" ? "YouTube" + (h.modo ? " " + (h.modo === "rapido" ? "Rápido" : "Preciso") : "") : "", h.duracao_s ? duracao(h.duracao_s) : "", STATUS[h.status] || ""];
      const b = el("button", { type: "button", className: "item" },
        el("strong", { textContent: h.nome }), el("span", { className: "miudo", textContent: info.filter(Boolean).join(" · ") }));
      b.addEventListener("click", () => {
        if (trabalhoAtivo()) { aviso("Espere a transcrição terminar ou clique em Parar."); return; }
        estado.visualizando = h; render(); window.scrollTo({ top: 0, behavior: "smooth" });
      });
      ul.append(el("li", {}, b));
    }
    mostrar($("#historico-vazio"), estado.historico.length === 0);
    if (estado.trabalho || estado.visualizando) renderPainel();
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
      if (primeiroSnapshot && d.trabalho && !ATIVAS.includes(d.trabalho.fase)) estado.dispensado = d.trabalho.id;
      if (primeiroSnapshot && d.trabalho && ATIVAS.includes(d.trabalho.fase)) estado.aba = d.trabalho.tipo === "youtube" ? "youtube" : "arquivo";
      primeiroSnapshot = false;
      render(); carregarHistorico();
    });
    es.addEventListener("estado", (e) => {
      const d = JSON.parse(e.data);
      const antes = estado.trabalho;
      Object.assign(estado, { hardware: d.hardware, modelo: d.modelo, trabalho: d.trabalho });
      if (d.trabalho && (!antes || antes.id !== d.trabalho.id)) {
        estado.linhas = []; estado.enviando = null; estado.visualizando = null;
        if (d.trabalho.tipo === "youtube") { estado.aba = "youtube"; reiniciarYoutube(); }
      }
      render();
    });
    es.addEventListener("linha", (e) => { estado.linhas.push(JSON.parse(e.data)); if (etapaPainel() === "andamento") desenharAoVivo(); });
    es.addEventListener("historico", carregarHistorico);
  }

  /* ---------- ações comuns ---------- */
  async function post(url) { try { await fetch(url, { method: "POST" }); } catch (e) { /* ver aviso de conexão */ } }
  $("#btn-baixar").addEventListener("click", () => post("/api/modelo/baixar"));
  $("#btn-tentar-modelo").addEventListener("click", () => post("/api/modelo/baixar"));
  $("#btn-parar").addEventListener("click", () => { $("#btn-parar").disabled = true; post("/api/parar"); });
  $("#btn-outro").addEventListener("click", () => {
    const eraYt = (estado.visualizando ? estado.visualizando.tipo : (estado.trabalho && estado.trabalho.tipo)) === "youtube";
    estado.dispensado = estado.trabalho ? estado.trabalho.id : null; estado.visualizando = null; estado.horarios = true;
    estado.aba = eraYt ? "youtube" : estado.aba;
    $("#chk-horarios").checked = true; render();
  });
  $("#chk-horarios").addEventListener("change", (e) => { estado.horarios = e.target.checked; carregarTextoFinal(idAtual()); });
  $("#btn-copiar").addEventListener("click", async () => {
    try {
      const r = await fetch("/api/historico/" + encodeURIComponent(idAtual()) + "/texto?horarios=" + estado.horarios);
      await copiar(await r.text(), "Texto copiado!");
    } catch (e) { aviso("Não consegui copiar. Selecione o texto e use Ctrl+C."); }
  });
  $("#btn-baixar-txt").addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = "/api/historico/" + encodeURIComponent(idAtual()) + "/texto?baixar=true&horarios=" + estado.horarios;
    document.body.append(a); a.click(); a.remove();
  });
  $("#btn-pasta").addEventListener("click", () => post("/api/historico/" + encodeURIComponent(idAtual()) + "/abrir-pasta"));

  /* ---------- aba Arquivo: envio (XHR para mostrar o progresso do envio) ---------- */
  function enviar(arquivo) {
    if (!arquivo) return;
    estado.enviando = { nome: arquivo.name, pct: 0 }; estado.dispensado = null; estado.visualizando = null; render();
    const dados = new FormData();
    dados.append("arquivo", arquivo);
    dados.append("idioma", $("#idioma").value);
    try { localStorage.setItem("idioma", $("#idioma").value); } catch (e) { /* sem armazenamento */ }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/arquivo");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && estado.enviando) { estado.enviando.pct = e.loaded / e.total; renderAndamento(); } };
    const falhou = (msg) => {
      estado.enviando = null; estado.visualizando = null; estado.dispensado = null;
      estado.trabalho = { id: "_erro", tipo: "arquivo", nome: arquivo.name, fase: "erro", erro: msg, aviso: "", progresso: 0, ajuda: null };
      render();
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

  /* ---------- aba YouTube ---------- */
  const ytUrl = $("#yt-url");
  const modoAtual = () => document.querySelector('input[name="yt-modo"]:checked').value;
  function erroYt(d) {
    const caixa = $("#yt-erro");
    caixa.replaceChildren(el("p", { textContent: (d && d.detail) || "Não consegui falar com o programa. Tente de novo." }));
    if (d && d.ajuda && d.ajuda.tipo === "atualizar_ytdlp") caixa.append(criarAjuda(d.ajuda));
    mostrar(caixa, true);
  }
  function limparErroYt() { mostrar($("#yt-erro"), false); $("#yt-erro").replaceChildren(); }
  function reiniciarYoutube() {
    estado.video = null; ytUrl.value = ""; limparErroYt(); mostrar($("#yt-video"), false);
    document.querySelector('input[name="yt-modo"][value="rapido"]').checked = true;
  }
  function renderVideo() {
    const v = estado.video;
    mostrar($("#yt-video"), !!v);
    if (!v) return;
    $("#yt-titulo").textContent = v.titulo;
    $("#yt-detalhes").textContent = [v.canal, v.duracao_s ? "duração " + duracao(v.duracao_s) : ""].filter(Boolean).join(" · ");
    const l = v.legendas || {};
    let info = "Não consegui verificar as legendas agora; o modo Rápido tenta mesmo assim.";
    if (l.disponivel === true) {
      info = "Tem legendas em " + nomeIdioma(l.idioma) + (l.automatica ? " (geradas automaticamente pelo YouTube)" : "") + ".";
      if (!(l.idioma || "").toLowerCase().startsWith("pt")) info += " Atenção: não são em português.";
    } else if (l.disponivel === false) info = "Sem legendas.";
    $("#yt-legenda-info").textContent = info;
    const semLegenda = l.disponivel === false, rapido = modoAtual() === "rapido";
    mostrar($("#yt-sem-legenda"), semLegenda && rapido);
    $("#btn-yt-transcrever").disabled = semLegenda && rapido;
  }
  async function consultarVideo() {
    const url = ytUrl.value.trim(), botao = $("#btn-yt-buscar");
    limparErroYt(); estado.video = null; renderVideo();
    if (!url) { erroYt({ detail: "Cole aqui o link do vídeo do YouTube." }); return; }
    botao.disabled = true; botao.textContent = "Buscando…";
    try {
      const r = await fetch("/api/youtube/consultar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
      const d = await r.json();
      if (!r.ok) erroYt(d); else { estado.video = d; renderVideo(); }
    } catch (e) {
      erroYt({ detail: "Não consegui falar com o programa. Confira se a janela preta ainda está aberta." });
    } finally { botao.disabled = false; botao.textContent = "Buscar vídeo"; }
  }
  async function iniciarYoutube(url, modo, botao) {
    limparErroYt();
    const antes = botao ? botao.textContent : "";
    if (botao) { botao.disabled = true; botao.textContent = "Preparando…"; }
    try {
      const r = await fetch("/api/youtube", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        url, modo, manter_audio: $("#yt-manter").checked, idioma: $("#yt-idioma").value }) });
      if (r.ok) { estado.dispensado = null; return; }
      const d = await r.json();
      if (r.status === 409) aviso(d.detail); else erroYt(d);
    } catch (e) {
      erroYt({ detail: "Não consegui falar com o programa. Confira se a janela preta ainda está aberta." });
    } finally { if (botao) { botao.disabled = false; botao.textContent = antes; renderVideo(); } }
  }
  $("#form-yt").addEventListener("submit", (e) => { e.preventDefault(); consultarVideo(); });
  for (const r of document.querySelectorAll('input[name="yt-modo"]')) r.addEventListener("change", renderVideo);
  $("#btn-yt-usar-preciso").addEventListener("click", () => {
    document.querySelector('input[name="yt-modo"][value="preciso"]').checked = true; renderVideo();
  });
  $("#btn-yt-transcrever").addEventListener("click", () => { if (estado.video) iniciarYoutube(estado.video.url, modoAtual(), $("#btn-yt-transcrever")); });
  $("#btn-fim-preciso").addEventListener("click", () => {
    const t = estado.trabalho;
    if (t && t.url) { estado.dispensado = t.id; iniciarYoutube(t.url, "preciso", $("#btn-fim-preciso")); }
  });
  $("#yt-manter").checked = (() => { try { return localStorage.getItem("ytManter") === "1"; } catch (e) { return false; } })();
  $("#yt-manter").addEventListener("change", (e) => { try { localStorage.setItem("ytManter", e.target.checked ? "1" : "0"); } catch (x) { /* ok */ } });

  try {
    const i = localStorage.getItem("idioma"); if (i) $("#idioma").value = i;
    const y = localStorage.getItem("ytIdioma"); if (y) $("#yt-idioma").value = y;
  } catch (e) { /* ok */ }
  $("#yt-idioma").addEventListener("change", (e) => { try { localStorage.setItem("ytIdioma", e.target.value); } catch (x) { /* ok */ } });
  conectar();
})();
