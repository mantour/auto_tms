/* auto_tms GUI application */

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
    // Auto-load data when switching tabs
    if (btn.dataset.tab === 'progress') loadStatus();
    if (btn.dataset.tab === 'run') loadStatus();
  });
});

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

const configForm = document.getElementById('config-form');
const configMsg = document.getElementById('config-msg');

async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    const data = await res.json();
    for (const [key, val] of Object.entries(data)) {
      const input = configForm.querySelector(`[name="${key}"]`);
      if (input) {
        if (input.type === 'password' && val === '****') {
          input.placeholder = '(已設定)';
        } else {
          input.value = val;
        }
      }
    }
  } catch (e) {
    console.error('Failed to load config:', e);
  }
}

configForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = {};
  new FormData(configForm).forEach((v, k) => { data[k] = v; });
  // Convert number fields
  data.max_pages = parseInt(data.max_pages) || 5;
  data.max_videos = parseInt(data.max_videos) || 2;

  try {
    const res = await fetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (res.ok) {
      configMsg.textContent = '已儲存';
      configMsg.className = 'msg ok';
    } else {
      configMsg.textContent = '儲存失敗';
      configMsg.className = 'msg err';
    }
    setTimeout(() => { configMsg.textContent = ''; }, 3000);
  } catch (e) {
    configMsg.textContent = '連線錯誤';
    configMsg.className = 'msg err';
  }
});

// ---------------------------------------------------------------------------
// Status / Progress
// ---------------------------------------------------------------------------

async function loadStatus(refresh = false) {
  try {
    const url = refresh ? '/api/status?refresh=true' : '/api/status';
    const res = await fetch(url);
    const data = await res.json();
    renderPrograms(data.programs || []);
    renderCourses(data.courses || []);
    updateRunStatus(data);
    updateStatusBar(data);
  } catch (e) {
    console.error('Failed to load status:', e);
  }
}

function renderPrograms(programs) {
  const el = document.getElementById('programs-list');
  if (!programs.length) {
    el.innerHTML = '<p style="color:#999">尚無資料，請先執行一次或點擊重新整理</p>';
    return;
  }
  const passed = programs.filter(p => p.passed).length;
  let html = `<div style="margin-bottom:8px;font-size:13px;color:#666">通過: ${passed}/${programs.length}</div>`;
  for (const p of programs) {
    const pct = p.total_required > 0
      ? Math.min(100, Math.round(p.total_completed / p.total_required * 100))
      : 100;
    const mark = p.passed ? '<span style="color:#16a34a">&#10003;</span>' : '<span style="color:#dc2626">&#10007;</span>';
    const barClass = p.passed ? 'done' : '';
    html += `
      <div class="program-item">
        <span class="mark">${mark}</span>
        <span class="name" title="${p.name}">${p.name}</span>
        <div class="bar-wrap"><div class="bar-fill ${barClass}" style="width:${pct}%"></div></div>
        <span class="hours">${p.total_completed.toFixed(0)}/${p.total_required.toFixed(0)}h</span>
      </div>`;
  }
  el.innerHTML = html;
}

function renderCourses(courses) {
  const el = document.getElementById('courses-list');
  if (!courses.length) {
    el.innerHTML = '<p style="color:#999">尚無課程</p>';
    return;
  }
  let html = '';
  for (const c of courses) {
    let iconClass, iconChar;
    if (c.status === 'done') {
      iconClass = 'done'; iconChar = '&#10003;';
    } else if (c.status === 'in_progress') {
      iconClass = 'progress'; iconChar = '&#9678;';
    } else {
      iconClass = 'pending'; iconChar = '&middot;';
    }
    // Material breakdown
    const types = {};
    for (const m of c.materials) {
      if (!types[m.type]) types[m.type] = [0, 0];
      types[m.type][1]++;
      if (m.status === 'done') types[m.type][0]++;
    }
    const mats = Object.entries(types).map(([t, [d, n]]) => `${t}:${d}/${n}`).join(' ');

    html += `
      <div class="course-item">
        <span class="icon ${iconClass}">${iconChar}</span>
        <span class="title" title="${c.title || c.course_id}">${c.title || c.course_id}</span>
        <span class="mats">${mats}</span>
      </div>`;
  }
  el.innerHTML = html;
}

document.getElementById('refresh-btn').addEventListener('click', () => loadStatus(true));

// ---------------------------------------------------------------------------
// Run control
// ---------------------------------------------------------------------------

