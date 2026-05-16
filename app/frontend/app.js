const SEGMENTS = {
  INERT: "Инертный",
  POT: "Потенциальный",
  VAR: "Изменчивый",
  COLD: "Холодный старт"
};

const SIGNALS = {
  churn: ["Снижение оборотов", "danger"],
  contract: ["Истекает договор", "warn"],
  suboptim: ["Неоптимальный тариф", "warn"],
  complaint: ["Жалоба на покрытие", "danger"],
  growth: ["Рост потребления", "info"],
  new: ["Новый клиент", "info"],
  geo: ["Смена географии", "warn"]
};

const ACTIONS = {
  accept: ["Принято", "good"],
  adjust: ["Скорректировано", "warn"],
  reject: ["Отклонено", "danger"]
};

const FACTORS = {
  coverage: "Покрытие АЗС",
  geo: "География клиента",
  fuel: "Тип топлива",
  peer: "Похожие клиенты",
  tariff: "Тарифная выгода"
};

const state = {
  role: "manager",
  screen: "priority",
  query: "",
  segmentFilter: "all",
  clients: [],
  suppliers: [],
  decisions: [],
  modelRuns: [],
  modelCatalog: [],
  dataSources: [],
  users: [],
  meta: null,
  activeClient: null,
  activeTab: "reco",
  recommendations: [],
  transactions: []
};

const content = document.getElementById("content");
const toast = document.getElementById("toast");
const modal = document.getElementById("modal");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatNum(value) {
  return new Intl.NumberFormat("ru-RU").format(value ?? 0);
}

function formatRub(value) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0
  }).format(value ?? 0);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(body.error || response.statusText);
  }
  return response.json();
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add("hidden"), 2200);
}

function tag(text, tone = "") {
  return `<span class="tag ${tone}">${escapeHtml(text)}</span>`;
}

function supplierById(id) {
  return state.suppliers.find((supplier) => supplier.id === id);
}

function signalTags(client) {
  const signals = client.signals || [];
  if (!signals.length) return `<span class="muted">нет</span>`;
  return signals.map((code) => {
    const item = SIGNALS[code] || [code, ""];
    return tag(item[0], item[1]);
  }).join(" ");
}

function sparkline(history) {
  const data = history || [];
  if (data.length < 2) return "";
  const values = data.map((item) => item.l);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values.map((value, index) => {
    const x = 2 + index * (76 / (values.length - 1));
    const y = 24 - ((value - min) / range) * 20;
    return `${x},${y}`;
  }).join(" ");
  return `<svg width="82" height="28" viewBox="0 0 82 28" aria-hidden="true">
    <polyline points="${points}" fill="none" stroke="#147d75" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline>
  </svg>`;
}

function downloadCsv(filename, rows) {
  const csv = rows.map((row) => row.map((cell) => `"${String(cell ?? "").replaceAll('"', '""')}"`).join(";")).join("\n");
  const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function loadBaseData() {
  const [health, meta, suppliers, decisions, runs, sources, users, models] = await Promise.all([
    api("/api/health"),
    api("/api/meta"),
    api("/api/suppliers"),
    api("/api/decisions"),
    api("/api/model-runs"),
    api("/api/data-sources"),
    api("/api/users"),
    api("/api/model-catalog")
  ]);
  document.getElementById("service-status").textContent = `${health.status} · ${health.time}`;
  document.getElementById("data-freshness").textContent = health.time;
  state.meta = meta;
  state.suppliers = suppliers;
  state.decisions = decisions;
  state.modelRuns = runs;
  state.dataSources = sources;
  state.users = users;
  state.modelCatalog = models;
}

async function loadClients() {
  const params = new URLSearchParams();
  if (state.query) params.set("search", state.query);
  if (state.screen === "priority") params.set("priority", "true");
  let clients = await api(`/api/clients?${params.toString()}`);
  if (state.segmentFilter !== "all" && state.screen === "clients") {
    clients = clients.filter((client) => client.segment === state.segmentFilter);
  }
  state.clients = clients;
}

async function openClient(clientId) {
  const [client, recommendations, transactions] = await Promise.all([
    api(`/api/clients/${encodeURIComponent(clientId)}`),
    api(`/api/clients/${encodeURIComponent(clientId)}/recommendations`),
    api(`/api/clients/${encodeURIComponent(clientId)}/transactions`)
  ]);
  state.activeClient = client;
  state.recommendations = recommendations;
  state.transactions = transactions;
  state.activeTab = "reco";
  renderClientDetail();
}

async function openClientSafe(clientId) {
  try {
    await openClient(clientId);
  } catch (error) {
    showToast("Клиент не найден в текущих данных");
  }
}

async function refreshCurrentScreen() {
  await loadBaseData();
  if (["priority", "clients", "decisions"].includes(state.screen)) {
    await loadClients();
  }
  render();
}

function render() {
  applyRoleUi();
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.screen === state.screen);
  });

  if (state.activeClient) {
    renderClientDetail();
    return;
  }

  if (state.role === "admin") {
    if (state.screen === "models") renderModelsScreen();
    else if (state.screen === "users") renderUsersScreen();
    else renderDataScreen();
    return;
  }

  if (state.screen === "decisions") renderDecisions();
  else renderClients(state.screen === "priority");
}

