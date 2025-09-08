// Minimal API module for Bullen UI. Exposes a global BullenAPI with typed helpers.

async function jsonOrThrow(res) {
  const txt = await res.text();
  let data;
  try { data = txt ? JSON.parse(txt) : {}; } catch { data = { error: txt }; }
  if (!res.ok) {
    const err = new Error((data && data.detail) || data.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function getConfig() {
  const r = await fetch('/api/config');
  return jsonOrThrow(r);
}

async function getState() {
  const r = await fetch('/api/state');
  return jsonOrThrow(r);
}

async function selectChannel(ch) {
  const r = await fetch(`/api/select/${ch}`, { method: 'POST' });
  return jsonOrThrow(r);
}

async function setGainDb(ch, gainDb) {
  const r = await fetch(`/api/gain/${ch}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ gain_db: gainDb })
  });
  return jsonOrThrow(r);
}

async function setGainLinear(ch, gainLinear) {
  const r = await fetch(`/api/gain/${ch}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ gain_linear: gainLinear })
  });
  return jsonOrThrow(r);
}

async function setMute(ch, on) {
  const r = await fetch(`/api/mute/${ch}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mute: !!on })
  });
  return jsonOrThrow(r);
}

// Attach to window so legacy inline scripts can use these
window.BullenAPI = {
  getConfig,
  getState,
  selectChannel,
  setGainDb,
  setGainLinear,
  setMute,
};
