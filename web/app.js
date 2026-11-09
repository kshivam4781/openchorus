"use strict";
/* OpenChorus web app: plain JS, no build step. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (t) => { if (!isFinite(t) || t < 0) t = 0; const m = Math.floor(t / 60); return `${m}:${String(Math.floor(t % 60)).padStart(2, "0")}`; };

const STYLE_CHIPS = ["pop", "synthwave", "lo-fi hip hop", "rock", "piano ballad", "edm", "acoustic folk", "jazz", "trap", "cinematic", "female vocals", "male vocals", "upbeat", "melancholic"];

const S = {
  mode: "simple", kind: "song", vocal: "", view: "all", q: "", songs: [], personas: [], status: null,
  current: null, open: null, peaks: new Map(), pollTimer: null, selection: null,
};

/* ---------------------------------------------------------------- api */
async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ");
    throw new Error(msg || `Request failed (${r.status})`);
  }
  return data;
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", err);
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), err ? 5000 : 2600);
}

/* --------------------------------------------------------- cover art */
function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function rng(a) { /* mulberry32 */
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* A tape-reel glyph: rings and arcs drawn from the song's seed. */
function drawCover(canvas, key, spinning = false) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height, cx = w / 2, cy = h / 2;
  const r = rng(hash(String(key)));
  const hues = [[242, 179, 61], [127, 196, 192], [236, 230, 214], [229, 103, 90]];
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#2e3e53";
  ctx.fillRect(0, 0, w, h);
  const rings = 4 + Math.floor(r() * 4);
  for (let i = 0; i < rings; i++) {
    const rad = (w * 0.44) * (1 - i / (rings + 1));
    const [cr, cg, cb] = hues[Math.floor(r() * (i === 0 ? 2 : hues.length))];
    const start = r() * Math.PI * 2, len = Math.PI * (0.5 + r() * 1.4);
    ctx.lineWidth = Math.max(1.5, w * (0.03 + r() * 0.06));
    ctx.lineCap = "round";
    ctx.strokeStyle = `rgba(${cr},${cg},${cb},${0.35 + r() * 0.65})`;
    ctx.beginPath();
    ctx.arc(cx, cy, rad, start, start + len);
    ctx.stroke();
  }
  ctx.fillStyle = "#1f2a3a";
  ctx.beginPath(); ctx.arc(cx, cy, w * 0.11, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = spinning ? "#f2b33d" : "#7fc4c0";
  ctx.beginPath(); ctx.arc(cx, cy, w * 0.045, 0, Math.PI * 2); ctx.fill();
}

/* Real cover art when the image model painted one, the tape reel otherwise. */
function artHTML(song, px, cls = "") {
  const pending = song.status === "queued" || song.status === "running";
  if (song.art_url && !pending) return `<img class="art ${cls}" src="${esc(song.art_url)}" alt="" width="${px}" height="${px}" loading="lazy">`;
  return `<canvas width="${px * 2}" height="${px * 2}" class="art ${cls} ${pending ? "spin" : ""}" data-cover="${esc(song.seed ?? song.id)}"></canvas>`;
}
function paintReels(root) { $$("canvas[data-cover]", root).forEach((c) => drawCover(c, c.dataset.cover, c.classList.contains("spin"))); }

const isBusy = (s) => s.status === "queued" || s.status === "running"
  || (s.params && (s.params.polishing || ["queued", "painting"].includes(s.params.art_status)));
const POLISH_LABEL = { queued: "Mastering", mastering: "Mastering", rating: "Rating the sound" };
function polishText(s) {
  const p = s.params || {};
  if (p.polishing) return POLISH_LABEL[p.polishing] || "Finishing";
  if (p.art_status === "queued" || p.art_status === "painting") return "Painting the cover";
  return "";
}

/* ----------------------------------------------------------- waveform */
let audioCtx = null;
const peakKey = (song, buckets = 600) => `${song.id}:${song.audio_file || ""}:${buckets}`;
async function peaksFor(song, buckets = 600) {
  const k = peakKey(song, buckets);
  if (S.peaks.has(k)) return S.peaks.get(k);
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  const buf = await (await fetch(song.stream_url || song.audio_url)).arrayBuffer();
  const audio = await audioCtx.decodeAudioData(buf);
  const ch = audio.getChannelData(0);
  const step = Math.max(1, Math.floor(ch.length / buckets));
  const out = new Float32Array(buckets);
  let max = 0;
  for (let i = 0; i < buckets; i++) {
    let p = 0;
    for (let j = i * step, e = Math.min(ch.length, j + step); j < e; j += 4) { const v = Math.abs(ch[j]); if (v > p) p = v; }
    out[i] = p; if (p > max) max = p;
  }
  if (max > 0) for (let i = 0; i < buckets; i++) out[i] /= max;
  S.peaks.set(k, out);
  return out;
}

function fitCanvas(c) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(c.clientWidth * dpr)), h = Math.max(1, Math.round(c.clientHeight * dpr));
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return dpr;
}

function drawWave(canvas, peaks, progress = 0, sel = null, duration = 0) {
  const dpr = fitCanvas(canvas);
  const ctx = canvas.getContext("2d");
  const { width: w, height: h } = canvas;
  ctx.clearRect(0, 0, w, h);
  if (sel && duration) {
    ctx.fillStyle = "rgba(242,179,61,0.18)";
    const x0 = (sel[0] / duration) * w, x1 = (sel[1] / duration) * w;
    ctx.fillRect(x0, 0, x1 - x0, h);
  }
  if (!peaks) {
    ctx.fillStyle = "#3a4b62";
    ctx.fillRect(0, h / 2 - dpr, w, 2 * dpr);
    return;
  }
  const bar = 2 * dpr, gap = 1 * dpr, n = Math.floor(w / (bar + gap));
  for (let i = 0; i < n; i++) {
    const p = peaks[Math.floor((i / n) * peaks.length)] || 0;
    const bh = Math.max(2 * dpr, p * (h - 4 * dpr));
    const x = i * (bar + gap);
    ctx.fillStyle = x / w <= progress ? "#f2b33d" : "#4b7d7b";
    ctx.fillRect(x, (h - bh) / 2, bar, bh);
  }
}

