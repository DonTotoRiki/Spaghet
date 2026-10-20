const status = document.getElementById("status");
const panel = document.getElementById("panel");

const KINDS = {
  router:   { icon: "📡", color: "#ffb347", label: "Routeur" },
  pc:       { icon: "💻", color: "#4a9eff", label: "Ordinateur" },
  phone:    { icon: "📱", color: "#7ed67e", label: "Téléphone" },
  printer:  { icon: "🖨️", color: "#c58cff", label: "Imprimante" },
  iot:      { icon: "💡", color: "#ffd84a", label: "Objet connecté" },
  internet: { icon: "🌐", color: "#888", label: "Destination Internet" },
  cloud:    { icon: "🌐", color: "#888", label: "Internet" },
  unknown:  { icon: "❔", color: "#666", label: "Inconnu" },
};
// Nœud parent qui regroupe toutes les destinations Internet ; un clic le replie ou le déplie.
const CLOUD = "@internet";
let collapsed = false;
const kindOf = n => KINDS[n.data("kind")] || KINDS.unknown;
const nameOf = n => n.data("name") || n.data("vendor") || n.data("ip");

// Mêmes catégories que spaghet/classify.py.
const PROTOCOLS = {
  web:       { label: "Web",          color: "#4a9eff" },
  dns:       { label: "DNS",          color: "#7ed67e" },
  streaming: { label: "Streaming",    color: "#ff5c8a" },
  p2p:       { label: "P2P",          color: "#ff8c42" },
  files:     { label: "Fichiers",     color: "#c58cff" },
  remote:    { label: "Accès distant", color: "#ffd84a" },
  mail:      { label: "Mail",         color: "#4ad8d8" },
  infra:     { label: "Réseau",       color: "#999" },
  other:     { label: "Autre",        color: "#666" },
};
// Protocole qui a fait passer le plus d'octets sur le lien.
const mainProtocolKey = e => {
  const entries = Object.entries(e.data("protocols") || {});
  if (!entries.length) return "other";
  const key = entries.reduce((a, b) => (b[1] > a[1] ? b : a))[0];
  return key in PROTOCOLS ? key : "other";
};
const mainProtocol = e => PROTOCOLS[mainProtocolKey(e)];
// Épaisseur en log : 1 kbit/s et 100 Mbit/s restent sur le même écran.
const edgeWidth = e => 1 + Math.max(0, Math.log10(1 + (e.data("bps") || 0)) - 2) * 1.5;
const STALE_AFTER = 60;   // s sans réponse avant de griser un appareil (le serveur le retire à 5 min)
const ACTIVE_BPS = 1000;  // en dessous, pas de particules
const HISTORY = 60;       // points (1/s) du mini-graphe de débit

const fmtBytes = n => {
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
};
const fmtBps = n => {
  const units = ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
};
const fmtTime = t => new Date(t * 1000).toLocaleTimeString("fr-FR");
// Les noms viennent du réseau (mDNS, DNS, SNI) : on les échappe avant tout innerHTML.
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);

// Longueur de lien selon le RTT, en log : 0,5 ms (filaire) et 100 ms (téléphone en veille) restent lisibles.
// Le RTT est mesuré depuis cette machine mais dessiné depuis la passerelle : approximation acceptable sur un LAN.
const edgeLength = e => {
  const other = e.source().data("kind") === "router" ? e.target() : e.source();
  const rtt = other.data("rtt_ms");
  return rtt == null ? 150 : 60 + 50 * Math.log2(1 + rtt);
};
const layoutOptions = { name: "cose", idealEdgeLength: edgeLength, nodeRepulsion: () => 8000 };
const relayout = () => cy.layout({ ...layoutOptions, randomize: false, animate: true, animationDuration: 500 }).run();