function applyRoleUi() {
  const isAdmin = state.role === "admin";
  document.querySelectorAll("[data-role]").forEach((button) => {
    button.classList.toggle("active", button.dataset.role === state.role);
  });
  document.querySelectorAll("[data-role-nav]").forEach((nav) => {
    nav.classList.toggle("hidden", nav.dataset.roleNav !== state.role);
  });

  document.getElementById("user-avatar").textContent = isAdmin ? "ДП" : "ЕС";
  document.getElementById("user-name").textContent = isAdmin ? "Д. Петров" : "Е. Соколова";
  document.getElementById("user-role").textContent = isAdmin ? "Администратор" : "Менеджер по сопровождению";

  const priorityCount = state.clients.filter((client) => client.priority >= 0.6).length;
  const priorityNode = document.getElementById("priority-count");
  if (priorityNode) priorityNode.textContent = priorityCount;
}

function renderClients(priorityOnly) {
  const total = state.clients.length;
  const hot = state.clients.filter((client) => client.priority >= 0.75).length;
  const liters = state.clients.reduce((sum, client) => sum + (client.monthlyLiters || 0), 0);
  const spend = state.clients.reduce((sum, client) => sum + (client.monthlySpend || 0), 0);
  const lastRun = state.modelRuns[0] || {};
  const title = priorityOnly ? "Приоритетные клиенты" : "Все клиенты";
  const filters = priorityOnly ? "" : `
    <select id="segment-filter" class="btn">
      <option value="all">Все сегменты</option>
      <option value="INERT">Инертные</option>
      <option value="POT">Потенциальные</option>
      <option value="VAR">Изменчивые</option>
      <option value="COLD">Холодный старт</option>
    </select>`;
  const primaryAction = priorityOnly ? "Сформировать список рассылки" : "Добавить клиента";

  content.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="title">${title}</h1>
        <div class="subtitle">Рабочий список менеджера по сопровождению</div>
      </div>
      <div class="page-actions">
        ${filters}
        <button id="export-clients" class="btn">Экспорт CSV</button>
        <button class="btn btn-primary">${primaryAction}</button>
      </div>
    </div>

    <div class="grid kpis">
      <div class="kpi"><div class="kpi-label">Клиентов</div><div class="kpi-value">${formatNum(total)}</div><div class="kpi-foot">горячих: ${formatNum(hot)}</div></div>
      <div class="kpi"><div class="kpi-label">Объем в месяц</div><div class="kpi-value">${formatNum(liters)} л</div><div class="kpi-foot">по активному списку</div></div>
      <div class="kpi"><div class="kpi-label">Оборот</div><div class="kpi-value">${formatRub(spend)}</div><div class="kpi-foot">последний месяц</div></div>
      <div class="kpi"><div class="kpi-label">HitRate@1</div><div class="kpi-value">${Number(lastRun.hitRateAt1 ?? 0).toFixed(2)}</div><div class="kpi-foot">${escapeHtml(lastRun.model || "модель не запускалась")}</div></div>
    </div>

    <div class="card">
      <div class="card-head">
        <h2 class="card-title">Клиенты</h2>
        <span class="muted">${state.meta ? `${state.meta.suppliers} поставщиков в каталоге` : ""}</span>
      </div>
      <table class="table">
        <thead>
          <tr>
            <th>Клиент</th>
            <th>Сегмент</th>
            <th>Сигналы</th>
            <th>Потребление</th>
            <th>Динамика</th>
            <th>Текущий поставщик</th>
            <th>Приоритет</th>
          </tr>
        </thead>
        <tbody>${state.clients.map(clientRow).join("") || `<tr><td colspan="7" class="empty">Клиенты не найдены</td></tr>`}</tbody>
      </table>
    </div>
  `;

  const segment = document.getElementById("segment-filter");
  if (segment) {
    segment.value = state.segmentFilter;
    segment.addEventListener("change", async (event) => {
      state.segmentFilter = event.target.value;
      await loadClients();
      render();
    });
  }

  document.getElementById("export-clients").addEventListener("click", () => {
    const rows = [["Код", "Клиент", "ИНН", "Сегмент", "Литры", "Оборот", "Приоритет"]];
    state.clients.forEach((client) => rows.push([client.id, client.name, client.inn, client.segment, client.monthlyLiters, client.monthlySpend, client.priority]));
    downloadCsv("clients.csv", rows);
  });

  content.querySelectorAll("[data-open-client]").forEach((row) => {
    row.addEventListener("click", () => openClientSafe(row.dataset.openClient));
  });
}

function clientRow(client) {
  const supplier = supplierById(client.currentSupplier);
  return `
    <tr class="clickable" data-open-client="${escapeHtml(client.id)}">
      <td><b>${escapeHtml(client.name)}</b><div class="mono">${escapeHtml(client.id)} · ИНН ${escapeHtml(client.inn)}</div></td>
      <td>${tag(SEGMENTS[client.segment] || client.segment, "accent")}</td>
      <td>${signalTags(client)}</td>
      <td class="num"><b>${formatNum(client.monthlyLiters)}</b> л<div class="mono">${formatRub(client.monthlySpend)}</div></td>
      <td>${sparkline(client.history)}</td>
      <td>${supplier ? `<b>${escapeHtml(supplier.name)}</b><div class="mono">до ${escapeHtml(client.contractEnd || "не указано")}</div>` : `<span class="muted">не подключен</span>`}</td>
      <td class="num"><b>${Number(client.priority).toFixed(2)}</b></td>
    </tr>`;
}

function renderClientDetail() {
  const client = state.activeClient;
  const current = supplierById(client.currentSupplier);
  const decisions = state.decisions.filter((decision) => decision.client === client.id);
  const tabs = [
    ["reco", "Рекомендации", ""],
    ["profile", "Профиль и потребление", ""],
    ["tx", "Транзакции", state.transactions.length],
    ["history", "История решений", decisions.length]
  ];

  content.innerHTML = `
    <button class="btn btn-ghost" id="back">← К списку</button>
    <div class="detail-head">
      <div>
        <div class="mono">${escapeHtml(client.id)} · ИНН ${escapeHtml(client.inn)}</div>
        <h1 class="detail-name">${escapeHtml(client.name)}</h1>
        <div class="detail-meta">
          <span>Отрасль: <b>${escapeHtml(client.industry)}</b></span>
          <span>Сегмент: <b>${escapeHtml(SEGMENTS[client.segment] || client.segment)}</b></span>
          <span>Парк: <b>${formatNum(client.fleet)} ТС</b></span>
          <span>Менеджер: <b>${escapeHtml(client.manager)}</b></span>
          <span>Поставщик: <b>${escapeHtml(current ? current.name : "не подключен")}</b></span>
        </div>
      </div>
      <div>${signalTags(client)}</div>
    </div>

    <div class="tabs">
      ${tabs.map(([id, label, count]) => `<button class="tab ${state.activeTab === id ? "active" : ""}" data-tab="${id}">${label} ${count !== "" ? `<span class="tab-count">(${count})</span>` : ""}</button>`).join("")}
    </div>

    <div id="client-tab-body"></div>`;

  document.getElementById("back").addEventListener("click", async () => {
    state.activeClient = null;
    state.recommendations = [];
    state.transactions = [];
    await loadClients();
    render();
  });

  content.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      renderClientDetail();
    });
  });

  const body = document.getElementById("client-tab-body");
  if (state.activeTab === "profile") body.innerHTML = profileTab(client);
  else if (state.activeTab === "tx") body.innerHTML = txTab(client);
  else if (state.activeTab === "history") body.innerHTML = historyTab(decisions);
  else body.innerHTML = recoTab(client, current);

  body.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => openDecisionDialog(button.dataset.action, button.dataset.supplier));
  });
  const exportTx = document.getElementById("export-tx");
  if (exportTx) {
    exportTx.addEventListener("click", () => {
      const rows = [["Время", "АЗС", "Регион", "Топливо", "Литры", "Сумма", "ТС"]];
      state.transactions.forEach((tx) => rows.push([tx.ts, tx.station, tx.region, tx.fuel, tx.liters, tx.amount, tx.vehicle]));
      downloadCsv(`${client.id}_transactions.csv`, rows);
    });
  }
  const runClient = document.getElementById("run-client-recommendations");
  if (runClient) {
    runClient.addEventListener("click", runClientRecommendations);
  }
}

function recoTab(client, current) {
  const top = state.recommendations[0];
  const currentRec = current ? state.recommendations.find((rec) => rec.supplier.id === current.id) : null;
  const banner = current && top && current.id !== top.supplier.id ? `
    <div class="banner warn">
      <div>
        <div class="banner-title">Возможен переход на более подходящего поставщика</div>
        <div class="banner-body">Сейчас клиент обслуживается у <b>${escapeHtml(current.name)}</b> (${Math.round((currentRec?.score || 0) * 100)}/100). Модель предлагает <b>${escapeHtml(top.supplier.name)}</b> — ${Math.round(top.score * 100)}/100.</div>
      </div>
    </div>` : "";

  return `
    <div class="split">
      <div>
        ${banner}
        <div class="card">
          <div class="card-head">
            <h2 class="card-title">Рекомендации поставщиков</h2>
            <div class="page-actions">
              <span class="muted">Гибридная модель · ${escapeHtml(SEGMENTS[client.segment] || client.segment)}</span>
              <button id="run-client-recommendations" class="btn">Пересчитать клиента</button>
            </div>
          </div>
          ${state.recommendations.map(recommendationRow).join("")}
        </div>
      </div>
      <aside>
        <div class="side-card">
          <h2 class="card-title">Оценка #1: ${escapeHtml(top?.supplier.name || "-")}</h2>
          <div class="score" style="text-align:left;margin-top:10px">${Math.round((top?.score || 0) * 100)}<small>/100</small></div>
          <div class="subtitle">${escapeHtml(top?.supplier.notes || "")}</div>
        </div>
        <div class="side-card">
          <h2 class="card-title">Профиль потребления</h2>
          <div class="bars" style="margin-top:12px">${bars(client.fuelMix)}</div>
        </div>
      </aside>
    </div>`;
}

function recommendationRow(rec) {
  const supplier = rec.supplier;
  const isTop = rec.rank === 1;
  const score = Math.round(rec.score * 100);
  return `
    <div class="reco-row ${isTop ? "rank-1" : ""}">
      <div class="rank">#${rec.rank}</div>
      <div>
        <div class="reco-name">
          ${escapeHtml(supplier.name)}
          ${tag(supplier.type === "aggregator" ? "Агрегатор" : "ВИНК", supplier.type === "aggregator" ? "accent" : "info")}
          ${rec.isCurrent ? tag("Текущий", "good") : ""}
        </div>
        <div class="reco-meta">${escapeHtml(supplier.full)} · покрытие ${Math.round(supplier.coverage * 100)}% · ${escapeHtml(supplier.regions)}</div>
        <div class="factors">${rec.factors.map(factorHtml).join("")}</div>
      </div>
      <div class="num">${formatRub(rec.estimatedMonthlySaving)}<div class="mono">оценка выгоды</div></div>
      <div class="score">${score}<small>/100</small></div>
      <div style="display:flex;gap:7px;justify-content:flex-end">
        ${isTop ? `<button class="btn btn-primary" data-action="accept" data-supplier="${escapeHtml(supplier.id)}">Принять</button>` : ""}
        <button class="btn" data-action="adjust" data-supplier="${escapeHtml(supplier.id)}">Выбрать</button>
        ${isTop ? `<button class="btn btn-danger" data-action="reject" data-supplier="${escapeHtml(supplier.id)}">Отклонить</button>` : ""}
      </div>
    </div>`;
}

function factorHtml(factor) {
  const label = FACTORS[factor.key] || factor.label;
  return `<span class="factor">${escapeHtml(label)}<span class="meter"><span style="width:${Math.round(factor.value * 100)}%"></span></span><b>${Math.round(factor.value * 100)}</b></span>`;
}

function profileTab(client) {
  return `
    <div class="split">
      <div class="card">
        <div class="card-head"><h2 class="card-title">Реквизиты и контракт</h2></div>
        <div class="stat-list">
          ${stat("ИНН", client.inn, true)}
          ${stat("Идентификатор", client.id, true)}
          ${stat("Отрасль", client.industry)}
          ${stat("Сегмент", SEGMENTS[client.segment] || client.segment)}
          ${stat("Автопарк", `${formatNum(client.fleet)} ТС`)}
          ${stat("Основное топливо", client.mainFuel)}
          ${stat("Договор до", client.contractEnd || "не указано")}
          ${stat("Менеджер", client.manager)}
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2 class="card-title">Динамика потребления</h2></div>
        <div style="padding:16px">${consumptionChart(client.history)}</div>
        <table class="table">
          <thead><tr><th>Месяц</th><th style="text-align:right">Литры</th><th style="text-align:right">Оборот</th></tr></thead>
          <tbody>${client.history.map((item) => `<tr><td class="mono">${escapeHtml(item.m)}</td><td class="num" style="text-align:right">${formatNum(item.l)}</td><td class="num" style="text-align:right">${formatRub(item.s)}</td></tr>`).join("")}</tbody>
        </table>
      </div>
    </div>`;
}

function stat(key, value, mono = false) {
  return `<div class="stat"><div class="k">${escapeHtml(key)}</div><div class="v ${mono ? "mono" : ""}">${escapeHtml(value)}</div></div>`;
}

function consumptionChart(history) {
  const values = history.map((item) => item.l);
  const min = Math.min(...values) * 0.9;
  const max = Math.max(...values) * 1.05;
  const range = max - min || 1;
  const points = history.map((item, index) => {
    const x = 26 + index * (408 / Math.max(1, history.length - 1));
    const y = 132 - ((item.l - min) / range) * 104;
    return `${x},${y}`;
  }).join(" ");
  const area = `26,132 ${points} 434,132`;
  return `<svg class="chart" viewBox="0 0 460 150">
    <polygon points="${area}" fill="rgba(20,125,117,.12)"></polygon>
    <polyline points="${points}" fill="none" stroke="#147d75" stroke-width="3"></polyline>
    ${history.map((item, index) => `<text x="${26 + index * (408 / Math.max(1, history.length - 1))}" y="146" text-anchor="middle" font-size="11" fill="#9aa5b1">${escapeHtml(item.m.slice(5))}</text>`).join("")}
  </svg>`;
}

function txTab() {
  return `
    <div class="card">
      <div class="card-head">
        <h2 class="card-title">Последние транзакции · ${state.transactions.length}</h2>
        <button id="export-tx" class="btn">Экспорт CSV</button>
      </div>
      <table class="table">
        <thead><tr><th>Время</th><th>АЗС</th><th>Регион</th><th>Топливо</th><th style="text-align:right">Литры</th><th style="text-align:right">Сумма</th><th>ТС</th></tr></thead>
        <tbody>${state.transactions.map((tx) => `<tr><td class="mono">${escapeHtml(tx.ts)}</td><td>${escapeHtml(tx.station)}</td><td>${escapeHtml(tx.region)}</td><td>${escapeHtml(tx.fuel)}</td><td class="num" style="text-align:right">${formatNum(tx.liters)}</td><td class="num" style="text-align:right">${formatRub(tx.amount)}</td><td class="mono">${escapeHtml(tx.vehicle)}</td></tr>`).join("")}</tbody>
      </table>
    </div>`;
}

function historyTab(decisions) {
  if (!decisions.length) return `<div class="card"><div class="empty">По клиенту пока нет зафиксированных решений.</div></div>`;
  return `<div class="card">
    <table class="table">
      <thead><tr><th>Время</th><th>Менеджер</th><th>Рекомендация</th><th>Решение</th><th>Комментарий</th></tr></thead>
      <tbody>${decisions.map((decision) => {
        const rec = supplierById(decision.rec);
        const action = ACTIONS[decision.action] || [decision.action, ""];
        return `<tr><td class="mono">${escapeHtml(decision.ts)}</td><td>${escapeHtml(decision.manager)}</td><td>${escapeHtml(rec ? rec.name : decision.rec)}</td><td>${tag(action[0], action[1])}</td><td>${escapeHtml(decision.note || "-")}</td></tr>`;
      }).join("")}</tbody>
    </table>
  </div>`;
}

function bars(source) {
  return Object.entries(source || {}).map(([key, value]) => `
    <div class="bar-row">
      <span>${escapeHtml(key)}</span>
      <span class="bar"><span style="width:${Math.round(value * 100)}%"></span></span>
      <b>${Math.round(value * 100)}%</b>
    </div>`).join("");
}

function renderDecisions() {
  const accepted = state.decisions.filter((item) => item.action === "accept").length;
  const adjusted = state.decisions.filter((item) => item.action === "adjust").length;
  const rejected = state.decisions.filter((item) => item.action === "reject").length;
  content.innerHTML = `
    <div class="page-head">
      <div><h1 class="title">Журнал решений</h1><div class="subtitle">Обратная связь менеджеров для контроля качества рекомендаций</div></div>
    </div>
    <div class="grid kpis">
      <div class="kpi"><div class="kpi-label">Принято</div><div class="kpi-value">${accepted}</div></div>
      <div class="kpi"><div class="kpi-label">Скорректировано</div><div class="kpi-value">${adjusted}</div></div>
      <div class="kpi"><div class="kpi-label">Отклонено</div><div class="kpi-value">${rejected}</div></div>
      <div class="kpi"><div class="kpi-label">Всего</div><div class="kpi-value">${state.decisions.length}</div></div>
    </div>
    <div class="card">
      <table class="table">
        <thead><tr><th>Время</th><th>Клиент</th><th>Менеджер</th><th>Рекомендация</th><th>Решение</th><th>Выбран</th><th>Комментарий</th></tr></thead>
        <tbody>${state.decisions.map(decisionRow).join("")}</tbody>
      </table>
    </div>`;
  content.querySelectorAll("[data-open-client]").forEach((row) => {
    row.addEventListener("click", () => openClientSafe(row.dataset.openClient));
  });
}

function decisionRow(decision) {
  const client = state.clients.find((item) => item.id === decision.client) || {};
  const rec = supplierById(decision.rec);
  const picked = supplierById(decision.picked);
  const action = ACTIONS[decision.action] || [decision.action, ""];
  const canOpen = Boolean(decision.client);
  return `<tr class="${canOpen ? "clickable" : ""}" ${canOpen ? `data-open-client="${escapeHtml(decision.client)}"` : ""}>
    <td class="mono">${escapeHtml(decision.ts)}</td>
    <td><b>${escapeHtml(client.name || decision.client)}</b><div class="mono">${escapeHtml(decision.client)}</div></td>
    <td>${escapeHtml(decision.manager)}</td>
    <td>${escapeHtml(rec ? rec.name : decision.rec)}</td>
    <td>${tag(action[0], action[1])}</td>
    <td>${escapeHtml(picked ? picked.name : (decision.action === "accept" && rec ? rec.name : "-"))}</td>
    <td>${escapeHtml(decision.note || "-")}</td>
  </tr>`;
}

function renderDataScreen() {
  const stale = state.dataSources.filter((source) => source.status === "stale");
  const lastRun = state.modelRuns[0] || {};
  const importState = state.meta?.importState;
  content.innerHTML = `
    <div class="page-head">
      <div><h1 class="title">Состояние данных и рекомендаций</h1><div class="subtitle">Контроль источников данных и запуск перерасчета</div></div>
      <div class="page-actions">
        <button id="import-excel-btn" class="btn">Импорт Excel-файлов</button>
        <button id="reload-all" class="btn">Обновить все</button>
        <button id="admin-recalculate" class="btn btn-primary">Запустить перерасчет</button>
      </div>
    </div>
    <div id="upload-panel" class="card hidden" style="margin-bottom: 24px; padding: 20px;">
      <h3 style="margin-top: 0;">Загрузка данных из Excel</h3>
      <div style="display: flex; gap: 20px; align-items: flex-end; margin-top: 16px;">
        <div>
          <label style="display: block; margin-bottom: 8px; font-weight: 500;">Файл клиентов (df_clients.xlsx)</label>
          <input type="file" id="clients-file" accept=".xlsx" class="btn" style="padding: 6px;">
        </div>
        <div>
          <label style="display: block; margin-bottom: 8px; font-weight: 500;">Файл транзакций (df_tx.xlsx)</label>
          <input type="file" id="tx-file" accept=".xlsx" class="btn" style="padding: 6px;">
        </div>
        <button id="submit-upload" class="btn btn-primary">Загрузить и импортировать</button>
        <button id="cancel-upload" class="btn">Отмена</button>
      </div>
    </div>
    ${stale.length ? `<div class="banner warn"><div><div class="banner-title">Есть устаревшие источники данных</div><div class="banner-body">${stale.length} источник(ов) требуют обновления перед пересчетом.</div></div></div>` : ""}
    <div class="grid kpis">
      <div class="kpi"><div class="kpi-label">Последний перерасчет</div><div class="kpi-value" style="font-size:17px">${escapeHtml(lastRun.ts || "-")}</div><div class="kpi-foot">расписание: 06:00 ежедневно</div></div>
      <div class="kpi"><div class="kpi-label">Клиентов всего</div><div class="kpi-value">${formatNum(state.meta?.clients)}</div></div>
      <div class="kpi"><div class="kpi-label">Импортировано транзакций</div><div class="kpi-value">${formatNum(importState?.transactions || 0)}</div><div class="kpi-foot">${escapeHtml(importState?.ts || "импорт еще не выполнялся")}</div></div>
      <div class="kpi"><div class="kpi-label">Источников</div><div class="kpi-value">${state.dataSources.length}</div></div>
    </div>
    <div class="card">
      <div class="card-head"><h2 class="card-title">Источники данных</h2><span class="muted">Всего: ${state.dataSources.length}</span></div>
      <table class="table">
        <thead><tr><th>Источник</th><th>Статус</th><th>Последнее обновление</th><th style="text-align:right">Записей</th><th>Прирост</th><th></th></tr></thead>
        <tbody>${state.dataSources.map(sourceRow).join("")}</tbody>
      </table>
    </div>`;

  document.getElementById("reload-all").addEventListener("click", () => reloadSource("all"));
  document.getElementById("import-excel-btn").addEventListener("click", () => {
    document.getElementById("upload-panel").classList.remove("hidden");
  });
  document.getElementById("cancel-upload").addEventListener("click", () => {
    document.getElementById("upload-panel").classList.add("hidden");
  });
  document.getElementById("submit-upload").addEventListener("click", importExcelData);
  document.getElementById("admin-recalculate").addEventListener("click", recalculate);
  content.querySelectorAll("[data-reload-source]").forEach((button) => button.addEventListener("click", () => reloadSource(button.dataset.reloadSource)));
}

function sourceRow(source) {
  const tone = source.status === "ok" ? "good" : source.status === "stale" ? "warn" : "danger";
  const label = source.status === "ok" ? "Актуальные" : source.status === "stale" ? "Устарели" : "Ошибка";
  return `<tr><td><b>${escapeHtml(source.name)}</b><div class="mono">${escapeHtml(source.id)}</div></td><td>${tag(label, tone)}</td><td class="mono">${escapeHtml(source.last)}</td><td class="num" style="text-align:right">${formatNum(source.rows)}</td><td class="num">${escapeHtml(source.delta)}</td><td style="text-align:right"><button class="btn" data-reload-source="${escapeHtml(source.id)}">Обновить</button></td></tr>`;
}

async function reloadSource(id) {
  state.dataSources = await api(`/api/data-sources/${encodeURIComponent(id)}/reload`, { method: "POST", body: "{}" });
  showToast("Источник обновлен");
  await loadBaseData();
  renderDataScreen();
}

async function recalculate() {
  const button = document.getElementById("admin-recalculate") || document.getElementById("recalculate");
  if (button) {
    button.disabled = true;
    button.textContent = "Идет перерасчет...";
  }
  await api("/api/recalculate", { method: "POST", body: "{}" });
  await refreshCurrentScreen();
  showToast("Пересчет завершен");
}

async function importExcelData() {
  const clientsFile = document.getElementById("clients-file").files[0];
  const txFile = document.getElementById("tx-file").files[0];
  
  if (!clientsFile || !txFile) {
    showToast("Пожалуйста, выберите оба файла (df_clients.xlsx и df_tx.xlsx)");
    return;
  }

  const button = document.getElementById("submit-upload");
  if (button) {
    button.disabled = true;
    button.textContent = "Импорт идет...";
  }
  
  try {
    const formData = new FormData();
    formData.append("clients_file", clientsFile);
    formData.append("tx_file", txFile);
    
    const response = await fetch("/api/import-excel", {
      method: "POST",
      body: formData
    });
    
    if (!response.ok) {
      const body = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(body.error || response.statusText);
    }
    
    const result = await response.json();
    
    await loadBaseData();
    await loadClients();
    showToast(`Импортировано клиентов: ${formatNum(result.clients_imported)}`);
    document.getElementById("upload-panel").classList.add("hidden");
    renderDataScreen();
  } catch (error) {
    showToast(`Ошибка импорта: ${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Загрузить и импортировать";
    }
  }
}