/* ------------------------------------------------------------ status */
async function loadStatus() {
  try {
    S.status = await api("/api/status");
  } catch { clearTimeout(loadStatus._t); loadStatus._t = setTimeout(loadStatus, 10000); return; }
  const st = S.status, box = $("#engineBox");
  const demo = st.active.name === "demo";
  const loading = demo && st.acestep.online && !st.acestep.ready;
  const detail = !demo ? esc((st.acestep.models || []).join(", ") || st.acestep.url)
    : loading ? "ACE-Step found, loading its models"
      : st.mode === "demo" ? "Demo mode is set in .env" : `ACE-Step not found at ${esc(st.acestep.url)}`;
  box.innerHTML = `<strong><span class="dot ${demo ? "demo" : "on"}"></span>${esc(st.active.label)}</strong>${detail}`;
  const banner = $("#banner");
  banner.hidden = !demo;
  if (loading) {
    banner.innerHTML = `<strong>ACE-Step is loading.</strong> It is downloading or loading its models, which takes a while the first time. New songs use the demo synth until it is ready, then this page switches over by itself.`;
  } else if (demo) {
    banner.innerHTML = `<strong>Demo engine.</strong> Songs are instrumental sketches with a synth line where the vocals go, so you can try every feature offline. For real songs with vocals, start ACE-Step 1.5 (<code>uv run acestep-api</code>) and this page switches over by itself.`;
  }
  clearTimeout(loadStatus._t);
  loadStatus._t = setTimeout(loadStatus, loading ? 10000 : 20000);
}

/* ----------------------------------------------------------- library */
async function loadSongs() {
  const qs = new URLSearchParams();
  if (S.view === "liked") qs.set("liked", "true");
  if (S.q) qs.set("q", S.q);
  const data = await api(`/api/songs?${qs}`);
  S.songs = data.songs;
  renderSongs();
  if (S.open) {
    const fresh = S.songs.find((s) => s.id === S.open.id) || (await api(`/api/songs/${S.open.id}`).catch(() => null))?.song;
    if (fresh && (fresh.status !== S.open.status || fresh.title !== S.open.title)) openDrawer(fresh);
    else if (fresh && JSON.stringify([fresh.cover_file, fresh.audio_file, fresh.params]) !== JSON.stringify([S.open.cover_file, S.open.audio_file, S.open.params])) refreshDrawer(fresh);
  }
  if (S.current) {
    const fresh = S.songs.find((s) => s.id === S.current.id);
    if (fresh) { S.current = { ...fresh }; paintPlayerArt(); }
  }
  schedulePoll();
}

function schedulePoll() {
  clearTimeout(S.pollTimer);
  const busy = S.songs.some(isBusy);
  if (busy) S.pollTimer = setTimeout(() => loadSongs().catch(() => schedulePoll()), 1500);
}

const ICON = {
  heart: '<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z" fill="currentColor"/></svg>',
  heartO: '<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M10 15v4a2 2 0 0 0 2 2l3-7V4H7.5a2 2 0 0 0-2 1.6l-1.3 7A2 2 0 0 0 6.2 15H10zM15 4h3a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
};

const ENGINE_TAG = { acestep: "ACE 1.5", demo: "demo" };
const ENGINE_NAME = { acestep: "ACE-Step 1.5", demo: "the offline demo synth" };

function dayLabel(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(now) - start(d)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

const TASK_LABEL = { extend: "Extended", cover: "Cover", replace: "Edited", upload: "Upload", stem: "Stem", edit: "Edit", remaster: "Remaster" };

function renderSongs() {
  const ul = $("#songs");
  if (!S.songs.length) {
    ul.innerHTML = S.view === "liked"
      ? `<li class="empty"><strong>No liked songs yet</strong>Tap the heart on any song to keep it here.</li>`
      : S.q ? `<li class="empty"><strong>No matches</strong>Try a different word from a title, style or lyric.</li>`
        : `<li class="empty"><strong>Your library is empty</strong>Describe a song on the left and press Create. You get two takes to choose from.</li>`;
    return;
  }
  let lastDay = "";
  ul.innerHTML = S.songs.map((s) => {
    const day = dayLabel(s.created_at);
    const head = day !== lastDay ? `<li class="day" role="presentation">${esc(day)}</li>` : "";
    lastDay = day;
    const pending = s.status === "queued" || s.status === "running";
    const finishing = !pending && s.status === "complete" ? polishText(s) : "";
    const side = pending
      ? `<span class="s-status">${esc(s.progress || (s.status === "queued" ? "Queued" : "Generating"))}</span>`
      : finishing ? `<span class="s-status soft">${esc(finishing)}</span><span>${fmt(s.duration)}</span>`
      : s.status === "failed"
        ? `<span class="s-status failed">Failed</span>`
        : `<span>${fmt(s.duration)}</span>`;
    const badge = TASK_LABEL[s.task] ? `<span class="s-badge">${TASK_LABEL[s.task]}</span>` : "";
    const maxed = s.params?.candidates > 1 ? `<span class="s-model max" title="Max Mode: best of ${s.params.candidates} renders">MAX</span>` : "";
    const model = maxed + (ENGINE_TAG[s.engine] ? `<span class="s-model" title="Made with ${esc(ENGINE_NAME[s.engine])}">${ENGINE_TAG[s.engine]}</span>` : "");
    return `${head}<li class="song ${S.current?.id === s.id ? "current" : ""} ${s.disliked ? "disliked" : ""}" data-id="${s.id}" tabindex="0" aria-label="${esc(s.title || "Untitled")}">
      ${artHTML(s, 56)}
      <div class="s-main"><div class="s-head"><div class="s-title">${esc(s.title || (pending ? "Writing…" : "Untitled"))}</div>${model}</div>
        <div class="s-style">${esc(s.style || s.description || (s.task === "upload" ? "Your upload" : ""))}</div></div>
      <div class="s-side">${badge}${side}
        <button class="icon-btn like ${s.liked ? "liked" : ""}" aria-label="${s.liked ? "Unlike" : "Like"}" aria-pressed="${s.liked}" ${s.status !== "complete" ? "disabled" : ""}>${s.liked ? ICON.heart : ICON.heartO}</button></div>
    </li>`;
  }).join("");
  paintReels(ul);
}

$("#songs").addEventListener("click", async (e) => {
  const li = e.target.closest(".song");
  if (!li) return;
  const song = S.songs.find((s) => s.id === li.dataset.id);
  if (!song) return;
  if (e.target.closest(".like")) {
    e.stopPropagation();
    await toggleLike(song);
    return;
  }
  openDrawer(song);
  if (song.status === "complete") play(song);
});
$("#songs").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.classList.contains("song")) e.target.click();
});

