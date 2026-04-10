const API_BASE = "http://localhost:8000/api/v1";
const POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 минут

let historyChart = null;

// Helper function to adjust UTC time by +5 hours
function adjustTimezoneOffset(dateString) {
  const date = new Date(dateString);
  const OFFSET_MS = 3 * 60 * 60 * 1000; // 5 hours in milliseconds
  return new Date(date.getTime() + OFFSET_MS);
}

async function fetchConfig() {
  try {
    const response = await fetch(`${API_BASE}/config`);
    return response.ok ? await response.json() : null;
  } catch (err) {
    console.error("fetchConfig failed:", err);
    return null;
  }
}

function renderUsername(config) {
  const loginForm = document.getElementById("login-form");
  const userInfo = document.getElementById("user-info");
  const usernameDisplay = document.getElementById("username-display");
  const getNowBtn = document.getElementById("get-now-btn");

  const isLoggedIn = config && config.username !== "Not logged in";

  if (!isLoggedIn) {
    loginForm.style.display = "block";
    userInfo.style.display = "none";
    getNowBtn.disabled = true;
    getNowBtn.title = "Please login first";
  } else {
    loginForm.style.display = "none";
    userInfo.style.display = "block";
    usernameDisplay.textContent = config.username;
    getNowBtn.disabled = false;
    getNowBtn.title = "";
  }
}

/**
 * Цветовая зона по пульсу (BPM):
 *   зелёный: 50-90   (норма покоя)
 *   жёлтый:  90-130  (умеренная нагрузка)
 *   красный: <50 или >130
 */
function zoneFor(bpm) {
  if (bpm == null) return "zone-unknown";
  if (bpm < 50 || bpm > 130) return "zone-red";
  if (bpm > 90) return "zone-yellow";
  return "zone-green";
}

function colorFor(bpm) {
  const zone = zoneFor(bpm);
  return {
    "zone-green": "#16a34a",
    "zone-yellow": "#eab308",
    "zone-red": "#dc2626",
    "zone-unknown": "#6b7280",
  }[zone];
}

/**
 * Цветовая зона по Body Battery (%):
 *   зелёный: 75-100  (высокий)
 *   жёлтый:  40-74   (средний)
 *   красный: 0-39    (низкий)
 */
function bbZoneFor(val) {
  if (val == null) return "bb-unknown";
  if (val >= 25) return "bb-high";
  if (val >= 20) return "bb-medium";
  return "bb-low";
}

function bbColorFor(val) {
  return {
    "bb-high":    "#16a34a",
    "bb-medium":  "#16a34a",//"#eab308",
    "bb-low":     "#dc2626",
    "bb-unknown": "#6b7280",
  }[bbZoneFor(val)];
}

async function fetchCurrent() {
  try {
    const response = await fetch(`${API_BASE}/battery/current`);
    return response.ok ? await response.json() : null;
  } catch (err) {
    console.error("fetchCurrent failed:", err);
    return null;
  }
}

async function fetchHistory() {
  try {
    const response = await fetch(`${API_BASE}/battery/history?hours=24`);
    return response.ok ? await response.json() : null;
  } catch (err) {
    console.error("fetchHistory failed:", err);
    return null;
  }
}

function renderCurrent(data) {
  const hrContainer = document.getElementById("current-status");
  const bbContainer = document.getElementById("battery-status");
  const detailsContainer = document.getElementById("current-details");
  const profileNameContainer = document.getElementById("profile-name");

  if (!data) {
    profileNameContainer.textContent = "Profile: —";
    hrContainer.innerHTML = `<div class="error">Ошибка загрузки</div>`;
    bbContainer.innerHTML = `<div class="error">—</div>`;
    detailsContainer.innerHTML = "";
    return;
  }

  const ts = data.timestamp ? adjustTimezoneOffset(data.timestamp).toLocaleString() : "—";
  profileNameContainer.textContent = `Profile: ${data.profile_name || "—"}`;

  // Heart Rate
  const hrZone = zoneFor(data.level);
  hrContainer.innerHTML = `
    <div class="metric ${hrZone}">
      <span class="value">${data.level}</span>
      <span class="unit">bpm</span>
    </div>
  `;

  // Body Battery
  const bbVal = data.battery_level;
  const bbZone = bbZoneFor(bbVal);
  bbContainer.innerHTML = bbVal != null
    ? `<div class="metric ${bbZone}">
         <span class="value">${bbVal}</span>
         <span class="unit">%</span>
       </div>`
    : `<div class="metric bb-unknown"><span class="value">—</span></div>`;

  // Details
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

function renderHistory(data) {
  const canvas = document.getElementById("historyChart");
  const ctx = canvas.getContext("2d");

  if (!data || !data.data || data.data.length === 0) {
    if (historyChart) {
      historyChart.destroy();
      historyChart = null;
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

  if (historyChart) {
    historyChart.data.labels = labels;
    historyChart.data.datasets[0].data = hrValues;
    historyChart.data.datasets[0].pointBackgroundColor = hrPointColors;
    historyChart.data.datasets[1].data = bbValues;
    historyChart.data.datasets[1].pointBackgroundColor = bbPointColors;
    historyChart.update();
    return;
  }

  historyChart = new Chart(ctx, {
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
      plugins: {
        legend: { display: true },
      },
    },
  });
}

async function login() {
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  if (!username || !password) {
    alert("Please enter username and password");
    return;
  }

  try {
    const response = await fetch(`${API_BASE}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const result = await response.json();
    if (result.success) {
      alert("Logged in successfully");
      // Очищаем input fields
      document.getElementById("username").value = "";
      document.getElementById("password").value = "";
      load(); // reload to update UI
    } else {
      alert("Login failed: " + result.message);
    }
  } catch (err) {
    console.error("Login error:", err);
    alert("Login error");
  }
}

async function logout() {
  try {
    const response = await fetch(`${API_BASE}/logout`, { method: "POST" });
    const result = await response.json();
    if (result.success) {
      alert("Logged out successfully");
      // Очищаем input fields
      document.getElementById("username").value = "";
      document.getElementById("password").value = "";
      load(); // reload to update UI
    } else {
      alert("Logout failed: " + result.message);
    }
  } catch (err) {
    console.error("Logout error:", err);
    alert("Logout error");
  }
}

async function getNow() {
  const btn = document.getElementById("get-now-btn");

  // Дополнительная проверка: убедиться, что пользователь залогинен
  const config = await fetchConfig();
  if (!config || config.username === "Not logged in") {
    alert("Please login first before refreshing data");
    return;
  }

  const originalText = btn.textContent;
  btn.textContent = "Updating...";
  btn.disabled = true;

  try {
    const response = await fetch(`${API_BASE}/refresh`, { method: "POST" });
    const result = await response.json();

    if (response.status === 401) {
      alert("Login session expired. Please login again.");
      await load(); // reload to update UI
      return;
    }

    if (!response.ok || !result.success) {
      throw new Error(result.detail || result.message || "Refresh failed");
    }
    await load();
  } catch (err) {
    console.error("GetNow error:", err);
    alert("Get Now failed: " + err.message);
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

async function load() {
  const [current, history, config] = await Promise.all([fetchCurrent(), fetchHistory(), fetchConfig()]);
  renderCurrent(current);
  renderHistory(history);
  renderUsername(config);
}

load();
setInterval(load, POLL_INTERVAL_MS);

// Event listeners
document.getElementById("login-btn").addEventListener("click", login);
document.getElementById("logout-btn").addEventListener("click", logout);
document.getElementById("get-now-btn").addEventListener("click", getNow);
