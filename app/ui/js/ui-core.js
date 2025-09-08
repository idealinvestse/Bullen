// Core UI module for Bullen Audio Router
// Handles Monitor view: VU updates, channel selection, gain/mute, keyboard shortcuts
(function(){
  let N = 6;
  let CONFIG = null;
  const channelButtons = document.getElementById('channelButtons');
  const meters = document.getElementById('meters');
  const gainMute = document.getElementById('gainMute');
  const selectedEl = document.getElementById('selected');
  const wsStatusEl = document.getElementById('wsStatus');
  let _vuPending = null;
  let _vuRaf = null;
  const _gainDebounced = {};

  // Helpers
  function debounce(fn, wait) {
    let t = null, lastArgs = null;
    return function(...args) {
      lastArgs = args;
      if (t) clearTimeout(t);
      t = setTimeout(() => { t = null; fn.apply(this, lastArgs); }, wait);
    };
  }
  function normFromDb(db) {
    const minDb = -60, maxDb = 0;
    const cl = Math.max(minDb, Math.min(maxDb, db));
    return (cl - minDb) / (maxDb - minDb);
  }
  function vToDb(v) {
    const eps = 1e-9;
    return 20 * Math.log10(Math.max(eps, v));
  }
  function debouncedGain(ch) {
    if (!_gainDebounced[ch]) _gainDebounced[ch] = debounce((v) => setGainDb(ch, v), 150);
    return _gainDebounced[ch];
  }

  // Build UI for N channels
  function buildUI(n) {
    N = n;
    // clear containers first (in case of rebuild)
    channelButtons.innerHTML = '';
    gainMute.innerHTML = '';
    meters.innerHTML = '';
    for (let i = 1; i <= n; i++) {
      const b = document.createElement('button');
      b.textContent = `CH ${i}`;
      b.id = `btn-${i}`;
      b.onclick = () => selectChannel(i);
      b.setAttribute('aria-label', `Select channel ${i}`);
      b.setAttribute('aria-pressed', 'false');
      channelButtons.appendChild(b);

      const card = window.BullenComponents.buildGainMuteCard(i, {
        onMute: (ch, on) => setMute(ch, on),
        onGainDb: (ch, val) => debouncedGain(ch)(val)
      });
      gainMute.appendChild(card);

      const mcard = window.BullenComponents.buildVuMeter(i);
      meters.appendChild(mcard);
    }
  }

  // Update VU displays
  function updateVU(vu) {
    const { vu_rms, vu_peak, selected_channel, mutes, gains_db } = vu;
    highlightSelected(selected_channel);
    for (let i = 1; i <= N; i++) {
      const idx = i - 1;
      const rmsDb = vToDb(vu_rms[idx]);
      const pkDb = vToDb(vu_peak[idx]);
      const bar = document.getElementById(`bar-${i}`);
      const pk = document.getElementById(`peak-${i}`);
      const muteBtn = document.getElementById(`mute-${i}`);
      const gainLabel = document.getElementById(`gaindb-${i}`);
      const gainSlider = document.getElementById(`gain-${i}`);
      if (bar) bar.style.width = `${(normFromDb(rmsDb) * 100).toFixed(1)}%`;
      if (pk) pk.style.left = `${(normFromDb(pkDb) * 100).toFixed(1)}%`;
      if (muteBtn) muteBtn.classList.toggle('on', !!mutes[idx]);
      if (muteBtn) muteBtn.setAttribute('aria-pressed', !!mutes[idx] ? 'true' : 'false');
      if (gainLabel) gainLabel.textContent = `${(gains_db[idx]).toFixed(1)} dB`;
      if (gainSlider) gainSlider.value = gains_db[idx];
    }
  }

  // Coalesce VU updates to animation frames
  function scheduleVU(vu) {
    _vuPending = vu;
    if (_vuRaf) return;
    _vuRaf = requestAnimationFrame(() => {
      _vuRaf = null;
      if (_vuPending) {
        const data = _vuPending; _vuPending = null;
        updateVU(data);
        // Also update test view if visible
        const testView = document.getElementById('testView');
        if (testView && testView.style.display !== 'none') { try { updateTestVU(data); } catch(_){} }
      }
    });
  }

  // Highlight selected channel
  function highlightSelected(ch) {
    selectedEl.textContent = ch;
    for (let i = 1; i <= N; i++) {
      const btn = document.getElementById(`btn-${i}`);
      if (!btn) continue;
      const isSel = i === ch;
      btn.classList.toggle('sel', isSel);
      btn.setAttribute('aria-pressed', isSel ? 'true' : 'false');
    }
  }

  // API calls
  async function selectChannel(ch) {
    try {
      await window.BullenAPI.selectChannel(ch);
    } catch (e) {
      showToast(`Kunde inte välja kanal ${ch}`, 'error');
      console.error('selectChannel failed', e);
    }
  }
  async function setGainDb(ch, db) {
    try {
      await window.BullenAPI.setGainDb(ch, db);
      const el = document.getElementById(`gaindb-${ch}`); if (el) el.textContent = `${db.toFixed(1)} dB`;
    } catch (e) {
      showToast(`Kunde inte sätta gain för CH${ch}`, 'error');
      console.error('setGainDb failed', e);
    }
  }
  async function setMute(ch, on) {
    try {
      await window.BullenAPI.setMute(ch, on);
      const btn = document.getElementById(`mute-${ch}`);
      if (btn) btn.classList.toggle('on', on);
    } catch (e) {
      showToast(`Kunde inte muta CH${ch}`, 'error');
      console.error('setMute failed', e);
    }
  }

  // Initialize state
  async function initState() {
    try {
      const s = await window.BullenAPI.getState();
      updateVU(s);
    } catch (e) {
      if (wsStatusEl) { wsStatusEl.textContent = 'API error: state'; wsStatusEl.style.color = '#ef476f'; }
      showToast('Kunde inte hämta systemstatus', 'error');
      console.error('initState failed', e);
    }
  }

  // Connect WS
  function wsConnect() {
    window.BullenWS.connect({
      onStatus: (s) => {
        if (!wsStatusEl) return;
        if (s === 'connected') { wsStatusEl.textContent = 'WS: connected'; wsStatusEl.style.color = '#3ddc97'; }
        else if (s === 'reconnecting') { wsStatusEl.textContent = 'WS: reconnecting…'; wsStatusEl.style.color = '#ffd166'; }
        else if (s === 'error') { wsStatusEl.textContent = 'WS: error'; wsStatusEl.style.color = '#ef476f'; }
      },
      onMessage: (data) => {
        try { if (typeof devOnWs === 'function') devOnWs(data); } catch(_){ }
        scheduleVU(data);
      }
    });
  }

  // Assume external functions for now
  function updateTestVU(vu) { /* placeholder */ }
  function devOnWs(data) { /* placeholder */ }
  function showToast(msg, type) { /* placeholder */ }

  window.BullenUI = {
    buildUI,
    updateVU,
    scheduleVU,
    highlightSelected,
    selectChannel,
    setGainDb,
    setMute,
    initState,
    wsConnect
  };
})();
