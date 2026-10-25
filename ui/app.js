/* Ztockz market cockpit - presentation only. Every number comes from the API. */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const WATCH_KEY = "ztockz.watchlist.v1";
const COLS_KEY = "ztockz.columns.v1";
const state = {
  rows: [],
  names: {},
  selected: null,
  filter: "all",
  query: "",
  sort: { key: "market_cap", dir: "desc" },
  page: 1,
  perPage: 20,
  region: "us",
  view: "discover",
  watch: new Set(readJSON(WATCH_KEY, [])),
  compare: [],
  hiddenColumns: new Set(readJSON(COLS_KEY, [])),
  source: "",
  universe: 0,
  asOf: null,
  loading: true,
  lastError: "",
};

function readJSON(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value ?? fallback;
  } catch {
    return fallback;
  }
}

function persist() {
  try {
    localStorage.setItem(WATCH_KEY, JSON.stringify([...state.watch]));
    localStorage.setItem(COLS_KEY, JSON.stringify([...state.hiddenColumns]));
  } catch {
    /* Storage is an enhancement, not a dependency. */
  }
}

function safeUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : "#";
  } catch {
    return "#";
  }
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function num(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function fmtPrice(value) {
  const n = num(value);
  if (n === null) return "--";
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(value, signed = true) {
  const n = num(value);
  if (n === null) return "--";
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function fmtCap(value) {
  const n = num(value);
  if (n === null) return "--";
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toFixed(0)}`;
}

function fmtRatio(value) {
  const n = num(value);
  return n === null ? "--" : n.toFixed(1);
}

function fmtInt(value) {
  const n = num(value);
  return n === null ? "--" : Math.round(n).toLocaleString("en-US");
}

function pctClass(value) {
  const n = num(value);
  if (n === null || Math.abs(n) < 0.005) return "flat";
  return n > 0 ? "up" : "down";
}

function fmtTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function fmtStamp(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return `${date.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${fmtTime(value)}`;
}

function mark(symbol) {
  return String(symbol || "").split(".")[0].slice(0, 4).toUpperCase();
}

function company(row) {
  return row.name || state.names[row.symbol] || "US equity";
}

function median(values) {
  const clean = values.filter((v) => num(v) !== null).sort((a, b) => a - b);
  if (!clean.length) return null;
  const middle = Math.floor(clean.length / 2);
  return clean.length % 2 ? clean[middle] : (clean[middle - 1] + clean[middle]) / 2;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.add("hidden"), 2600);
}

async function fetchJSON(url, options = {}, timeout = 25000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = body.errorMessage || body.error || `Request failed (${response.status})`;
      throw new Error(message);
    }
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The scan timed out. Try refresh.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function loadData() {
  state.loading = true;
  state.lastError = "";
  renderLoading();
  const region = state.region;
  const selectBody = {
    mode: "criteria",
    region,
    criteria: { filters: [], sort: "market_cap", limit: 24 },
    view: "overview",
  };
  const snapshotUrl = `/screens?source=tradingview${region === "us" ? "" : `&region=${region}`}`;
  const selectionRequest = fetchJSON("/screens/select", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(selectBody),
  }).catch(async (error) => {
    const fallback = await fetchJSON(`/screens?source=multi&region=${region}`, {}, 30000);
    return { ...fallback, fallback, selectError: error.message };
  });
  const snapshotRequest = fetchJSON(snapshotUrl, {}, 12000).catch(() => null);

  try {
    const [selection, snapshot] = await Promise.all([selectionRequest, snapshotRequest]);
    const rows = Array.isArray(selection.rows) ? selection.rows : [];
    const snapshotRows = Array.isArray(snapshot?.rows) ? snapshot.rows : [];
    const snapshotBySymbol = new Map(snapshotRows.map((row) => [row.symbol, row]));
    state.names = {};
    for (const row of snapshotRows) {
      if (row.symbol && row.name) state.names[row.symbol] = row.name;
    }
    const seenAliases = new Set(["GOOGN", "GOOGM", "JPM/PD", "JPM/PC", "JPM/PL", "JPM/PK"]);
    const richBySymbol = new Map(rows.filter((row) => !seenAliases.has(row.symbol)).map((row) => [row.symbol, row]));
    const merged = [];
    for (const light of snapshotRows) {
      const rich = richBySymbol.get(light.symbol) || {};
      merged.push({
        ...rich,
        ...light,
        market_cap: num(rich.market_cap) ?? num(rich.market_cap_usd) ?? num(light.market_cap_usd),
        change_pct: num(rich.change_pct) ?? num(light.change_pct),
        price: num(rich.price) ?? num(light.price),
      });
      richBySymbol.delete(light.symbol);
      if (merged.length >= 20) break;
    }
    for (const rich of richBySymbol.values()) {
      if (merged.length >= 20) break;
      merged.push(rich);
    }
    state.rows = merged.map((row, index) => ({
      ...row,
      rank: index + 1,
      name: row.name || state.names[row.symbol] || "",
      sector: row.sector || "",
    }));
    state.source = selection.source || selection.fallback?.source || "tradingview";
    state.universe = num(selection.universe_total) ?? num(selection.total) ?? rows.length;
    state.asOf = selection.as_of || selection.fallback?.as_of || null;
    state.loading = false;
    if (selection.selectError) {
      state.lastError = `Rich fundamentals unavailable: ${selection.selectError}. Showing the verified screen rows.`;
    }
    if (!state.selected || !state.rows.some((row) => row.symbol === state.selected)) {
      state.selected = state.rows[0]?.symbol || null;
    }
    renderAll();
  } catch (error) {
    state.loading = false;
    state.rows = [];
    state.lastError = error.message || "The market scan is unavailable.";
    renderAll();
  }
}

function renderAll() {
  renderNotice();
  renderStats();
  renderTable();
  renderDetail();
  renderSignals();
  renderCompare();
  renderWatchCounts();
  renderColumnState();
}

function renderLoading() {
  $("#tableBody").innerHTML = Array.from(
    { length: 5 },
    () => '<tr class="skeleton-row"><td colspan="10"><div class="skeleton wide"></div></td></tr>',
  ).join("");
  $("#emptyState").classList.add("hidden");
  $("#selectionMeta").textContent = "Loading scan";
  $("#rowCount").textContent = "--";
}

function renderNotice() {
  const notice = $("#notice");
  if (!state.lastError) {
    notice.classList.add("hidden");
    return;
  }
  notice.textContent = state.lastError;
  notice.classList.remove("hidden");
}

function renderStats() {
  const rows = state.rows;
  $("#statUniverse").textContent = state.universe ? fmtInt(state.universe) : "--";
  const advancing = rows.filter((row) => num(row.change_pct) > 0).length;
  $("#statAdvancing").textContent = rows.length ? `${advancing}/${rows.length}` : "--";
  $("#statAdvancingNote").textContent = "of current page";
  const pe = median(rows.map((row) => row.pe));
  $("#statMedianPe").textContent = pe === null ? "--" : `${pe.toFixed(1)}×`;
  $("#sideUsCount").textContent = state.region === "us" && state.universe ? fmtInt(state.universe) : $("#sideUsCount").textContent;
  $("#selectionMeta").textContent = `${state.source || "scan"}${state.asOf ? ` · ${fmtStamp(state.asOf)}` : ""}`;
  $("#rowCount").textContent = String(rows.length);
  $("#sideStatus").textContent = state.loading ? "Scanning market" : state.rows.length ? "Scan connected" : "Scan unavailable";
  $("#heroClock").textContent = `${new Date().toISOString().slice(11, 16)} UTC`;
}

function filteredRows() {
  let rows = [...state.rows];
  if (state.filter === "momentum") rows = rows.filter((row) => num(row.change_pct) > 0);
  if (state.filter === "value") rows = rows.filter((row) => num(row.pe) !== null && row.pe > 0 && row.pe < 25);
  if (state.filter === "quality") rows = rows.filter((row) => num(row.roe) !== null && row.roe >= 15);
  if (state.filter === "watchlist") rows = rows.filter((row) => state.watch.has(row.symbol));
  if (state.query) {
    const query = state.query.toLowerCase();
    rows = rows.filter((row) => `${row.symbol} ${company(row)}`.toLowerCase().includes(query));
  }
  const { key, dir } = state.sort;
  const direction = dir === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    const left = num(a[key]);
    const right = num(b[key]);
    if (left === null && right === null) return 0;
    if (left === null) return 1;
    if (right === null) return -1;
    return (left - right) * direction;
  });
  return rows;
}

function renderTable() {
  if (state.loading) return;
  const rows = filteredRows();
  const totalPages = Math.max(1, Math.ceil(rows.length / state.perPage));
  state.page = Math.min(state.page, totalPages);
  const start = (state.page - 1) * state.perPage;
  const pageRows = rows.slice(start, start + state.perPage);
  const body = $("#tableBody");
  $("#emptyState").classList.toggle("hidden", pageRows.length > 0);
  $("#marketTable").classList.toggle("is-empty", pageRows.length === 0);
  $("#pageLabel").textContent = `${state.page} / ${totalPages}`;
  $("#prevPage").disabled = state.page <= 1;
  $("#nextPage").disabled = state.page >= totalPages;
  $("#tableFootNote").textContent = state.query
    ? `${rows.length} matches for "${state.query}"`
    : "One scan, 20 names. Every cell keeps its source.";

  if (!pageRows.length) {
    body.innerHTML = "";
    return;
  }

  body.innerHTML = pageRows.map((row) => {
    const changeClass = pctClass(row.change_pct);
    const rsi = num(row.rsi_14);
    const rsiWidth = rsi === null ? 0 : Math.max(3, Math.min(100, rsi));
    const selected = state.selected === row.symbol;
    return `
      <tr class="data-row${selected ? " is-selected" : ""}" data-symbol="${esc(row.symbol)}" tabindex="0">
        <td class="col-rank"><span class="rank-num">${esc(String(row.rank).padStart(2, "0"))}</span></td>
        <td class="col-name">
          <div class="ticker-wrap">
            <span class="ticker-mark">${esc(mark(row.symbol))}</span>
            <span class="ticker-copy"><span class="ticker" title="${esc(company(row))}">${esc(row.symbol)}</span><span class="company" title="${esc(company(row))}">${esc(company(row))}</span></span>
          </div>
        </td>
        <td class="col-num"><span class="price">${fmtPrice(row.price)}</span></td>
        <td class="col-num"><span class="change ${changeClass}">${fmtPct(row.change_pct)}</span></td>
        <td class="col-num"><span class="cap">${fmtCap(row.market_cap)}</span></td>
        <td class="col-num"><span class="metric">${fmtRatio(row.pe)}</span></td>
        <td class="col-num col-fpe"><span class="metric dim">${fmtRatio(row.forward_pe)}</span></td>
        <td class="col-num col-rsi">
          <span class="rsi-wrap"><span class="rsi-bar"><i style="width:${rsiWidth}%"></i></span><span class="metric${rsi !== null && rsi > 70 ? "" : " dim"}">${rsi === null ? "--" : rsi.toFixed(1)}</span></span>
        </td>
        <td class="col-num col-ytd"><span class="metric ${pctClass(row.perf_ytd)}">${fmtPct(row.perf_ytd)}</span></td>
        <td class="col-star"><button class="star${state.watch.has(row.symbol) ? " is-on" : ""}" data-star="${esc(row.symbol)}" aria-label="${state.watch.has(row.symbol) ? "Remove" : "Add"} ${esc(row.symbol)} from watchlist">${state.watch.has(row.symbol) ? "★" : "☆"}</button></td>
      </tr>`;
  }).join("");

  $$(".star", body).forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleWatch(button.dataset.star);
    });
  });
  $$(".data-row", body).forEach((row) => {
    row.addEventListener("click", () => selectSymbol(row.dataset.symbol, true));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectSymbol(row.dataset.symbol, true);
      }
    });
  });
  updateSortHeaders();
}

function updateSortHeaders() {
  $$(".market-table th.sortable").forEach((header) => {
    const active = header.dataset.sort === state.sort.key;
    header.classList.toggle("is-sorted", active);
    if (active) header.dataset.dir = state.sort.dir;
    else delete header.dataset.dir;
  });
  const label = {
    market_cap: "Market cap",
    change_pct: "Day move",
    pe: "P/E",
    forward_pe: "Fwd P/E",
    rsi_14: "RSI",
    perf_ytd: "YTD",
    price: "Price",
  }[state.sort.key] || "Sort";
  $("#sortButton").innerHTML = `${label} <span>${state.sort.dir === "desc" ? "↓" : "↑"}</span>`;
}

function selectSymbol(symbol, scroll = false) {
  if (!symbol) return;
  state.selected = symbol;
  renderTable();
  renderDetail();
  if (scroll && window.innerWidth > 940) {
    $("#detailCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function renderDetail() {
  const row = state.rows.find((item) => item.symbol === state.selected);
  $("#detailEmpty").classList.toggle("hidden", Boolean(row));
  $("#detailBody").classList.toggle("hidden", !row);
  $("#detailStar").classList.toggle("is-on", Boolean(row && state.watch.has(row.symbol)));
  if (!row) {
    $("#detailSymbol").textContent = "--";
    $("#detailStar").textContent = "☆";
    return;
  }
  $("#detailSymbol").textContent = row.symbol;
  $("#detailStar").textContent = state.watch.has(row.symbol) ? "★" : "☆";
  $("#detailPrice").textContent = fmtPrice(row.price);
  const change = $("#detailChange");
  change.textContent = fmtPct(row.change_pct);
  change.className = `change-pill${num(row.change_pct) < 0 ? " down" : ""}`;
  $("#detailName").textContent = company(row);
  $("#detailCap").textContent = fmtCap(row.market_cap);
  $("#detailPe").textContent = fmtRatio(row.pe);
  $("#detailFpe").textContent = fmtRatio(row.forward_pe);
  $("#detailRsi").textContent = row.rsi_14 === null || row.rsi_14 === undefined ? "--" : Number(row.rsi_14).toFixed(1);
  $("#detailYtd").textContent = fmtPct(row.perf_ytd);
  $("#detailBeta").textContent = fmtRatio(row.beta);
  $("#detailSource").textContent = `${state.source || "scan"} · ${fmtStamp(row.as_of || state.asOf)}`;
  const low = num(row.week52_low);
  const high = num(row.week52_high);
  const price = num(row.price);
  const range = low !== null && high !== null && high > low && price !== null ? Math.max(0, Math.min(100, ((price - low) / (high - low)) * 100)) : 50;
  $("#detailRangeFill").style.width = `${range}%`;
  $("#detailRangeDot").style.left = `${range}%`;
  $("#detailRangeLabel").textContent = `${Math.round(range)}% of range`;
  $("#detailLow").textContent = fmtPrice(low);
  $("#detailHigh").textContent = fmtPrice(high);
  $("#compareButton").textContent = state.compare.includes(row.symbol) ? "In compare" : "Add to compare";
}

function renderSignals() {
  const sorted = [...state.rows].sort((a, b) => (num(b.change_pct) ?? -Infinity) - (num(a.change_pct) ?? -Infinity)).slice(0, 4);
  const list = $("#signalList");
  if (!sorted.length) {
    list.innerHTML = '<div class="detail-empty">Signals appear after the scan.</div>';
    return;
  }
  list.innerHTML = sorted.map((row, index) => `
    <button class="signal-item" data-signal="${esc(row.symbol)}">
      <span class="signal-rank">0${index + 1}</span>
      <span class="signal-copy"><b>${esc(row.symbol)}</b><span>${esc(company(row))}</span></span>
      <span class="signal-change ${pctClass(row.change_pct) === "down" ? "down" : ""}">${fmtPct(row.change_pct)}</span>
    </button>`).join("");
  $$(".signal-item", list).forEach((button) => {
    button.addEventListener("click", () => selectSymbol(button.dataset.signal));
  });
}

function renderWatchCounts() {
  $("#navWatchCount").textContent = String(state.watch.size);
  $("#watchChipCount").textContent = String(state.watch.size);
  $("#navWatchCount").classList.toggle("hidden", state.watch.size === 0);
  if (state.filter === "watchlist") renderTable();
}

function toggleWatch(symbol) {
  if (!symbol) return;
  const active = state.watch.has(symbol);
  if (active) state.watch.delete(symbol);
  else state.watch.add(symbol);
  persist();
  toast(active ? `${symbol} removed from watchlist` : `${symbol} added to watchlist`);
  renderAll();
}

function setFilter(filter) {
  state.filter = filter;
  state.page = 1;
  $$(".chip").forEach((chip) => chip.classList.toggle("is-active", chip.dataset.filter === filter));
  renderTable();
}

function setView(view) {
  state.view = view;
  $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === view));
  const labels = { discover: "Discover", watchlist: "Watchlist", signals: "Signals" };
  $("#crumbCurrent").textContent = labels[view] || "Discover";
  if (view === "watchlist") setFilter("watchlist");
  else if (view === "signals") setFilter("momentum");
  else setFilter("all");
}

function setSort(key) {
  if (!key) return;
  if (state.sort.key === key) {
    state.sort.dir = state.sort.dir === "desc" ? "asc" : "desc";
  } else {
    state.sort = { key, dir: "desc" };
  }
  state.page = 1;
  renderTable();
}

function renderCompare() {
  const tray = $("#compareTray");
  tray.classList.toggle("hidden", state.compare.length === 0);
  $("#compareCount").textContent = `${state.compare.length} / 3`;
  $("#compareItems").innerHTML = state.compare.map((symbol) => `
    <span class="compare-token">${esc(symbol)}<button data-remove-compare="${esc(symbol)}" aria-label="Remove ${esc(symbol)}">×</button></span>`).join("");
  $$("[data-remove-compare]").forEach((button) => {
    button.addEventListener("click", () => {
      state.compare = state.compare.filter((symbol) => symbol !== button.dataset.removeCompare);
      renderCompare();
      renderDetail();
    });
  });
}

function addCompare(symbol) {
  if (!symbol) return;
  if (state.compare.includes(symbol)) {
    state.compare = state.compare.filter((item) => item !== symbol);
    toast(`${symbol} removed from compare`);
  } else if (state.compare.length >= 3) {
    toast("Compare holds three names at a time");
    return;
  } else {
    state.compare.push(symbol);
    toast(`${symbol} added to compare`);
  }
  renderCompare();
  renderDetail();
}

function openCompare() {
  if (state.compare.length < 2) {
    toast("Add one more name to compare");
    return;
  }
  const rows = state.compare.map((symbol) => state.rows.find((row) => row.symbol === symbol)).filter(Boolean);
  $("#compareGrid").innerHTML = rows.map((row) => `
    <article class="compare-column">
      <h3>${esc(row.symbol)}</h3>
      <div class="compare-name">${esc(company(row))}</div>
      <dl>
        <div><dt>Price</dt><dd>${fmtPrice(row.price)}</dd></div>
        <div><dt>Day</dt><dd>${fmtPct(row.change_pct)}</dd></div>
        <div><dt>Market cap</dt><dd>${fmtCap(row.market_cap)}</dd></div>
        <div><dt>P/E</dt><dd>${fmtRatio(row.pe)}</dd></div>
        <div><dt>Fwd P/E</dt><dd>${fmtRatio(row.forward_pe)}</dd></div>
        <div><dt>RSI</dt><dd>${row.rsi_14 === null ? "--" : Number(row.rsi_14).toFixed(1)}</dd></div>
        <div><dt>YTD</dt><dd>${fmtPct(row.perf_ytd)}</dd></div>
      </dl>
    </article>`).join("");
  $("#compareModal").classList.remove("hidden");
}

function closeCompare() {
  $("#compareModal").classList.add("hidden");
}

function openNews() {
  const row = state.rows.find((item) => item.symbol === state.selected);
  if (!row) return;
  const modal = document.createElement("div");
  modal.className = "modal";
  modal.id = "newsModal";
  modal.innerHTML = `
    <div class="modal-panel news-panel">
      <div class="modal-head">
        <div><span class="panel-kicker">News enrichment</span><h2>${esc(row.symbol)} · headlines</h2></div>
        <button class="icon-button" data-close-news aria-label="Close news">×</button>
      </div>
      <div class="news-list" id="newsList"><div class="detail-empty">Loading source-backed headlines...</div></div>
    </div>`;
  document.body.append(modal);
  modal.addEventListener("click", (event) => {
    if (event.target === modal || event.target.closest("[data-close-news]")) modal.remove();
  });
  fetchJSON(`/news/${encodeURIComponent(row.symbol)}?limit=6`, {}, 15000)
    .then((body) => {
      const items = Array.isArray(body.items) ? body.items : [];
      const list = modal.querySelector("#newsList");
      if (!items.length) {
        list.innerHTML = '<div class="detail-empty">No headlines returned for this symbol.</div>';
        return;
      }
      list.innerHTML = items.map((item) => `
        <a class="news-item" href="${esc(safeUrl(item.url))}" target="_blank" rel="noreferrer">
          <span class="news-source">${esc(item.source || "wire")}</span>
          <strong>${esc(item.headline || "Untitled")}</strong>
          <span class="news-time">${esc(fmtStamp(item.published_at))}</span>
        </a>`).join("");
    })
    .catch((error) => {
      modal.querySelector("#newsList").innerHTML = `<div class="detail-empty">${esc(error.message)}</div>`;
    });
}

function openPalette() {
  const palette = $("#palette");
  palette.classList.remove("hidden");
  $("#paletteInput").value = "";
  renderPalette("");
  setTimeout(() => $("#paletteInput").focus(), 30);
}

function closePalette() {
  $("#palette").classList.add("hidden");
}

function paletteCommands(query = "") {
  const commands = [
    { icon: "⌕", label: "Search this screen", key: "/", run: () => { closePalette(); $("#searchInput").focus(); } },
    { icon: "◇", label: "Open watchlist", key: "2", run: () => { closePalette(); setView("watchlist"); } },
    { icon: "⌁", label: "Show momentum names", key: "3", run: () => { closePalette(); setView("signals"); } },
    { icon: "↻", label: "Refresh market scan", key: "R", run: () => { closePalette(); loadData(); } },
    { icon: "⌫", label: "Clear filters", key: "X", run: () => { closePalette(); clearFilters(); } },
    { icon: "▤", label: "Toggle compact density", key: "C", run: () => { closePalette(); toggleCompact(); } },
  ];
  if (state.compare.length) {
    commands.unshift({ icon: "↗", label: `Compare ${state.compare.length} selected names`, key: "↵", run: () => { closePalette(); openCompare(); } });
  }
  const needle = query.trim().toLowerCase();
  return needle ? commands.filter((command) => command.label.toLowerCase().includes(needle)) : commands;
}

let paletteIndex = 0;
function renderPalette(query) {
  const commands = paletteCommands(query);
  const list = $("#paletteList");
  paletteIndex = Math.max(0, Math.min(paletteIndex, commands.length - 1));
  if (!commands.length) {
    list.innerHTML = '<div class="palette-row">No matching command</div>';
    return;
  }
  list.innerHTML = commands.map((command, index) => `
    <div class="palette-row${index === paletteIndex ? " is-active" : ""}" data-command="${index}">
      <span class="p-icon">${command.icon}</span><span>${esc(command.label)}</span><span class="p-key">${esc(command.key)}</span>
    </div>`).join("");
  $$("[data-command]", list).forEach((row) => {
    row.addEventListener("click", () => commands[Number(row.dataset.command)].run());
  });
}

function movePalette(delta) {
  const commands = paletteCommands($("#paletteInput").value);
  if (!commands.length) return;
  paletteIndex = (paletteIndex + delta + commands.length) % commands.length;
  renderPalette($("#paletteInput").value);
}

function clearFilters() {
  state.filter = "all";
  state.query = "";
  state.page = 1;
  $("#searchInput").value = "";
  $$(".chip").forEach((chip) => chip.classList.toggle("is-active", chip.dataset.filter === "all"));
  renderTable();
}

function toggleCompact() {
  document.body.classList.toggle("is-compact");
  const active = document.body.classList.contains("is-compact");
  $("#compactButton").textContent = active ? "Comfortable" : "Compact";
}

const columnLabels = { fpe: "Fwd P/E", rsi: "RSI", ytd: "YTD" };
function renderColumnState() {
  for (const [column, hidden] of Object.entries({
    fpe: state.hiddenColumns.has("fpe"),
    rsi: state.hiddenColumns.has("rsi"),
    ytd: state.hiddenColumns.has("ytd"),
  })) {
    $$(`.col-${column}`).forEach((cell) => cell.classList.toggle("col-hidden", hidden));
  }
}

function openColumnsMenu() {
  const existing = $("#columnMenu");
  if (existing) {
    existing.remove();
    return;
  }
  const menu = document.createElement("div");
  menu.id = "columnMenu";
  menu.className = "column-menu";
  menu.innerHTML = Object.entries(columnLabels).map(([key, label]) => `
    <label><input type="checkbox" data-column="${key}" ${state.hiddenColumns.has(key) ? "" : "checked"} /> <span>${label}</span></label>`).join("");
  document.body.append(menu);
  const button = $("#columnsButton").getBoundingClientRect();
  menu.style.top = `${button.bottom + 8}px`;
  menu.style.left = `${Math.max(10, button.right - 160)}px`;
  $$("input[data-column]", menu).forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) state.hiddenColumns.delete(input.dataset.column);
      else state.hiddenColumns.add(input.dataset.column);
      persist();
      renderColumnState();
    });
  });
  setTimeout(() => {
    const close = (event) => {
      if (!menu.contains(event.target) && event.target !== $("#columnsButton")) {
        menu.remove();
        document.removeEventListener("click", close);
      }
    };
    document.addEventListener("click", close);
  }, 0);
}

function wire() {
  $("#searchTrigger").addEventListener("click", openPalette);
  $("#paletteButton").addEventListener("click", openPalette);
  $("#footerPalette").addEventListener("click", openPalette);
  $("#refreshButton").addEventListener("click", loadData);
  $("#exploreButton").addEventListener("click", () => $("#marketTable").scrollIntoView({ behavior: "smooth", block: "start" }));
  $("#palette").addEventListener("click", (event) => {
    if (event.target === $("#palette")) closePalette();
  });
  $("#paletteInput").addEventListener("input", (event) => {
    paletteIndex = 0;
    renderPalette(event.target.value);
  });
  $("#paletteInput").addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); movePalette(1); }
    if (event.key === "ArrowUp") { event.preventDefault(); movePalette(-1); }
    if (event.key === "Enter") {
      event.preventDefault();
      const commands = paletteCommands(event.target.value);
      commands[paletteIndex]?.run();
    }
  });
  $("#searchInput").addEventListener("input", (event) => {
    state.query = event.target.value;
    state.page = 1;
    renderTable();
  });
  $("#filterChips").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-filter]");
    if (chip) setFilter(chip.dataset.filter);
  });
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));
  $$(".side-row").forEach((item) => item.addEventListener("click", () => {
    state.region = item.dataset.region;
    $$(".side-row").forEach((row) => row.classList.toggle("is-active", row === item));
    loadData();
  }));
  $$(".market-table th.sortable").forEach((header) => header.addEventListener("click", () => setSort(header.dataset.sort)));
  $("#sortButton").addEventListener("click", () => {
    const keys = ["market_cap", "change_pct", "pe", "forward_pe", "rsi_14", "perf_ytd"];
    const current = keys.indexOf(state.sort.key);
    setSort(keys[(current + 1) % keys.length]);
  });
  $("#compactButton").addEventListener("click", toggleCompact);
  $("#columnsButton").addEventListener("click", openColumnsMenu);
  $("#clearFilters").addEventListener("click", clearFilters);
  $("#prevPage").addEventListener("click", () => { state.page = Math.max(1, state.page - 1); renderTable(); });
  $("#nextPage").addEventListener("click", () => {
    const total = Math.max(1, Math.ceil(filteredRows().length / state.perPage));
    state.page = Math.min(total, state.page + 1);
    renderTable();
  });
  $("#detailStar").addEventListener("click", () => toggleWatch(state.selected));
  $("#compareButton").addEventListener("click", () => addCompare(state.selected));
  $("#newsButton").addEventListener("click", openNews);
  $("#clearCompare").addEventListener("click", () => { state.compare = []; renderCompare(); renderDetail(); });
  $("#openCompare").addEventListener("click", openCompare);
  $("#closeCompare").addEventListener("click", closeCompare);
  $("#compareModal").addEventListener("click", (event) => {
    if (event.target === $("#compareModal")) closeCompare();
  });
  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement?.tagName;
    const editing = tag === "INPUT" || tag === "TEXTAREA" || document.activeElement?.isContentEditable;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openPalette();
      return;
    }
    if (event.key === "Escape") {
      closePalette();
      closeCompare();
      $("#newsModal")?.remove();
      $("#columnMenu")?.remove();
      return;
    }
    if (editing) return;
    if (event.key === "/") { event.preventDefault(); $("#searchInput").focus(); }
    if (event.key === "1") setView("discover");
    if (event.key === "2") setView("watchlist");
    if (event.key === "3") setView("signals");
    if (event.key === "r" || event.key === "R") loadData();
    if (event.key === "c" || event.key === "C") toggleCompact();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const rows = filteredRows();
      if (!rows.length) return;
      event.preventDefault();
      const current = rows.findIndex((row) => row.symbol === state.selected);
      const next = event.key === "ArrowDown" ? Math.min(rows.length - 1, current + 1) : Math.max(0, current - 1);
      selectSymbol(rows[next].symbol);
    }
  });
  setInterval(() => { $("#heroClock").textContent = `${new Date().toISOString().slice(11, 16)} UTC`; }, 60000);
}

wire();
loadData();
