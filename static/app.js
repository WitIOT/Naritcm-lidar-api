const elStatus = document.getElementById('status');
const raw = document.getElementById('raw');

const t_in = document.getElementById('t_indoor');
const h_in = document.getElementById('h_indoor');
const d_in = document.getElementById('d_indoor');

const t_out = document.getElementById('t_outdoor');
const h_out = document.getElementById('h_outdoor');
const d_out = document.getElementById('d_outdoor');

// Roof elements
const roofState = document.getElementById('roof_state');
const roofMsg = document.getElementById('roof_msg');
const btnOpen = document.getElementById('btn_open');
const btnClose = document.getElementById('btn_close');
const btnStop = document.getElementById('btn_stop');
const btnStatus = document.getElementById('btn_status');

function fmt1(x) { return (typeof x === 'number' && isFinite(x)) ? x.toFixed(1) : '-'; }

async function apiPost(url, body) {
  const opt = { method: 'POST', headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = (j && (j.detail || j.error)) ? (j.detail || j.error) : `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return j;
}

async function apiGet(url) {
  const r = await fetch(url);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = (j && (j.detail || j.error)) ? (j.detail || j.error) : `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return j;
}

// Roof control actions (เรียก API door ที่ backend มีอยู่)
async function roofOpen() {
  roofMsg.textContent = 'Sending: roof open...';
  try {
    const j = await apiPost('/door/open'); // default pulse
    roofMsg.textContent = `OK: ${JSON.stringify(j)}`;
    await roofStatus();
  } catch (e) {
    roofMsg.textContent = `ERROR: ${e.message}`;
  }
}

async function roofClose() {
  roofMsg.textContent = 'Sending: roof close...';
  try {
    const j = await apiPost('/door/close'); // default pulse
    roofMsg.textContent = `OK: ${JSON.stringify(j)}`;
    await roofStatus();
  } catch (e) {
    roofMsg.textContent = `ERROR: ${e.message}`;
  }
}

async function roofStop() {
  roofMsg.textContent = 'Sending: stop...';
  try {
    const j = await apiPost('/door/stop');
    roofMsg.textContent = `OK: ${JSON.stringify(j)}`;
    await roofStatus();
  } catch (e) {
    roofMsg.textContent = `ERROR: ${e.message}`;
  }
}

async function roofStatus() {
  try {
    const j = await apiGet('/door/status');
    const st = j && j.status && j.status.state ? j.status.state : '-';
    roofState.textContent = `state: ${st}`;
    return j;
  } catch (e) {
    roofState.textContent = 'state: -';
    roofMsg.textContent = `ERROR: ${e.message}`;
  }
}

btnOpen.onclick = roofOpen;
btnClose.onclick = roofClose;
btnStop.onclick = roofStop;
btnStatus.onclick = roofStatus;

// Charts
const chTemp = new Chart(document.getElementById('temp').getContext('2d'), {
  type: 'line',
  data: { datasets: [
    { label: 'Temp Indoor (°C)', data: [], borderColor: 'blue', borderWidth: 2, tension: 0.2 },
    { label: 'Temp Outdoor (°C)', data: [], borderColor: 'red', borderWidth: 2, tension: 0.2 }
  ]},
  options: {
    parsing: false, animation: false, interaction: { mode: 'nearest', intersect: false },
    scales: { x: { type: 'time', time: { unit: 'second' }, title: { display: true, text: 'Time' } },
              y: { title: { display: true, text: '°C' } } }
  }
});

const chHumi = new Chart(document.getElementById('humi').getContext('2d'), {
  type: 'line',
  data: { datasets: [
    { label: 'Humi Indoor (%RH)', data: [], borderColor: 'blue', borderWidth: 2, tension: 0.2 },
    { label: 'Humi Outdoor (%RH)', data: [], borderColor: 'red', borderWidth: 2, tension: 0.2 }
  ]},
  options: {
    parsing: false, animation: false, interaction: { mode: 'nearest', intersect: false },
    scales: { x: { type: 'time', time: { unit: 'second' }, title: { display: true, text: 'Time' } },
              y: { title: { display: true, text: '%RH' } } }
  }
});

const chDew = new Chart(document.getElementById('dew').getContext('2d'), {
  type: 'line',
  data: { datasets: [
    { label: 'Dew Point Indoor (°C)', data: [], borderColor: 'blue', borderWidth: 2, tension: 0.2 },
    { label: 'Dew Point Outdoor (°C)', data: [], borderColor: 'red', borderWidth: 2, tension: 0.2 }
  ]},
  options: {
    parsing: false, animation: false, interaction: { mode: 'nearest', intersect: false },
    scales: { x: { type: 'time', time: { unit: 'second' }, title: { display: true, text: 'Time' } },
              y: { title: { display: true, text: '°C' } } }
  }
});

// IMPORTANT: backend ของคุณใช้ /ws/sensor (ไม่ใช่ /ws)
const wsUrl = (location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.host + '/ws/sensor';
const ws = new WebSocket(wsUrl);

ws.onopen  = () => { elStatus.textContent = 'connected'; roofStatus(); };
ws.onclose = () => { elStatus.textContent = 'disconnected'; };
ws.onmessage = (e) => {
  const j = JSON.parse(e.data);
  const ts = j.ts;

  // raw text
  const r1 = j.indoor && Array.isArray(j.indoor.raw) ? j.indoor.raw.join(',') : '-';
  const r2 = j.outdoor && Array.isArray(j.outdoor.raw) ? j.outdoor.raw.join(',') : '-';
  raw.textContent = `raw: indoor=${r1} | outdoor=${r2}`;

  // KPI update
  if (j.indoor) {
    if (typeof j.indoor.temp === 'number') t_in.textContent = fmt1(j.indoor.temp);
    if (typeof j.indoor.humi === 'number') h_in.textContent = fmt1(j.indoor.humi);
    if (typeof j.indoor.dewpoint === 'number') d_in.textContent = fmt1(j.indoor.dewpoint);
  }
  if (j.outdoor) {
    if (typeof j.outdoor.temp === 'number') t_out.textContent = fmt1(j.outdoor.temp);
    if (typeof j.outdoor.humi === 'number') h_out.textContent = fmt1(j.outdoor.humi);
    if (typeof j.outdoor.dewpoint === 'number') d_out.textContent = fmt1(j.outdoor.dewpoint);
  }

  // Chart update
  if (j.indoor && typeof j.indoor.temp === 'number') chTemp.data.datasets[0].data.push({ x: ts, y: j.indoor.temp });
  if (j.outdoor && typeof j.outdoor.temp === 'number') chTemp.data.datasets[1].data.push({ x: ts, y: j.outdoor.temp });

  if (j.indoor && typeof j.indoor.humi === 'number') chHumi.data.datasets[0].data.push({ x: ts, y: j.indoor.humi });
  if (j.outdoor && typeof j.outdoor.humi === 'number') chHumi.data.datasets[1].data.push({ x: ts, y: j.outdoor.humi });

  if (j.indoor && typeof j.indoor.dewpoint === 'number') chDew.data.datasets[0].data.push({ x: ts, y: j.indoor.dewpoint });
  if (j.outdoor && typeof j.outdoor.dewpoint === 'number') chDew.data.datasets[1].data.push({ x: ts, y: j.outdoor.dewpoint });

  const MAX = 600;
  for (const ds of chTemp.data.datasets) while (ds.data.length > MAX) ds.data.shift();
  for (const ds of chHumi.data.datasets) while (ds.data.length > MAX) ds.data.shift();
  for (const ds of chDew.data.datasets) while (ds.data.length > MAX) ds.data.shift();

  chTemp.update('none'); 
  chHumi.update('none');
  chDew.update('none');
};