const btnStart = document.getElementById('btn-start');
const btnStop = document.getElementById('btn-stop');
const btnReset = document.getElementById('btn-reset');

btnStart.addEventListener('click', async () => {
  const courseId = document.getElementById('run-course-id').value.trim();
  const mode = document.getElementById('run-mode').value;
  const body = courseId ? { course_id: courseId } : { mode };

  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.status === 'started') {
      btnStart.disabled = true;
      btnStop.disabled = false;
      document.getElementById('rs-state').textContent = '執行中';
      updateStatusBar({ running: true });
    } else if (data.status === 'already_running') {
      alert('Pipeline 已在執行中');
    }
  } catch (e) {
    alert('啟動失敗: ' + e.message);
  }
});

btnStop.addEventListener('click', async () => {
  try {
    await fetch('/api/stop', { method: 'POST' });
    btnStart.disabled = false;
    btnStop.disabled = true;
    document.getElementById('rs-state').textContent = '已停止';
    updateStatusBar({ running: false });
  } catch (e) {
    alert('停止失敗: ' + e.message);
  }
});

btnReset.addEventListener('click', async () => {
  if (!confirm('確定要清除所有進度？')) return;
  try {
    await fetch('/api/reset', { method: 'POST' });
    document.getElementById('rs-state').textContent = '已重置';
    document.getElementById('rs-iteration').textContent = '-';
    document.getElementById('rs-courses').textContent = '-';
    loadStatus();
  } catch (e) {
    alert('重置失敗: ' + e.message);
  }
});

function updateRunStatus(data) {
  if (data.running) {
    document.getElementById('rs-state').textContent = '執行中';
    btnStart.disabled = true;
    btnStop.disabled = false;
  } else {
    document.getElementById('rs-state').textContent = '待命';
    btnStart.disabled = false;
    btnStop.disabled = true;
  }
  if (data.iteration) {
    document.getElementById('rs-iteration').textContent = `${data.iteration}/3`;
  }
  const courses = data.courses || [];
  const done = courses.filter(c => c.status === 'done').length;
  document.getElementById('rs-courses').textContent = courses.length ? `${done}/${courses.length}` : '-';
}

function updateStatusBar(data) {
  const stateEl = document.getElementById('sb-state');
  const infoEl = document.getElementById('sb-info');
  if (data.running) {
    stateEl.textContent = '執行中';
    stateEl.className = 'running';
  } else {
    stateEl.textContent = '待命';
    stateEl.className = 'stopped';
  }
  const courses = data.courses || [];
  if (courses.length) {
    const done = courses.filter(c => c.status === 'done').length;
    const parts = [`${done}/${courses.length} 課程`];
    if (data.iteration) parts.push(`迭代 ${data.iteration}/3`);
    infoEl.textContent = parts.join(' | ');
  }
}

// ---------------------------------------------------------------------------
// WebSocket for live log + status
// ---------------------------------------------------------------------------

let ws = null;
let autoScroll = true;
let errorCount = 0;

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/progress`);

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.type === 'log') {
      appendLog(msg.line, msg.level);
      if (msg.level === 'ERROR') {
        errorCount++;
        document.getElementById('rs-errors').textContent = errorCount;
      }
    } else if (msg.type === 'status') {
      handleStatusEvent(msg.data);
    }
  };

  ws.onclose = () => {
    setTimeout(connectWS, 2000);
  };
}

function appendLog(line, level) {
  const el = document.getElementById('log-output');
  const div = document.createElement('div');
  div.className = `log-line ${level || ''}`;
  div.textContent = line;
  el.appendChild(div);

  // Keep max 2000 lines
  while (el.children.length > 2000) el.removeChild(el.firstChild);

  if (autoScroll) el.scrollTop = el.scrollHeight;
}

// Detect manual scroll
document.getElementById('log-output').addEventListener('scroll', function () {
  const el = this;
  autoScroll = (el.scrollTop + el.clientHeight >= el.scrollHeight - 20);
});

function handleStatusEvent(data) {
  if (data.event === 'pipeline_done') {
    btnStart.disabled = false;
    btnStop.disabled = true;
    document.getElementById('rs-state').textContent = '完成';
    updateStatusBar({ running: false });
    loadStatus();
  } else if (data.event === 'course_done') {
    loadStatus();
  } else if (data.event === 'iteration') {
    document.getElementById('rs-iteration').textContent = `${data.iteration}/3`;
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadConfig();
loadStatus();
connectWS();