async function toggleLike(song) {
  try {
    await api(`/api/songs/${song.id}`, { method: "PATCH", json: { liked: !song.liked } });
    await loadSongs();
  } catch (err) { toast(err.message, true); }
}

/* ---------------------------------------------------------- personas */
async function loadPersonas() {
  S.personas = (await api("/api/personas")).personas;
  const sel = $("#persona");
  const cur = sel.value;
  sel.innerHTML = `<option value="">None</option>` + S.personas.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  sel.value = S.personas.some((p) => p.id === cur) ? cur : "";
  $("#personaField").hidden = !S.personas.length;
  const box = $("#personas");
  box.innerHTML = S.personas.length
    ? S.personas.map((p) => `<div class="persona"><h3>${esc(p.name)}</h3><p>${esc(p.style || "No style text")}</p>
        <div class="acts"><button class="btn" data-use="${p.id}">Use</button><button class="btn danger" data-del="${p.id}">Delete</button></div></div>`).join("")
    : `<div class="empty"><strong>No personas yet</strong>Open a song you like and choose Save as persona. New songs can then reuse its style and sound.</div>`;
}
$("#personas").addEventListener("click", async (e) => {
  const use = e.target.closest("[data-use]"), del = e.target.closest("[data-del]");
  if (use) {
    $("#persona").value = use.dataset.use;
    setMode("custom");
    $("#lyrics").focus();
    toast("Persona selected for your next song");
  } else if (del) {
    try { await api(`/api/personas/${del.dataset.del}`, { method: "DELETE" }); await loadPersonas(); toast("Persona deleted"); }
    catch (err) { toast(err.message, true); }
  }
});

/* -------------------------------------------------------------- views */
function setView(view) {
  S.view = view;
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  const personas = view === "personas";
  $("#songs").hidden = personas;
  $("#personas").hidden = !personas;
  $("#search").hidden = personas;
  $("#libTitle").textContent = personas ? "Personas" : view === "liked" ? "Liked" : "Library";
  if (personas) loadPersonas(); else loadSongs();
}
$$(".nav-btn").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
let searchT;
$("#search").addEventListener("input", (e) => { clearTimeout(searchT); searchT = setTimeout(() => { S.q = e.target.value.trim(); loadSongs(); }, 250); });

/* ------------------------------------------------------------- create */
function setMode(mode) {
  S.mode = mode;
  $("#createForm").className = `mode-${mode}`;
  $$(".seg-btn").forEach((b) => { const on = b.dataset.mode === mode; b.classList.toggle("active", on); b.setAttribute("aria-selected", on); });
}
$$(".create-head .seg-btn").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
function setKind(kind) {
  S.kind = kind;
  $(".create").classList.toggle("kind-sound", kind === "sound");
  $$(".kind-seg .seg-btn").forEach((b) => { const on = b.dataset.kind === kind; b.classList.toggle("active", on); b.setAttribute("aria-selected", on); });
  $("#createTitle").textContent = kind === "sound" ? "Make a sound" : "Make a song";
  $("#description").placeholder = kind === "sound"
    ? "A deep cinematic boom with a long metallic tail"
    : "An upbeat synthwave song about driving through a neon city at 2am";
  $("#style").placeholder = kind === "sound" ? "808 kick loop, 120 BPM, punchy, dry" : "synthwave, retro 80s, driving bass, female vocals";
}
$$(".kind-seg .seg-btn").forEach((b) => b.addEventListener("click", () => setKind(b.dataset.kind)));
setMode("simple");

$("#vocalGender").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.vocal = b.dataset.v;
  $$("#vocalGender .seg-btn").forEach((x) => { const on = x === b; x.classList.toggle("active", on); x.setAttribute("aria-checked", on); });
});
const pct = (inp, out) => { const f = () => ($(out).textContent = `${$(inp).value}%`); $(inp).addEventListener("input", f); f(); };
pct("#weirdness", "#weirdOut"); pct("#styleInfluence", "#infOut");

$("#inspireInput").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const lbl = e.target.closest("label");
  lbl.classList.add("busy"); lbl.lastChild.textContent = "Looking at your picture…";
  const fd = new FormData(); fd.append("file", f); fd.append("note", $("#description").value.trim());
  try {
    const idea = await api("/api/inspire", { method: "POST", body: fd });
    $("#description").value = idea.description;
    if (idea.style) $("#style").value = idea.style;
    if (idea.title && S.mode === "custom") $("#title").value = idea.title;
    toast("Song idea written from your picture");
  } catch (err) { toast(err.message, true); }
  finally { lbl.classList.remove("busy"); lbl.lastChild.textContent = "Use a picture"; e.target.value = ""; }
});