async function runClientRecommendations() {
  if (!state.activeClient) return;
  const button = document.getElementById("run-client-recommendations");
  if (button) {
    button.disabled = true;
    button.textContent = "Пересчет...";
  }
  try {
    const result = await api(`/api/clients/${encodeURIComponent(state.activeClient.id)}/recommendations/run`, {
      method: "POST",
      body: "{}"
    });
    state.recommendations = result.recommendations;
    state.modelRuns = await api("/api/model-runs");
    showToast("Рекомендации клиента пересчитаны");
    renderClientDetail();
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Пересчитать клиента";
    }
  }
}

function renderModelsScreen() {
  const accepted = state.decisions.filter((item) => item.action === "accept").length;
  const acceptance = state.decisions.length ? Math.round((accepted / state.decisions.length) * 100) : 0;
  content.innerHTML = `
    <div class="page-head">
      <div><h1 class="title">Модели и качество рекомендаций</h1><div class="subtitle">Сравнение моделей, метрики и обратная связь</div></div>
      <div class="page-actions"><button class="btn">Журнал обучения</button><button class="btn btn-primary">A/B-сравнение</button></div>
    </div>
    <div class="grid kpis">
      <div class="kpi"><div class="kpi-label">Доля принятых</div><div class="kpi-value">${acceptance}%</div><div class="kpi-foot">по журналу решений</div></div>
      <div class="kpi"><div class="kpi-label">Активных моделей</div><div class="kpi-value">${state.modelCatalog.filter((m) => m.status === "production").length}</div></div>
      <div class="kpi"><div class="kpi-label">Лучший HR@1</div><div class="kpi-value">${Math.max(...state.modelCatalog.map((m) => m.hr1)).toFixed(3)}</div></div>
      <div class="kpi"><div class="kpi-label">Запусков</div><div class="kpi-value">${state.modelRuns.length}</div></div>
    </div>
    <div class="card" style="margin-bottom:16px">
      <div class="card-head"><h2 class="card-title">Активные модели</h2></div>
      <table class="table">
        <thead><tr><th>Модель</th><th>Назначение</th><th>Статус</th><th style="text-align:right">HR@1</th><th style="text-align:right">NDCG@3</th><th style="text-align:right">MAP@3</th></tr></thead>
        <tbody>${state.modelCatalog.map(modelRow).join("")}</tbody>
      </table>
    </div>
    <div class="split">
      <div class="card"><div class="card-head"><h2 class="card-title">Качество по сегментам</h2></div><div style="padding:16px">${segmentQuality()}</div></div>
      <div class="card"><div class="card-head"><h2 class="card-title">Последние запуски</h2></div><table class="table"><tbody>${state.modelRuns.slice(0, 5).map(runRow).join("")}</tbody></table></div>
    </div>`;
}

