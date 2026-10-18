'use strict';

const $ = (id) => document.getElementById(id);

const STATE_LABELS = {
  idle: '대기', starting: '준비 중', countdown: '시작 대기', capturing: '캡처 중',
  building: 'PDF 생성 중', ocr: 'OCR 중', done: '완료', stopped: '중지됨', error: '오류',
};
const JOB_LABELS = {
  capturing: '캡처 중', building: 'PDF 생성 중', ocr: 'OCR 중', done: '완료', stopped: '중단됨', error: '오류',
};
const LANG_LABELS = { eng: '영어', kor: '한국어', jpn: '일본어', chi_sim: '중국어(간체)', chi_tra: '중국어(번체)' };

const NUM_FIELDS = { delay: parseFloat, max_pages: (v) => parseInt(v, 10), start_delay: (v) => parseInt(v, 10) };
const NUM_INFO = { delay: ['넘긴 뒤 대기', '초', '로'], max_pages: ['최대 쪽수', '쪽', '으로'], start_delay: ['시작 전 대기', '초', '로'] };
const TEXT_FIELDS = ['title', 'output_dir', 'next_mode', 'next_key', 'ocr_lang'];
const BOOL_FIELDS = ['focus_click', 'ocr'];
const REGION_KEYS = ['x', 'y', 'w', 'h'];

let info = null;      // /api/info 응답
let cfg = null;       // 현재 캡처 설정 (서버에 자동 저장)
let display = null;   // 미리보기에 띄운 디스플레이 (전역 좌표, 포인트)
let mode = 'region';  // 'region' | 'point'
let drag = null;
let status = null;
let thumbKey = '';

// ------------------------------------------------------------ 공통

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

function toast(msg, kind = '') {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'show ' + kind;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.className = ''), kind === 'error' ? 6000 : 3500);
}

async function api(path, opts = {}) {
  const headers = opts.body ? { 'Content-Type': 'application/json' } : {};
  const res = await fetch(path, { credentials: 'same-origin', ...opts, headers });
  if (res.status === 401 && path !== '/api/login') {
    showLogin();
    throw new Error('로그인이 필요합니다');
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join(', ') : body.detail || msg;
    } catch (_) { /* JSON이 아닌 응답 */ }
    throw new Error(msg);
  }
  return res;
}
const getJSON = async (path) => (await api(path)).json();
const send = (path, method, body) => api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

async function withBusy(btn, fn) {
  btn.disabled = true;
  btn.classList.add('loading');
  try {
    await fn();
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    btn.classList.remove('loading');
    btn.disabled = !!(status && status.busy);
  }
}

function fmtSize(bytes) {
  if (!bytes) return '';
  return bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// ------------------------------------------------------------ 로그인

function showLogin() {
  $('login').hidden = false;
  $('login-token').focus();
}

async function login(token) {
  await api('/api/login', { method: 'POST', body: JSON.stringify({ token }) });
}

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await login($('login-token').value.trim());
    $('login').hidden = true;
    $('login-error').textContent = '';
    await init();
  } catch (err) {
    $('login-error').textContent = err.message;
  }
});

// ------------------------------------------------------------ 초기화 · 설정 폼

let started = false;

async function init() {
  info = await getJSON('/api/info');
  cfg = info.config;
  renderPerms();
  renderDisplays();
  renderLangs();
  renderLimits();
  fillForm();
  if (!started) {
    started = true;
    pollLoop();
    loadJobs();
    if (info.permissions.screen !== false) withBusy($('btn-refresh'), loadScreen); // 윈도우는 권한 항목 없음
  }
}

