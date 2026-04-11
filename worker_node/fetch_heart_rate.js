/**
 * Garmin heart rate fetcher (Node.js helper).
 *
 * Called by Python worker via subprocess. Reads credentials from env
 * GARMIN_USERNAME / GARMIN_PASSWORD, and a date range from argv:
 *     node fetch_heart_rate.js <start_iso> <end_iso>
 *
 * On first successful login tokens are saved to ./tokens/ so subsequent
 * runs do not hit the login endpoint (avoiding Cloudflare rate limits).
 *
 * Output (stdout): JSON array of {measured_at: ISO8601, level: int}.
 * On failure: exit code != 0 and error written to stderr.
 */

const { GarminConnect } = require('garmin-connect');
const path = require('path');
const fs = require('fs');

// Метод getBodyBattery отсутствует в библиотеке — добавляем по аналогии с getHeartRate.
// BB данные живут внутри dailyStress endpoint, поле bodyBatteryValuesArray.
// Каждый элемент: [timestampMs, status, bodyBatteryLevel, delta].
GarminConnect.prototype.getBodyBattery = async function(date = new Date()) {
  const dateString = date.toISOString().slice(0, 10);
  const data = await this.client.get(
    'https://connectapi.garmin.com/wellness-service/wellness/dailyStress/' + dateString
  );
  return (data && data.bodyBatteryValuesArray) || [];
};

const TOKEN_DIR = process.env.GARMIN_TOKEN_DIR || path.join(__dirname, 'tokens');

function log(...args) {
  // Весь лог — в stderr, чтобы stdout был чистым JSON
  console.error('[node]', ...args);
}

async function main() {
  const [startArg, endArg] = process.argv.slice(2);
  if (!startArg || !endArg) {
    console.error('Usage: node fetch_heart_rate.js <start_iso> <end_iso>');
    process.exit(2);
  }

  const start = new Date(startArg);
  const end = new Date(endArg);
  if (isNaN(start) || isNaN(end)) {
    console.error('Invalid ISO date');
    process.exit(2);
  }

  const username = process.env.GARMIN_USERNAME;
  const password = process.env.GARMIN_PASSWORD;
  if (!username || !password) {
    console.error('GARMIN_USERNAME / GARMIN_PASSWORD not set');
    process.exit(2);
  }

  if (!fs.existsSync(TOKEN_DIR)) fs.mkdirSync(TOKEN_DIR, { recursive: true });

  const client = new GarminConnect({ username, password });

  // Попытка загрузить сохранённые токены. Если не удалось или токены для другого
  // пользователя — делаем полноценный login.
  let usedTokens = false;
  try {
    client.loadTokenByFile(TOKEN_DIR);
    const profile = await client.getUserProfile();
    const tokenUsername = (profile.userName || profile.userDisplayName || profile.displayName || '').toLowerCase();
    if (!tokenUsername || tokenUsername !== username.toLowerCase()) {
      throw new Error('Token user mismatch');
    }
    usedTokens = true;
    log('Logged in via saved tokens for', tokenUsername);
  } catch (e) {
    log('Saved tokens unavailable or invalid, performing full login:', e?.message || e);
    try {
      await client.login(username, password);
      client.exportTokenToFile(TOKEN_DIR);
      log('Login successful, tokens saved to', TOKEN_DIR);
    } catch (loginErr) {
      console.error('LOGIN_FAILED:', loginErr?.message || loginErr);
      process.exit(3);
    }
  }

  let profileName = null;
  try {
    const profile = await client.getUserProfile();
    profileName = profile.fullName || profile.displayName || profile.userDisplayName || `${profile.firstName || ''} ${profile.lastName || ''}`.trim() || null;
    log('Extracted profile_name:', profileName);
  } catch (e) {
    log('Failed to fetch profile name:', e?.message || e);
  }

  const dayMs = 24 * 60 * 60 * 1000;
  const startDay = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
  const endDay = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate()));
  // Собираем heart rate за каждый день в диапазоне [start..end]
  const entries = [];
  for (let d = startDay.getTime(); d <= endDay.getTime(); d += dayMs) {
    const cdate = new Date(d);
    log('Fetching heart rate for', cdate.toISOString().slice(0, 10));
    let data;
    try {
      data = await client.getHeartRate(cdate);
    } catch (e) {
      log('No HR data for', cdate.toISOString().slice(0, 10), '-', e?.message || e);
      continue;
    }

    const values = (data && data.heartRateValues) || [];
    for (const item of values) {
      if (!item || item.length < 2) continue;
      const [tsMs, bpm] = item;
      if (bpm == null) continue;
      const measuredAt = new Date(tsMs);
      if (measuredAt >= start && measuredAt <= end) {
        entries.push({
          measured_at: measuredAt.toISOString(),
          level: Math.round(bpm),
          battery_level: null,
        });
      }
    }
  }
  log(`Fetched ${entries.length} heart rate points`);

  // Собираем Body Battery за тот же диапазон
  const bbPoints = []; // {ts: ms, value: number}
  for (let d = startDay.getTime(); d <= endDay.getTime(); d += dayMs) {
    const cdate = new Date(d);
    log('Fetching body battery for', cdate.toISOString().slice(0, 10));
    let data;
    try {
      data = await client.getBodyBattery(cdate);
    } catch (e) {
      log('No BB data for', cdate.toISOString().slice(0, 10), '-', e?.message || e);
      continue;
    }
    // data = [[timestampMs, status, bodyBatteryLevel, delta], ...]
    if (!Array.isArray(data)) continue;
    for (const item of data) {
      if (!Array.isArray(item) || item.length < 3) continue;
      const [tsMs, , level] = item;
      if (tsMs == null || level == null) continue;
      bbPoints.push({ ts: tsMs, value: level });
    }
  }
  log(`Fetched ${bbPoints.length} body battery points`);

  // Привязываем ближайшее BB значение к каждой HR точке (±30 мин)
  if (bbPoints.length > 0) {
    bbPoints.sort((a, b) => a.ts - b.ts);
    const MAX_DELTA_MS = 30 * 60 * 1000;
    for (const entry of entries) {
      const tsMs = new Date(entry.measured_at).getTime();
      let lo = 0, hi = bbPoints.length - 1, best = -1, bestDelta = Infinity;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const delta = Math.abs(bbPoints[mid].ts - tsMs);
        if (delta < bestDelta) { bestDelta = delta; best = mid; }
        if (bbPoints[mid].ts < tsMs) lo = mid + 1;
        else if (bbPoints[mid].ts > tsMs) hi = mid - 1;
        else break;
      }
      entry.battery_level = (best >= 0 && bestDelta <= MAX_DELTA_MS) ? bbPoints[best].value : null;
    }
  }

  log(`Done (usedTokens=${usedTokens})`);
  const output = JSON.stringify({ profile_name: profileName, entries });
  const outputFile = path.join(TOKEN_DIR, '_result.json');
  fs.writeFileSync(outputFile, output, 'utf8');
  log('Result written to', outputFile);
}

main().catch((err) => {
  console.error('FATAL:', err?.message || err);
  process.exit(1);
});