function modelRow(model) {
  const tone = model.status === "production" ? "good" : "info";
  return `<tr><td><b>${escapeHtml(model.name)}</b><div class="mono">${escapeHtml(model.id)}</div></td><td>${escapeHtml(model.segment)}</td><td>${tag(model.status, tone)}</td><td class="num" style="text-align:right">${Number(model.hr1).toFixed(3)}</td><td class="num" style="text-align:right">${Number(model.ndcg3).toFixed(3)}</td><td class="num" style="text-align:right">${Number(model.map3).toFixed(3)}</td></tr>`;
}

function runRow(run) {
  return `<tr><td class="mono">${escapeHtml(run.ts)}</td><td><b>${escapeHtml(run.model)}</b></td><td>${tag(run.status, "good")}</td><td class="num">${Number(run.hitRateAt1).toFixed(2)}</td><td class="num">${Number(run.ndcgAt3).toFixed(2)}</td></tr>`;
}

function segmentQuality() {
  const rows = [
    ["Инертные", "ALS", 0.997, 88.6],
    ["Потенциальные", "EASE", 0.926, 7.4],
    ["Изменчивые", "EASE", 0.881, 2.6],
    ["Холодный старт", "EASE", 0.993, 1.4]
  ];
  return rows.map(([name, model, score, share]) => `
    <div style="margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;margin-bottom:5px"><b>${name}</b><span class="muted">${model} · ${share}% портфеля</span></div>
      <div class="bar-row"><span>NDCG@3</span><span class="bar"><span style="width:${score * 100}%"></span></span><b>${score.toFixed(2)}</b></div>
    </div>`).join("");
}