$("#styleChips").innerHTML = STYLE_CHIPS.map((c) => `<button type="button">${esc(c)}</button>`).join("");
$("#styleChips").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const st = $("#style");
  const parts = st.value.split(",").map((x) => x.trim()).filter(Boolean);
  if (!parts.includes(b.textContent)) parts.push(b.textContent);
  st.value = parts.join(", ");
});

$(".tags").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const ta = $("#lyrics");
  const pos = ta.selectionStart ?? ta.value.length;
  const before = ta.value.slice(0, pos), after = ta.value.slice(pos);
  const pre = before && !before.endsWith("\n\n") ? (before.endsWith("\n") ? "\n" : "\n\n") : "";
  const ins = `${pre}${b.dataset.tag}\n`;
  ta.value = before + ins + after;
  ta.focus();
  ta.selectionStart = ta.selectionEnd = pos + ins.length;
});

$("#writeLyrics").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  const prompt = $("#title").value.trim() || $("#description").value.trim() || $("#style").value.trim();
  btn.disabled = true; btn.textContent = "Writing…";
  try {
    const res = await api("/api/lyrics", { json: { prompt, style: $("#style").value.trim() } });
    $("#lyrics").value = res.lyrics;
    if (!$("#style").value.trim() && res.style) $("#style").value = res.style;
    toast(res.source === "built-in" ? "Draft lyrics added. Edit them to make them yours" : "Lyrics written");
  } catch (err) { toast(err.message, true); }
  finally { btn.disabled = false; btn.textContent = "Write lyrics"; }
});

$("#surprise").addEventListener("click", async () => {
  try {
    const idea = await api("/api/idea");
    if (S.mode === "simple") $("#description").value = idea.description;
    else { $("#style").value = idea.style; if (idea.lyrics) $("#lyrics").value = idea.lyrics; }
  } catch (err) { toast(err.message, true); }
});

function numOrNull(id) { const v = $(id).value.trim(); return v === "" ? null : Number(v); }

$("#createForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#formError");
  err.hidden = true;
  const body = {
    mode: S.mode,
    description: $("#description").value.trim(),
    title: S.mode === "custom" ? $("#title").value.trim() : "",
    style: S.mode === "custom" ? $("#style").value.trim() : "",
    lyrics: S.mode === "custom" ? $("#lyrics").value.trim() : "",
    instrumental: $("#instrumental").checked,
    variations: Number($("#variations").value),
    duration: numOrNull("#duration"), bpm: numOrNull("#bpm"), seed: numOrNull("#seed"),
    key_scale: $("#key").value.trim(), vocal_language: $("#lang").value.trim() || "en",
    persona_id: $("#persona").value || null,
    quality: $("#quality").value,
    kind: S.kind, vocal_gender: S.kind === "song" ? S.vocal : "",
    weirdness: Number($("#weirdness").value), style_influence: Number($("#styleInfluence").value),
    variety: $("#variety").value, exclude_styles: $("#exclude").value.trim(), max_mode: $("#maxMode").checked,
  };
  if (S.kind === "sound") { body.lyrics = ""; body.instrumental = true; body.persona_id = null; }
  try { localStorage.setItem("oc-quality", body.quality); localStorage.setItem("oc-max", body.max_mode ? "1" : ""); } catch { /* storage may be blocked */ }
  if (S.mode === "simple" && !body.description) { err.textContent = "Describe the song you want first."; err.hidden = false; $("#description").focus(); return; }
  if (S.mode === "custom" && !body.style && !body.lyrics && !body.persona_id) { err.textContent = S.kind === "sound" ? "Describe the sound in the style box." : "Add lyrics or a style of music."; err.hidden = false; $("#style").focus(); return; }
  const btn = $("#createBtn");
  btn.disabled = true; btn.textContent = "Creating…";
  try {
    await api("/api/generate", { json: body });
    if (S.view !== "all") setView("all"); else await loadSongs();
    toast(`${body.variations} ${body.variations === 1 ? "take" : "takes"} on the way${body.max_mode ? " (Max Mode)" : ""}`);
  } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  finally { btn.disabled = false; btn.textContent = "Create"; }
});

/* ------------------------------------------------------------- upload */
$("#uploadInput").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f); fd.append("title", "");
  toast("Uploading…");
  try {
    const { song } = await api("/api/upload", { method: "POST", body: fd });
    setView("all"); openDrawer(song);
    toast("Uploaded. Extend it or make a cover");
  } catch (err) { toast(err.message, true); }
  e.target.value = "";
});

