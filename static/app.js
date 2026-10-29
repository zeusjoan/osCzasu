"use strict";

const MODE_LABELS = {
  "in passenger vehicle": "samochód", cycling: "rower", walking: "pieszo", running: "bieg",
  "in bus": "autobus", "in train": "pociąg", "in tram": "tramwaj", "in subway": "metro",
  "in ferry": "prom", flying: "samolot", motorcycling: "motocykl", skiing: "narty",
  sailing: "żeglowanie", unknown: "nieznany",
};
const MODE_ICONS = {
  "in passenger vehicle": "🚗", cycling: "🚲", walking: "🚶", running: "🏃", "in bus": "🚌",
  "in train": "🚆", "in tram": "🚋", "in subway": "🚇", "in ferry": "⛴", flying: "✈️",
  motorcycling: "🏍", skiing: "⛷", sailing: "⛵", unknown: "❔",
};
const MODE_COLORS = {
  "in passenger vehicle": "#d93025", cycling: "#188038", walking: "#f29900", running: "#e37400",
  "in bus": "#9334e6", "in train": "#1a73e8", "in tram": "#12b5cb", "in subway": "#185abc",
  "in ferry": "#007b83", flying: "#5f6368", motorcycling: "#b31412", skiing: "#4285f4",
  sailing: "#00897b", unknown: "#80868b",
};
const CATEGORY_ICONS = [
  [/^dom$/, "🏠"], [/^praca$/, "🏢"], [/supermarket|sklep|market|piekarnia|mięsny|centrum handlowe/, "🛒"],
  [/stacja paliw/, "⛽"], [/restauracja|kawiarnia|fast food/, "🍴"], [/apteka|przychodnia|szpital/, "💊"],
  [/park|plac zabaw/, "🌳"], [/parking/, "🅿️"], [/szkoła|przedszkole/, "🏫"], [/hotel/, "🛏"],
];

