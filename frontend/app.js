const API_BASE = "http://localhost:8000/api/v1";
const POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 минут
const SLOTS = ["left", "right"];

const charts = { left: null, right: null };

function adjustTimezoneOffset(dateString) {
  const date = new Date(dateString);
  const OFFSET_MS = 3 * 60 * 60 * 1000;
  return new Date(date.getTime() + OFFSET_MS);
}

function slotRoot(slot, kind) {
  // kind: "header" | "card" | "chart"
  const selector = {
    header: `.header-slot[data-slot="${slot}"]`,
    card: `.slot-card[data-slot="${slot}"]`,
    chart: `.chart-card[data-slot="${slot}"]`,
  }[kind];
  return document.querySelector(selector);
}

function el(slot, kind, role) {
  return slotRoot(slot, kind).querySelector(`[data-role="${role}"]`);
}

async function fetchConfig(slot) {
  try {
    const r = await fetch(`${API_BASE}/config?slot=${slot}`);
    return r.ok ? await r.json() : null;
  } catch (err) {
    console.error(`fetchConfig(${slot}) failed:`, err);
    return null;
  }
}

async function fetchCurrent(slot) {
  try {
    const r = await fetch(`${API_BASE}/battery/current?slot=${slot}`);
    return r.ok ? await r.json() : null;
  } catch (err) {
    console.error(`fetchCurrent(${slot}) failed:`, err);
    return null;
  }
}

async function fetchHistory(slot) {
  try {
    const r = await fetch(`${API_BASE}/battery/history?slot=${slot}&hours=24`);
    return r.ok ? await r.json() : null;
  } catch (err) {
    console.error(`fetchHistory(${slot}) failed:`, err);
    return null;
  }
}

function renderUsername(slot, config) {
  const loginForm = el(slot, "header", "login-form");
  const userInfo = el(slot, "header", "user-info");
  const usernameDisplay = el(slot, "header", "username-display");
  const getNowBtn = el(slot, "card", "get-now-btn");

  const isLoggedIn = config && config.username && config.username !== "Not logged in";

  loginForm.hidden = isLoggedIn;
  userInfo.hidden = !isLoggedIn;

  if (isLoggedIn) {
    usernameDisplay.textContent = config.username;
    getNowBtn.disabled = false;
    getNowBtn.title = "";
  } else {
    getNowBtn.disabled = true;
    getNowBtn.title = "Please login first";
  }
}

function zoneFor(bpm) {
  if (bpm == null) return "zone-unknown";
  if (bpm < 50 || bpm > 130) return "zone-red";
  if (bpm > 90) return "zone-yellow";
  return "zone-green";
}

function colorFor(bpm) {
  return {
    "zone-green": "#16a34a",
    "zone-yellow": "#eab308",
    "zone-red": "#dc2626",
    "zone-unknown": "#6b7280",
  }[zoneFor(bpm)];
}

function bbZoneFor(val) {
  if (val == null) return "bb-unknown";
  if (val >= 25) return "bb-high";
  if (val >= 20) return "bb-medium";
  return "bb-low";
}

function bbColorFor(val) {
  return {
    "bb-high":    "#16a34a",
    "bb-medium":  "#16a34a",
    "bb-low":     "#dc2626",
    "bb-unknown": "#6b7280",
  }[bbZoneFor(val)];
}

function renderCurrent(slot, data) {
  const hrContainer = el(slot, "card", "current-status");
  const bbContainer = el(slot, "card", "battery-status");
  const detailsContainer = el(slot, "card", "current-details");
  const profileNameContainer = el(slot, "card", "profile-name");

  if (!data) {
    profileNameContainer.textContent = "Profile: —";
    hrContainer.innerHTML = `<div class="error">Нет данных</div>`;
    bbContainer.innerHTML = `<div class="error">—</div>`;
    detailsContainer.innerHTML = "";
    return;
  }

  const ts = data.timestamp ? adjustTimezoneOffset(data.timestamp).toLocaleString() : "—";
  profileNameContainer.textContent = `Profile: ${data.profile_name || "—"}`;

  const hrZone = zoneFor(data.level);
  hrContainer.innerHTML = `
    <div class="metric ${hrZone}">
      <span class="value">${data.level}</span>
      <span class="unit">bpm</span>
    </div>
  `;

  const bbVal = data.battery_level;
  const bbZone = bbZoneFor(bbVal);
  bbContainer.innerHTML = bbVal != null
    ? `<div class="metric ${bbZone}">
         <span class="value">${bbVal}</span>
         <span class="unit">%</span>
       </div>`
    : `<div class="metric bb-unknown"><span class="value">—</span></div>`;

  detailsContainer.innerHTML = `
    <div class="details">
      <div>Обновлено: ${ts}</div>
      <div>HR статус: ${data.status || "—"}</div>
      <div class="${data.is_stale ? "stale" : "fresh"}">
        ${data.is_stale ? "⚠ Данные устарели" : "✓ Данные свежие"}
      </div>
    </div>
  `;
}