function renderUsersScreen() {
  const active = state.users.filter((user) => user.status === "active").length;
  const admins = state.users.filter((user) => user.role === "admin").length;
  content.innerHTML = `
    <div class="page-head">
      <div><h1 class="title">Пользователи системы</h1><div class="subtitle">Управление учетными записями и ролями</div></div>
      <button id="add-user" class="btn btn-primary">Добавить пользователя</button>
    </div>
    <div class="grid kpis">
      <div class="kpi"><div class="kpi-label">Всего</div><div class="kpi-value">${state.users.length}</div></div>
      <div class="kpi"><div class="kpi-label">Активные</div><div class="kpi-value">${active}</div></div>
      <div class="kpi"><div class="kpi-label">Администраторы</div><div class="kpi-value">${admins}</div></div>
      <div class="kpi"><div class="kpi-label">Менеджеры</div><div class="kpi-value">${state.users.length - admins}</div></div>
    </div>
    <div class="card">
      <table class="table">
        <thead><tr><th>Пользователь</th><th>Логин</th><th>Роль</th><th>Статус</th><th>Последний вход</th><th></th></tr></thead>
        <tbody>${state.users.map(userRow).join("")}</tbody>
      </table>
    </div>`;
  document.getElementById("add-user").addEventListener("click", openUserDialog);
  content.querySelectorAll("[data-toggle-user]").forEach((button) => button.addEventListener("click", () => toggleUser(button.dataset.toggleUser)));
}

