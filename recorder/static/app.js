"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const WD = ["월", "화", "수", "목", "금", "토", "일"];

const store = {
  get(k, d = "") { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 사생활 보호 모드 */ } },
};
let token = store.get("token");
let state = null;
let tab = "rec";
let autoStopMin = "";

// ---------- 공통 ----------
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 2600);
}
function buzz(ms = 40) { try { navigator.vibrate?.(ms); } catch { } }

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { Authorization: "Bearer " + token, ...(opts.body ? { "Content-Type": "application/json" } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { askToken(); throw new Error("토큰이 필요합니다"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { }
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
  if (dlg.open) return;
  $("#token-input").value = token;
  dlg.showModal();
}
$("#token-dlg").addEventListener("close", () => {
  token = $("#token-input").value.trim();
  store.set("token", token);
  refresh().then(loadRecent);
});

const fmt = (sec) => {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
};
const gb = (b) => (b / 1024 ** 3).toFixed(b > 100 * 1024 ** 3 ? 0 : 1) + "GB";
const markerTime = (m) => m.kind === "point" ? fmt(m.t) : `${fmt(m.start)}~${m.end == null ? "…" : fmt(m.end)}`;

// ---------- 탭 ----------
document.querySelectorAll(".tabbar button").forEach((b) =>
  b.addEventListener("click", () => {
    tab = b.dataset.tab;
    document.querySelectorAll(".tabbar button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.id === "tab-" + tab));
    if (tab === "hist") loadSessions();
    if (tab === "sched") loadJobs();
  }));

// ---------- 녹화 탭 ----------
$("#autostop-chips").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  let v = b.dataset.min;
  if (v === "custom") {
    v = prompt("몇 분 뒤에 자동으로 멈출까요?", "24");
    if (!v || !(+v > 0)) return;
    b.textContent = `${+v}분`;
  }
  autoStopMin = v;
  document.querySelectorAll("#autostop-chips .chip").forEach((x) => x.classList.toggle("on", x === b));
});

$("#btn-start").addEventListener("click", async () => {
  const btn = $("#btn-start");
  btn.disabled = true; btn.textContent = "시작하는 중…";
  await act(() => api("/api/record/start", {
    method: "POST",
    body: { url: $("#url").value.trim(), title: $("#title").value.trim(), auto_stop_min: autoStopMin ? +autoStopMin : null },
  }), "녹화를 시작했습니다");
  btn.disabled = false; btn.textContent = "● 녹화 시작";
  buzz(80);
});

$("#btn-stop").addEventListener("click", () => {
  if (!confirm("녹화를 멈출까요?")) return;
  act(() => api("/api/record/stop", { method: "POST" }), "녹화를 저장했습니다");
});
$("#btn-pause").addEventListener("click", () => {
  const paused = state?.obs?.paused;
  act(() => api(paused ? "/api/record/resume" : "/api/record/pause", { method: "POST" }));
});

async function mark(kind, label = "") {
  buzz();
  const r = await act(() => api("/api/mark", { method: "POST", body: { kind, label } }));
  if (r) toast(kind === "in" ? "구간 시작" : kind === "out" ? "구간 저장" : `★ ${fmt(r.t)} ${label}`);
}
$("#btn-mark").addEventListener("click", () => mark("point"));
$("#btn-range").addEventListener("click", () => {
  const open = state?.session?.markers?.some((m) => m.kind === "range" && m.end == null);
  mark(open ? "out" : "in");
});
$("#quick-labels").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (b) mark("point", b.dataset.label);
});

