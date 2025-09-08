// Developer Panel component module
// Encapsulates Developer Tools: tabs, API console, WS inspector, sequences, metrics
(function(){
  const dev = {
    paused: false,
    lastTs: null,
    intervals: [], // ms
    maxIntervals: 200,
    count: 0,
    log: [],
    maxLog: 200,
    seqStop: false,
    rmsMax: Array(6).fill(0),
    peakMax: Array(6).fill(0),
  };

  // Set tab
  function setTab(key) {
    const tabs = ['api','ws','seq','metrics'];
    tabs.forEach(t => {
      const btn = document.getElementById('tab-' + t);
      const panel = document.getElementById('tabc-' + t);
      const active = (t === key);
      if (btn) {
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
        btn.tabIndex = active ? 0 : -1;
      }
      if (panel) {
        panel.classList.toggle('active', active);
        if (active) panel.removeAttribute('hidden'); else panel.setAttribute('hidden', '');
      }
    });
    const current = document.getElementById('tab-' + key);
    if (current) current.focus();
  }

  // Send API request
  async function sendApi() {
    const method = document.getElementById('apiMethod').value;
    const path = document.getElementById('apiPath').value.trim() || '/api/state';
    const bodyText = document.getElementById('apiBody').value.trim();
    const statusEl = document.getElementById('apiStatus');
    const timeEl = document.getElementById('apiTime');
    const respEl = document.getElementById('apiResponse');
    let init = { method, headers: {} };
    if (method === 'POST') {
      init.headers['Content-Type'] = 'application/json';
      try { init.body = bodyText ? JSON.stringify(JSON.parse(bodyText)) : '{}'; }
      catch (e) { respEl.textContent = `Body JSON parse error: ${e}`; return; }
    }
    const t0 = performance.now();
    try {
      const r = await fetch(path, init);
      const t1 = performance.now();
      statusEl.value = `${r.status} ${r.statusText}`;
      timeEl.value = (t1 - t0).toFixed(1);
      let txt = await r.text();
      try { const j = JSON.parse(txt); txt = JSON.stringify(j, null, 2); } catch (_) {}
      respEl.textContent = txt;
    } catch (e) {
      statusEl.value = 'ERROR';
      timeEl.value = '-';
      respEl.textContent = String(e);
    }
  }

  // Fill API form
  function fillApi(kind) {
    const method = document.getElementById('apiMethod');
    const path = document.getElementById('apiPath');
    const body = document.getElementById('apiBody');
    if (kind === 'state') { method.value = 'GET'; path.value = '/api/state'; body.value = ''; }
    if (kind === 'config') { method.value = 'GET'; path.value = '/api/config'; body.value = ''; }
    if (kind === 'select') { method.value = 'POST'; path.value = '/api/select/1'; body.value = '{}'; }
    if (kind === 'gaindb') { method.value = 'POST'; path.value = '/api/gain/1'; body.value = '{"gain_db": -6}'; }
    if (kind === 'mute') { method.value = 'POST'; path.value = '/api/mute/1'; body.value = '{"mute": true}'; }
  }

  // On WS message
  function onWs(payload) {
    const now = Date.now();
    if (dev.lastTs != null) {
      const dt = now - dev.lastTs;
      dev.intervals.push(dt);
      if (dev.intervals.length > dev.maxIntervals) dev.intervals.shift();
    }
    dev.lastTs = now;
    dev.count += 1;
    // track maxima
    if (payload && payload.vu_rms && payload.vu_peak) {
      for (let i = 0; i < Math.min(6, payload.vu_rms.length); i++) {
        dev.rmsMax[i] = Math.max(dev.rmsMax[i], payload.vu_rms[i] || 0);
        dev.peakMax[i] = Math.max(dev.peakMax[i], payload.vu_peak[i] || 0);
      }
    }
    // log line
    try {
      const line = JSON.stringify({ t: now, payload });
      dev.log.push(line);
      if (dev.log.length > dev.maxLog) dev.log.shift();
      if (!dev.paused) {
        const el = document.getElementById('wsLog');
        if (el) {
          const ts = new Date(now).toLocaleTimeString();
          const short = JSON.stringify(payload).slice(0, 200);
          const div = document.createElement('div');
          div.textContent = `[${ts}] ${short}`;
          el.appendChild(div);
          // trim dom children
          while (el.childNodes.length > dev.maxLog) el.removeChild(el.firstChild);
          el.scrollTop = el.scrollHeight;
        }
      }
    } catch (_) {}
    updateMetrics();
  }

  // Update metrics
  function updateMetrics() {
    const cnt = dev.count;
    const avg = dev.intervals.length ? (dev.intervals.reduce((a,b)=>a+b,0) / dev.intervals.length) : 0;
    const min = dev.intervals.length ? Math.min(...dev.intervals) : 0;
    const max = dev.intervals.length ? Math.max(...dev.intervals) : 0;
    const rate = avg > 0 ? (1000 / avg) : 0;
    const last = dev.lastTs ? new Date(dev.lastTs).toLocaleTimeString() : '-';
    const wsCount = document.getElementById('wsCount'); if (wsCount) wsCount.value = String(cnt);
    const wsAvg = document.getElementById('wsAvg'); if (wsAvg) wsAvg.value = avg.toFixed(1);
    const wsMinMax = document.getElementById('wsMinMax'); if (wsMinMax) wsMinMax.value = `${min.toFixed(1)} / ${max.toFixed(1)}`;
    const wsRate = document.getElementById('wsRate'); if (wsRate) wsRate.value = rate.toFixed(2);
    const wsLastTime = document.getElementById('wsLastTime'); if (wsLastTime) wsLastTime.value = last;
    // metrics tab content
    const mc = document.getElementById('metricsContent');
    if (mc) {
      const toDb = v => 20 * Math.log10(Math.max(1e-9, v));
      const rmsDb = dev.rmsMax.map(toDb).map(v=>v.toFixed(1));
      const peakDb = dev.peakMax.map(toDb).map(v=>v.toFixed(1));
      mc.textContent = [
        `WS messages: ${cnt}`,
        `Avg dt: ${avg.toFixed(1)} ms (min ${min.toFixed(1)}, max ${max.toFixed(1)})`,
        `Approx rate: ${rate.toFixed(2)} Hz`,
        `Last: ${last}`,
        `RMS max (dB): [${rmsDb.join(', ')}]`,
        `Peak max (dB): [${peakDb.join(', ')}]`
      ].join('\n');
    }
  }

  // Toggle WS pause
  function wsToggle() {
    dev.paused = !dev.paused;
    const b = document.getElementById('wsPauseBtn');
    if (b) b.textContent = dev.paused ? 'Resume logging' : 'Pause logging';
  }

  // Clear WS log
  function wsClear() {
    dev.log = [];
    const el = document.getElementById('wsLog'); if (el) el.innerHTML = '';
  }

  // Copy last WS log
  function wsCopyLast() {
    if (!dev.log.length) return;
    const last = dev.log[dev.log.length - 1];
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(last);
    else {
      const ta = document.createElement('textarea'); ta.value = last; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
    }
  }

  // Download WS log
  function wsDownload() {
    const blob = new Blob([dev.log.join('\n')], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `ws_log_${Date.now()}.jsonl`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // Start sequence
  async function seqStart(kind) {
    dev.seqStop = false;
    const status = document.getElementById('seqStatus');
    const log = (msg) => { if (status) { const line = document.createElement('div'); line.textContent = msg; status.appendChild(line); status.scrollTop = status.scrollHeight; } };
    if (kind === 'cycle') {
      const delay = parseInt(document.getElementById('seqCycleDelay').value || '500', 10);
      const loops = parseInt(document.getElementById('seqCycleLoops').value || '1', 10);
      log(`Start cycle: delay ${delay} ms, loops ${loops}`);
      for (let L = 0; L < loops && !dev.seqStop; L++) {
        for (let i = 1; i <= 6 && !dev.seqStop; i++) {
          await window.BullenUI.selectChannel(i);
          log(`Select CH ${i}`);
          await new Promise(r => setTimeout(r, delay));
        }
      }
      log('Cycle done.');
    }
    if (kind === 'ramp') {
      const delay = parseInt(document.getElementById('seqRampDelay').value || '500', 10);
      const min = parseInt(document.getElementById('seqRampMin').value || '-18', 10);
      const max = parseInt(document.getElementById('seqRampMax').value || '6', 10);
      const step = parseInt(document.getElementById('seqRampStep').value || '2', 10);
      log(`Ramp gains: ${min} to ${max} dB, step ${step} dB, delay ${delay} ms`);
      for (let g = min; g <= max && !dev.seqStop; g += step) {
        for (let i = 1; i <= 6 && !dev.seqStop; i++) {
          await window.BullenUI.setGainDb(i, g);
          log(`CH ${i} gain ${g.toFixed(1)} dB`);
          await new Promise(r => setTimeout(r, delay));
        }
      }
      log('Ramp done.');
    }
    if (kind === 'mute') {
      const delay = parseInt(document.getElementById('seqMuteDelay').value || '500', 10);
      log('Mute all');
      for (let i = 1; i <= 6 && !dev.seqStop; i++) {
        await window.BullenUI.setMute(i, true);
      }
      await new Promise(r => setTimeout(r, delay));
      log('Unmute all');
      for (let i = 1; i <= 6 && !dev.seqStop; i++) {
        await window.BullenUI.setMute(i, false);
      }
      log('Mute/unmute sequence done.');
    }
  }

  // Stop sequence
  function seqStopAll() { dev.seqStop = true; const status = document.getElementById('seqStatus'); if (status) { const d = document.createElement('div'); d.textContent = 'STOP requested'; status.appendChild(d); } }

  window.BullenComponents = window.BullenComponents || {};
  window.BullenComponents.DeveloperPanel = {
    setTab,
    sendApi,
    fillApi,
    onWs,
    updateMetrics,
    wsToggle,
    wsClear,
    wsCopyLast,
    wsDownload,
    seqStart,
    seqStopAll
  };
})();