const cy = cytoscape({
  container: document.getElementById("graph"),
  style: [
    { selector: "node", style: {
        label: n => {
          const rtt = n.data("rtt_ms");
          return `${kindOf(n).icon} ${nameOf(n)}` + (rtt == null ? "" : ` · ${rtt.toFixed(1)} ms`);
        },
        "background-color": n => kindOf(n).color, color: "#eee", "font-size": 11,
        "text-valign": "bottom", "text-margin-y": 4 } },
    { selector: "node[kind = 'router']", style: { width: 40, height: 40 } },
    { selector: "node[kind = 'cloud']", style: {
        "background-opacity": 0.08, "border-width": 1, "border-color": "#555", "text-valign": "top" } },
    { selector: "node.stale", style: { opacity: 0.3 } },
    { selector: "node:selected", style: { "border-width": 3, "border-color": "#fff" } },
    { selector: "edge", style: { width: edgeWidth, "line-color": e => mainProtocol(e).color, opacity: 0.8 } },
    { selector: "edge.lan", style: { width: 1, "line-color": "#555", "line-style": "dashed" } },
    { selector: "edge.agg", style: { "line-style": "dotted" } },
    // Particules : tirets très courts à bouts ronds = points ; plus le débit est fort, plus ils sont serrés.
    { selector: "edge.active", style: {
        "line-style": "dashed", "line-cap": "round",
        "line-dash-pattern": e => [0.1, Math.max(6, 36 - 4 * Math.log10(e.data("bps")))] } },
    { selector: ".collapsed, .off, .filtered", style: { display: "none" } },
  ],
});

// Étoile autour de la passerelle : lien visuel en pointillé tant qu'aucun trafic réel ne relie l'appareil à elle.
function ensureStar() {
  const router = cy.nodes("[kind = 'router']").first();
  if (router.empty()) return;
  cy.nodes("[kind != 'internet'][kind != 'cloud']").not(router).forEach(n => {
    const star = cy.getElementById(`lan-${n.id()}`);
    const hasTraffic = n.edgesWith(router).not(".lan").nonempty();
    if (hasTraffic) star.remove();
    else if (star.empty()) cy.add({ data: { id: `lan-${n.id()}`, source: router.id(), target: n.id() }, classes: "lan" });
  });
}

// Replié : les destinations sont masquées et chaque appareil a un seul lien vers « Internet », avec le débit cumulé.
function applyCollapse() {
  const cloud = cy.getElementById(CLOUD);
  cy.edges(".agg").remove();
  cloud.children().toggleClass("collapsed", collapsed);
  if (!collapsed) return;
  const totals = {};
  cloud.children().connectedEdges().forEach(e => {
    const lanIsSource = !e.source().isChild();
    const lan = lanIsSource ? e.source() : e.target();
    const t = (totals[lan.id()] ??= { bps: 0, bps_ab: 0, bytes_total: 0, protocols: {} });
    const bps = e.data("bps") || 0, ab = e.data("bps_ab") || 0;
    t.bps += bps;
    t.bps_ab += lanIsSource ? ab : bps - ab;  // sens appareil → Internet
    t.bytes_total += e.data("bytes_total") || 0;
    for (const [p, b] of Object.entries(e.data("protocols") || {})) t.protocols[p] = (t.protocols[p] || 0) + b;
  });
  for (const [lan, t] of Object.entries(totals))
    cy.add({ data: { id: `agg-${lan}`, source: lan, target: CLOUD, ...t }, classes: "agg" });
}

// Filtres : protocoles masqués (clic dans la légende) et Internet affiché ou non.
const hiddenProtocols = new Set();
const showInternet = document.getElementById("show-internet");

function applyFilters() {
  const cloud = cy.getElementById(CLOUD);
  cloud.union(cloud.children()).toggleClass("off", !showInternet.checked);
  cy.edges().not(".lan").forEach(e => e.toggleClass("filtered", hiddenProtocols.has(mainProtocolKey(e))));
  cy.edges().forEach(e => e.toggleClass("active", (e.data("bps") || 0) > ACTIVE_BPS));
}