/* ------------------------------------------------------------- player */
const audio = $("#audio");
async function play(song) {
  if (S.current?.id !== song.id || S.src !== song.stream_url) {
    const resume = S.current?.id === song.id ? audio.currentTime : 0;
    S.current = { ...song };
    S.src = song.stream_url || song.audio_url;
    audio.src = S.src;
    if (resume) audio.addEventListener("loadedmetadata", () => { audio.currentTime = resume; }, { once: true });
    $("#pTitle").textContent = song.title || "Untitled";
    $("#pSub").textContent = song.style || (song.task === "upload" ? "Your upload" : "");
    drawCover($("#pCover"), song.seed ?? song.id, true);
    paintPlayerArt();
    const dl = $("#pDl"); dl.hidden = false; dl.href = `${song.audio_url}?download=1`;
    $("#pPlay").disabled = false;
    drawWave($("#pWave"), null);
    peaksFor(song).then(() => paintPlayer()).catch(() => {});
    renderSongs();
  }
  try { await audio.play(); } catch { /* autoplay may be blocked until a click */ }
}
function paintPlayerArt() {
  const img = $("#pArt"), url = S.current?.art_url;
  if (url) { if (img.getAttribute("src") !== url) img.src = url; img.hidden = false; $("#pCover").hidden = true; }
  else { img.hidden = true; $("#pCover").hidden = false; }
}
function paintPlayer() {
  if (!S.current) return;
  const p = S.peaks.get(peakKey(S.current));
  const prog = audio.duration ? audio.currentTime / audio.duration : 0;
  drawWave($("#pWave"), p, prog);
  $("#pCur").textContent = fmt(audio.currentTime);
  $("#pDur").textContent = fmt(audio.duration || S.current.duration);
  $("#pWave").setAttribute("aria-valuenow", Math.round(prog * 100));
  if (S.open && S.open.id === S.current.id) paintDrawerWave();
}
$("#pPlay").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
audio.addEventListener("play", () => { $("#pPlay").classList.add("playing"); $("#pPlay").setAttribute("aria-label", "Pause"); });
audio.addEventListener("pause", () => { $("#pPlay").classList.remove("playing"); $("#pPlay").setAttribute("aria-label", "Play"); });
audio.addEventListener("timeupdate", paintPlayer);
audio.addEventListener("loadedmetadata", paintPlayer);
$("#pWave").addEventListener("click", (e) => {
  if (!audio.duration) return;
  const r = e.currentTarget.getBoundingClientRect();
  audio.currentTime = ((e.clientX - r.left) / r.width) * audio.duration;
});
$("#pWave").addEventListener("keydown", (e) => {
  if (!audio.duration) return;
  if (e.key === "ArrowRight") audio.currentTime = Math.min(audio.duration, audio.currentTime + 5);
  if (e.key === "ArrowLeft") audio.currentTime = Math.max(0, audio.currentTime - 5);
});
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !/INPUT|TEXTAREA|SELECT|BUTTON/.test(document.activeElement.tagName) && !document.activeElement.isContentEditable && S.current) {
    e.preventDefault(); audio.paused ? audio.play() : audio.pause();
  }
  if (e.key === "Escape" && S.open) closeDrawer();
});
window.addEventListener("resize", () => { paintPlayer(); paintDrawerWave(); });

/* ------------------------------------------------------------- drawer */
function closeDrawer() { S.open = null; S.selection = null; $("#drawer").hidden = true; $(".app").classList.remove("drawer-open"); requestAnimationFrame(paintPlayer); }

const QUALITY_ROWS = [["CE", "Enjoyment"], ["PQ", "Production quality"], ["PC", "Complexity"], ["CU", "Usefulness"]];

function subText(song) {
  const pending = song.status === "queued" || song.status === "running";
  if (pending) return song.progress || "Generating";
  if (song.status === "failed") return "Generation failed";
  return polishText(song) || new Date(song.created_at * 1000).toLocaleString();
}

function infoHTML(song) {
  const p = song.params || {};
  const q = p.rating, rows = [];
  if (q && typeof q.overall === "number") {
    rows.push(`<div class="q-score"><strong>${q.overall.toFixed(1)}</strong><span>out of 10, rated by the AI judge</span></div>`);
    rows.push(`<div class="q-bars">${QUALITY_ROWS.map(([k, label]) => typeof q[k] === "number"
      ? `<div class="q-row"><span>${label}</span><span class="q-bar"><i style="width:${Math.max(0, Math.min(100, q[k] * 10))}%"></i></span><span class="q-num">${q[k].toFixed(1)}</span></div>` : "").join("")}${typeof q.clarity === "number"
      ? `<div class="q-row" title="How many of the lyrics a speech model can make out"><span>Lyric clarity</span><span class="q-bar"><i class="clar" style="width:${Math.round(q.clarity * 100)}%"></i></span><span class="q-num">${Math.round(q.clarity * 100)}%</span></div>` : ""}</div>`);
  }
  const notes = [];
  if (p.candidates > 1) notes.push(`Max Mode picked the best of ${p.candidates} renders.`);
  if (p.loudness && typeof p.loudness.after === "number") {
    notes.push(`Mastered to ${p.loudness.after.toFixed(1)} LUFS${typeof p.loudness.before === "number" ? ` (raw take was ${p.loudness.before.toFixed(1)})` : ""}, with a 24-bit WAV kept for download.`);
  }
  if (p.planner && String(p.planner).startsWith("ollama:")) notes.push(`Lyrics and production brief written by ${esc(String(p.planner).slice(7))}.`);
  if (!rows.length && !notes.length) return "";
  return `<div class="d-section"><h4>Sound check</h4>${rows.join("")}${notes.map((n) => `<p class="d-note">${n}</p>`).join("")}</div>`;
}

function artNoteHTML(song) {
  const p = song.params || {};
  if (p.art_status === "failed") return `<p class="d-note warn">The cover could not be painted${p.art_error ? `: ${esc(p.art_error)}` : ""}.</p>`;
  if (p.art_prompt && p.art_prompt !== "(uploaded)" && song.art_url) return `<p class="d-note">Cover: ${esc(p.art_prompt)}</p>`;
  return "";
}

/* Update the parts of an open drawer that change while a song is being finished, without
   wiping a panel the person is using. */
function refreshDrawer(song) {
  const old = S.open;
  S.open = song;
  const art = $("#dArtBox");
  if (art && (old.cover_file !== song.cover_file || old.status !== song.status)) { art.innerHTML = artHTML(song, 120, "d-art"); paintReels(art); }
  const sub = $("#dSub"); if (sub) sub.textContent = subText(song);
  const info = $("#dInfo"); if (info) info.innerHTML = infoHTML(song);
  const note = $("#dArtNote"); if (note) note.innerHTML = artNoteHTML(song);
  const dis = $("#dDislike"); if (dis) dis.classList.toggle("on", !!song.disliked);
  if (old.audio_file !== song.audio_file) peaksFor(song).then(paintDrawerWave).catch(() => {});
}