function renderPerms() {
  const p = info.permissions; // 윈도우는 필요한 권한이 없어 비어 있다
  const fix = '캡처 PC의 시스템 설정 > 개인정보 보호 및 보안에서 서버를 실행한 터미널 앱에 권한을 준 뒤 서버를 다시 실행하세요';
  const badge = (ok, label, tip) => el('span', { class: 'badge ' + (ok ? 'ok' : 'bad'), title: ok ? '준비됨' : tip }, `${ok ? '✓' : '✕'} ${label}`);
  $('perms').replaceChildren(
    'screen' in p ? badge(p.screen, '화면 기록', fix) : '',
    'accessibility' in p ? badge(p.accessibility, '손쉬운 사용', fix) : '',
    badge(info.ocr_available, 'OCR', '캡처 PC에서 설치: ' + info.ocr_hint),
  );
  $('coord-unit').textContent = info.coord_unit === 'pt' ? '포인트 단위' : '픽셀 단위';
}

const missingPermission = () => Object.values(info.permissions).some((ok) => !ok);

function renderDisplays() {
  const sel = $('display');
  sel.replaceChildren(...info.displays.map((d) => el('option', { value: d.id }, `${d.name} · ${d.w}×${d.h}`)));
  const find = (id) => info.displays.find((d) => d.id === id);
  display = (display && find(display.id)) || find(cfg.display_id) || info.displays.find((d) => d.main) || info.displays[0];
  if (display) sel.value = display.id;
  sel.hidden = info.displays.length < 2;
}

function renderLangs() {
  const langs = info.ocr_langs;
  const opts = langs.includes('kor') && langs.includes('eng') ? ['kor+eng', ...langs] : [...langs];
  if (cfg.ocr_lang && !opts.includes(cfg.ocr_lang)) opts.push(cfg.ocr_lang);
  const label = (code) => code.split('+').map((c) => LANG_LABELS[c] || c).join(' + ');
  $('ocr_lang').replaceChildren(...opts.map((c) => el('option', { value: c }, label(c))));
}

// 허용 범위는 서버(CaptureConfig)가 알려준 값을 쓴다
function renderLimits() {
  for (const [k, [, unit]] of Object.entries(NUM_INFO)) {
    const { min, max } = info.limits[k];
    $(k).min = min;
    $(k).max = max;
    $('range-' + k).textContent = `${min}~${max}${unit}`;
  }
}

const inRange = (k, v) => v >= info.limits[k].min && v <= info.limits[k].max;

function clampNumber(k) {
  const v = NUM_FIELDS[k]($(k).value);
  if (!Number.isFinite(v) || inRange(k, v)) return; // 빈칸은 fillForm이 이전 값으로 되돌린다
  const { min, max } = info.limits[k];
  const [label, unit, josa] = NUM_INFO[k];
  const fixed = Math.min(max, Math.max(min, v));
  $(k).value = fixed;
  toast(`${label}는 ${min}~${max}${unit}만 가능해서 ${fixed}${unit}${josa} 맞췄습니다`);
}

function fillForm() {
  for (const k of Object.keys(NUM_FIELDS)) $(k).value = cfg[k] ?? '';
  for (const k of TEXT_FIELDS) $(k).value = cfg[k] ?? '';
  for (const k of BOOL_FIELDS) $(k).checked = !!cfg[k];
  for (const k of REGION_KEYS) $('r_' + k).value = cfg.region ? cfg.region[k] : '';
  const p = cfg.next_point;
  $('point-info').textContent = p
    ? `다음 버튼 위치: (${p.x}, ${p.y})`
    : "다음 버튼 위치가 없습니다 — 왼쪽 화면에서 '다음 버튼 위치' 모드로 버튼을 클릭하세요";
  $('key-row').hidden = cfg.next_mode !== 'key';
  $('click-row').hidden = cfg.next_mode !== 'click';
  renderNamePreview();
}

// 서버(store.new_job, safe_name)와 같은 규칙으로 만들어질 파일 이름을 미리 보여준다
function renderNamePreview() {
  const stem = (cfg.title || '').trim().replace(/\.pdf$/i, '')
    .replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').replace(/^[ .]+|[ .]+$/g, '').slice(0, 80) || '문서 (시작 시각)';
  $('name-preview').textContent = `→ ${stem}.pdf`
    + (cfg.ocr ? ' · 글자 검색 가능' : '') + (cfg.output_dir ? '' : ' · 앱 기본 폴더에 저장');
  $('name-preview').title = '같은 이름의 파일이 이미 있으면 덮어쓰지 않고 “이름 (2)”처럼 번호를 붙입니다';
}