function renderMarkers(ul, s, withClip) {
  ul.innerHTML = (s.markers || []).slice().reverse().map((m) => `
    <li data-sid="${esc(s.id)}" data-mid="${esc(m.id)}">
      <span class="ts">${markerTime(m)}</span>
      <span class="lbl">${esc(m.label) || '<span class="muted">(설명 없음)</span>'}
        ${withClip && m.clip_path ? '<br><span class="ok">클립 저장됨</span>' : ""}
        ${withClip && m.clip_error ? `<br><span class="err">${esc(m.clip_error)}</span>` : ""}</span>
      <button class="small" data-act="edit">수정</button>
      <button class="small danger" data-act="del">삭제</button>
    </li>`).join("");
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest(".markers button"); if (!b) return;
  const li = b.closest("li"), sid = li.dataset.sid, mid = li.dataset.mid;
  const path = `/api/sessions/${sid}/markers/${mid}`;
  if (b.dataset.act === "edit") {
    const cur = li.querySelector(".lbl").childNodes[0]?.textContent?.trim() || "";
    const label = prompt("장면 설명", cur.startsWith("(") ? "" : cur);
    if (label === null) return;
    await act(() => api(path, { method: "PATCH", body: { label } }));
  } else if (confirm("이 표시를 지울까요?")) {
    await act(() => api(path, { method: "DELETE" }));
  }
  if (tab === "hist") loadSessions();
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
  $("#autostop").classList.toggle("hidden", !(live && s.auto_stop_in != null));
  if (s.auto_stop_in != null) $("#autostop").textContent = `${fmt(s.auto_stop_in)} 후 자동 정지`;
  $("#btn-pause").textContent = obs.paused ? "▶ 다시 녹화" : "⏸ 일시정지";

  const ql = $("#quick-labels");
  const want = s.quick_labels.join("|");
  if (ql.dataset.v !== want) {
    ql.dataset.v = want;
    ql.innerHTML = s.quick_labels.map((l) => `<button class="chip" data-label="${esc(l)}">${esc(l)}</button>`).join("");
  }
  const sess = s.session;
  const open = sess?.markers?.find((m) => m.kind === "range" && m.end == null);
  $("#btn-range").textContent = open ? `구간 끝 (${fmt(open.start)}~)` : "구간 시작";
  $("#btn-range").classList.toggle("on", !!open);
  if (sess) {
    $("#mark-count").textContent = sess.markers.length ? `${sess.markers.length}개` : "";
    renderMarkers($("#live-markers"), sess, false);
  } else {
    $("#live-markers").innerHTML = ""; $("#mark-count").textContent = "";
  }
  $("#events").innerHTML = s.events.map((e) => `<li class="${e.level}">${e.time} · ${esc(e.msg)}</li>`).join("");
}