function openDrawer(song) {
  const sameSong = S.open && S.open.id === song.id;
  S.open = song;
  const d = $("#drawer"), inner = $("#drawerInner");
  if (!sameSong) { S.selection = null; d.scrollTop = 0; }
  d.hidden = false;
  $(".app").classList.add("drawer-open");
  const done = song.status === "complete";
  const pending = song.status === "queued" || song.status === "running";
  const parent = song.parent_id ? `<a href="#" class="link" data-parent="${song.parent_id}">Open source clip</a>` : "";
  const meta = [song.bpm && `${song.bpm} BPM`, song.key_scale, song.duration && fmt(song.duration), song.engine && `via ${song.engine}`, TASK_LABEL[song.task], song.params?.kind === "sound" && "Sound"]
    .filter(Boolean).map((m) => `<span class="s-badge">${esc(m)}</span>`).join("");
  const dur = song.duration || 0;
  const stemsOk = S.status?.stems;
  const artOk = S.status?.aux?.cover;
  inner.innerHTML = `
    <div class="d-top">
      <div class="d-art-box" id="dArtBox">${artHTML(song, 120, "d-art")}</div>
      <div class="d-head">
        <div class="d-title" id="dTitle" ${done ? 'contenteditable="plaintext-only" spellcheck="false" title="Click to rename"' : ""}>${esc(song.title || "Untitled")}</div>
        <div class="d-sub" id="dSub">${esc(subText(song))}</div>
        ${parent}
        ${done ? `<div class="d-art-acts">
          <button class="link" data-act="art" ${artOk ? "" : 'disabled title="Start the AI service (setup_ai_service.bat) to paint covers"'}>${song.art_url ? "New cover" : "Paint a cover"}</button>
          <label class="link">Use my image<input type="file" id="artUpload" accept="image/png,image/jpeg,image/webp"></label>
        </div>` : ""}
      </div>
      <div class="d-tools">
        <button class="icon-btn d-close" aria-label="Close details" id="dClose">${ICON.close}</button>
        ${done ? `<button class="icon-btn dislike ${song.disliked ? "on" : ""}" id="dDislike" aria-label="Not for me" title="Not for me">${ICON.down}</button>` : ""}
      </div>
    </div>
    <div id="dArtNote">${artNoteHTML(song)}</div>
    <div class="d-meta">${meta}</div>
    ${song.status === "failed" ? `<p class="d-error">${esc(song.error || "Unknown error")}</p>` : ""}
    ${done ? `
      <canvas class="d-wave" id="dWave" aria-label="Waveform. Drag to pick a section"></canvas>
      <p class="d-hint" id="dHint">Drag across the waveform to pick a section to replace, crop or cut.</p>
      <div class="d-actions">
        <button class="btn primary" data-act="play">Play</button>
        <button class="btn" data-act="download">Download</button>
        <button class="btn" data-act="extend">Extend</button>
        <button class="btn" data-act="cover">Cover in new style</button>
        <button class="btn" data-act="remaster">Remaster</button>
        <button class="btn" data-act="edit">Edit</button>
        <button class="btn" data-act="stems" ${stemsOk ? "" : 'disabled title="Start the AI service or install Demucs to split stems"'}>Split stems</button>
        <button class="btn" data-act="persona">Save as persona</button>
        <button class="btn" data-act="reuse">Reuse prompt</button>
        <button class="btn danger" data-act="delete">Delete</button>
      </div>
      <div id="dPanel"></div>` : ""}
    ${!pending && !done ? `<div class="d-actions"><button class="btn danger" data-act="delete">Delete</button></div>` : ""}
    <div id="dInfo">${infoHTML(song)}</div>
    ${song.style ? `<div class="d-section"><h4>Style</h4><p class="d-style">${esc(song.style)}</p></div>` : ""}
    ${song.lyrics ? `<div class="d-section"><h4>Lyrics</h4><pre class="d-lyrics">${esc(song.lyrics)}</pre></div>` : ""}`;
  paintReels(inner);
  $("#dClose").onclick = closeDrawer;
  const par = $("[data-parent]", inner);
  if (par) par.onclick = async (e) => { e.preventDefault(); try { openDrawer((await api(`/api/songs/${par.dataset.parent}`)).song); } catch (err) { toast(err.message, true); } };
  const t = $("#dTitle");
  if (t && done) {
    t.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); t.blur(); } });
    t.addEventListener("blur", async () => {
      const v = t.textContent.trim();
      if (!v || v === song.title) { t.textContent = song.title; return; }
      try { await api(`/api/songs/${song.id}`, { method: "PATCH", json: { title: v } }); song.title = v; if (S.current?.id === song.id) { S.current.title = v; $("#pTitle").textContent = v; } toast("Renamed"); loadSongs(); }
      catch (err) { toast(err.message, true); t.textContent = song.title; }
    });
  }
  $$("[data-act]", inner).forEach((b) => b.addEventListener("click", () => act(b.dataset.act, S.open)));
  const dis = $("#dDislike");
  if (dis) dis.onclick = async () => {
    try {
      const { song: s2 } = await api(`/api/songs/${song.id}`, { method: "PATCH", json: { disliked: !S.open.disliked, liked: false } });
      refreshDrawer(s2); loadSongs(); toast(s2.disliked ? "Noted. You will see this one dimmed" : "Okay, back to neutral");
    } catch (err) { toast(err.message, true); }
  };
  const up = $("#artUpload");
  if (up) up.onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { const { song: s2 } = await api(`/api/songs/${song.id}/art/upload`, { method: "POST", body: fd }); refreshDrawer(s2); loadSongs(); toast("Cover updated"); }
    catch (err) { toast(err.message, true); }
    e.target.value = "";
  };
  if (done) {
    peaksFor(song).then(paintDrawerWave).catch(() => {});
    bindSelection($("#dWave"), dur);
  }
}

function paintDrawerWave() {
  const c = $("#dWave");
  if (!c || !S.open) return;
  const p = S.peaks.get(peakKey(S.open));
  const prog = S.current?.id === S.open.id && audio.duration ? audio.currentTime / audio.duration : 0;
  drawWave(c, p, prog, S.selection, S.open.duration || audio.duration || 0);
}

