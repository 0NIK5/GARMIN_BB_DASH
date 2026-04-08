const API_BASE = "http://localhost:8000/api/v1";

async function fetchCurrent() {
  const response = await fetch(`${API_BASE}/battery/current`);
  return response.ok ? response.json() : null;
}

async function fetchHistory() {
  const response = await fetch(`${API_BASE}/battery/history?hours=24`);
  return response.ok ? response.json() : null;
}

function renderCurrent(data) {
  const container = document.getElementById("current-status");
  if (!data) {
    container.textContent = "Ошибка загрузки данных";
    return;
  }
  container.innerHTML = `
    <div class="metric">
      <span class="value">${data.level}</span>
      <span class="unit">%</span>
    </div>
    <div>Updated ${data.timestamp}</div>
    <div>Status: ${data.status}</div>
    <div>${data.is_stale ? "Данные устарели" : "Данные свежие"}</div>
  `;
}

function renderHistory(data) {
  const ctx = document.getElementById("historyChart").getContext("2d");
  if (!data) {
    ctx.canvas.parentElement.innerText = "Ошибка загрузки истории";
    return;
  }
  const labels = data.data.map((item) => new Date(item.time).toLocaleString());
  const values = data.data.map((item) => item.level);
  new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Body Battery",
          data: values,
          borderColor: "#2563eb",
          backgroundColor: "rgba(37, 99, 235, 0.15)",
          fill: true,
          tension: 0.3,
        },
      ],
    },
    options: {
      scales: {
        y: { min: 0, max: 100 },
      },
    },
  });
}

async function load() {
  const [current, history] = await Promise.all([fetchCurrent(), fetchHistory()]);
  renderCurrent(current);
  renderHistory(history);
}

load();