function readForm() {
  for (const [k, parse] of Object.entries(NUM_FIELDS)) {
    const v = parse($(k).value);
    if (Number.isFinite(v) && inRange(k, v)) cfg[k] = v; // 입력 중인 범위 밖 값은 저장하지 않는다
  }
  for (const k of TEXT_FIELDS) cfg[k] = $(k).value;
  for (const k of BOOL_FIELDS) cfg[k] = $(k).checked;
  const r = REGION_KEYS.map((k) => parseInt($('r_' + k).value, 10));
  if (r.every(Number.isFinite) && r[2] > 0 && r[3] > 0) cfg.region = { x: r[0], y: r[1], w: r[2], h: r[3] };
  renderNamePreview();
}

$('btn-pick-folder').addEventListener('click', () => withBusy($('btn-pick-folder'), async () => {
  toast('캡처 PC 화면에 폴더 선택 창이 열립니다');
  const { path } = await (await send('/api/pick-folder', 'POST', { initial: cfg.output_dir || '' })).json();
  if (!path) return; // 취소
  cfg.output_dir = path;
  fillForm();
  saveSoon();
}));

let saveTimer = null;
function saveSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      await send('/api/config', 'PUT', cfg);
    } catch (e) {
      toast('설정 저장 실패: ' + e.message, 'error');
    }
  }, 400);
}

const formIds = [...Object.keys(NUM_FIELDS), ...TEXT_FIELDS, ...BOOL_FIELDS, ...REGION_KEYS.map((k) => 'r_' + k)];
for (const id of formIds) {
  $(id).addEventListener('input', () => { readForm(); draw(); saveSoon(); });
  $(id).addEventListener('change', () => {
    if (id in NUM_FIELDS) clampNumber(id);
    readForm(); fillForm(); draw(); saveSoon();
  });
}

// ------------------------------------------------------------ 화면 미리보기 · 좌표 지정

const screenImg = $('screen');
const overlay = $('overlay');

function grabDelay() {
  const sec = parseInt($('grab-delay').value, 10) || 0;
  if (sec) toast(`${sec}초 안에 캡처할 창을 앞으로 가져오세요`);
  return sec;
}

async function loadScreen() {
  info = await getJSON('/api/info'); // 권한·모니터 목록도 새로 확인
  renderPerms();
  renderDisplays();
  const delay = grabDelay();
  const blob = await (await api(`/api/screen?display_id=${display.id}&delay=${delay}`)).blob();
  if (screenImg.src) URL.revokeObjectURL(screenImg.src);
  screenImg.src = URL.createObjectURL(blob);
  await screenImg.decode();
  $('stage').classList.add('loaded');
  resizeOverlay();
}

function resizeOverlay() {
  const dpr = window.devicePixelRatio || 1;
  overlay.width = Math.round(screenImg.clientWidth * dpr);
  overlay.height = Math.round(screenImg.clientHeight * dpr);
  overlay.style.width = screenImg.clientWidth + 'px';
  overlay.style.height = screenImg.clientHeight + 'px';
  draw();
}

const scale = () => screenImg.clientWidth / display.w; // 화면 CSS px / 포인트
const toGlobal = (cx, cy) => [Math.round(display.x + cx / scale()), Math.round(display.y + cy / scale())];
const toCanvas = (gx, gy) => [(gx - display.x) * scale(), (gy - display.y) * scale()];

function normRect(d) {
  return { x: Math.min(d.x0, d.x1), y: Math.min(d.y0, d.y1), w: Math.abs(d.x1 - d.x0), h: Math.abs(d.y1 - d.y0) };
}