function userRow(user) {
  const initials = user.name.split(" ").map((part) => part[0]).join("");
  return `<tr><td><div style="display:flex;align-items:center;gap:10px"><div class="avatar">${escapeHtml(initials)}</div><div><b>${escapeHtml(user.name)}</b><div class="mono">${escapeHtml(user.id)}</div></div></div></td><td class="mono">${escapeHtml(user.login)}</td><td>${tag(user.role === "admin" ? "Администратор" : "Менеджер", user.role === "admin" ? "accent" : "info")}</td><td>${tag(user.status === "active" ? "Активен" : "Заблокирован", user.status === "active" ? "good" : "danger")}</td><td class="mono">${escapeHtml(user.last)}</td><td style="text-align:right"><button class="btn ${user.status === "active" ? "btn-danger" : ""}" data-toggle-user="${escapeHtml(user.id)}">${user.status === "active" ? "Заблокировать" : "Разблокировать"}</button></td></tr>`;
}

async function toggleUser(id) {
  await api(`/api/users/${encodeURIComponent(id)}/toggle`, { method: "POST", body: "{}" });
  state.users = await api("/api/users");
  showToast("Статус пользователя обновлен");
  renderUsersScreen();
}

function openUserDialog() {
  modal.innerHTML = `
    <div class="dialog">
      <h3>Добавить пользователя</h3>
      <form class="form" id="user-form">
        <label>Имя <input name="name" required placeholder="Е. Соколова"></label>
        <label>Логин <input name="login" required placeholder="sokolova"></label>
        <label>Роль <select name="role"><option value="manager">Менеджер</option><option value="admin">Администратор</option></select></label>
        <div class="dialog-actions"><button type="button" class="btn" id="modal-close">Отмена</button><button class="btn btn-primary">Создать</button></div>
      </form>
    </div>`;
  modal.classList.remove("hidden");
  document.getElementById("modal-close").addEventListener("click", closeModal);
  document.getElementById("user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await api("/api/users", {
      method: "POST",
      body: JSON.stringify({ name: form.get("name"), login: form.get("login"), role: form.get("role") })
    });
    state.users = await api("/api/users");
    closeModal();
    showToast("Пользователь создан");
    renderUsersScreen();
  });
}