function bindSelection(c, dur) {
  let start = null;
  const at = (e) => { const r = c.getBoundingClientRect(); return Math.max(0, Math.min(dur, ((e.clientX - r.left) / r.width) * dur)); };
  c.addEventListener("pointerdown", (e) => { start = at(e); c.setPointerCapture(e.pointerId); });
  c.addEventListener("pointermove", (e) => {
    if (start === null) return;
    const x = at(e); S.selection = [Math.min(start, x), Math.max(start, x)]; paintDrawerWave();
  });
  c.addEventListener("pointerup", (e) => {
    if (start === null) return;
    const x = at(e); start = null;
    if (!S.selection || S.selection[1] - S.selection[0] < 0.5) {
      S.selection = null;
      if (S.current?.id === S.open.id && audio.duration) audio.currentTime = x;
      paintDrawerWave();
      return;
    }
    $("#dHint").textContent = `Selected ${S.selection[0].toFixed(1)}s to ${S.selection[1].toFixed(1)}s.`;
    act("section", S.open);
  });
}

function panel(html) { const p = $("#dPanel"); p.innerHTML = `<div class="d-panel">${html}</div>`; return p; }

async function runEdit(path, body, label) {
  try {
    const res = await api(path, { json: body });
    const songs = res.songs || (res.song ? [res.song] : []);
    toast(label);
    await loadSongs();
    if ($("#dPanel")) $("#dPanel").innerHTML = "";
    if (songs[0]) openDrawer(songs[0]);
  } catch (err) { toast(err.message, true); }
}