// Ajoute ou met à jour ; renvoie true si un élément est nouveau (il faut alors replacer le graphe).
function upsert(elements, group) {
  let added = false;
  const router = cy.nodes("[kind = 'router']").first();
  const origin = router.empty() ? { x: 0, y: 0 } : router.position();
  for (const el of elements) {
    const existing = cy.getElementById(el.data.id);
    if (existing.nonempty()) {
      existing.data(el.data);
    } else {
      // Nouveau nœud posé près de la passerelle : le layout l'écarte ensuite sans bouleverser le reste.
      const position = { x: origin.x + Math.random() * 40 - 20, y: origin.y + Math.random() * 40 - 20 };
      const data = el.data.kind === "internet" ? { ...el.data, parent: CLOUD } : el.data;
      cy.add({ group, data, position });
      added = true;
    }
  }
  return added;
}

let serverOffset = 0; // heure serveur - heure navigateur, en s

function markStale() {
  const now = Date.now() / 1000 + serverOffset;
  cy.nodes().forEach(n => n.toggleClass("stale", now - n.data("last_seen") > STALE_AFTER));
  const destinations = cy.nodes("[kind = 'internet']").length;
  status.textContent = `· ${cy.nodes("[kind != 'internet'][kind != 'cloud']").length} appareils · ${destinations} destinations Internet`;
}

// Historique de débit par nœud, échantillonné dans le navigateur chaque seconde (vrais liens seulement).
const history = new Map();

function sampleHistory() {
  cy.nodes("[kind != 'cloud']").forEach(n => {
    const bps = n.connectedEdges().not(".lan, .agg").reduce((sum, e) => sum + (e.data("bps") || 0), 0);
    const h = history.get(n.id()) || [];
    h.push(bps);
    if (h.length > HISTORY) h.shift();
    history.set(n.id(), h);
  });
  for (const id of history.keys()) if (cy.getElementById(id).empty()) history.delete(id);
}