function renderHistory(slot, data) {
  const canvas = el(slot, "chart", "historyChart");
  const ctx = canvas.getContext("2d");

  if (!data || !data.data || data.data.length === 0) {
    if (charts[slot]) {
      charts[slot].destroy();
      charts[slot] = null;
    }
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.font = "16px sans-serif";
    ctx.fillStyle = "#6b7280";
    ctx.fillText("Нет данных", 20, 30);
    return;
  }

  const labels = data.data.map((item) => adjustTimezoneOffset(item.time).toLocaleTimeString());
  const hrValues = data.data.map((item) => item.level);
  const bbValues = data.data.map((item) => item.battery_level ?? null);
  const hrPointColors = hrValues.map(colorFor);
  const bbPointColors = bbValues.map(bbColorFor);

  if (charts[slot]) {
    charts[slot].data.labels = labels;
    charts[slot].data.datasets[0].data = hrValues;
    charts[slot].data.datasets[0].pointBackgroundColor = hrPointColors;
    charts[slot].data.datasets[1].data = bbValues;
    charts[slot].data.datasets[1].pointBackgroundColor = bbPointColors;
    charts[slot].update();
    return;
  }

  charts[slot] = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Heart Rate (BPM)",
          data: hrValues,
          borderColor: "#2563eb",
          backgroundColor: "rgba(37, 99, 235, 0.08)",
          pointBackgroundColor: hrPointColors,
          pointRadius: 2,
          fill: false,
          tension: 0.3,
          yAxisID: "yHR",
        },
        {
          label: "Body Battery (%)",
          data: bbValues,
          borderColor: "#16a34a",
          backgroundColor: "rgba(22, 163, 74, 0.08)",
          pointBackgroundColor: bbPointColors,
          pointRadius: 3,
          fill: true,
          tension: 0.4,
          spanGaps: true,
          yAxisID: "yBB",
        },
      ],
    },
    options: {
      responsive: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        yHR: {
          type: "linear",
          position: "left",
          min: 30,
          max: 200,
          title: { display: true, text: "BPM" },
          grid: { color: "rgba(37,99,235,0.1)" },
        },
        yBB: {
          type: "linear",
          position: "right",
          min: 0,
          max: 100,
          title: { display: true, text: "Battery %" },
          grid: { drawOnChartArea: false },
        },
        x: { ticks: { maxTicksLimit: 12 } },
      },
      plugins: { legend: { display: true } },
    },
  });
}

async function login(slot) {
  const username = el(slot, "header", "username").value;
  const password = el(slot, "header", "password").value;
  if (!username || !password) {
    alert("Please enter username and password");
    return;
  }

  try {
    const r = await fetch(`${API_BASE}/login?slot=${slot}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const result = await r.json();
    if (result.success) {
      el(slot, "header", "username").value = "";
      el(slot, "header", "password").value = "";
      loadSlot(slot);
    } else {
      alert("Login failed: " + result.message);
    }
  } catch (err) {
    console.error("Login error:", err);
    alert("Login error");
  }
}

async function logout(slot) {
  try {
    const r = await fetch(`${API_BASE}/logout?slot=${slot}`, { method: "POST" });
    const result = await r.json();
    if (result.success) {
      loadSlot(slot);
    } else {
      alert("Logout failed: " + result.message);
    }
  } catch (err) {
    console.error("Logout error:", err);
    alert("Logout error");
  }
}

async function getNow(slot) {
  const btn = el(slot, "card", "get-now-btn");

  const config = await fetchConfig(slot);
  if (!config || config.username === "Not logged in") {
    alert("Please login first before refreshing data");
    return;
  }

  const originalText = btn.textContent;
  btn.textContent = "Updating...";
  btn.disabled = true;

  try {
    const r = await fetch(`${API_BASE}/refresh?slot=${slot}`, { method: "POST" });
    const result = await r.json();

    if (r.status === 401) {
      alert("Login session expired. Please login again.");
      await loadSlot(slot);
      return;
    }

    if (!r.ok || !result.success) {
      throw new Error(result.detail || result.message || "Refresh failed");
    }
    await loadSlot(slot);
  } catch (err) {
    console.error("GetNow error:", err);
    alert("Get Now failed: " + err.message);
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

async function loadSlot(slot) {
  const [current, history, config] = await Promise.all([
    fetchCurrent(slot),
    fetchHistory(slot),
    fetchConfig(slot),
  ]);
  renderCurrent(slot, current);
  renderHistory(slot, history);
  renderUsername(slot, config);
}

async function loadAll() {
  await Promise.all(SLOTS.map(loadSlot));
}

for (const slot of SLOTS) {
  el(slot, "header", "login-btn").addEventListener("click", () => login(slot));
  el(slot, "header", "logout-btn").addEventListener("click", () => logout(slot));
  el(slot, "card", "get-now-btn").addEventListener("click", () => getNow(slot));
}

loadAll();
setInterval(loadAll, POLL_INTERVAL_MS);