async function refresh() {
  if (!token) return askToken();
  try { state = await api("/api/status"); renderStatus(); }
  catch (e) { $("#conn").textContent = "PC 연결 안 됨"; $("#conn").className = "pill bad"; }
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

async function loadRecent() {
  if (!token) return;
  try {
    const list = await api("/api/recent-urls");
    $("#recent").innerHTML = list.map((r) => `<option value="${esc(r.url)}">${esc(r.title)}</option>`).join("");
  } catch { }
}

// ---------- 기록 탭 ----------
async function loadSessions() {
  let list;
  try { list = await api("/api/sessions"); } catch (e) { return toast(e.message, true); }
  const ul = $("#sessions");
  if (!list.length) { ul.innerHTML = '<p class="muted">아직 녹화 기록이 없습니다.</p>'; return; }
  ul.innerHTML = list.map((s) => {
    const live = !s.ended_at;
    const st = { running: "클립 만드는 중…", done: s.export_message, error: s.export_message }[s.export_state] || "";
    return `<li class="card" data-sid="${esc(s.id)}">
      <div class="head"><b>${esc(s.title)}</b>${live ? '<span class="pill bad">녹화 중</span>' : ""}</div>
      <div class="meta">${esc(s.started_at.replace("T", " "))}${s.duration ? " · " + fmt(s.duration) : ""} · 표시 ${s.markers.length}개</div>
      ${s.output_path ? `<div class="meta">${esc(s.output_path)}</div>` : live ? "" : '<div class="meta state-error">녹화 파일 경로를 찾지 못했습니다</div>'}
      ${st ? `<div class="meta state-${s.export_state}">${esc(st)}</div>` : ""}
      <ul class="markers"></ul>
      <div class="actions">
        ${!live && s.markers.length && s.output_path ? `<button class="small" data-act="export" ${s.export_state === "running" ? "disabled" : ""}>클립 만들기</button>` : ""}
        ${!live ? '<button class="small danger" data-act="delete">기록 삭제</button>' : ""}
      </div></li>`;
  }).join("");
  list.forEach((s) => renderMarkers(ul.querySelector(`[data-sid="${CSS.escape(s.id)}"] .markers`), s, true));
  if (list.some((s) => s.export_state === "running")) setTimeout(() => tab === "hist" && loadSessions(), 2000);
}
$("#sessions").addEventListener("click", async (e) => {
  const b = e.target.closest(".actions button"); if (!b) return;
  const sid = b.closest(".card").dataset.sid;
  if (b.dataset.act === "export") await act(() => api(`/api/sessions/${sid}/export`, { method: "POST" }), "클립을 만들기 시작했습니다");
  if (b.dataset.act === "delete" && confirm("기록만 지웁니다 (녹화 파일과 클립은 PC에 남습니다).")) await act(() => api(`/api/sessions/${sid}`, { method: "DELETE" }));
  loadSessions();
});

// ---------- 예약 탭 ----------
$("#weekday-chips").innerHTML = WD.map((d, i) => `<button type="button" class="chip" data-d="${i}">${d}</button>`).join("");
$("#weekday-chips").addEventListener("click", (e) => { const b = e.target.closest(".chip"); if (b) b.classList.toggle("on"); });

async function loadJobs() {
  let jobs;
  try { jobs = await api("/api/schedules"); } catch (e) { return toast(e.message, true); }
  $("#jobs").innerHTML = jobs.length ? jobs.map((j) => {
    const when = j.date ? j.date : j.weekdays.length ? "매주 " + j.weekdays.map((d) => WD[d]).join("·") : "매일";
    return `<li class="card" data-jid="${esc(j.id)}">
      <div class="head"><b>${esc(j.name)}</b><span class="pill ${j.enabled ? "ok" : ""}">${j.enabled ? "켜짐" : "꺼짐"}</span></div>
      <div class="meta">${when} ${esc(j.time)} · ${j.duration_min}분</div>
      ${j.url ? `<div class="meta">${esc(j.url)}</div>` : ""}
      <div class="meta">${j.next_run ? "다음 녹화: " + esc(j.next_run.replace("T", " ")) : "예정 없음"}</div>
      <div class="actions">
        <button class="small" data-act="toggle">${j.enabled ? "끄기" : "켜기"}</button>
        <button class="small danger" data-act="del">삭제</button>
      </div></li>`;
  }).join("") : '<p class="muted">예약이 없습니다.</p>';
  $("#jobs").dataset.json = JSON.stringify(jobs);
}
$("#jobs").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const jid = b.closest(".card").dataset.jid;
  const job = JSON.parse($("#jobs").dataset.json).find((j) => j.id === jid);
  if (b.dataset.act === "toggle") await act(() => api(`/api/schedules/${jid}`, { method: "PATCH", body: { enabled: !job.enabled } }));
  if (b.dataset.act === "del" && confirm(`'${job.name}' 예약을 지울까요?`)) await act(() => api(`/api/schedules/${jid}`, { method: "DELETE" }));
  loadJobs();
});
$("#job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    name: f.get("name"), url: f.get("url"), time: f.get("time"),
    duration_min: +f.get("duration_min"), date: f.get("date") || null,
    weekdays: [...document.querySelectorAll("#weekday-chips .chip.on")].map((b) => +b.dataset.d),
  };
  if (body.date) body.weekdays = [];
  const r = await act(() => api("/api/schedules", { method: "POST", body }), "예약을 추가했습니다");
  if (r) { e.target.reset(); document.querySelectorAll("#weekday-chips .chip").forEach((b) => b.classList.remove("on")); loadJobs(); }
});

// ---------- 시작 ----------
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => { });
refresh().then(loadRecent);
setInterval(() => !document.hidden && refresh(), 1000);
setInterval(refreshPreview, 2500);
document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); refreshPreview(); } });
