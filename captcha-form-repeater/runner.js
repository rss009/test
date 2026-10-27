const $ = id => document.getElementById(id);

const el = {
  pageUrl: $('pageUrl'),
  captchaUrl: $('captchaUrl'),
  captchaField: $('captchaField'),
  count: $('count'),
  delay: $('delay'),
  extra: $('extra'),
  marker: $('marker'),
  start: $('start'),
  stop: $('stop'),
  status: $('status'),
  img: $('captchaImg'),
  answer: $('answer'),
  submit: $('submit'),
  log: $('log'),
};

let running = false;
let pendingAnswer = null; // resolver for the captcha the user is currently typing
let imageUrl = null;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function log(msg) {
  el.log.textContent += `${new Date().toLocaleTimeString()}  ${msg}\n`;
  el.log.scrollTop = el.log.scrollHeight;
}

function readConfig() {
  const pageUrl = el.pageUrl.value.trim();
  new URL(pageUrl); // throws on an invalid URL

  const extra = el.extra.value
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => {
      const i = line.indexOf('=');
      if (i < 1) throw new Error(`bad extra field "${line}" (use name=value)`);
      return [line.slice(0, i).trim(), line.slice(i + 1)];
    });

  const count = parseInt(el.count.value, 10);
  if (!(count > 0)) throw new Error('repeat count must be greater than 0');

  return {
    pageUrl,
    captchaUrl: el.captchaUrl.value.trim(),
    captchaField: el.captchaField.value.trim() || 'txtCaptcha',
    extra,
    marker: el.marker.value,
    count,
    delay: Math.max(0, parseInt(el.delay.value, 10) || 0),
  };
}

// Fresh session: remove every cookie the browser holds for the target host,
// so the next request gets a new ASP.NET_SessionId.
async function clearSession(pageUrl) {
  const { hostname } = new URL(pageUrl);
  const cookies = await chrome.cookies.getAll({ domain: hostname });
  for (const c of cookies) {
    const host = c.domain.replace(/^\./, '');
    const scheme = c.secure ? 'https' : 'http';
    await chrome.cookies.remove({
      url: `${scheme}://${host}${c.path}`,
      name: c.name,
      storeId: c.storeId,
    });
  }
  return cookies.length;
}

function parseState(html, pageUrl) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const form = doc.querySelector('form');
  if (!form) throw new Error('no <form> found on the page');

  const fields = [...doc.querySelectorAll('input[type=hidden][name]')]
    .map(input => [input.name, input.value]);

  const action = new URL(form.getAttribute('action') || pageUrl, pageUrl).href;

  const img = doc.querySelector('img[src*="captcha" i]');
  const captchaSrc = img ? new URL(img.getAttribute('src'), pageUrl).href : null;

  return { fields, action, captchaSrc };
}

async function loadCaptcha(url) {
  const res = await fetch(url, { credentials: 'include', cache: 'no-store' });
  if (!res.ok) throw new Error(`captcha HTTP ${res.status}`);
  const blob = await res.blob();
  if (imageUrl) URL.revokeObjectURL(imageUrl);
  imageUrl = URL.createObjectURL(blob);
  el.img.src = imageUrl;
}

// Waits until the user types the captcha they read and presses Submit (or Enter).
// Resolves with null if the run is stopped.
function waitForAnswer() {
  el.answer.value = '';
  el.answer.disabled = false;
  el.submit.disabled = false;
  el.answer.focus();
  return new Promise(resolve => { pendingAnswer = resolve; });
}

function submitAnswer() {
  const value = el.answer.value.trim();
  if (!pendingAnswer || !value) return;
  const resolve = pendingAnswer;
  pendingAnswer = null;
  el.answer.disabled = true;
  el.submit.disabled = true;
  resolve(value);
}

el.submit.onclick = submitAnswer;
el.answer.onkeydown = e => { if (e.key === 'Enter') submitAnswer(); };

async function runOne(n, cfg) {
  const cleared = await clearSession(cfg.pageUrl);
  log(`[${n}] cleared ${cleared} cookie(s), starting a new session`);

  const pageRes = await fetch(cfg.pageUrl, { credentials: 'include', cache: 'no-store' });
  const state = parseState(await pageRes.text(), cfg.pageUrl);

  const vs = state.fields.find(([k]) => k === '__VIEWSTATE');
  const ev = state.fields.find(([k]) => k === '__EVENTVALIDATION');
  log(`[${n}] VIEWSTATE ${vs ? vs[1].length : 'MISSING'} chars, ` +
      `EVENTVALIDATION ${ev ? ev[1].length : 'MISSING'} chars`);

  const captchaUrl = cfg.captchaUrl || state.captchaSrc;
  if (!captchaUrl) throw new Error('no captcha URL set and no captcha <img> on the page');
  await loadCaptcha(captchaUrl); // same cookie jar, so the captcha belongs to this session

  setStatus(`Iteration ${n}/${cfg.count}: type the captcha shown above and press Submit`);
  const answer = await waitForAnswer();
  if (answer === null) return;

  const body = new FormData();
  for (const [k, v] of state.fields) body.append(k, v);
  for (const [k, v] of cfg.extra) body.append(k, v);
  body.append(cfg.captchaField, answer);

  const res = await fetch(state.action, { method: 'POST', body, credentials: 'include', cache: 'no-store' });
  const text = await res.text();
  const m = text.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  const title = m ? m[1].trim() : '';

  let line = `[${n}] POST ${res.status} ${res.url} | title="${title}" | ${text.length} bytes | captcha="${answer}"`;
  if (cfg.marker) line += ` | marker ${text.includes(cfg.marker) ? 'FOUND' : 'missing'}`;
  log(line);
}

function setStatus(msg) {
  el.status.textContent = msg;
}

el.start.onclick = async () => {
  if (running) return;

  let cfg;
  try {
    cfg = readConfig();
  } catch (e) {
    log(`config error: ${e.message}`);
    return;
  }

  running = true;
  el.start.disabled = true;
  el.stop.disabled = false;

  for (let n = 1; n <= cfg.count && running; n++) {
    try {
      await runOne(n, cfg);
    } catch (e) {
      log(`[${n}] error: ${e.message}`);
    }
    if (running && n < cfg.count) await sleep(cfg.delay);
  }

  running = false;
  pendingAnswer = null;
  el.answer.disabled = true;
  el.submit.disabled = true;
  el.start.disabled = false;
  el.stop.disabled = true;
  setStatus('Idle');
  log('finished');
};

el.stop.onclick = () => {
  running = false;
  if (pendingAnswer) {
    const resolve = pendingAnswer;
    pendingAnswer = null;
    resolve(null);
  }
  log('stop requested');
};
