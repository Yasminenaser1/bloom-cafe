const money = cents =>
  "$" + (cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const hourLabel = h => `${h % 12 || 12}${h < 12 ? "am" : "pm"}`;

// Keep chart objects so switching 7/30/90 replaces them instead of stacking new ones
const charts = {};

function drawChart(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}

function chartOptions(formatY) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false } },
      y: { beginAtZero: true, ticks: { callback: formatY } },
    },
  };
}

function showKpi(key, kpi, format) {
  document.getElementById("kpi-" + key).textContent = format(kpi.value);
  const el = document.getElementById("chg-" + key);
  if (kpi.change_pct === null) {
    el.textContent = "No earlier data to compare";
    el.className = "change";
    return;
  }
  const up = kpi.change_pct >= 0;
  el.textContent = `${up ? "▲" : "▼"} ${Math.abs(kpi.change_pct)}% vs previous period`;
  el.className = "change " + (up ? "up" : "down");
}

async function load(days) {
  const res = await fetch(`/api/insights?days=${days}`);
  if (!res.ok) throw new Error("insights request failed");
  const d = await res.json();

  document.getElementById("period").textContent = `${d.period.start} to ${d.period.end}`;

  showKpi("revenue_cents", d.kpis.revenue_cents, money);
  showKpi("orders", d.kpis.orders, n => n.toLocaleString("en-US"));
  showKpi("avg_ticket_cents", d.kpis.avg_ticket_cents, money);

  drawChart("daily-chart", {
    type: "line",
    data: {
      labels: d.daily.map(x => x.date.slice(5)),          // "09-23"
      datasets: [{
        data: d.daily.map(x => x.revenue_cents / 100),
        borderColor: "#3b2f2a",
        backgroundColor: "rgba(201, 162, 126, 0.25)",
        fill: true,
        tension: 0.3,
        pointRadius: 0,
      }],
    },
    options: chartOptions(v => "$" + v),
  });

  drawChart("hour-chart", {
    type: "bar",
    data: {
      labels: d.by_hour.map(x => hourLabel(x.hour)),
      datasets: [{
        data: d.by_hour.map(x => x.orders),
        backgroundColor: "#c9a27e",
        borderRadius: 6,
      }],
    },
    options: chartOptions(v => v),
  });

  const tbody = document.querySelector("#top tbody");
  tbody.innerHTML = "";
  for (const t of d.top_items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td></td><td class="num"></td><td class="num"></td>`;
    tr.children[0].textContent = t.item;
    tr.children[1].textContent = t.sold.toLocaleString("en-US");
    tr.children[2].textContent = money(t.revenue_cents);
    tbody.append(tr);
  }

  const pairs = document.getElementById("pairs");
  pairs.innerHTML = "";
  for (const p of d.bought_together) {
    const li = document.createElement("li");
    li.textContent = `${p.items[0]} + ${p.items[1]} (${p.orders} orders)`;
    pairs.append(li);
  }
}

function showError() {
  document.getElementById("period").textContent = "Couldn't load insights.";
}

for (const btn of document.querySelectorAll(".range button")) {
  btn.addEventListener("click", () => {
    document.querySelector(".range .active").classList.remove("active");
    btn.classList.add("active");
    load(Number(btn.dataset.days)).catch(showError);
  });
}

load(30).catch(showError);

// --- Alerts (anomaly detector) ---
const shortDate = iso =>
  new Date(iso + "T00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });

async function loadAlerts() {
  const list = document.getElementById("alerts");
  const res = await fetch("/api/anomalies");
  if (!res.ok) throw new Error("anomalies request failed");
  const events = await res.json();

  list.innerHTML = "";
  if (events.length === 0) {
    list.innerHTML = `<li class="muted">No unusual activity in the last 60 days.</li>`;
    return;
  }

  for (const e of events) {
    const li = document.createElement("li");
    li.className = "alert " + (e.note ? "expected" : e.direction);
    li.innerHTML = `<span class="icon"></span><div><strong></strong><p></p></div>`;

    const icon = e.note ? "🍂" : e.direction === "drop" ? "▼" : "▲";
    const what = e.direction === "drop" ? "unusually low" : "unusually high";
    const unit = e.metric === "All orders" ? "orders" : "sold";
    const span = e.days === 1 ? shortDate(e.start) : `${shortDate(e.start)} – ${shortDate(e.end)}`;

    li.querySelector(".icon").textContent = icon;
    li.querySelector("strong").textContent = `${e.metric}: ${what}`;
    li.querySelector("p").textContent =
      `${span} · ${e.actual} ${unit} vs ~${e.expected} expected` + (e.note ? ` · ${e.note}` : "");
    list.append(li);
  }
}

loadAlerts().catch(() => {
  document.getElementById("alerts").innerHTML = `<li class="muted">Couldn't load alerts.</li>`;
});

// --- Morning report + AI insights (published daily by GitHub Actions) ---
const whenGenerated = iso =>
  new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

const writtenBy = source =>
  source && source.startsWith("model") ? "AI-written title" : "template title";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function loadReport() {
  const box = document.getElementById("report");
  const data = await (await fetch("/api/report/latest")).json();
  box.innerHTML = "";
  if (!data.available) {
    box.append(el("p", "muted", "No report published yet."));
    return;
  }
  const f = data.facts;
  document.getElementById("report-day").textContent = f.date;
  box.append(el("p", "report-headline", data.headline));

  const list = el("ul", "report-facts");
  for (const line of [
    `Revenue: ${f.revenue} from ${f.orders} (average ticket ${f.avg_ticket})`,
    `Top sellers: ${f.top_sellers}`,
    `Busiest hour: ${f.busiest_hour}`,
    `Alerts: ${f.alerts}`,
  ]) list.append(el("li", null, line));
  box.append(list);

  const headlineBy = data.source.startsWith("model") ? "AI-written headline" : "template headline";
  box.append(el("p", "meta", `Generated ${whenGenerated(data.generated_at)} · ${headlineBy}`));
}

async function loadInsights() {
  const box = document.getElementById("ai-insights");
  const data = await (await fetch("/api/ai-insights")).json();
  box.innerHTML = "";
  if (!data.available || !data.insights.length) {
    box.append(el("p", "muted", "No insights published yet."));
    return;
  }
  document.getElementById("insights-day").textContent = `as of ${data.as_of}`;

  for (const ins of data.insights) {
    const card = el("div", "insight");
    const head = el("div", "insight-head");
    head.append(el("h3", null, ins.title), el("span", "badge", writtenBy(ins.title_source)));
    card.append(head, el("p", null, ins.recommendation));

    const details = el("details");
    details.append(el("summary", null, "Show the numbers"), el("p", "muted", ins.summary));
    card.append(details);
    box.append(card);
  }
  box.append(el("p", "meta",
    `Generated ${whenGenerated(data.generated_at)} with ${data.model} · recommendations are chosen by code from the data`));
}

loadReport().catch(() => {
  document.getElementById("report").innerHTML = "<p class='muted'>Couldn't load the report.</p>";
});
loadInsights().catch(() => {
  document.getElementById("ai-insights").innerHTML = "<p class='muted'>Couldn't load insights.</p>";
});
