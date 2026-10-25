/* Ztockz market cockpit - presentation only. Every number comes from the API. */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const WATCH_KEY = "ztockz.watchlist.v1";
const COLS_KEY = "ztockz.columns.v1";

const COLUMN_META = {
  price: ["Price", "price"],
  prev_close: ["Prev close", "price"],
  change_pct: ["1D", "pct"],
  volume: ["Volume", "int"],
  avg_volume: ["Avg vol", "int"],
  market_cap: ["Mkt cap", "cap"],
  market_cap_usd: ["Mkt cap", "cap"],
  enterprise_value: ["EV", "cap"],
  pe: ["P/E", "ratio"],
  forward_pe: ["Fwd P/E", "ratio"],
  peg: ["PEG", "ratio"],
  ps: ["P/S", "ratio"],
  pb: ["P/B", "ratio"],
  pc: ["P/C", "ratio"],
  pfcf: ["P/FCF", "ratio"],
  ev_ebitda: ["EV/EBITDA", "ratio"],
  ev_sales: ["EV/Sales", "ratio"],
  eps_ttm: ["EPS ttm", "ratio"],
  eps_next_y: ["EPS next Y", "ratio"],
  rsi_14: ["RSI", "rsi"],
  rel_volume: ["Rel vol", "ratio"],
  atr_14: ["ATR", "ratio"],
  beta: ["Beta", "ratio"],
  perf_week: ["1W", "pct"],
  perf_month: ["1M", "pct"],
  perf_quarter: ["1Q", "pct"],
  perf_half_y: ["6M", "pct"],
  perf_ytd: ["YTD", "pct"],
  perf_year: ["1Y", "pct"],
  perf_3y: ["3Y", "pct"],
  perf_5y: ["5Y", "pct"],
  perf_10y: ["10Y", "pct"],
  week52_high: ["52W high", "price"],
  week52_low: ["52W low", "price"],
  insider_own: ["Insider own", "pct_plain"],
  insider_trans: ["Insider trans", "pct"],
  inst_own: ["Inst own", "pct_plain"],
  inst_trans: ["Inst trans", "pct"],
  shares_outstanding: ["Shares out", "int"],
  shares_float: ["Float", "int"],
  option_short: ["Shortable", "text"],
  income: ["Income", "cap"],
  sales: ["Sales", "cap"],
  book_per_share: ["Book/sh", "ratio"],
  cash_per_share: ["Cash/sh", "ratio"],
  eps_next_q: ["EPS next Q", "ratio"],
  eps_this_y: ["EPS this Y", "pct"],
  eps_next_y_growth: ["EPS next Y", "pct"],
  eps_next_5y: ["EPS next 5Y", "pct"],
  eps_past_3_5y: ["EPS past 3/5Y", "pct"],
  sales_past_3_5y: ["Sales past 3/5Y", "pct"],
  eps_yoy_ttm: ["EPS YOY ttm", "pct"],
  sales_yoy_ttm: ["Sales YOY ttm", "pct"],
  eps_qoq: ["EPS Q/Q", "pct"],
  sales_qoq: ["Sales Q/Q", "pct"],
  roa: ["ROA", "pct"],
  roe: ["ROE", "pct"],
  roic: ["ROIC", "pct"],
  gross_margin: ["Gross margin", "pct"],
  oper_margin: ["Oper margin", "pct"],
  profit_margin: ["Profit margin", "pct"],
  quick_ratio: ["Quick ratio", "ratio"],
  current_ratio: ["Current ratio", "ratio"],
  debt_to_equity: ["Debt/Eq", "ratio"],
  lt_debt_to_equity: ["LT Debt/Eq", "ratio"],
  employees: ["Employees", "int"],
  ipo: ["IPO", "text"],
  sma20: ["SMA20", "ratio"],
  sma50: ["SMA50", "ratio"],
  sma200: ["SMA200", "ratio"],
  volatility: ["Volatility", "ratio"],
  index: ["Index", "text"],
};