function sparkline(values) {
  const w = 280, h = 60, max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / (HISTORY - 1)) * w},${h - (v / max) * (h - 4) - 2}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" class="spark" role="img" aria-label="Débit des 60 dernières secondes, max ${fmtBps(max)}">
    <polyline points="${pts}" /></svg><div class="muted">max ${fmtBps(max)}</div>`;
}

let selected = null;

// Traceroute à la demande : nœud → "…" (en cours) ou message de résultat. Les sauts arrivent aussi par le diff.
const traces = new Map();

async function trace(id) {
  if (traces.get(id) === "…") return;
  traces.set(id, "…");
  renderPanel();
  try {
    const res = await fetch(`/api/traceroute/${encodeURIComponent(id)}`, { method: "POST" });
    const body = await res.json();
    traces.set(id, res.ok ? (body.hops == null ? "La cible n'a pas répondu" : `${body.hops} sauts`) : body.detail);
  } catch (e) {
    traces.set(id, `Erreur : ${e.message}`);
  }
  renderPanel();
}

function renderPanel() {
  const n = selected && cy.getElementById(selected);
  if (!n || n.empty()) { panel.hidden = true; selected = null; return; }
  const d = n.data(), k = kindOf(n);
  const rows = [
    [d.kind === "internet" ? "Domaine" : "IP", d.ip],
    ["MAC", d.mac], ["Fabricant", d.vendor], ["Type", k.label],
    ["Latence", d.rtt_ms != null && `${d.rtt_ms.toFixed(1)} ms`],
    ["Sauts", d.hops != null && String(d.hops)],
    ["Adresse", d.kind === "internet" && d.addr],
    ["Lieu", [d.city, d.country].filter(Boolean).join(", ")],
    ["Services", (d.services || []).join(", ")],
    ["Volume total", fmtBytes(d.bytes_total || 0)],
    ["Vu depuis", fmtTime(d.first_seen)], ["Dernière activité", fmtTime(d.last_seen)],
  ].filter(([, v]) => v);
  const edges = n.connectedEdges().not(".lan, .agg").sort((a, b) => b.data("bytes_total") - a.data("bytes_total"));
  const connections = edges.map(e => {
    const other = e.source().same(n) ? e.target() : e.source();
    const p = mainProtocol(e);
    return `<li data-id="${esc(other.id())}"><i style="background:${p.color}" title="${esc(p.label)}"></i>
      <span class="name">${esc(kindOf(other).icon)} ${esc(nameOf(other))}</span>
      <span class="muted">${fmtBytes(e.data("bytes_total"))} · ${fmtBps(e.data("bps") || 0)}</span></li>`;
  }).join("");
  panel.innerHTML = `
    <header><h2>${esc(k.icon)} ${esc(nameOf(n))}</h2><button id="close" aria-label="Fermer">✕</button></header>
    <dl>${rows.map(([t, v]) => `<dt>${t}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    <button class="trace" ${traces.get(n.id()) === "…" ? "disabled" : ""}>Tracer la route</button>
    ${traces.has(n.id()) && traces.get(n.id()) !== "…" ? `<div class="muted">${esc(traces.get(n.id()))}</div>` : ""}
    <h3>Débit</h3>${sparkline(history.get(n.id()) || [0])}
    <h3>Connexions (${edges.length})</h3><ol class="connections">${connections || "<li class='muted'>Aucun trafic vu</li>"}</ol>`;
  panel.hidden = false;
}

panel.addEventListener("click", ev => {
  if (ev.target.closest(".trace")) { trace(selected); return; }
  if (ev.target.closest("#close")) { cy.$(":selected").unselect(); selected = null; renderPanel(); return; }
  const li = ev.target.closest("li[data-id]");
  if (li) { cy.$(":selected").unselect(); cy.getElementById(li.dataset.id).select(); selected = li.dataset.id; renderPanel(); }
});

cy.on("tap", "node", ev => {
  const n = ev.target;
  if (n.id() === CLOUD) {
    collapsed = !collapsed;
    applyCollapse();
    applyFilters();
    relayout();
    return;
  }
  selected = n.id();
  renderPanel();
});
cy.on("cxttap", "node", ev => {  // clic droit : traceroute direct
  if (ev.target.id() === CLOUD) return;
  selected = ev.target.id();
  trace(selected);
});
cy.on("tap", ev => { if (ev.target === cy) { selected = null; renderPanel(); } });

// Carte des destinations Internet géolocalisées, créée au premier affichage de l'onglet.
let map = null;
const markers = new Map();

function updateMap() {
  if (!map) return;
  const located = cy.nodes("[kind = 'internet']").filter(n => n.data("lat") != null);
  for (const [id, marker] of markers)
    if (located.getElementById(id).empty()) { marker.remove(); markers.delete(id); }
  located.forEach(n => {
    const edge = n.connectedEdges().not(".agg").max(e => e.data("bytes_total") || 0).ele;
    const color = edge ? mainProtocol(edge).color : "#888";
    const radius = 4 + Math.log10(1 + (n.data("bytes_total") || 0));
    let marker = markers.get(n.id());
    if (!marker) {
      marker = L.circleMarker([n.data("lat"), n.data("lon")]).addTo(map).on("click", () => { selected = n.id(); renderPanel(); });
      markers.set(n.id(), marker);
    }
    marker.setStyle({ color, fillColor: color, fillOpacity: 0.6, weight: 1 }).setRadius(radius);
    // Leaflet interprète le texte des infobulles comme du HTML : on échappe.
    marker.bindTooltip(`${esc(nameOf(n))}<br>${esc([n.data("city"), n.data("country")].filter(Boolean).join(", "))} · ${fmtBytes(n.data("bytes_total") || 0)}`);
  });
}

function showTab(name) {
  const onMap = name === "map";
  document.getElementById("graph").hidden = onMap;
  document.getElementById("map").hidden = !onMap;
  document.getElementById("tab-graph").setAttribute("aria-selected", String(!onMap));
  document.getElementById("tab-map").setAttribute("aria-selected", String(onMap));
  if (onMap && !map) {
    map = L.map("map", { worldCopyJump: true }).setView([30, 0], 2);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: "© contributeurs OpenStreetMap",
    }).addTo(map);
  }
  if (onMap) { map.invalidateSize(); updateMap(); } else cy.resize();
}
document.getElementById("tab-graph").addEventListener("click", () => showTab("graph"));