function openDecisionDialog(action, supplierId) {
  const rec = supplierById(supplierId);
  const actionText = ACTIONS[action]?.[0] || action;
  const select = action === "adjust" ? `
    <label>Фактически выбранный поставщик
      <div class="choice-grid">
        ${state.recommendations.slice(0, 6).map((item) => `<button type="button" class="choice ${item.supplier.id === supplierId ? "selected" : ""}" data-pick="${escapeHtml(item.supplier.id)}"><b>${escapeHtml(item.supplier.name)}</b><span>оценка ${Math.round(item.score * 100)}/100</span></button>`).join("")}
      </div>
    </label>` : "";
  modal.innerHTML = `
    <div class="dialog">
      <h3>${escapeHtml(actionText)}: ${escapeHtml(rec ? rec.name : supplierId)}</h3>
      <form class="form" id="decision-form">
        ${select}
        <input type="hidden" name="picked" value="${escapeHtml(supplierId)}">
        <label>Комментарий
          <textarea name="note" rows="4" placeholder="Причина решения"></textarea>
        </label>
        <div class="dialog-actions"><button type="button" class="btn" id="modal-close">Отмена</button><button type="submit" class="btn btn-primary">Сохранить</button></div>
      </form>
    </div>`;
  modal.classList.remove("hidden");
  document.getElementById("modal-close").addEventListener("click", closeModal);
  modal.querySelectorAll("[data-pick]").forEach((button) => {
    button.addEventListener("click", () => {
      modal.querySelectorAll(".choice").forEach((item) => item.classList.remove("selected"));
      button.classList.add("selected");
      modal.querySelector("[name='picked']").value = button.dataset.pick;
    });
  });
  document.getElementById("decision-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = {
      client: state.activeClient.id,
      manager: state.activeClient.manager,
      rec: state.recommendations[0].supplier.id,
      action,
      picked: action === "adjust" ? form.get("picked") : null,
      note: form.get("note") || ""
    };
    await api("/api/decisions", { method: "POST", body: JSON.stringify(payload) });
    state.decisions = await api("/api/decisions");
    state.meta = await api("/api/meta");
    closeModal();
    showToast("Решение сохранено");
    renderClientDetail();
  });
}

function closeModal() {
  modal.classList.add("hidden");
  modal.innerHTML = "";
}

function wireEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", async () => {
      state.screen = button.dataset.screen;
      state.activeClient = null;
      state.recommendations = [];
      state.transactions = [];
      if (["priority", "clients", "decisions"].includes(state.screen)) await loadClients();
      render();
    });
  });

  document.querySelectorAll("[data-role]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.role = button.dataset.role;
      state.screen = state.role === "admin" ? "data" : "priority";
      state.activeClient = null;
      state.recommendations = [];
      state.transactions = [];
      if (state.role === "manager") await loadClients();
      render();
    });
  });

  document.getElementById("search").addEventListener("input", async (event) => {
    state.query = event.target.value.trim();
    state.role = "manager";
    state.activeClient = null;
    state.screen = "clients";
    await loadClients();
    render();
  });

  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });
}

async function boot() {
  wireEvents();
  try {
    await loadBaseData();
    await loadClients();
    render();
  } catch (error) {
    document.getElementById("service-status").textContent = "ошибка";
    content.innerHTML = `<div class="empty">Не удалось подключиться к API: ${escapeHtml(error.message)}</div>`;
  }
}

boot();
