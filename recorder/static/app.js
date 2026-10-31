"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const store = {
  get(k, d = "") { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 사생활 보호 모드 */ } },
};
let token = store.get("token");
let state = null;
let tab = "rec";

// ---------- 공유받은 주소 ----------
// 안드로이드 앱: #token=...&share=...   /   설치한 웹앱(PWA) 공유: ?url=...&text=...&title=...
const hashParams = new URLSearchParams(location.hash.slice(1));
const queryParams = new URLSearchParams(location.search);
if (hashParams.get("token")) { token = hashParams.get("token"); store.set("token", token); }
const sharedText = [hashParams.get("share"), queryParams.get("url"), queryParams.get("text")].filter(Boolean).join(" ");
const sharedUrl = (sharedText.match(/https?:\/\/[^\s]+/) || [])[0] || "";
const sharedTitle = queryParams.get("title") || hashParams.get("title") || "";
if (location.hash || location.search) history.replaceState(null, "", location.pathname);
const nativeApp = window.AnimeRecorderApp; // 안드로이드 앱 안에서 실행 중이면 존재

// ---------- 공통 ----------
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 2800);
}
function buzz(ms = 40) { try { navigator.vibrate?.(ms); } catch { } }

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { Authorization: "Bearer " + token, ...(opts.body ? { "Content-Type": "application/json" } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { askToken(); throw new Error("PC 연결이 필요합니다"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : (j.detail?.[0]?.msg || JSON.stringify(j.detail)); } catch { }
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res.blob();
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); await refresh(); return r; }
  catch (e) { toast(e.message, true); }
}

function askToken() {
  const dlg = $("#token-dlg");
  if (dlg.open || nativeApp) return;
  $("#token-input").value = token;
  dlg.showModal();
}
$("#token-dlg").addEventListener("close", () => {
  token = $("#token-input").value.trim();
  store.set("token", token);
  refresh();
});

const fmt = (sec) => {
  if (sec == null || !isFinite(sec)) return "--:--";
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
};
const gb = (b) => (b / 1024 ** 3).toFixed(b > 100 * 1024 ** 3 ? 0 : 1) + "GB";
const fileName = (p) => (p || "").split(/[\\/]/).pop();