// Exports : image du graphe entier, et état brut du serveur.
const stamp = () => new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-");
function download(href, filename) {
  const a = Object.assign(document.createElement("a"), { href, download: filename });
  a.click();
}
document.getElementById("export-png").addEventListener("click", () => {
  if (!document.getElementById("graph").hidden) cy.resize();
  download(cy.png({ full: true, scale: 2, bg: "#111" }), `spaghet-${stamp()}.png`);
});
document.getElementById("export-json").addEventListener("click", async () => {
  const blob = new Blob([JSON.stringify(await (await fetch("/api/snapshot")).json(), null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  download(url, `spaghet-${stamp()}.json`);
  setTimeout(() => URL.revokeObjectURL(url), 1000);  // révoquer tout de suite peut annuler le téléchargement (Firefox)
});
document.getElementById("tab-map").addEventListener("click", () => showTab("map"));

function refresh() {
  ensureStar();
  applyCollapse();
  applyFilters();
  markStale();
  renderPanel();
  updateMap();
}

function onMessage(msg) {
  serverOffset = msg.now - Date.now() / 1000;
  if (msg.type === "snapshot") {
    cy.elements().remove();
    cy.add({ data: { id: CLOUD, kind: "cloud", name: "Internet" } });
    upsert(msg.nodes, "nodes");
    upsert(msg.links, "edges");
    refresh();
    cy.layout({ ...layoutOptions, animate: false }).run();
  } else {
    // Suppressions d'abord : un appareil retiré puis revenu dans la même seconde doit rester affiché.
    for (const id of msg.removed) cy.getElementById(id).remove();
    let changed = upsert(msg.nodes, "nodes");
    changed = upsert(msg.links, "edges") || changed;
    refresh();
    if (changed || msg.removed.length) relayout();
  }
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = e => onMessage(JSON.parse(e.data));
  ws.onclose = () => {
    status.textContent = "· déconnecté, nouvelle tentative…";
    setTimeout(connect, 2000);
  };
}

// Fait défiler les particules dans le sens dominant du trafic, plus vite quand le débit est fort.
function animate(t) {
  cy.batch(() => cy.edges(".active").forEach(e => {
    const bps = e.data("bps"), towardTarget = (e.data("bps_ab") || 0) >= bps / 2;
    const speed = 10 + 15 * Math.log10(bps / ACTIVE_BPS);  // px/s
    const offset = (t / 1000) * speed;
    e.style("line-dash-offset", towardTarget ? -offset : offset);
  }));
  requestAnimationFrame(animate);
}

const legend = document.getElementById("legend");
legend.innerHTML = Object.entries(PROTOCOLS)
  .map(([key, p]) => `<button data-proto="${key}" aria-pressed="true"><i style="background:${p.color}"></i>${p.label}</button>`).join("");
legend.addEventListener("click", ev => {
  const button = ev.target.closest("button[data-proto]");
  if (!button) return;
  const key = button.dataset.proto;
  hiddenProtocols.has(key) ? hiddenProtocols.delete(key) : hiddenProtocols.add(key);
  button.setAttribute("aria-pressed", String(!hiddenProtocols.has(key)));
  applyFilters();
});
showInternet.addEventListener("change", applyFilters);

connect();
setInterval(markStale, 5000);
setInterval(() => { sampleHistory(); if (selected) renderPanel(); }, 1000);
if (!matchMedia("(prefers-reduced-motion: reduce)").matches) requestAnimationFrame(animate);