function draw() {
  const ctx = overlay.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  if (!display || !$('stage').classList.contains('loaded')) return;
  const W = screenImg.clientWidth;
  const H = screenImg.clientHeight;
  const k = scale();

  let r = null;
  if (drag) r = normRect(drag);
  else if (cfg.region) {
    const [x, y] = toCanvas(cfg.region.x, cfg.region.y);
    r = { x, y, w: cfg.region.w * k, h: cfg.region.h * k };
  }
  if (r) {
    ctx.fillStyle = 'rgba(15, 23, 42, 0.55)';
    ctx.fillRect(0, 0, W, H);
    ctx.clearRect(r.x, r.y, r.w, r.h);
    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 2;
    ctx.strokeRect(r.x, r.y, r.w, r.h);
    const text = `${Math.round(r.w / k)} × ${Math.round(r.h / k)} ${info.coord_unit}`;
    ctx.font = '600 12px -apple-system, sans-serif';
    const tw = ctx.measureText(text).width + 10;
    const ty = r.y > 22 ? r.y - 22 : r.y + 4;
    ctx.fillStyle = '#ef4444';
    ctx.fillRect(r.x, ty, tw, 18);
    ctx.fillStyle = '#fff';
    ctx.fillText(text, r.x + 5, ty + 13);
  }

  if (cfg.next_point && (cfg.next_mode === 'click' || mode === 'point')) {
    const [px, py] = toCanvas(cfg.next_point.x, cfg.next_point.y);
    ctx.strokeStyle = '#3b82f6';
    ctx.fillStyle = 'rgba(59, 130, 246, 0.25)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(px, py, 12, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(px - 18, py); ctx.lineTo(px + 18, py);
    ctx.moveTo(px, py - 18); ctx.lineTo(px, py + 18);
    ctx.stroke();
    ctx.fillStyle = '#3b82f6';
    ctx.font = '600 12px -apple-system, sans-serif';
    ctx.fillText('다음', px + 16, py - 14);
  }
}

function localPos(e) {
  const b = overlay.getBoundingClientRect();
  const clamp = (v, max) => Math.max(0, Math.min(max, v));
  return [clamp(e.clientX - b.left, b.width), clamp(e.clientY - b.top, b.height)];
}

overlay.addEventListener('pointerdown', (e) => {
  if (!display || !$('stage').classList.contains('loaded') || (status && status.busy)) return;
  const [x, y] = localPos(e);
  if (mode === 'point') {
    const [gx, gy] = toGlobal(x, y);
    cfg.next_point = { x: gx, y: gy };
    cfg.next_mode = 'click';
    fillForm();
    draw();
    saveSoon();
    toast('다음 버튼 위치를 지정했습니다');
    return;
  }
  drag = { x0: x, y0: y, x1: x, y1: y };
  overlay.setPointerCapture(e.pointerId);
});

overlay.addEventListener('pointermove', (e) => {
  if (!drag) return;
  [drag.x1, drag.y1] = localPos(e);
  draw();
});

overlay.addEventListener('pointerup', () => {
  if (!drag) return;
  const r = normRect(drag);
  drag = null;
  const k = scale();
  if (r.w / k >= 20 && r.h / k >= 20) {
    const [gx, gy] = toGlobal(r.x, r.y);
    cfg.region = { x: gx, y: gy, w: Math.round(r.w / k), h: Math.round(r.h / k) };
    cfg.display_id = display.id;
    saveSoon();
  }
  fillForm();
  draw();
});

overlay.addEventListener('pointercancel', () => { drag = null; draw(); });
window.addEventListener('resize', () => { if ($('stage').classList.contains('loaded')) resizeOverlay(); });

for (const radio of document.querySelectorAll('input[name=mode]')) {
  radio.addEventListener('change', () => {
    mode = radio.value;
    $('hint').textContent = mode === 'region'
      ? '드래그해서 PDF 한 쪽이 될 영역을 지정하세요'
      : "뷰어의 '다음 쪽' 버튼을 클릭해 위치를 지정하세요";
    draw();
  });
}