const state = {
  persons: [], personId: null, date: null, modes: [], items: [],
  layers: {}, progress: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = `Błąd ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* not JSON */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

const jsonBody = (method, data) => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
});

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

// ---- formatting ----

const hhmm = (iso) => iso.slice(11, 16);
const isoDate = (iso) => iso.slice(0, 10);

function clock(iso) {
  // Show the date too when the time falls on another day than the one being viewed.
  if (state.date && isoDate(iso) !== state.date) {
    const [, m, d] = isoDate(iso).split("-");
    return `${d}.${m} ${hhmm(iso)}`;
  }
  return hhmm(iso);
}

function duration(seconds) {
  const min = Math.round(seconds / 60);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  return `${h} h ${String(min % 60).padStart(2, "0")} min`;
}

const km = (m) => `${(m / 1000).toFixed(1).replace(".", ",")} km`;
const plDate = (d) => d.split("-").reverse().join(".");

function visitsWord(n) {
  if (n === 1) return "wizyta";
  const last = n % 10, lastTwo = n % 100;
  return last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14) ? "wizyty" : "wizyt";
}

function categoryIcon(name, category) {
  const n = (name || "").toLowerCase(), c = (category || "").toLowerCase();
  for (const [re, icon] of CATEGORY_ICONS) if (re.test(n) || re.test(c)) return icon;
  return "📍";
}

function shiftDate(iso, days) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

// ---- map ----

const map = L.map("map").setView([52.07, 19.48], 6);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const dayLayer = L.layerGroup().addTo(map);
const legend = L.control({ position: "bottomright" });
legend.onAdd = () => el("div", { class: "legend" });
legend.addTo(map);

function updateLegend(modes) {
  const box = legend.getContainer();
  box.replaceChildren();
  box.hidden = modes.length === 0;
  for (const m of modes) {
    const swatch = el("i");
    swatch.style.background = MODE_COLORS[m] || MODE_COLORS.unknown;
    box.append(el("div", {}, swatch, MODE_LABELS[m] || m));
  }
}

function highlight(key, { scroll = false, zoom = false } = {}) {
  document.querySelectorAll(".item.active").forEach((n) => n.classList.remove("active"));
  for (const [k, layer] of Object.entries(state.layers)) {
    const on = k === key;
    if (layer instanceof L.Polyline) layer.setStyle({ weight: on ? 8 : 4, opacity: on ? 1 : 0.75 });
    else layer.setStyle({ radius: on ? 11 : 7, weight: on ? 3 : 2 });
  }
  const row = document.querySelector(`.item[data-key="${key}"]`);
  if (row) {
    row.classList.add("active");
    if (scroll) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  const layer = state.layers[key];
  if (layer && zoom) {
    if (layer instanceof L.Polyline) map.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 16 });
    else map.setView(layer.getLatLng(), Math.max(map.getZoom(), 16));
  }
  if (layer && layer.bringToFront) layer.bringToFront();
}

function drawDay(items, fit) {
  dayLayer.clearLayers();
  state.layers = {};
  const bounds = [];
  const modes = new Set();
  const visits = [];
  for (const it of items) {
    if (it.kind === "activity") {
      modes.add(it.mode);
      const line = L.polyline(it.geometry, {
        color: MODE_COLORS[it.mode] || MODE_COLORS.unknown, weight: 4, opacity: 0.75,
      }).addTo(dayLayer);
      line.on("click", () => highlight(`activity-${it.id}`, { scroll: true }));
      state.layers[`activity-${it.id}`] = line;
      bounds.push(...it.geometry);
    } else {
      visits.push(it, ...it.children);
    }
  }
  for (const v of visits) {
    const marker = L.circleMarker([v.lat, v.lng], {
      radius: 7, color: "#202124", weight: 2, fillColor: "#fff", fillOpacity: 1,
    }).addTo(dayLayer);
    // Names come from OSM (editable by anyone): pass a text node so they are never parsed as HTML.
    marker.bindTooltip(el("span", {}, `${v.name} · ${clock(v.start)}–${clock(v.end)} · ${duration(v.duration_s)}`));
    marker.on("click", () => highlight(`visit-${v.id}`, { scroll: true }));
    state.layers[`visit-${v.id}`] = marker;
    bounds.push([v.lat, v.lng]);
  }
  updateLegend([...modes]);
  if (fit && bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 16 });
}

// ---- day list ----

function visitRow(v) {
  const title = v.resolved || v.name !== "Nieznane miejsce"
    ? v.name : `${v.name} (${v.lat.toFixed(5)}, ${v.lng.toFixed(5)})`;
  const sub = [v.category, v.address].filter(Boolean).join(" · ");
  const li = el("li", { class: "item visit", "data-key": `visit-${v.id}` },
    el("div", { class: "title" }, `${categoryIcon(v.name, v.category)} ${title}`),
    sub ? el("div", { class: "meta" }, sub) : null,
    el("div", { class: "meta" }, `${clock(v.start)} – ${clock(v.end)} · ${duration(v.duration_s)}`));
  li.addEventListener("click", (e) => {
    e.stopPropagation();
    highlight(`visit-${v.id}`, { zoom: true });
  });
  if (v.children.length) li.append(el("ol", { class: "children" }, ...v.children.map(visitRow)));
  return li;
}

function activityRow(a) {
  const key = `activity-${a.id}`;
  const label = el("span", { class: "title" }, `${MODE_ICONS[a.mode] || "❔"} ${MODE_LABELS[a.mode] || a.mode}`);
  const li = el("li", { class: "item activity", "data-key": key },
    label, " ",
    a.corrected ? el("span", { class: "corrected" }, "(poprawione)") : null,
    el("div", { class: "meta" }, `${km(a.distance_m)} · ${duration(a.duration_s)} · ${clock(a.start)}–${clock(a.end)}`));
  li.addEventListener("click", () => {
    const wasActive = li.classList.contains("active");
    highlight(key, { zoom: true });
    if (wasActive && !li.querySelector("select")) li.append(modeSelect(a));
  });
  li.title = "Kliknij, aby pokazać; kliknij ponownie, aby poprawić rodzaj transportu";
  return li;
}

function modeSelect(a) {
  const select = el("select", { onclick: (e) => e.stopPropagation() },
    ...state.modes.map((m) => {
      const opt = el("option", { value: m }, `${MODE_ICONS[m] || ""} ${MODE_LABELS[m] || m}`);
      if (m === a.mode) opt.selected = true;
      return opt;
    }));
  select.addEventListener("change", async () => {
    try {
      await api(`/api/activities/${a.id}/mode`, jsonBody("PUT", { mode: select.value }));
      await loadDay(state.date, { fit: false, keep: `activity-${a.id}` });
    } catch (err) {
      alert(err.message);
    }
  });
  return el("div", {}, el("span", { class: "meta" }, "Rodzaj transportu: "), select);
}

function renderList(items) {
  const list = $("day-list");
  if (!items.length) {
    list.replaceChildren(el("li", { class: "empty" },
      state.persons.length ? (state.date ? "Brak wpisów w tym dniu." : "Brak danych — wczytaj plik.")
        : "Dodaj osobę i wczytaj plik Timeline.json."));
    return;
  }
  list.replaceChildren(...items.map((it) => (it.kind === "visit" ? visitRow(it) : activityRow(it))));
}

async function loadDay(date, { fit = true, keep = null } = {}) {
  if (!state.personId) {
    state.date = null;
    state.items = [];
    renderList([]);
    drawDay([], false);
    return;
  }
  const query = date ? `?date=${date}` : "";
  const data = await api(`/api/persons/${state.personId}/day${query}`);
  state.date = data.date;
  state.items = data.items;
  $("day-input").value = data.date || "";
  renderList(data.items);
  drawDay(data.items, fit);
  if (keep) highlight(keep);
}

// ---- persons ----

async function loadPersons(selectId = state.personId) {
  state.persons = await api("/api/persons");
  const ids = state.persons.map((p) => p.id);
  state.personId = ids.includes(selectId) ? selectId : (ids[0] ?? null);
  $("person-select").replaceChildren(...state.persons.map((p) => {
    const opt = el("option", { value: p.id }, p.name);
    if (p.id === state.personId) opt.selected = true;
    return opt;
  }));
  const none = !state.persons.length;
  $("person-rename").disabled = none;
  $("person-delete").disabled = none;
}

$("person-select").addEventListener("change", async (e) => {
  state.personId = Number(e.target.value);
  await loadDay(null);
});

$("person-add").addEventListener("click", async () => {
  const name = prompt("Imię nowej osoby:");
  if (!name || !name.trim()) return;
  try {
    const p = await api("/api/persons", jsonBody("POST", { name }));
    await loadPersons(p.id);
    await loadDay(null);
  } catch (err) { alert(err.message); }
});

$("person-rename").addEventListener("click", async () => {
  const current = state.persons.find((p) => p.id === state.personId);
  const name = prompt("Nowe imię:", current ? current.name : "");
  if (!name || !name.trim()) return;
  try {
    await api(`/api/persons/${state.personId}`, jsonBody("PATCH", { name }));
    await loadPersons();
  } catch (err) { alert(err.message); }
});

$("person-delete").addEventListener("click", async () => {
  const current = state.persons.find((p) => p.id === state.personId);
  if (!current || !confirm(`Usunąć osobę „${current.name}” i wszystkie jej dane?`)) return;
  try {
    await api(`/api/persons/${state.personId}`, { method: "DELETE" });
    await loadPersons(null);
    await loadDay(null);
  } catch (err) { alert(err.message); }
});

// ---- day navigation ----

$("day-prev").addEventListener("click", () => state.date && loadDay(shiftDate(state.date, -1)));
$("day-next").addEventListener("click", () => state.date && loadDay(shiftDate(state.date, 1)));
$("day-input").addEventListener("change", (e) => e.target.value && loadDay(e.target.value));

// ---- upload ----

const NEW_PERSON = "__new__";

function fillUploadPersons() {
  const select = $("upload-person");
  select.replaceChildren(
    ...state.persons.map((p) => {
      const opt = el("option", { value: p.id }, p.name);
      if (p.id === state.personId) opt.selected = true;
      return opt;
    }),
    el("option", { value: NEW_PERSON }, "— nowa osoba —"));
  if (!state.persons.length) select.value = NEW_PERSON;
  $("upload-new-wrap").hidden = select.value !== NEW_PERSON;
}

$("upload-open").addEventListener("click", () => {
  $("upload-result").textContent = "";
  $("upload-result").className = "";
  $("upload-form").reset();
  fillUploadPersons();
  $("upload-dialog").showModal();
});
$("upload-person").addEventListener("change", (e) => {
  $("upload-new-wrap").hidden = e.target.value !== NEW_PERSON;
});
$("upload-close").addEventListener("click", () => $("upload-dialog").close());

$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const result = $("upload-result");
  const file = $("upload-file").files[0];
  if (!file) return;
  $("upload-submit").disabled = true;
  result.className = "";
  result.textContent = "Wczytywanie…";
  try {
    let personId = $("upload-person").value;
    if (personId === NEW_PERSON) {
      const p = await api("/api/persons", jsonBody("POST", { name: $("upload-new-name").value }));
      personId = p.id;
      // Select the new person right away, so retrying after a failed upload does not create another one.
      await loadPersons(p.id);
      fillUploadPersons();
      await loadDay(null);
    }
    const form = new FormData();
    form.append("file", file);
    const r = await api(`/api/persons/${personId}/files`, { method: "POST", body: form });
    result.textContent = `Dodano ${r.added_visits} wizyt i ${r.added_activities} przejazdów, `
      + `zastąpiono ${r.replaced}, pominięto ${r.skipped}.`;
    await loadPersons(Number(personId));
    fillUploadPersons();
    await loadDay(null);
    pollProgress();
  } catch (err) {
    result.className = "error";
    result.textContent = err.message;
  } finally {
    $("upload-submit").disabled = false;
  }
});

// ---- places progress ----

let progressTimer = null;

async function pollProgress() {
  clearTimeout(progressTimer);
  try {
    const p = await api("/api/places/progress");
    const box = $("progress");
    const pending = p.done < p.total;
    box.hidden = !pending;
    box.textContent = `Nazwy miejsc: ${p.done}/${p.total}`;
    const advanced = state.progress && p.done > state.progress.done;
    state.progress = p;
    const unresolved = state.items.some((it) => it.kind === "visit"
      && (!it.resolved || it.children.some((c) => !c.resolved)));
    if (advanced && unresolved && state.date && !$("day-view").hidden && !$("day-list").querySelector("select")) {
      const active = document.querySelector(".item.active");
      await loadDay(state.date, { fit: false, keep: active ? active.dataset.key : null });
    }
    if (pending) progressTimer = setTimeout(pollProgress, 5000);
  } catch (_) {
    progressTimer = setTimeout(pollProgress, 15000);
  }
}

// ---- search ----

function showSearch(on) {
  $("search-view").hidden = !on;
  $("day-view").hidden = on;
  if (on) $("search-q").focus();
}

$("search-open").addEventListener("click", () => showSearch(true));
$("search-back").addEventListener("click", () => showSearch(false));

$("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const params = new URLSearchParams();
  const q = $("search-q").value.trim();
  if (q) params.set("q", q);
  if ($("search-from").value) params.set("from", $("search-from").value);
  if ($("search-to").value) params.set("to", $("search-to").value);
  if ($("search-person").value === "current" && state.personId) params.set("person_id", state.personId);
  const box = $("search-results");
  box.replaceChildren(el("p", { class: "hint" }, "Szukam…"));
  try {
    renderResults(await api(`/api/search?${params}`), $("search-person").value === "all");
  } catch (err) {
    box.replaceChildren(el("p", { class: "error" }, err.message));
  }
});

function renderResults(groups, showPerson) {
  const box = $("search-results");
  if (!groups.length) {
    box.replaceChildren(el("p", { class: "empty" }, "Nic nie znaleziono."));
    return;
  }
  box.replaceChildren(...groups.map((g) => {
    let summary = g.count
      ? `${g.count} ${visitsWord(g.count)} · łącznie ${duration(g.total_s)}` : "brak pewnych wizyt";
    if (g.possible_count) summary += ` · +${g.possible_count} możliwe`;
    const rows = g.visits.map((v) => el("tr", { class: v.possible ? "possible" : "" },
      el("td", {}, plDate(v.date), showPerson ? el("div", { class: "meta" }, v.person_name) : null,
        v.possible ? el("div", {}, `możliwie tutaj (główny: ${v.primary_name})`) : null),
      el("td", {}, `${hhmm(v.start)}–${hhmm(v.end)}`),
      el("td", {}, duration(v.duration_s)),
      el("td", {}, el("button", {
        class: "link",
        onclick: async () => {
          if (v.person_id !== state.personId) await loadPersons(v.person_id);
          showSearch(false);
          await loadDay(v.date);
          highlight(`visit-${v.visit_id}`, { zoom: true, scroll: true });
        },
      }, "pokaż dzień →"))));
    return el("details", { class: "group" },
      el("summary", {},
        el("strong", {}, `${categoryIcon(g.name, g.category)} ${g.name}`),
        g.address ? el("span", { class: "addr" }, ` — ${g.address}`) : null,
        el("div", { class: "meta" }, summary)),
      el("table", {},
        el("thead", {}, el("tr", {}, el("th", {}, "Data"), el("th", {}, "Od–do"), el("th", {}, "Czas"), el("th", {}))),
        el("tbody", {}, ...rows)));
  }));
}

// ---- start ----

(async function init() {
  try {
    state.modes = await api("/api/modes");
    await loadPersons();
    await loadDay(null);
  } catch (err) {
    $("day-list").replaceChildren(el("li", { class: "empty" }, `Błąd: ${err.message}`));
  }
  pollProgress();
})();
