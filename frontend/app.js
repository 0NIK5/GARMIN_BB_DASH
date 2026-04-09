const API_BASE = "http://localhost:8000/api/v1";
const POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 минут

let historyChart = null;

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
  const container = document.getElementById("current-status");
  const profileNameContainer = document.getElementById("profile-name");
  if (!data) {
    profileNameContainer.textContent = "Profile: —";
    container.innerHTML = `<div class="error">Ошибка загрузки данных</div>`;
    return;
  }
  const zone = zoneFor(data.level);
  const ts = data.timestamp ? new Date(data.timestamp).toLocaleString() : "—";
  profileNameContainer.textContent = `Profile: ${data.profile_name || "—"}`;
  container.innerHTML = `
    <div class="metric ${zone}">
      <span class="value">${data.level}</span>
      <span class="unit">bpm</span>
    </div>
    <div class="details">
      <div>Обновлено: ${ts}</div>
      <div>Статус: ${data.status || "—"}</div>
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

  const labels = data.data.map((item) => new Date(item.time).toLocaleTimeString());
  const values = data.data.map((item) => item.level);
  const pointColors = values.map(colorFor);

  if (historyChart) {
    historyChart.data.labels = labels;
    historyChart.data.datasets[0].data = values;
    historyChart.data.datasets[0].pointBackgroundColor = pointColors;
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
          data: values,
          borderColor: "#2563eb",
          backgroundColor: "rgba(37, 99, 235, 0.15)",
          pointBackgroundColor: pointColors,
          pointRadius: 3,
          fill: true,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: false,
      scales: {
        y: { min: 30, max: 200, title: { display: true, text: "BPM" } },
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
