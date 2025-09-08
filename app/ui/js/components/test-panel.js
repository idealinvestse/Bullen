// Test Panel component module
// Encapsulates Test view logic: channel controls, WAV generation, feed controls
(function(){
  let N = 6;
  let CONFIG = null;
  const devWavs = { mapping: {}, listedAt: 0 };
  let selectedFile = null;
  let uploadedFiles = [];

  // Helpers
  function logTA(msg) {
    const el = document.getElementById('testAudioLog');
    if (el) {
      const d = document.createElement('div');
      d.textContent = msg;
      el.appendChild(d);
      el.scrollTop = el.scrollHeight;
    }
  }
  function logUpload(msg) {
    const el = document.getElementById('uploadPlayLog');
    if (el) {
      const d = document.createElement('div');
      d.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
      el.appendChild(d);
      el.scrollTop = el.scrollHeight;
    }
  }
  function vToDb(v) {
    const eps = 1e-9;
    return 20 * Math.log10(Math.max(eps, v));
  }
  function normFromDb(db) {
    const minDb = -60, maxDb = 0;
    const cl = Math.max(minDb, Math.min(maxDb, db));
    return (cl - minDb) / (maxDb - minDb);
  }

  // Initialize test view with channel controls
  function initTestView(n) {
    N = n;
    const testChannels = document.getElementById('testChannels');
    testChannels.innerHTML = '';
    for (let i = 1; i <= N; i++) {
      const channel = document.createElement('div');
      channel.className = 'test-channel';
      channel.id = `test-channel-${i}`;
      channel.innerHTML = `
        <h4>
          CH ${i}
          <span class="channel-status status-inactive" id="test-status-${i}">Inaktiv</span>
        </h4>
        <div class="test-meter" id="test-meter-${i}">
          <div class="bar" id="test-bar-${i}"></div>
          <div class="peak" id="test-peak-${i}"></div>
        </div>
        <div class="channel-gain">
          <input type="range" id="test-gain-${i}" min="-60" max="20" step="0.5" value="0" 
                 oninput="window.BullenComponents.TestPanel.updateTestGainDisplay(${i}, parseFloat(this.value))">
          <div class="gain-value" id="test-gain-value-${i}">0.0 dB</div>
        </div>
        <div class="channel-mute">
          <button class="mute" id="test-mute-${i}" onclick="window.BullenComponents.TestPanel.toggleChannelMute(${i})">
            Mute CH ${i}
          </button>
        </div>
      `;
      testChannels.appendChild(channel);
    }
  }

  // Update test view VU
  function updateTestVU(vu) {
    const { vu_rms, vu_peak, selected_channel, mutes, gains_db } = vu;
    const vuSensitivity = parseFloat(document.getElementById('vuSensitivity')?.value || 1);
    const peakHold = document.getElementById('vuPeakHold')?.checked || false;
    for (let i = 1; i <= N; i++) {
      const idx = i - 1;
      const rmsDb = vToDb(vu_rms[idx]) * vuSensitivity;
      const pkDb = vToDb(vu_peak[idx]) * vuSensitivity;
      const bar = document.getElementById(`test-bar-${i}`);
      const peak = document.getElementById(`test-peak-${i}`);
      const status = document.getElementById(`test-status-${i}`);
      const muteBtn = document.getElementById(`test-mute-${i}`);
      const gainSlider = document.getElementById(`test-gain-${i}`);
      const gainValue = document.getElementById(`test-gain-value-${i}`);
      if (bar) bar.style.width = `${(normFromDb(rmsDb) * 100).toFixed(1)}%`;
      if (peak) peak.style.left = `${(normFromDb(pkDb) * 100).toFixed(1)}%`;
      if (status) {
        const isSelected = i === selected_channel;
        const isActive = gains_db[idx] !== 0 || !mutes[idx];
        if (isSelected) {
          status.textContent = 'Vald';
          status.className = 'channel-status status-selected';
        } else if (isActive) {
          status.textContent = 'Aktiv';
          status.className = 'channel-status status-active';
        } else {
          status.textContent = 'Inaktiv';
          status.className = 'channel-status status-inactive';
        }
        status.classList.toggle('active', isSelected);
      }
      if (muteBtn) muteBtn.classList.toggle('on', mutes[idx]);
      if (gainSlider) gainSlider.value = gains_db[idx];
      if (gainValue) gainValue.textContent = `${gains_db[idx].toFixed(1)} dB`;
    }
  }

  // Test gain display update
  function updateTestGainDisplay(ch, val) {
    const el = document.getElementById(`test-gain-value-${ch}`);
    if (el) el.textContent = `${parseFloat(val).toFixed(1)} dB`;
  }

  // Toggle channel mute
  async function toggleChannelMute(ch) {
    const btn = document.getElementById(`test-mute-${ch}`);
    const current = btn ? btn.classList.contains('on') : false;
    try { await window.BullenAPI.setMute(ch, !current); } catch(_) {}
  }

  // Generate test WAVs
  async function genTestWavs() {
    try {
      const r = await fetch('/api/tools/generate_wavs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ seconds: 2.0, samplerate: 48000 }) });
      const j = await r.json();
      if (!r.ok) throw new Error(JSON.stringify(j));
      await refreshWavsList();
    } catch (e) { logTA(`Fel vid generering: ${e}`); }
  }

  // Refresh WAV list
  async function refreshWavsList() {
    try {
      const r = await fetch('/api/tools/wavs');
      const j = await r.json();
      devWavs.mapping = j.files || {};
      devWavs.listedAt = Date.now();
      const chSel = document.getElementById('inFeedChannel');
      const fileSel = document.getElementById('inFeedFile');
      if (chSel && fileSel) {
        chSel.innerHTML = '';
        for (let i = 1; i <= N; i++) {
          const opt = document.createElement('option'); opt.value = String(i); opt.textContent = `CH ${i}`; chSel.appendChild(opt);
        }
        onInChannelChange();
      }
    } catch (e) { logTA(`Fel vid listning av WAVs: ${e}`); }
  }

  // Start feed
  async function startFeedSelected() {
    const ch = parseInt(document.getElementById('inFeedChannel').value || '1', 10);
    const file = document.getElementById('inFeedFile').value;
    const loop = document.getElementById('inFeedLoop').value === 'true';
    const gain = parseFloat(document.getElementById('inFeedGain').value || '0');
    if (!file) { logTA('Ingen fil vald. Klicka "Generera test-WAVs" först.'); return; }
    try {
      const r = await fetch('/api/tools/feed/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file, input: ch, loop, gain_db: gain }) });
      const j = await r.json();
      if (!r.ok) throw new Error(JSON.stringify(j));
      logTA(`Startade feed CH ${ch} (pid=${j.pid})`);
    } catch (e) { logTA(`Fel vid start av feed: ${e}`); }
  }

  // Stop feed
  async function stopFeedSelected() {
    const ch = parseInt(document.getElementById('inFeedChannel').value || '1', 10);
    try {
      const r = await fetch('/api/tools/feed/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ input: ch }) });
      await r.json();
      logTA(`Stoppade feed CH ${ch}`);
    } catch (e) { logTA(`Fel vid stopp: ${e}`); }
  }

  // Stop all feeds
  async function stopAllFeeds() {
    for (let i = 1; i <= N; i++) {
      try { await fetch('/api/tools/feed/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ input: i }) }); } catch {}
    }
    logTA('Stoppa alla feedar: skickat.');
  }

  // Ensure WAVs or generate
  async function ensureWavsOrGenerate() {
    const files = Object.values(devWavs.mapping).flat();
    if (files.length) return;
    await genTestWavs();
    await refreshWavsList();
  }

  // Feed sequence
  async function feedSequence() {
    await ensureWavsOrGenerate();
    const delay = parseInt(document.getElementById('outCycleDelay').value || '800', 10);
    let prev = 0;
    for (let i = 1; i <= N; i++) {
      if (prev) { try { await fetch('/api/tools/feed/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ input: prev }) }); } catch {} }
      const files = devWavs.mapping[i] || [];
      if (!files.length) { logTA(`Ingen WAV för CH ${i}`); continue; }
      const file = files[0];
      await fetch('/api/tools/feed/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file, input: i, loop: true }) });
      logTA(`Feed -> CH ${i}: ${file}`);
      await new Promise(r => setTimeout(r, delay));
      prev = i;
    }
  }

  // On channel change
  function onInChannelChange() {
    const ch = parseInt(document.getElementById('inFeedChannel').value || '1', 10);
    const fileSel = document.getElementById('inFeedFile');
    const files = (devWavs.mapping && devWavs.mapping[ch]) ? devWavs.mapping[ch] : [];
    fileSel.innerHTML = '';
    for (const f of files) { const opt = document.createElement('option'); opt.value = f; opt.textContent = f; fileSel.appendChild(opt); }
  }

  // Initialize uploaded files
  async function refreshUploadedFiles() {
    try {
      const response = await fetch('/api/upload/list');
      const result = await response.json();
      uploadedFiles = result.files || [];
      const select = document.getElementById('uploadedFilesList');
      select.innerHTML = '';
      if (uploadedFiles.length === 0) {
        const opt = document.createElement('option'); opt.value = ''; opt.textContent = 'Inga uppladdade filer'; select.appendChild(opt);
      } else {
        uploadedFiles.forEach(file => {
          const opt = document.createElement('option'); opt.value = file.filename; opt.textContent = `${file.filename} (${file.duration.toFixed(1)}s)`; select.appendChild(opt);
        });
      }
      selectUploadedFile();
    } catch (error) { logUpload(`Fel vid hämtning av filer: ${error.message}`); }
  }

  // Select uploaded file
  function selectUploadedFile() {
    const select = document.getElementById('uploadedFilesList');
    const info = document.getElementById('uploadedFileInfo');
    if (!select.value || uploadedFiles.length === 0) {
      info.textContent = '';
      return;
    }
    const file = uploadedFiles.find(f => f.filename === select.value);
    if (file) info.textContent = `Längd: ${file.duration.toFixed(1)}s, ${file.samplerate} Hz, ${file.channels} kanal(er)`;
  }

  // Delete uploaded file
  async function deleteUploadedFile() {
    const select = document.getElementById('uploadedFilesList');
    const filename = select.value;
    if (!filename) { alert('Välj en fil att ta bort'); return; }
    if (!confirm(`Ta bort ${filename}?`)) return;
    try {
      const response = await fetch(`/api/upload/${filename}`, { method: 'DELETE' });
      if (response.ok) {
        logUpload(`Tog bort: ${filename}`);
        await refreshUploadedFiles();
      } else {
        const error = await response.json();
        logUpload(`Fel vid borttagning: ${error.detail}`);
      }
    } catch (error) { logUpload(`Fel: ${error.message}`); }
  }

  // Start uploaded playback
  async function startUploadedPlayback() {
    const select = document.getElementById('uploadedFilesList');
    const filename = select.value;
    if (!filename) { logUpload('Välj en fil att spela'); return; }
    const file = uploadedFiles.find(f => f.filename === filename);
    if (!file) { logUpload('Fil inte funnen'); return; }
    const channel = parseInt(document.getElementById('uploadPlayChannel').value);
    const loop = document.getElementById('uploadPlayLoop').value === 'true';
    const gain = parseFloat(document.getElementById('uploadPlayGain').value || '0');
    try {
      const response = await fetch('/api/tools/feed/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file: file.path, input: channel, loop, gain_db: gain }) });
      const result = await response.json();
      if (response.ok) logUpload(`▶ Spelar "${file.filename}" på kanal ${channel} (PID: ${result.pid})`);
      else logUpload(`Fel: ${result.detail}`);
    } catch (error) { logUpload(`Fel vid uppspelning: ${error.message}`); }
  }

  // Stop uploaded playback
  async function stopUploadedPlayback() {
    const channel = parseInt(document.getElementById('uploadPlayChannel').value);
    try {
      const response = await fetch('/api/tools/feed/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ input: channel }) });
      if (response.ok) logUpload(`⏹ Stoppade uppspelning på kanal ${channel}`);
    } catch (error) { logUpload(`Fel vid stopp: ${error.message}`); }
  }

  // Handle file select
  function handleFileSelect(event) {
    const files = event.target.files;
    if (files.length > 0) {
      selectedFile = files[0];
      const status = document.getElementById('uploadStatus');
      status.textContent = `Vald fil: ${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(2)} MB)`;
    }
  }

  // Upload selected file
  async function uploadSelectedFile() {
    if (!selectedFile) { document.getElementById('uploadStatus').textContent = 'Ingen fil vald'; return; }
    const status = document.getElementById('uploadStatus');
    status.textContent = 'Laddar upp...';
    const formData = new FormData();
    formData.append('file', selectedFile);
    try {
      const response = await fetch('/api/upload/audio', { method: 'POST', body: formData });
      const result = await response.json();
      if (response.ok) {
        status.textContent = `✅ Uppladdad: ${result.original_name} (${result.duration.toFixed(1)}s @ ${result.samplerate} Hz)`;
        await refreshUploadedFiles();
        selectedFile = null;
        document.getElementById('audioFileInput').value = '';
      } else {
        status.textContent = `❌ Fel: ${result.detail}`;
      }
    } catch (error) { status.textContent = `❌ Uppladdningsfel: ${error.message}`; }
  }

  // Initialize
  setTimeout(() => {
    if (document.getElementById('uploadedFilesList')) refreshUploadedFiles();
  }, 500);

  window.BullenComponents = window.BullenComponents || {};
  window.BullenComponents.TestPanel = {
    initTestView,
    updateTestVU,
    updateTestGainDisplay,
    toggleChannelMute,
    genTestWavs,
    refreshWavsList,
    startFeedSelected,
    stopFeedSelected,
    stopAllFeeds,
    ensureWavsOrGenerate,
    feedSequence,
    onInChannelChange,
    refreshUploadedFiles,
    selectUploadedFile,
    deleteUploadedFile,
    startUploadedPlayback,
    stopUploadedPlayback,
    handleFileSelect,
    uploadSelectedFile
  };
})();