const SCREEN_VIEWS = {
  overview: { label: "Overview", columns: ["price", "change_pct", "market_cap", "pe", "forward_pe", "rsi_14", "perf_ytd", "index"] },
  valuation: { label: "Valuation", columns: ["market_cap", "enterprise_value", "pe", "forward_pe", "peg", "ps", "pb", "pc", "pfcf", "ev_ebitda", "ev_sales"] },
  performance: { label: "Performance", columns: ["perf_week", "perf_month", "perf_quarter", "perf_half_y", "perf_ytd", "perf_year", "perf_3y", "perf_5y", "perf_10y", "week52_high", "week52_low"] },
  ownership: { label: "Ownership", columns: ["insider_own", "insider_trans", "inst_own", "inst_trans", "shares_outstanding", "shares_float", "option_short"] },
  financial: { label: "Financial", columns: ["income", "sales", "book_per_share", "cash_per_share", "eps_ttm", "eps_next_y", "eps_next_q", "eps_this_y", "eps_next_y_growth", "eps_next_5y", "eps_past_3_5y", "sales_past_3_5y", "eps_yoy_ttm", "sales_yoy_ttm", "eps_qoq", "sales_qoq", "roa", "roe", "roic", "gross_margin", "oper_margin", "profit_margin", "quick_ratio", "current_ratio", "debt_to_equity", "lt_debt_to_equity", "employees", "ipo"] },
  technical: { label: "Technical", columns: ["market_cap", "price", "prev_close", "change_pct", "volume", "avg_volume", "rel_volume", "atr_14", "rsi_14", "sma20", "sma50", "sma200", "volatility", "beta"] },
};
SCREEN_VIEWS.all = {
  label: "All columns",
  columns: [...new Set(Object.values(SCREEN_VIEWS).flatMap((view) => view.columns))],
};
const state = {
  rows: [],
  names: {},
  selected: null,
  filter: "all",
  query: "",
  sort: { key: "market_cap", dir: "desc" },
  page: 1,
  perPage: 20,
  offset: 0,
  total: 0,
  region: "us",
  view: "discover",
  screenView: "all",
  overviewBySymbol: new Map(),
  serverFilters: [],
  serverSort: "market_cap",
  watch: new Set(readJSON(WATCH_KEY, [])),
  compare: [],
  hiddenColumns: new Set(),
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
  return `${date.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${fmtTime(value)} UTC`;
}

function mark(symbol) {
  return String(symbol || "").split(".")[0].slice(0, 4).toUpperCase();
}

function company(row) {
  return row.name || state.names[row.symbol] || "US equity";
}

function renderCell(row, key) {
  const [label, kind] = COLUMN_META[key] || [key, "ratio"];
  const value = row[key];
  if (value === null || value === undefined || value === "") {
    return '<span class="metric dim" title="' + esc(label) + '">--</span>';
  }
  if (kind === "price") return `<span class="price">${fmtPrice(value)}</span>`;
  if (kind === "cap") return `<span class="cap">${fmtCap(value)}</span>`;
  if (kind === "int") return `<span class="metric">${fmtInt(value)}</span>`;
  if (kind === "pct") return `<span class="metric ${pctClass(value)}">${fmtPct(value)}</span>`;
  if (kind === "pct_plain") {
    const n = num(value);
    return `<span class="metric">${n === null ? "--" : `${n.toFixed(2)}%`}</span>`;
  }
  if (kind === "rsi") {
    const n = num(value);
    return `<span class="rsi-wrap"><span class="rsi-bar"><i style="width:${n === null ? 0 : Math.max(3, Math.min(100, n))}%"></i></span><span class="metric">${n === null ? "--" : n.toFixed(1)}</span></span>`;
  }
  if (kind === "text") return `<span class="metric">${esc(value)}</span>`;
  return `<span class="metric">${fmtRatio(value)}</span>`;
}