async function act(what, song) {
  const dur = song.duration || 0;
  switch (what) {
    case "play": play(song); break;
    case "reuse":
      setMode("custom");
      $("#style").value = song.style || "";
      $("#lyrics").value = song.lyrics || "";
      $("#title").value = "";
      $("#instrumental").checked = !!song.instrumental;
      toast("Prompt copied into Custom mode");
      break;
    case "extend": {
      const p = panel(`<h4>Extend</h4>
        <label>Add seconds (5 to 90)<input type="number" id="xSec" min="5" max="90" value="30"></label>
        <label>Lyrics for the new part <span class="opt">optional</span><textarea id="xLyr" rows="4" placeholder="[Verse 3]&#10;..."></textarea></label>
        <label>Style <textarea id="xStyle" rows="2">${esc(song.style)}</textarea></label>
        <button class="btn primary" id="xGo">Extend from ${fmt(dur)}</button>`);
      $("#xGo", p).onclick = () => runEdit(`/api/songs/${song.id}/extend`, {
        seconds: Number($("#xSec").value), lyrics: $("#xLyr").value.trim() || null, style: $("#xStyle").value.trim() || null,
      }, "Extending your song");
      break;
    }
    case "cover": {
      const p = panel(`<h4>Cover in new style</h4>
        <label>New style<textarea id="cStyle" rows="2" placeholder="jazz trio, upright bass, brushed drums"></textarea></label>
        <label>How closely to follow the original: <span id="cVal">0.6</span><input type="range" id="cStr" min="0.1" max="1" step="0.05" value="0.6"></label>
        <button class="btn primary" id="cGo">Make cover</button>`);
      $("#cStr", p).oninput = (e) => ($("#cVal").textContent = e.target.value);
      $("#cGo", p).onclick = () => {
        const style = $("#cStyle").value.trim();
        if (!style) { toast("Describe the new style first", true); $("#cStyle").focus(); return; }
        runEdit(`/api/songs/${song.id}/cover`, { style, strength: Number($("#cStr").value) }, "Two cover takes on the way");
      };
      break;
    }
    case "replace": {
      const sel = S.selection || [Math.max(0, dur * 0.4), Math.min(dur, dur * 0.4 + 10)];
      const p = panel(`<h4>Replace section</h4>
        <div class="two"><label>Start (s)<input type="number" id="rS" step="0.1" min="0" max="${dur}" value="${sel[0].toFixed(1)}"></label>
        <label>End (s)<input type="number" id="rE" step="0.1" min="0" max="${dur}" value="${sel[1].toFixed(1)}"></label></div>
        <label>New lyrics for this part <span class="opt">optional</span><textarea id="rLyr" rows="3"></textarea></label>
        <button class="btn primary" id="rGo">Replace section</button>`);
      const sync = () => { S.selection = [Number($("#rS").value), Number($("#rE").value)]; paintDrawerWave(); };
      $("#rS", p).oninput = sync; $("#rE", p).oninput = sync; sync();
      $("#rGo", p).onclick = () => runEdit(`/api/songs/${song.id}/replace`, {
        start: Number($("#rS").value), end: Number($("#rE").value), lyrics: $("#rLyr").value.trim() || null,
      }, "Rewriting that section");
      break;
    }
    case "section": {
      const sel = S.selection; if (!sel) break;
      const len = sel[1] - sel[0];
      const p = panel(`<h4>${sel[0].toFixed(1)}s to ${sel[1].toFixed(1)}s</h4>
        <p class="d-note">Pick what to do with this part of the song.</p>
        <div class="two"><button class="btn primary" id="sRep" ${len < 3 ? 'disabled title="Pick at least 3 seconds to replace"' : ""}>Replace with new music</button>
        <button class="btn" id="sCrop">Keep only this</button>
        <button class="btn" id="sCut">Cut this out</button>
        <button class="btn" id="sClear">Clear selection</button></div>`);
      $("#sRep", p).onclick = () => act("replace", song);
      $("#sCrop", p).onclick = () => runEdit(`/api/songs/${song.id}/edit`, { op: "crop", start: sel[0], end: sel[1] }, "Cropped copy added to your library");
      $("#sCut", p).onclick = () => runEdit(`/api/songs/${song.id}/edit`, { op: "remove", start: sel[0], end: sel[1] }, "Copy with that part cut added");
      $("#sClear", p).onclick = () => { S.selection = null; paintDrawerWave(); $("#dPanel").innerHTML = ""; $("#dHint").textContent = "Drag across the waveform to pick a section to replace, crop or cut."; };
      break;
    }
    case "edit": {
      const p = panel(`<h4>Edit</h4>
        <p class="d-note">Each edit saves a new copy and keeps the original. To crop or cut a part, drag across the waveform above.</p>
        <label>Speed <span id="eSpV">1.00x</span><input type="range" id="eSp" min="0.5" max="2" step="0.05" value="1"></label>
        <button class="btn" id="eSpGo">Change speed</button>
        <div class="two" style="margin-top:12px">
          <label>Fade length (s)<input type="number" id="eFd" min="0.5" max="30" step="0.5" value="3"></label><span></span>
          <button class="btn" id="eIn">Fade in</button><button class="btn" id="eOut">Fade out</button>
        </div>
        <button class="btn" id="eRev" style="margin-top:10px;width:100%">Reverse</button>`);
      $("#eSp", p).oninput = (e) => ($("#eSpV").textContent = `${Number(e.target.value).toFixed(2)}x`);
      $("#eSpGo", p).onclick = () => runEdit(`/api/songs/${song.id}/edit`, { op: "speed", factor: Number($("#eSp").value) }, "Speed changed in a new copy");
      $("#eIn", p).onclick = () => runEdit(`/api/songs/${song.id}/edit`, { op: "fade_in", seconds: Number($("#eFd").value) }, "Faded in");
      $("#eOut", p).onclick = () => runEdit(`/api/songs/${song.id}/edit`, { op: "fade_out", seconds: Number($("#eFd").value) }, "Faded out");
      $("#eRev", p).onclick = () => runEdit(`/api/songs/${song.id}/edit`, { op: "reverse" }, "Reversed copy added");
      break;
    }
    case "download": {
      const base = `${song.audio_url}?download=1`;
      panel(`<h4>Download</h4>
        <div class="dl-list">
          <a class="btn" href="${base}&format=mp3">MP3 <small>320 kbps${song.has_wav ? ", mastered" : ""}</small></a>
          <a class="btn" href="${base}&format=wav">WAV <small>${song.has_wav ? "24-bit, mastered" : "lossless"}</small></a>
          <a class="btn" href="${base}&format=mp4" id="dlVid">Video <small>cover art and waveform, MP4</small></a>
        </div>`);
      $("#dlVid").onclick = () => toast("Rendering the video. The download starts when it is ready");
      break;
    }
    case "remaster":
      runEdit(`/api/songs/${song.id}/remaster`, {}, "Remastering at best quality");
      break;
    case "art": {
      const prompt = song.params?.art_prompt && song.params.art_prompt !== "(uploaded)" ? song.params.art_prompt : "";
      const p = panel(`<h4>Cover art</h4>
        <label>Describe the picture <span class="opt">leave blank to let the AI choose</span><textarea id="aPrompt" rows="3" maxlength="1200">${esc(prompt)}</textarea></label>
        <button class="btn primary" id="aGo">Paint cover</button>`);
      $("#aGo", p).onclick = async () => {
        try {
          const { song: s2 } = await api(`/api/songs/${song.id}/art`, { json: { prompt: $("#aPrompt").value.trim() } });
          $("#dPanel").innerHTML = ""; refreshDrawer(s2); await loadSongs(); toast("Painting a new cover");
        } catch (err) { toast(err.message, true); }
      };
      break;
    }
    case "persona": {
      const p = panel(`<h4>Save as persona</h4>
        <label>Name<input id="pName" maxlength="60" value="${esc((song.title || "My") + " persona")}"></label>
        <button class="btn primary" id="pGo">Save persona</button>`);
      $("#pGo", p).onclick = async () => {
        try {
          await api("/api/personas", { json: { song_id: song.id, name: $("#pName").value.trim() } });
          await loadPersonas(); $("#dPanel").innerHTML = ""; toast("Persona saved. Pick it when you create");
        } catch (err) { toast(err.message, true); }
      };
      break;
    }
    case "stems": {
      const four = S.status?.aux?.stems;
      const p = panel(`<h4>Split stems</h4>
        <div class="two"><button class="btn primary" id="st4" ${four ? "" : 'disabled title="Four stems need the AI service"'}>Vocals, drums, bass, other</button>
        <button class="btn" id="st2">Vocals and instrumental</button></div>`);
      $("#st4", p).onclick = () => runEdit(`/api/songs/${song.id}/stems`, { stems: 4 }, "Splitting into four stems");
      $("#st2", p).onclick = () => runEdit(`/api/songs/${song.id}/stems`, { stems: 2 }, "Splitting into vocals and instrumental");
      break;
    }
    case "delete":
      if (!confirm(`Delete "${song.title || "Untitled"}"? This removes the audio file too.`)) return;
      try {
        await api(`/api/songs/${song.id}`, { method: "DELETE" });
        if (S.current?.id === song.id) { audio.pause(); audio.removeAttribute("src"); S.current = null; $("#pTitle").textContent = "Nothing playing"; $("#pSub").textContent = "Pick a song from your library"; $("#pPlay").disabled = true; $("#pDl").hidden = true; drawWave($("#pWave"), null); }
        closeDrawer(); await loadSongs(); toast("Deleted");
      } catch (err) { toast(err.message, true); }
      break;
  }
}

/* --------------------------------------------------------------- boot */
try {
  const q = localStorage.getItem("oc-quality"); if (q) $("#quality").value = q;
  if (localStorage.getItem("oc-max")) $("#maxMode").checked = true;
} catch { /* ignore */ }

(async function boot() {
  drawWave($("#pWave"), null);
  drawCover($("#pCover"), "openchorus");
  await loadStatus();
  await Promise.all([loadSongs(), loadPersonas()]);
})();
