/* Interface do transcritor-local (sem build). Fala com a API por fetch e recebe eventos por SSE. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const mostrar = (el, sim) => { el.hidden = !sim; };
  const ATIVAS = ["preparando", "baixando", "transcrevendo", "gravando", "parando"];

  const estado = {
    hardware: {}, modelo: { fase: "verificando" }, trabalho: null, linhas: [],
    aba: "arquivo",
    enviando: null,        // {nome, pct} durante o envio do arquivo para o programa
    visualizando: null,    // item do histórico aberto pelo usuário
    dispensado: null,      // id do resultado que o usuário fechou ("Fazer outra transcrição")
    historico: [], horarios: true,
    video: null,           // vídeo consultado na aba YouTube
    hwAberto: false,
    grav: { info: null, erro: "", confirmando: false },   // aba Gravar
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
  const MSG_ENCERRADO = "O programa foi encerrado. Feche esta aba e abra o iniciar.bat de novo. Seus áudios e textos anteriores estão salvos.";
  function aviso(msg, ms = 2500) {
    const t = $("#aviso-rapido");
    t.textContent = msg; t.hidden = false;
    clearTimeout(aviso.t); aviso.t = setTimeout(() => { t.hidden = true; }, ms);
  }
  function mbTexto(mb) { return mb >= 1000 ? (mb / 1024).toFixed(1).replace(".", ",") + " GB" : Math.round(mb) + " MB"; }
  function barra(el, frac) {
    const pct = Math.max(0, Math.min(100, Math.round(frac * 100)));
    el.firstElementChild.style.width = pct + "%";
    el.setAttribute("aria-valuenow", pct);
    return pct;
  }
  const FONTES = { reuniao: "Reunião", ambiente: "Ambiente" };
  const IDIOMAS = { pt: "português", "pt-BR": "português", en: "inglês", es: "espanhol", fr: "francês", de: "alemão", it: "italiano" };
  const nomeIdioma = (c) => IDIOMAS[c] || IDIOMAS[(c || "").split("-")[0]] || c;
  const MODOS = { rapido: "modo Rápido", preciso: "modo Preciso" };
  function el(tag, props, ...filhos) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) { if (k === "role") e.setAttribute(k, v); else e[k] = v; }
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
    if (!estado.linhas.length && !alvo.firstChild) {
      const grav = estado.trabalho && estado.trabalho.tipo === "gravacao";
      alvo.append(el("span", { className: "vazio", textContent: grav ? "O texto aparece aqui a cada 30 segundos, mais ou menos…" : "O texto vai aparecendo aqui…" }));
    }
    if (colado) alvo.scrollTop = alvo.scrollHeight;
  }

  /* ---------- ajuda para atualizar o yt-dlp ---------- */
  // Nunca atualiza sozinho: o botão só pede a confirmação; a instalação começa no "Continuar".
  function criarAjuda(ajuda) {
    const cmd = el("code", { textContent: ajuda.comando });
    const copia = el("button", { className: "btn btn-sec", type: "button", textContent: "Copiar comando" });
    copia.addEventListener("click", () => copiar(ajuda.comando, "Comando copiado!"));

    const atualizar = el("button", { className: "btn btn-primario", type: "button", textContent: "Atualizar yt-dlp agora" });
    const pergunta = el("p", { textContent: "Vou instalar a versão mais recente do yt-dlp. Continuar?" });
    const sim = el("button", { className: "btn btn-primario", type: "button", textContent: "Continuar" });
    const nao = el("button", { className: "btn btn-sec", type: "button", textContent: "Cancelar" });
    const botoes = el("div", { className: "acoes" }, sim, nao);
    const confirmar = el("div", { className: "confirmar", role: "alertdialog" }, pergunta, botoes);
    confirmar.hidden = true;
    const resultado = el("p", { className: "ajuda-resultado", role: "status" });
    resultado.hidden = true;

    const mostrarResultado = (falha, ...filhos) => {
      resultado.className = "ajuda-resultado" + (falha ? " falha" : "");
      resultado.replaceChildren(...filhos); resultado.hidden = false;
    };
    atualizar.addEventListener("click", () => { atualizar.hidden = true; resultado.hidden = true; confirmar.hidden = false; nao.focus(); });
    nao.addEventListener("click", () => { confirmar.hidden = true; atualizar.hidden = false; atualizar.focus(); });
    sim.addEventListener("click", async () => {
      botoes.hidden = true;
      pergunta.textContent = "Atualizando o yt-dlp… isso pode levar até um minuto.";
      try {
        const r = await fetch("/api/ytdlp/atualizar", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirmado: true }) });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || "Não consegui atualizar agora.");
        const versoes = "Versão antes: " + (d.antes || "desconhecida") + " · depois: " + (d.depois || "desconhecida");
        const linhas = [el("span", { textContent: d.mudou ? "yt-dlp atualizado! Agora é só tentar de novo." : "Você já estava com a versão mais recente." }),
          el("small", { textContent: versoes })];
        if (d.reiniciar) linhas.push(el("small", { textContent: "Para valer a atualização, feche a janela preta e abra o iniciar.bat de novo." }));
        mostrarResultado(false, ...linhas);
        confirmar.hidden = true;           // deu certo: não oferece atualizar outra vez
      } catch (e) {
        const msg = e instanceof TypeError ? MSG_ENCERRADO : e.message;
        mostrarResultado(true, document.createTextNode(msg));
        confirmar.hidden = true; atualizar.hidden = false; atualizar.textContent = "Tentar de novo";
      } finally { botoes.hidden = false; pergunta.textContent = "Vou instalar a versão mais recente do yt-dlp. Continuar?"; }
    });

    const manual = el("details", { className: "manual" },
      el("summary", { textContent: "Prefere atualizar manualmente?" }),
      el("ol", {},
        el("li", { textContent: "Feche a janela preta do programa." }),
        el("li", {}, document.createTextNode("Aperte a tecla Windows, digite "), el("strong", { textContent: "cmd" }), document.createTextNode(" e tecle Enter.")),
        el("li", { textContent: "Cole o comando abaixo e tecle Enter:" })),
      el("div", { className: "comando" }, cmd, copia),
      el("p", {}, document.createTextNode("Depois abra o "), el("strong", { textContent: "iniciar.bat" }), document.createTextNode(" de novo e tente outra vez.")));

    return el("div", { className: "ajuda" },
      el("p", {}, el("strong", { textContent: "Atualizar o yt-dlp" }),
        document.createTextNode(" — o programa que baixa o áudio" + (ajuda.versao ? " (versão instalada: " + ajuda.versao + ")" : "") +
          ". Atualizar costuma resolver.")),
      atualizar, confirmar, resultado, manual);
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
  function gravandoAgora() { return !!(estado.trabalho && estado.trabalho.tipo === "gravacao" && ATIVAS.includes(estado.trabalho.fase)); }
  function trabalhoAtivo() { return !!(estado.enviando || (estado.trabalho && ATIVAS.includes(estado.trabalho.fase))); }
  for (const b of document.querySelectorAll(".aba[data-aba]")) {
    b.addEventListener("click", () => {
      if (trabalhoAtivo()) { aviso(gravandoAgora() ? "Tem uma gravação em andamento. Clique em Parar gravação para terminar." : "Tem uma transcrição em andamento. Espere terminar ou clique em Parar."); return; }
      estado.aba = b.dataset.aba; estado.visualizando = null; estado.dispensado = estado.trabalho ? estado.trabalho.id : null;
      estado.grav.confirmando = false;
      render();
      if (estado.aba === "gravar") carregarDispositivos();
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
    mostrar($("#inicio-gravar"), etapa === "inicio" && estado.aba === "gravar");
    mostrar($("#arq-andamento"), etapa === "andamento");
    mostrar($("#arq-fim"), etapa === "fim");
    if (etapa === "andamento") renderAndamento();
    if (etapa === "fim") renderFim();
    if (etapa === "inicio" && estado.aba === "gravar") renderGravar();
    sincronizarPrevia();
  }
  function renderAndamento() {
    const t = estado.trabalho, env = estado.enviando;
    const btn = $("#btn-parar");
    if (env) {
      mostrar($("#grav-extra"), false); $("#and-nome").hidden = false; $("#barra-job").hidden = false; $("#linha-status").hidden = false;
      btn.textContent = "Parar"; btn.classList.remove("btn-grande");
      $("#and-tipo").textContent = "Arquivo";
      $("#and-nome").textContent = env.nome;
      $("#barra-job").classList.remove("indeterminada");
      barra($("#barra-job"), env.pct);
      $("#and-status").textContent = "Enviando o arquivo… " + Math.round(env.pct * 100) + "%";
      $("#and-restante").textContent = "";
      btn.disabled = true;
      return;
    }
    const grav = t.tipo === "gravacao";
    mostrar($("#grav-extra"), grav);
    $("#and-nome").hidden = grav; $("#barra-job").hidden = grav; $("#linha-status").hidden = grav;
    btn.textContent = grav ? "Parar gravação" : "Parar";
    btn.classList.toggle("btn-grande", grav);
    if (grav) { renderGravando(t); btn.disabled = t.fase === "parando"; desenharAoVivo(); return; }
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
                    canal: v.canal, legenda: v.legenda, audio_guardado: v.audio_guardado, codigo_erro: "", ajuda: null,
                    fonte: v.fonte }
                : estado.trabalho;
    if (!t) return;
    const meta = estado.historico.find((h) => h.id === t.id) || {};
    const yt = t.tipo === "youtube", gr = t.tipo === "gravacao";
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
    if (gr && (t.fonte || meta.fonte)) partes.push("modo " + (FONTES[t.fonte || meta.fonte] || ""));
    if (meta.duracao_s) partes.push((yt ? "vídeo" : gr ? "gravação" : "áudio") + " de " + duracao(meta.duracao_s));
    if (meta.tempo_s && t.fase === "concluido" && !gr && !(yt && (t.modo || meta.modo) === "rapido")) partes.push("transcrito em " + duracao(meta.tempo_s));
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
    const assinatura = t.ajuda && t.ajuda.tipo === "atualizar_ytdlp" ? t.id + ":" + t.codigo_erro : "";
    if (ajuda.dataset.assinatura !== assinatura) {      // só refaz quando muda, para não apagar o que o usuário está fazendo
      ajuda.replaceChildren(); ajuda.dataset.assinatura = assinatura;
      if (assinatura) ajuda.append(criarAjuda(t.ajuda));
    }
    mostrar(ajuda, ajuda.childElementCount > 0);
    mostrar($("#fim-acao-preciso"), t.codigo_erro === "sem_legenda" && !!t.url);
    const temTexto = !!(meta.linhas || (!v && estado.linhas.length));
    mostrar($("#fim-texto-bloco"), temTexto);
    $("#btn-pasta").hidden = !temTexto;
    mostrar($("#fim-acao-pasta"), gr && !temTexto && !!(t.audio_guardado || meta.audio_guardado));
    $("#btn-outro").textContent = yt ? "Transcrever outro vídeo" : gr ? "Fazer outra gravação" : "Transcrever outro arquivo";
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
      const info = [dataBonita(h.criado), h.tipo === "youtube" ? "YouTube" + (h.modo ? " " + (h.modo === "rapido" ? "Rápido" : "Preciso") : "") : h.tipo === "gravacao" ? "Gravação" + (h.fonte ? " · " + FONTES[h.fonte] : "") : "", h.duracao_s ? duracao(h.duracao_s) : "", STATUS[h.status] || ""];
      const b = el("button", { type: "button", className: "item" },
        el("strong", { textContent: h.nome }), el("span", { className: "miudo", textContent: info.filter(Boolean).join(" · ") }));
      b.addEventListener("click", () => {
        if (trabalhoAtivo()) { aviso(gravandoAgora() ? "Clique em Parar gravação antes de abrir outra transcrição." : "Espere a transcrição terminar ou clique em Parar."); return; }
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
      if (primeiroSnapshot && d.trabalho && ATIVAS.includes(d.trabalho.fase)) estado.aba = d.trabalho.tipo === "youtube" ? "youtube" : d.trabalho.tipo === "gravacao" ? "gravar" : "arquivo";
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
        if (d.trabalho.tipo === "gravacao") { estado.aba = "gravar"; estado.grav.confirmando = false; }
      }
      render();
    });
    es.addEventListener("linha", (e) => { estado.linhas.push(JSON.parse(e.data)); if (etapaPainel() === "andamento") desenharAoVivo(); });
    es.addEventListener("historico", carregarHistorico);
    es.addEventListener("aviso", (e) => aviso(JSON.parse(e.data).texto, 8000));
    es.addEventListener("nivel", (e) => pintarNiveis(JSON.parse(e.data).niveis));
  }

  /* ---------- ações comuns ---------- */
  async function post(url) { try { await fetch(url, { method: "POST" }); } catch (e) { /* ver aviso de conexão */ } }
  $("#btn-baixar").addEventListener("click", () => post("/api/modelo/baixar"));
  $("#btn-tentar-modelo").addEventListener("click", () => post("/api/modelo/baixar"));
  $("#btn-parar").addEventListener("click", () => { $("#btn-parar").disabled = true; post("/api/parar"); });
  $("#btn-outro").addEventListener("click", () => {
    const tipo = estado.visualizando ? estado.visualizando.tipo : (estado.trabalho && estado.trabalho.tipo);
    estado.dispensado = estado.trabalho ? estado.trabalho.id : null; estado.visualizando = null; estado.horarios = true;
    estado.aba = tipo === "youtube" ? "youtube" : tipo === "gravacao" ? "gravar" : estado.aba;
    if (estado.aba === "gravar") carregarDispositivos();
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
  $("#btn-fim-pasta").addEventListener("click", () => post("/api/historico/" + encodeURIComponent(idAtual()) + "/abrir-pasta"));

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
      erroYt({ detail: MSG_ENCERRADO });
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
      erroYt({ detail: MSG_ENCERRADO });
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
  /* ---------- aba Gravar ---------- */
  const NOMES_MEDIDOR = { mic: "Seu microfone", sistema: "Som do computador" };
  const AVISOS_MEDIDOR = {
    mic: { baixo: "O som está muito baixo. Chegue mais perto do microfone ou aumente o volume dele.",
           mudo: "Não estou ouvindo nada pelo microfone. Confira se ele está ligado e se é o escolhido em Avançado." },
    sistema: { baixo: "O som do computador está muito baixo. Aumente o volume da reunião.",
               mudo: "Nenhum som saindo do computador agora." },
  };
  const fonteAtual = () => document.querySelector('input[name="grav-fonte"]:checked').value;
  const chavesDaFonte = (f) => f === "reuniao" ? ["mic", "sistema"] : ["mic"];
  const micAtual = () => $("#grav-mic").value;

  function montarMedidores(alvo, chaves) {
    if (Array.from(alvo.children).map((c) => c.dataset.chave).join() === chaves.join()) return;
    alvo.replaceChildren(...chaves.map((k) => {
      const nivel = el("div", { className: "med-nivel" });
      const barra = el("div", { className: "med-barra", role: "meter" }, nivel);
      barra.setAttribute("aria-label", NOMES_MEDIDOR[k]);
      barra.setAttribute("aria-valuemin", "0"); barra.setAttribute("aria-valuemax", "100"); barra.setAttribute("aria-valuenow", "0");
      const aviso = el("p", { className: "med-aviso" }); aviso.hidden = true;
      const m = el("div", { className: "medidor" }, el("div", { className: "med-topo" }, el("span", { textContent: NOMES_MEDIDOR[k] })), barra, aviso);
      m.dataset.chave = k;
      return m;
    }));
  }
  function pintarNiveis(niveis) {
    for (const [chave, v] of Object.entries(niveis || {})) {
      for (const m of document.querySelectorAll('.medidor[data-chave="' + chave + '"]')) {
        const pct = Math.round(v.n * 100);
        m.querySelector(".med-nivel").style.width = pct + "%";
        m.querySelector(".med-barra").setAttribute("aria-valuenow", pct);
        if (m.dataset.aviso !== v.aviso) {
          m.dataset.aviso = v.aviso;
          const a = m.querySelector(".med-aviso"), texto = v.aviso ? AVISOS_MEDIDOR[chave][v.aviso] : "";
          a.textContent = texto; a.hidden = !texto;
          a.className = "med-aviso" + (v.aviso && !(chave === "sistema" && v.aviso === "mudo") ? " alerta" : "");
        }
      }
    }
  }
  function renderGravando(t) {
    const parando = t.fase === "parando";
    $("#and-tipo").textContent = "Gravação · " + (FONTES[t.fonte] || "");
    $(".grav-topo").classList.toggle("parando", parando);
    $("#grav-estado").textContent = parando
      ? "Terminando a transcrição…" + (t.pendentes ? " (faltam " + t.pendentes + (t.pendentes === 1 ? " trecho)" : " trechos)") : "")
      : "Gravando";
    $("#grav-tempo").textContent = tempoRelogio(t.gravado_s);
    mostrar($("#med-grav"), !parando);
    montarMedidores($("#med-grav"), chavesDaFonte(t.fonte));
    const at = $("#grav-atraso");
    at.textContent = "A transcrição está cerca de " + duracao(t.atraso_s) + " atrás da gravação: o computador está demorando para acompanhar. " +
      "O áudio continua sendo gravado e o texto alcança depois.";
    mostrar(at, !!t.atrasado && !parando);
  }
  function tempoRelogio(s) {
    s = Math.floor(s || 0);
    return String(Math.floor(s / 3600)).padStart(2, "0") + ":" + String(Math.floor(s % 3600 / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }

  function renderGravar() {
    const info = estado.grav.info;
    const sup = !info || info.suportado;
    const ind = $("#grav-indisponivel");
    ind.textContent = info && !info.suportado ? info.motivo : ""; mostrar(ind, !!(info && !info.suportado));
    mostrar($("#grav-ok"), sup);
    montarMedidores($("#med-previa"), chavesDaFonte(fonteAtual()));
    const e = $("#grav-erro");
    e.textContent = estado.grav.erro; mostrar(e, !!estado.grav.erro);
    mostrar($("#grav-aviso-reuniao"), estado.grav.confirmando);
    mostrar($("#btn-gravar-iniciar"), !estado.grav.confirmando);
  }
  function preencherMicrofones(info) {
    const sel = $("#grav-mic");
    const lista = (info && info.microfones) || [];
    const assinatura = JSON.stringify(lista);
    if (sel.dataset.assinatura === assinatura) return;
    sel.dataset.assinatura = assinatura;
    let guardado = "";
    try { guardado = localStorage.getItem("gravMic") || ""; } catch (e) { /* ok */ }
    sel.replaceChildren(el("option", { value: "", textContent: "Microfone padrão do Windows" }),
      ...lista.map((m) => el("option", { value: m.id, textContent: m.nome + (m.padrao ? " (padrão)" : "") })));
    sel.value = lista.some((m) => m.id === guardado) ? guardado : "";
  }
  async function carregarDispositivos() {
    try {
      const r = await fetch("/api/gravacao/dispositivos");
      estado.grav.info = await r.json();
    } catch (e) { return; }
    preencherMicrofones(estado.grav.info);
    render();
  }

  // Prévia do medidor: liga quando a aba Gravar está parada e visível; a página a renova a cada 5 s
  // (o programa desliga sozinho se ela parar de renovar) e ela só mede: nada é gravado.
  const previa = { chave: "", timer: null };
  function previaDesejada() {
    const i = estado.grav.info;
    return estado.aba === "gravar" && estado.modelo.fase === "pronto" && etapaPainel() === "inicio" &&
      !!(i && i.suportado) && document.visibilityState === "visible";
  }
  async function ligarPrevia() {
    const chave = previa.chave;
    if (!chave) return;
    try {
      const r = await fetch("/api/gravacao/previa", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fonte: fonteAtual(), microfone: micAtual() }) });
      if (chave !== previa.chave) return;
      if (r.ok) { if (estado.grav.erro) { estado.grav.erro = ""; renderGravar(); } return; }
      const d = await r.json();
      if (r.status === 409) return;
      estado.grav.erro = d.detail || "Não consegui ligar o microfone."; renderGravar();
      pintarNiveis(Object.fromEntries(chavesDaFonte(fonteAtual()).map((k) => [k, { n: 0, aviso: "" }])));
      carregarDispositivos();           // quem conectou o microfone agora aparece na lista
    } catch (e) { /* o aviso de conexão cuida disso */ }
  }
  function sincronizarPrevia() {
    const quer = previaDesejada();
    const chave = quer ? fonteAtual() + "|" + micAtual() : "";
    if (chave === previa.chave) return;
    const estavaLigada = !!previa.chave;
    previa.chave = chave;
    if (!chave) {
      clearInterval(previa.timer); previa.timer = null;
      if (estavaLigada) post("/api/gravacao/previa/parar");
      return;
    }
    estado.grav.erro = "";
    ligarPrevia();
    if (!previa.timer) previa.timer = setInterval(ligarPrevia, 5000);
  }
  document.addEventListener("visibilitychange", render);
  window.addEventListener("pagehide", () => { if (previa.chave) navigator.sendBeacon("/api/gravacao/previa/parar"); });

  async function iniciarGravacao() {
    const botao = $("#btn-gravar-iniciar");
    estado.grav.confirmando = false; estado.grav.erro = "";
    botao.disabled = true;
    try {
      try { localStorage.setItem("gravFonte", fonteAtual()); localStorage.setItem("gravMic", micAtual()); } catch (e) { /* ok */ }
      const r = await fetch("/api/gravacao", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fonte: fonteAtual(), microfone: micAtual() }) });
      if (r.ok) { estado.dispensado = null; return; }
      const d = await r.json();
      if (r.status === 409) aviso(d.detail); else estado.grav.erro = d.detail || "Não consegui começar a gravar.";
    } catch (e) {
      estado.grav.erro = MSG_ENCERRADO;
    } finally { botao.disabled = false; render(); }
  }
  $("#btn-gravar-iniciar").addEventListener("click", () => {
    if (fonteAtual() === "reuniao") { estado.grav.confirmando = true; render(); $("#btn-grav-confirmar").focus(); }
    else iniciarGravacao();
  });
  $("#btn-grav-confirmar").addEventListener("click", iniciarGravacao);
  $("#btn-grav-voltar").addEventListener("click", () => { estado.grav.confirmando = false; render(); $("#btn-gravar-iniciar").focus(); });
  for (const r of document.querySelectorAll('input[name="grav-fonte"]')) {
    r.addEventListener("change", () => {
      estado.grav.confirmando = false;
      try { localStorage.setItem("gravFonte", fonteAtual()); } catch (e) { /* ok */ }
      render();
    });
  }
  $("#grav-mic").addEventListener("change", () => { try { localStorage.setItem("gravMic", micAtual()); } catch (e) { /* ok */ } render(); });
  try {
    const f = localStorage.getItem("gravFonte");
    if (f) { const r = document.querySelector('input[name="grav-fonte"][value="' + f + '"]'); if (r) r.checked = true; }
  } catch (e) { /* ok */ }

  conectar();
  carregarDispositivos();
})();