function currentColumns() {
  return SCREEN_VIEWS[state.screenView]?.columns || SCREEN_VIEWS.overview.columns;
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
  const requestedView = state.screenView;
  const selectBody = {
    mode: "criteria",
    region,
    criteria: { filters: state.serverFilters, sort: state.serverSort, limit: state.perPage, offset: state.offset },
    view: requestedView,
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
    const seenAliases = new Set(["GOOGN", "GOOGM"]);
    const richBySymbol = new Map(rows.filter((row) => !seenAliases.has(row.symbol)).map((row) => [row.symbol, row]));
    const merged = [];
    const hasServerFilters = state.serverFilters.length > 0 || state.offset > 0;
    for (const light of snapshotRows) {
      if (hasServerFilters && !richBySymbol.has(light.symbol)) continue;
      const rich = richBySymbol.get(light.symbol) || {};
      const base = state.overviewBySymbol.get(light.symbol) || {};
      merged.push({
        ...base,
        ...rich,
        ...light,
        market_cap: num(rich.market_cap) ?? num(base.market_cap) ?? num(rich.market_cap_usd) ?? num(light.market_cap_usd),
        change_pct: num(rich.change_pct) ?? num(base.change_pct) ?? num(light.change_pct),
        price: num(rich.price) ?? num(base.price) ?? num(light.price),
      });
      richBySymbol.delete(light.symbol);
      if (merged.length >= 20) break;
    }
    for (const rich of richBySymbol.values()) {
      if (merged.length >= 20) break;
      const base = state.overviewBySymbol.get(rich.symbol) || {};
      merged.push({ ...base, ...rich, market_cap: num(rich.market_cap) ?? num(base.market_cap) ?? num(rich.market_cap_usd) });
    }
    state.rows = merged.map((row, index) => ({
      ...row,
      rank: state.offset + index + 1,
      name: row.name || state.names[row.symbol] || "",
      sector: row.sector || "",
    }));
    if (requestedView === "overview") {
      state.overviewBySymbol = new Map(state.rows.map((row) => [row.symbol, row]));
    }
    state.source = selection.source || selection.fallback?.source || "tradingview";
    state.universe = num(selection.universe_total) ?? num(selection.total) ?? rows.length;
    state.total = num(selection.total) ?? state.universe ?? rows.length;
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
  syncLayout();
  renderNotice();
  renderStats();
  renderTable();
  renderDetail();
  renderSignals();
  renderCompare();
  renderWatchCounts();
}

function syncLayout() {
  document.body.classList.toggle("is-all-view", state.screenView === "all");
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
  $("#selectionMeta").textContent = state.asOf ? `Live scan · ${fmtStamp(state.asOf)}` : "Live scan";
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
  if (!key) return rows;
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
  const columns = currentColumns();
  const total = Math.max(state.total, rows.length);
  const totalPages = Math.max(1, Math.ceil(total / state.perPage));
  const pageRows = rows;
  const start = state.offset;
  const body = $("#tableBody");
  const head = $("#tableHead");

  head.innerHTML = `
    <th class="col-rank">#</th>
    <th class="col-name">Company</th>
    ${columns.map((key) => {
      const [label] = COLUMN_META[key] || [key, "ratio"];
      return `<th class="col-num sortable" data-sort="${esc(key)}">${esc(label)}</th>`;
    }).join("")}
    <th class="col-star">Watch</th>`;

  $("#emptyState").classList.toggle("hidden", pageRows.length > 0);
  $("#marketTable").classList.toggle("is-empty", pageRows.length === 0);
  $("#pageLabel").textContent = `${Math.min(totalPages, Math.floor(state.offset / state.perPage) + 1)} / ${totalPages}`;
  $("#prevPage").disabled = state.offset <= 0;
  $("#nextPage").disabled = state.offset + state.perPage >= total;
  $("#tableFootNote").textContent = state.query
    ? `${rows.length} matches for "${state.query}" in ${SCREEN_VIEWS[state.screenView].label.toLowerCase()}`
    : state.screenView === "all"
      ? `${start + 1}-${start + pageRows.length} of ${fmtInt(total)} names · ${columns.length} columns · scroll horizontally`
      : `${start + 1}-${start + pageRows.length} of ${fmtInt(total)} names in ${SCREEN_VIEWS[state.screenView].label.toLowerCase()}. Missing values stay missing.`;

  if (!pageRows.length) {
    body.innerHTML = "";
    updateSortHeaders();
    return;
  }

  body.innerHTML = pageRows.map((row) => {
    const selected = state.selected === row.symbol;
    return `
      <tr class="data-row${selected ? " is-selected" : ""}" data-symbol="${esc(row.symbol)}" tabindex="0">
        <td class="col-rank"><span class="rank-num">${esc(String(row.rank).padStart(2, "0"))}</span></td>
        <td class="col-name">
          <div class="ticker-wrap">
            <span class="ticker-mark"><span class="ticker-fallback">${esc(mark(row.symbol))}</span><img class="ticker-logo" src="${esc(row.logo || "")}" alt="" loading="eager" /></span>
            <span class="ticker-copy"><span class="ticker" title="${esc(company(row))}">${esc(row.symbol)}</span><span class="company" title="${esc(company(row))}">${esc(company(row))}</span></span>
          </div>
        </td>
        ${columns.map((key) => {
          return `<td class="col-num">${renderCell(row, key)}</td>`;
        }).join("")}
        <td class="col-star"><button class="star${state.watch.has(row.symbol) ? " is-on" : ""}" data-star="${esc(row.symbol)}" aria-label="${state.watch.has(row.symbol) ? "Remove" : "Add"} ${esc(row.symbol)} from watchlist">${state.watch.has(row.symbol) ? "★" : "☆"}</button></td>
      </tr>`;
  }).join("");

  $$(".ticker-logo", body).forEach((image) => {
    image.addEventListener("error", () => image.remove());
  });
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
  $$("#tableHead th.sortable").forEach((header) => {
    header.addEventListener("click", () => setSort(header.dataset.sort));
  });
  updateSortHeaders();
}

function columnShort(key) {
  return { forward_pe: "fpe", rsi_14: "rsi", perf_ytd: "ytd" }[key] || key;
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
    $("#detailLogo").innerHTML = "Z";
    $("#detailStar").textContent = "☆";
    return;
  }
  $("#detailSymbol").textContent = row.symbol;
  $("#detailLogo").innerHTML = `<span class="ticker-fallback">${esc(mark(row.symbol))}</span><img class="ticker-logo" src="${esc(row.logo || "")}" alt="" />`;
  const detailImage = $("#detailLogo .ticker-logo");
  if (detailImage) detailImage.addEventListener("error", () => detailImage.remove());
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
  $("#detailSource").textContent = fmtStamp(row.as_of || state.asOf);
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
  state.offset = 0;
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

function setScreenView(name) {
  if (!SCREEN_VIEWS[name] || state.screenView === name) return;
  state.screenView = name;
  state.offset = 0;
  $$(".view-tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.screenView === name));
  syncLayout();
  loadData();
}

function exportJson() {
  const rows = filteredRows();
  const columns = currentColumns();
  const document = {
    engine: "Ztockz",
    view: state.screenView,
    as_of: state.asOf,
    count: rows.length,
    rows: rows.map((row) => {
      const usable = { rank: row.rank, symbol: row.symbol, name: company(row), logo: row.logo || "" };
      for (const key of columns) usable[key] = row[key] ?? null;
      return usable;
    }),
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(document, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `ztockz-${state.screenView}-${new Date().toISOString().slice(0, 10)}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
  toast(`${rows.length} rows exported as JSON`);
}

function exportCsv() {
  const rows = filteredRows();
  const columns = currentColumns();
  const header = ["Symbol", "Company", ...columns.map((key) => COLUMN_META[key]?.[0] || key)];
  const lines = [header, ...rows.map((row) => [
    row.symbol,
    company(row),
    ...columns.map((key) => {
      const value = row[key];
      return value === null || value === undefined ? "" : value;
    }),
  ])];
  const csv = lines.map((line) => line.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `ztockz-${state.screenView}-${new Date().toISOString().slice(0, 10)}.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
  toast(`${rows.length} rows exported as CSV`);
}

function setSort(key) {
  if (!key) return;
  if (state.sort.key === key) {
    state.sort.dir = state.sort.dir === "desc" ? "asc" : "desc";
  } else {
    state.sort = { key, dir: "desc" };
  }
  state.offset = 0;
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
      <div class="news-list" id="newsList"><div class="detail-empty">Loading engine headlines...</div></div>
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
          <span class="news-source">NEWS</span>
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

function filterFields() {
  return [
    ["filterCap", "cap", "gte", 1e9],
    ["filterPe", "fa_pe", "lte", 1],
    ["filterPrice", "sh_price", "gte", 1],
    ["filterRsi", "ta_rsi", "lte", 1],
  ];
}

function openFilters() {
  const byKey = new Map(state.serverFilters.map((filter) => [filter.key, filter.value]));
  $("#filterCap").value = byKey.has("cap") ? byKey.get("cap") / 1e9 : "";
  $("#filterPe").value = byKey.get("fa_pe") ?? "";
  $("#filterPrice").value = byKey.get("sh_price") ?? "";
  $("#filterRsi").value = byKey.get("ta_rsi") ?? "";
  $("#filterSort").value = state.serverSort;
  const sector = state.serverFilters.find((filter) => filter.key === "sec");
  $("#filterSector").value = sector?.value || "";
  $("#filterModal").classList.remove("hidden");
  setTimeout(() => $("#filterCap").focus(), 30);
}

function closeFilters() {
  $("#filterModal").classList.add("hidden");
}

function updateFilterCount() {
  const count = state.serverFilters.length;
  $("#filterCount").textContent = String(count);
  $("#filterButton").classList.toggle("has-filters", count > 0);
}

function applyFilters() {
  const filters = [];
  const sector = $("#filterSector").value;
  if (sector) filters.push({ key: "sec", op: "eq", value: sector });
  for (const [id, key, op, scale] of filterFields()) {
    const raw = $(`#${id}`).value.trim();
    if (raw === "") continue;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      toast("Enter a valid positive number");
      return;
    }
    filters.push({ key, op, value: value * scale });
  }
  state.serverFilters = filters;
  state.serverSort = $("#filterSort").value;
  state.sort = { key: null, dir: "desc" };
  state.offset = 0;
  updateFilterCount();
  closeFilters();
  loadData();
  toast(filters.length ? `${filters.length} engine filters applied` : "Engine filters cleared");
}

function clearFilters() {
  const hadServerFilters = state.serverFilters.length > 0;
  state.filter = "all";
  state.query = "";
  state.offset = 0;
  state.serverFilters = [];
  state.serverSort = "market_cap";
  state.sort = { key: "market_cap", dir: "desc" };
  $("#searchInput").value = "";
  $$(".chip").forEach((chip) => chip.classList.toggle("is-active", chip.dataset.filter === "all"));
  updateFilterCount();
  if (hadServerFilters) loadData();
  else renderTable();
}

function toggleCompact() {
  document.body.classList.toggle("is-compact");
  const active = document.body.classList.contains("is-compact");
  $("#compactButton").textContent = active ? "Comfortable" : "Compact";
}

const columnLabels = { fpe: "Fwd P/E", rsi: "RSI", ytd: "YTD" };
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
    state.offset = 0;
    renderTable();
  });
  $("#filterChips").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-filter]");
    if (chip) setFilter(chip.dataset.filter);
  });
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));
  $$(".side-row").forEach((item) => item.addEventListener("click", () => {
    state.region = item.dataset.region;
    state.offset = 0;
    state.overviewBySymbol = new Map();
    $$(".side-row").forEach((row) => row.classList.toggle("is-active", row === item));
    loadData();
  }));
  $$(".view-tab").forEach((tab) => tab.addEventListener("click", () => setScreenView(tab.dataset.screenView)));
  $("#sortButton").addEventListener("click", () => {
    const keys = ["market_cap", "change_pct", "pe", "forward_pe", "rsi_14", "perf_ytd"];
    const current = keys.indexOf(state.sort.key);
    setSort(keys[(current + 1) % keys.length]);
  });
  $("#compactButton").addEventListener("click", toggleCompact);
  $("#exportButton").addEventListener("click", exportCsv);
  $("#exportJsonButton").addEventListener("click", exportJson);
  $("#filterButton").addEventListener("click", openFilters);
  $("#closeFilter").addEventListener("click", closeFilters);
  $("#applyFilters").addEventListener("click", applyFilters);
  $("#resetFilters").addEventListener("click", () => {
    $$(".filter-form input").forEach((input) => { input.value = ""; });
    $("#filterSector").value = "";
    $("#filterSort").value = "market_cap";
  });
  $("#filterModal").addEventListener("click", (event) => {
    if (event.target === $("#filterModal")) closeFilters();
  });
  $("#clearFilters").addEventListener("click", clearFilters);
  $("#prevPage").addEventListener("click", () => {
    if (state.offset <= 0) return;
    state.offset = Math.max(0, state.offset - state.perPage);
    loadData();
  });
  $("#nextPage").addEventListener("click", () => {
    if (state.offset + state.perPage >= state.total) return;
    state.offset += state.perPage;
    loadData();
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
      closeFilters();
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