$('display').addEventListener('change', () => {
  display = info.displays.find((d) => String(d.id) === $('display').value);
  withBusy($('btn-refresh'), loadScreen);
});
$('btn-refresh').addEventListener('click', () => withBusy($('btn-refresh'), loadScreen));

// ------------------------------------------------------------ 테스트

function showPreview(blob, caption, kind = '') {
  if ($('preview').src) URL.revokeObjectURL($('preview').src);
  $('preview').src = URL.createObjectURL(blob);
  $('preview-caption').textContent = caption;
  $('preview-caption').className = 'caption ' + kind;
  $('preview-card').hidden = false;
  $('preview-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

$('btn-test-capture').addEventListener('click', () => withBusy($('btn-test-capture'), async () => {
  readForm();
  if (!cfg.region) throw new Error('캡처 영역을 먼저 지정하세요');
  const res = await send('/api/test/capture?delay=' + grabDelay(), 'POST', cfg.region);
  showPreview(await res.blob(), '이 영역이 PDF 한 쪽이 됩니다');
}));

$('btn-test-turn').addEventListener('click', () => withBusy($('btn-test-turn'), async () => {
  readForm();
  const res = await send('/api/test/turn', 'POST', cfg);
  const changed = res.headers.get('X-Page-Changed') === '1';
  showPreview(
    await res.blob(),
    changed
      ? '✓ 쪽이 넘어갔습니다. 뷰어를 첫 쪽으로 되돌린 뒤 캡처를 시작하세요.'
      : '⚠ 화면이 바뀌지 않았습니다. 넘김 방식(키/버튼)이나 대기 시간을 바꿔 보세요.',
    changed ? 'ok' : 'warn',
  );
}));

// ------------------------------------------------------------ 실행 · 상태

async function startCapture() {
  readForm();
  if (!cfg.region) return toast('캡처 영역을 먼저 지정하세요', 'error');
  if (cfg.next_mode === 'click' && !cfg.next_point) return toast("'다음 버튼 위치'를 먼저 지정하세요", 'error');
  if (missingPermission()
      && !confirm('캡처 PC에 화면 기록 또는 손쉬운 사용 권한이 없는 것으로 보입니다. 그래도 시작할까요?')) return;
  try {
    await send('/api/start', 'POST', { config: cfg });
    toast('시작합니다. 캡처가 끝날 때까지 캡처 PC의 마우스·키보드를 건드리지 마세요');
    await refreshStatus();
    loadJobs();
  } catch (e) {
    toast(e.message, 'error');
  }
}

$('btn-start').addEventListener('click', () => startCapture());
$('btn-stop').addEventListener('click', async () => {
  try {
    await send('/api/stop', 'POST');
    toast('중지 요청을 보냈습니다');
  } catch (e) {
    toast(e.message, 'error');
  }
});

function renderStatus(s) {
  $('state').textContent = STATE_LABELS[s.state] || s.state;
  $('state').className = 'state ' + s.state;
  $('pages').textContent = s.job_id && s.pages ? `${s.pages}쪽` : '';
  $('message').textContent = s.state === 'countdown'
    ? `${s.countdown}초 후 시작 — 캡처 PC를 건드리지 마세요`
    : s.message;
  $('btn-start').disabled = s.busy;
  $('btn-stop').disabled = !s.busy;
  for (const id of ['btn-test-capture', 'btn-test-turn']) {
    if (!$(id).classList.contains('loading')) $(id).disabled = s.busy;
  }
  const key = s.job_id && s.pages ? `${s.job_id}/${s.pages}` : '';
  if (key && key !== thumbKey) {
    thumbKey = key;
    $('last-thumb').src = `/api/jobs/${s.job_id}/thumb/last?k=${encodeURIComponent(key)}`;
    $('last-thumb').hidden = false;
  }
  $('log').textContent = s.log.join('\n');
}

async function refreshStatus() {
  const wasBusy = status && status.busy;
  status = await getJSON('/api/status');
  renderStatus(status);
  if (wasBusy && !status.busy) {
    loadJobs();
    if (status.state === 'done') toast('완료되었습니다. 작업 목록에서 PDF를 받으세요');
    if (status.state === 'error') toast(status.message, 'error');
  }
}

async function pollLoop() {
  try {
    await refreshStatus();
  } catch (_) { /* 연결 끊김 등 — 다음 주기에 재시도 */ }
  setTimeout(pollLoop, status && status.busy ? 1000 : 3000);
}

// ------------------------------------------------------------ 작업 목록

async function loadJobs() {
  let jobs;
  try {
    jobs = await getJSON('/api/jobs');
  } catch (e) {
    return toast(e.message, 'error');
  }
  const body = $('jobs-body');
  if (!jobs.length) {
    body.replaceChildren(el('tr', {}, el('td', { colspan: 5, class: 'empty' }, '아직 작업이 없습니다')));
    return;
  }
  body.replaceChildren(...jobs.map(jobRow));
}

function jobRow(j) {
  const busy = !!(status && status.busy);
  const running = busy && status.job_id === j.id;
  const files = el('div', { class: 'files' },
    j.pdf_size ? el('a', { href: `/api/jobs/${j.id}/pdf`, title: j.pdf }, `PDF ${fmtSize(j.pdf_size)}`) : null,
    j.pdf_size && j.ocr ? el('span', { class: 'tag', title: 'PDF에서 글자 검색·복사가 됩니다' }, 'OCR') : null,
  );
  const actions = el('div', { class: 'actions' },
    el('button', { disabled: busy || !j.pages, onclick: () => rebuildJob(j) }, 'PDF 다시 만들기'),
    el('button', { onclick: () => revealJob(j), title: '캡처 PC에서 저장 폴더를 엽니다' },
      info.platform === 'windows' ? '탐색기에서 보기' : 'Finder에서 보기'),
    el('button', { class: 'link-danger', disabled: running, onclick: () => deleteJob(j) }, '삭제'),
  );
  return el('tr', {},
    el('td', {},
      el('strong', {}, j.title),
      el('div', { class: 'muted' }, j.created),
      el('div', { class: 'muted location' }, `저장 위치: ${j.output_display || '앱 기본 폴더'}`)),
    el('td', {}, `${j.pages}쪽`),
    el('td', {},
      el('span', { class: 'tag ' + j.status }, JOB_LABELS[j.status] || j.status),
      j.message ? el('div', { class: 'muted' }, j.message) : null),
    el('td', {}, files),
    el('td', {}, actions),
  );
}

async function rebuildJob(j) {
  readForm();
  try {
    await send(`/api/jobs/${j.id}/build`, 'POST', { ocr: cfg.ocr, ocr_lang: cfg.ocr_lang });
    toast('PDF를 다시 만드는 중입니다');
    await refreshStatus();
    loadJobs();
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function revealJob(j) {
  try {
    await send(`/api/jobs/${j.id}/reveal`, 'POST');
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function deleteJob(j) {
  const msg = j.output_dir
    ? `'${j.title}'의 캡처 이미지와 작업 기록을 삭제합니다.\n저장 폴더(${j.output_display})의 PDF는 그대로 남습니다.`
    : `'${j.title}'의 캡처 이미지와 PDF를 모두 삭제합니다. 되돌릴 수 없습니다.`;
  if (!confirm(msg)) return;
  try {
    await send(`/api/jobs/${j.id}`, 'DELETE');
    loadJobs();
  } catch (e) {
    toast(e.message, 'error');
  }
}

$('btn-jobs-refresh').addEventListener('click', loadJobs);

// ------------------------------------------------------------ 시작

(async function boot() {
  const token = new URLSearchParams(location.search).get('token');
  if (token) {
    try { await login(token); } catch (_) { /* 아래 init에서 로그인 창이 뜸 */ }
    history.replaceState(null, '', location.pathname);
  }
  try {
    await init();
  } catch (e) {
    if ($('login').hidden) toast(e.message, 'error');
  }
})();