// ---------- 탭 ----------
document.querySelectorAll(".tabbar button").forEach((b) =>
  b.addEventListener("click", () => {
    tab = b.dataset.tab;
    document.querySelectorAll(".tabbar button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + tab));
    if (tab === "list") loadRecordings();
  }));

// ---------- 녹화 시작 ----------
if (sharedUrl) {
  $("#url").value = sharedUrl;
  $("#shared").classList.remove("hidden");
  $("#shared-url").textContent = sharedTitle || sharedUrl;
}

$("#btn-start").addEventListener("click", async () => {
  const btn = $("#btn-start");
  const url = $("#url").value.trim();
  btn.disabled = true;
  btn.textContent = url ? "PC에서 페이지 여는 중…" : "시작하는 중…";
  const r = await act(() => api("/api/record/start", {
    method: "POST",
    body: { url, title: $("#title").value.trim(), start_at: $("#start-at").value.trim() || null, stop_on_end: $("#stop-on-end").checked },
  }), "PC에서 녹화를 시작했습니다");
  btn.disabled = false; btn.textContent = "● PC에서 녹화 시작";
  if (r) { buzz(80); $("#shared").classList.add("hidden"); }
});

// ---------- 녹화 중 ----------
$("#btn-stop").addEventListener("click", () => {
  if (!confirm("녹화를 멈추고 저장할까요?")) return;
  act(() => api("/api/record/stop", { method: "POST" }), "녹화를 저장했습니다");
});
$("#btn-pause").addEventListener("click", () => {
  const paused = state?.obs?.paused;
  act(() => api(paused ? "/api/record/resume" : "/api/record/pause", { method: "POST" }));
});
const seek = (delta) => act(() => api("/api/player/seek", { method: "POST", body: { delta } }));
$("#btn-back").addEventListener("click", () => seek(-10));
$("#btn-fwd").addEventListener("click", () => seek(10));
$("#btn-live-rename").addEventListener("click", () => {
  const sid = state?.session?.id, title = $("#live-title").value.trim();
  if (!sid || !title) return;
  act(() => api(`/api/recordings/${sid}`, { method: "PATCH", body: { title } }), "녹화가 끝나면 이 이름으로 저장됩니다");
});

function renderStatus() {
  const s = state, obs = s.obs;
  const conn = $("#conn");
  conn.textContent = obs.connected ? "OBS 연결됨" : "OBS 꺼짐";
  conn.className = "pill " + (obs.connected ? "ok" : "bad");
  $("#disk").textContent = s.disk ? `남은 용량 ${gb(s.disk.free)}` : "";

  const live = obs.recording;
  $("#idle-panel").classList.toggle("hidden", live || s.starting);
  $("#live-panel").classList.toggle("hidden", !live);
  $("#rec-badge").classList.toggle("hidden", !live);
  $("#rec-badge").classList.toggle("paused", obs.paused);
  $("#timer").textContent = (obs.paused ? "일시정지 " : "") + fmt(obs.duration_ms / 1000);
  $("#btn-pause").textContent = obs.paused ? "▶ 다시 재생" : "⏸ 일시정지";

  const p = s.player;
  const canSeek = s.controllable && p?.found;
  $("#btn-back").disabled = $("#btn-fwd").disabled = !canSeek;
  $("#np-title").textContent = s.session?.title || p?.title || "";
  if (p?.found) {
    $("#np-time").textContent = fmt(p.time);
    $("#np-end").textContent = (p.duration ? fmt(p.duration) : "") + (s.stop_on_end ? " · 끝나면 자동 정지" : "");
    $("#np-fill").style.width = p.duration ? `${Math.min(100, (p.time / p.duration) * 100)}%` : "0";
  } else {
    $("#np-time").textContent = s.session?.url ? "영상을 찾지 못해 화면 그대로 녹화 중" : "PC 화면 녹화 중";
    $("#np-end").textContent = "";
    $("#np-fill").style.width = "0";
  }
  const lt = $("#live-title");
  if (s.session && document.activeElement !== lt && lt.dataset.sid !== s.session.id) {
    lt.value = s.session.title; lt.dataset.sid = s.session.id;
  }

  renderIsolation(s);
  $("#events").innerHTML = s.events.map((e) => `<li class="${e.level}">${e.time} · ${esc(e.msg)}</li>`).join("");
}

function renderIsolation(s) {
  const items = s.isolation || [];
  const box = $("#iso");
  box.classList.toggle("hidden", !items.length);
  if (!items.length) return;
  const warns = items.filter((i) => i.level === "warn").length;
  box.classList.toggle("has-warn", warns > 0);
  const sum = $("#iso-sum");
  sum.textContent = warns ? `확인 필요 ${warns}개` : "모두 정상";
  sum.className = warns ? "sum-warn" : "sum-ok";
  const extra = s.browser_window ? [{ level: "ok", msg: `녹화 창: ${s.browser_window}` }] : [];
  const html = [...extra, ...items].map((i) => `<li class="${i.level}">${esc(i.msg)}</li>`).join("");
  if ($("#iso-list").dataset.v !== html) { $("#iso-list").innerHTML = html; $("#iso-list").dataset.v = html; }
}

async function refresh() {
  if (!token) return askToken();
  try {
    state = await api("/api/status"); renderStatus();
    try { nativeApp?.onStatus(JSON.stringify({ recording: state.obs.recording })); } catch { }
  } catch (e) { $("#conn").textContent = "PC 연결 안 됨"; $("#conn").className = "pill bad"; }
}

let lastPreviewUrl = null;
async function refreshPreview() {
  if (tab !== "rec" || document.hidden || !token || !state?.obs?.connected) return;
  try {
    const blob = await api("/api/preview?width=" + Math.min(1280, Math.round(innerWidth * devicePixelRatio)));
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    $("#preview").src = url;
    $("#preview-empty").classList.add("hidden");
    if (lastPreviewUrl) URL.revokeObjectURL(lastPreviewUrl);
    lastPreviewUrl = url;
  } catch { /* 미리보기는 실패해도 무시 */ }
}

// ---------- 녹화본 ----------
async function loadRecordings() {
  let list;
  try { list = await api("/api/recordings"); } catch (e) { return toast(e.message, true); }
  const ul = $("#recordings");
  if (!list.length) { ul.innerHTML = '<p class="muted">아직 녹화본이 없습니다.</p>'; return; }
  ul.innerHTML = list.map((s) => {
    const live = !s.ended_at;
    return `<li class="card" data-sid="${esc(s.id)}" data-title="${esc(s.title)}">
      <div class="head"><b>${esc(s.title)}</b>${live ? '<span class="pill bad">녹화 중</span>' : ""}</div>
      <div class="meta">${esc(s.started_at.replace("T", " "))}${s.duration ? " · " + fmt(s.duration) : ""}</div>
      ${s.output_path ? `<div class="meta file">${esc(fileName(s.output_path))}</div>`
        : live ? "" : '<div class="meta state-error">녹화 파일 경로를 찾지 못했습니다</div>'}
      <div class="actions">
        <button class="small" data-act="rename">이름 변경</button>
        ${!live ? '<button class="small danger" data-act="delete">목록에서 삭제</button>' : ""}
      </div></li>`;
  }).join("");
}
$("#recordings").addEventListener("click", async (e) => {
  const b = e.target.closest(".actions button"); if (!b) return;
  const card = b.closest(".card"), sid = card.dataset.sid;
  if (b.dataset.act === "rename") {
    const title = prompt("새 이름 (PC의 파일 이름도 바뀝니다)", card.dataset.title);
    if (!title || title === card.dataset.title) return;
    await act(() => api(`/api/recordings/${sid}`, { method: "PATCH", body: { title } }), "이름을 바꿨습니다");
  }
  if (b.dataset.act === "delete" && confirm("목록에서만 지웁니다 (PC의 녹화 파일은 그대로 남습니다).")) {
    await act(() => api(`/api/recordings/${sid}`, { method: "DELETE" }));
  }
  loadRecordings();
});

// ---------- 시작 ----------
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => { });
refresh();
setInterval(() => !document.hidden && refresh(), 1000);
setInterval(refreshPreview, 2500);
document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); refreshPreview(); } });
