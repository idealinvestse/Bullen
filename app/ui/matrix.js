// Configuration
const NUM_INPUTS = 6;
const NUM_OUTPUTS = 8;
let ws = null;
let autoDetectEnabled = false;
let autoDetectThreshold = -30;
let currentRouting = new Map(); // Map of input -> Set of outputs
let inputStates = new Array(NUM_INPUTS).fill({ active: false, level: 0, peak: 0 });
let outputStates = new Array(NUM_OUTPUTS).fill({ active: false, level: 0 });
let peakHoldValues = new Array(NUM_INPUTS).fill(0);
let peakHoldTimers = new Array(NUM_INPUTS).fill(null);

// Initialize UI
function initializeUI() {
    createInputChannels();
    createOutputChannels();
    createRoutingMatrix();
    connectWebSocket();
    loadState();
    setupEventListeners();
}

// Create input channel strips
function createInputChannels() {
    const container = document.getElementById('inputChannels');
    container.innerHTML = '';
    
    for (let i = 0; i < NUM_INPUTS; i++) {
        const channelDiv = document.createElement('div');
        channelDiv.className = 'channel-strip';
        channelDiv.id = `input-${i}`;
        channelDiv.innerHTML = `
            <div class="channel-header">
                <div>
                    <div class="channel-number">IN ${i + 1}</div>
                    <div class="channel-name">Kanal ${i + 1}</div>
                </div>
                <div class="signal-indicator" id="signal-in-${i}">
                    <div class="auto-detect" id="auto-${i}"></div>
                </div>
            </div>
            <div class="vu-meter">
                <div class="vu-meter-fill" id="vu-in-${i}" style="width: 0%"></div>
                <div class="vu-meter-peak" id="peak-in-${i}" style="left: 0%"></div>
            </div>
            <div class="gain-control">
                <input type="range" class="gain-slider" id="gain-${i}" 
                       min="-60" max="12" value="0" 
                       onchange="updateGain(${i}, this.value)">
                <span class="gain-value" id="gain-value-${i}">0 dB</span>
            </div>
            <div class="controls">
                <button class="control-btn" onclick="toggleMute(${i})" id="mute-${i}">Mute</button>
                <button class="control-btn" onclick="soloInput(${i})" id="solo-${i}">Solo</button>
            </div>
        `;
        container.appendChild(channelDiv);
    }
}

// Create output channel strips
function createOutputChannels() {
    const container = document.getElementById('outputChannels');
    container.innerHTML = '';
    
    for (let i = 0; i < NUM_OUTPUTS; i++) {
        const channelDiv = document.createElement('div');
        channelDiv.className = 'output-channel';
        channelDiv.id = `output-${i}`;
        
        let channelName = '';
        if (i === 0) channelName = 'L';
        else if (i === 1) channelName = 'R';
        else channelName = (i + 1).toString();
        
        channelDiv.innerHTML = `
            <div class="channel-number">OUT ${channelName}</div>
            <div class="signal-indicator" id="signal-out-${i}"></div>
            <div class="vu-meter">
                <div class="vu-meter-fill" id="vu-out-${i}" style="width: 0%"></div>
            </div>
        `;
        container.appendChild(channelDiv);
    }
}

// Create routing matrix grid
function createRoutingMatrix() {
    const container = document.getElementById('matrixGrid');
    container.innerHTML = '';
    
    for (let input = 0; input < NUM_INPUTS; input++) {
        for (let output = 0; output < NUM_OUTPUTS; output++) {
            const point = document.createElement('div');
            point.className = 'matrix-point';
            point.id = `matrix-${input}-${output}`;
            point.onclick = () => toggleRoute(input, output);
            
            let outputName = '';
            if (output === 0) outputName = 'L';
            else if (output === 1) outputName = 'R';
            else outputName = (output + 1).toString();
            
            point.innerHTML = `
                <span style="font-size: 16px; font-weight: bold;">
                    ${input + 1}→${outputName}
                </span>
                <span class="route-label">IN${input + 1}→OUT${outputName}</span>
            `;
            container.appendChild(point);
        }
    }
}

// WebSocket connection
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/vu`;
    
    ws = new WebSocket(wsUrl);
    
    ws.onopen = () => {
        console.log('WebSocket connected');
        document.getElementById('connectionStatus').classList.remove('inactive');
        // Get initial state
        fetch('/api/state')
            .then(response => response.json())
            .then(data => {
                updateFromBackendState(data);
            });
        
        // Get current routing matrix
        fetch('/api/matrix/routes')
            .then(response => response.json())
            .then(data => {
                if (data.routes) {
                    // Clear current routing
                    currentRouting.clear();
                    document.querySelectorAll('.matrix-point').forEach(point => {
                        point.classList.remove('active');
                    });
                    
                    // Apply routes from backend
                    data.routes.forEach(route => {
                        if (route.gain > 0.001) {
                            if (!currentRouting.has(route.input)) {
                                currentRouting.set(route.input, new Set());
                            }
                            currentRouting.get(route.input).add(route.output);
                            const matrixPoint = document.getElementById(`matrix-${route.input}-${route.output}`);
                            if (matrixPoint) {
                                matrixPoint.classList.add('active');
                            }
                        }
                    });
                }
            });
    };
    
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        updateVUMeters(data);
        
        if (autoDetectEnabled) {
            detectActiveInputs(data);
        }
    };
    
    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        document.getElementById('connectionStatus').classList.add('inactive');
    };
    
    ws.onclose = () => {
        console.log('WebSocket disconnected');
        document.getElementById('connectionStatus').classList.add('inactive');
        // Reconnect after 2 seconds
        setTimeout(connectWebSocket, 2000);
    };
}

// Update VU meters
function updateVUMeters(data) {
    if (data.vu_peak && data.vu_rms) {
        for (let i = 0; i < NUM_INPUTS; i++) {
            const peak = data.vu_peak[i] || 0;
            const rms = data.vu_rms[i] || 0;
            const peakDb = linearToDb(peak);
            
            // Update input VU meter
            const vuBar = document.getElementById(`vu-in-${i}`);
            if (vuBar) {
                const percent = Math.min(100, Math.max(0, (peakDb + 60) / 72 * 100));
                vuBar.style.width = `${percent}%`;
            }
            
            // Update peak hold
            if (document.getElementById('showPeakHold').checked) {
                updatePeakHold(i, peakDb);
            }
            
            // Update signal indicator
            const signalIndicator = document.getElementById(`signal-in-${i}`);
            if (signalIndicator) {
                if (peak > 0.01) {
                    signalIndicator.classList.add('active');
                    document.getElementById(`input-${i}`).classList.add('active');
                } else {
                    signalIndicator.classList.remove('active');
                    document.getElementById(`input-${i}`).classList.remove('active');
                }
            }
            
            // Update input state
            inputStates[i] = { 
                active: peak > 0.01, 
                level: peak, 
                peak: Math.max(inputStates[i].peak, peak)
            };
        }
    }
    
    // Update output VU meters based on routing
    updateOutputMeters();
}

// Update peak hold indicators
function updatePeakHold(channel, peakDb) {
    const peakBar = document.getElementById(`peak-in-${channel}`);
    if (!peakBar) return;
    
    if (peakDb > peakHoldValues[channel]) {
        peakHoldValues[channel] = peakDb;
        const percent = Math.min(100, Math.max(0, (peakDb + 60) / 72 * 100));
        peakBar.style.left = `${percent}%`;
        
        // Clear previous timer
        if (peakHoldTimers[channel]) {
            clearTimeout(peakHoldTimers[channel]);
        }
        
        // Set new timer to decay peak hold
        peakHoldTimers[channel] = setTimeout(() => {
            peakHoldValues[channel] = -60;
            peakBar.style.left = '0%';
        }, 2000);
    }
}

// Update output meters based on current routing
function updateOutputMeters() {
    for (let output = 0; output < NUM_OUTPUTS; output++) {
        let maxLevel = 0;
        let hasSignal = false;
        
        // Check all inputs routed to this output
        currentRouting.forEach((outputs, input) => {
            if (outputs.has(output) && inputStates[input].active) {
                maxLevel = Math.max(maxLevel, inputStates[input].level);
                hasSignal = true;
            }
        });
        
        // Update output VU meter
        const vuBar = document.getElementById(`vu-out-${output}`);
        if (vuBar) {
            const peakDb = linearToDb(maxLevel);
            const percent = Math.min(100, Math.max(0, (peakDb + 60) / 72 * 100));
            vuBar.style.width = `${percent}%`;
        }
        
        // Update output signal indicator
        const signalIndicator = document.getElementById(`signal-out-${output}`);
        if (signalIndicator) {
            if (hasSignal) {
                signalIndicator.classList.add('active');
            } else {
                signalIndicator.classList.remove('active');
            }
        }
        
        // Update output channel style
        const outputChannel = document.getElementById(`output-${output}`);
        if (outputChannel) {
            if (hasSignal) {
                outputChannel.classList.add('active');
            } else {
                outputChannel.classList.remove('active');
            }
        }
    }
}

// Toggle route between input and output
function toggleRoute(input, output) {
    if (!currentRouting.has(input)) {
        currentRouting.set(input, new Set());
    }
    
    const outputs = currentRouting.get(input);
    const matrixPoint = document.getElementById(`matrix-${input}-${output}`);
    
    if (outputs.has(output)) {
        outputs.delete(output);
        matrixPoint.classList.remove('active');
    } else {
        outputs.add(output);
        matrixPoint.classList.add('active');
    }
    
    // If all outputs are removed, delete the input from routing
    if (outputs.size === 0) {
        currentRouting.delete(input);
    }
    
    // Send routing update to backend
    updateBackendRouting(input, output, outputs.has(output));
    
    // Save state
    saveState();
}

// Clear all routes
function clearAllRoutes() {
    currentRouting.clear();
    document.querySelectorAll('.matrix-point').forEach(point => {
        point.classList.remove('active');
    });
    
    // Clear routes in backend
    fetch('/api/matrix/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    });
    
    saveState();
}

// Update gain
function updateGain(channel, value) {
    document.getElementById(`gain-value-${channel}`).textContent = `${value} dB`;
    
    // Send to backend
    fetch(`/api/gain/${channel + 1}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gain_db: parseFloat(value) })
    });
    
    saveState();
}

// Toggle mute
function toggleMute(channel) {
    const btn = document.getElementById(`mute-${channel}`);
    const isMuted = btn.classList.contains('active');
    
    if (isMuted) {
        btn.classList.remove('active');
    } else {
        btn.classList.add('active');
    }
    
    // Send to backend
    fetch(`/api/mute/${channel + 1}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mute: !isMuted })
    });
}

// Solo input
function soloInput(channel) {
    const btn = document.getElementById(`solo-${channel}`);
    const wasSolo = btn.classList.contains('active');
    
    // Clear all solos
    for (let i = 0; i < NUM_INPUTS; i++) {
        document.getElementById(`solo-${i}`).classList.remove('active');
    }
    
    if (!wasSolo) {
        btn.classList.add('active');
        // Route this input to all outputs
        clearAllRoutes();
        for (let output = 0; output < NUM_OUTPUTS; output++) {
            toggleRoute(channel, output);
        }
    }
}

// Auto-detect active inputs
function detectActiveInputs(data) {
    if (!data.vu_peak) return;
    
    for (let i = 0; i < NUM_INPUTS; i++) {
        const peakDb = linearToDb(data.vu_peak[i] || 0);
        const autoIndicator = document.getElementById(`auto-${i}`);
        
        if (peakDb > autoDetectThreshold) {
            autoIndicator.classList.add('active');
            // Auto-route to default outputs if not already routed
            if (!currentRouting.has(i)) {
                // Route to stereo outputs by default
                if (!currentRouting.has(i) || currentRouting.get(i).size === 0) {
                    toggleRoute(i, 0); // Left
                    toggleRoute(i, 1); // Right
                }
            }
        } else {
            autoIndicator.classList.remove('active');
        }
    }
}

// Toggle auto-detect
function toggleAutoDetect() {
    autoDetectEnabled = !autoDetectEnabled;
    const btn = document.getElementById('autoDetectBtn');
    
    if (autoDetectEnabled) {
        btn.classList.add('active');
        btn.textContent = 'Auto-detect PÅ';
    } else {
        btn.classList.remove('active');
        btn.textContent = 'Auto-detect AV';
        // Clear auto-detect indicators
        for (let i = 0; i < NUM_INPUTS; i++) {
            document.getElementById(`auto-${i}`).classList.remove('active');
        }
    }
    
    document.getElementById('autoDetectEnabled').checked = autoDetectEnabled;
    saveState();
}

// Toggle settings panel
function toggleSettings() {
    const panel = document.getElementById('settingsPanel');
    panel.classList.toggle('open');
}

// Update backend routing
function updateBackendRouting(input, output, enable) {
    // Use the new matrix routing API
    fetch('/api/matrix/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            input: input,
            output: output,
            gain: 1.0,
            enable: enable
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.ok) {
            console.log(`Routing updated: IN${input + 1} -> OUT${output + 1}: ${enable}`);
        }
    })
    .catch(error => {
        console.error('Error updating routing:', error);
    });
}

// Update from backend state
function updateFromBackendState(state) {
    if (state.samplerate) {
        document.getElementById('sampleRate').textContent = `${state.samplerate / 1000} kHz`;
    }
    
    // Update gains
    if (state.gains_db) {
        for (let i = 0; i < NUM_INPUTS && i < state.gains_db.length; i++) {
            const gain = state.gains_db[i];
            document.getElementById(`gain-${i}`).value = gain;
            document.getElementById(`gain-value-${i}`).textContent = `${gain.toFixed(1)} dB`;
        }
    }
    
    // Update mutes
    if (state.mutes) {
        for (let i = 0; i < NUM_INPUTS && i < state.mutes.length; i++) {
            const btn = document.getElementById(`mute-${i}`);
            if (state.mutes[i]) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        }
    }
    
    // Update selected channel (temporary until matrix routing)
    if (state.selected_channel) {
        const selected = state.selected_channel - 1;
        // Don't auto-route in matrix mode, let user decide
    }
}

// Save and load presets
function savePreset() {
    const name = prompt('Ange namn för preset:');
    if (!name) return;
    
    const preset = {
        routing: Array.from(currentRouting.entries()).map(([input, outputs]) => ({
            input,
            outputs: Array.from(outputs)
        })),
        gains: [],
        mutes: [],
        autoDetect: autoDetectEnabled,
        autoDetectThreshold
    };
    
    // Collect gains and mutes
    for (let i = 0; i < NUM_INPUTS; i++) {
        preset.gains.push(parseFloat(document.getElementById(`gain-${i}`).value));
        preset.mutes.push(document.getElementById(`mute-${i}`).classList.contains('active'));
    }
    
    // Save to localStorage
    const presets = JSON.parse(localStorage.getItem('bullenPresets') || '{}');
    presets[name] = preset;
    localStorage.setItem('bullenPresets', JSON.stringify(presets));
    
    alert(`Preset "${name}" sparat!`);
}

function loadPreset() {
    const presets = JSON.parse(localStorage.getItem('bullenPresets') || '{}');
    const names = Object.keys(presets);
    
    if (names.length === 0) {
        alert('Inga sparade presets');
        return;
    }
    
    const name = prompt(`Välj preset:\n${names.join('\n')}`);
    if (!name || !presets[name]) return;
    
    const preset = presets[name];
    
    // Clear current routing
    clearAllRoutes();
    
    // Load routing
    if (preset.routing) {
        preset.routing.forEach(({ input, outputs }) => {
            outputs.forEach(output => {
                toggleRoute(input, output);
            });
        });
    }
    
    // Load gains
    if (preset.gains) {
        preset.gains.forEach((gain, i) => {
            document.getElementById(`gain-${i}`).value = gain;
            updateGain(i, gain);
        });
    }
    
    // Load mutes
    if (preset.mutes) {
        preset.mutes.forEach((muted, i) => {
            const btn = document.getElementById(`mute-${i}`);
            if (muted && !btn.classList.contains('active')) {
                toggleMute(i);
            } else if (!muted && btn.classList.contains('active')) {
                toggleMute(i);
            }
        });
    }
    
    // Load auto-detect settings
    if (preset.autoDetect !== undefined) {
        autoDetectEnabled = !preset.autoDetect; // Toggle to opposite first
        toggleAutoDetect(); // Then toggle to desired state
    }
    if (preset.autoDetectThreshold !== undefined) {
        autoDetectThreshold = preset.autoDetectThreshold;
        document.getElementById('autoDetectThreshold').value = autoDetectThreshold;
        document.getElementById('thresholdValue').textContent = `${autoDetectThreshold} dB`;
    }
}

// Setup event listeners
function setupEventListeners() {
    // Auto-detect settings
    document.getElementById('autoDetectEnabled').addEventListener('change', (e) => {
        if (e.target.checked !== autoDetectEnabled) {
            toggleAutoDetect();
        }
    });
    
    document.getElementById('autoDetectThreshold').addEventListener('input', (e) => {
        autoDetectThreshold = parseFloat(e.target.value);
        document.getElementById('thresholdValue').textContent = `${autoDetectThreshold} dB`;
        saveState();
    });
    
    // Preset selector
    document.getElementById('presetSelect').addEventListener('change', (e) => {
        const value = e.target.value;
        if (!value) return;
        
        clearAllRoutes();
        
        switch(value) {
            case 'stereo':
            case 'mono':
            case 'surround':
                // Use backend preset API
                fetch('/api/matrix/preset', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ preset: value })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.ok) {
                        // Refresh routing display
                        setTimeout(() => {
                            fetch('/api/matrix/routes')
                                .then(response => response.json())
                                .then(data => {
                                    if (data.routes) {
                                        // Update UI to match backend
                                        currentRouting.clear();
                                        document.querySelectorAll('.matrix-point').forEach(point => {
                                            point.classList.remove('active');
                                        });
                                        
                                        data.routes.forEach(route => {
                                            if (route.gain > 0.001) {
                                                if (!currentRouting.has(route.input)) {
                                                    currentRouting.set(route.input, new Set());
                                                }
                                                currentRouting.get(route.input).add(route.output);
                                                const matrixPoint = document.getElementById(`matrix-${route.input}-${route.output}`);
                                                if (matrixPoint) {
                                                    matrixPoint.classList.add('active');
                                                }
                                            }
                                        });
                                    }
                                });
                        }, 100);
                    }
                });
                break;
        }
        
        e.target.value = ''; // Reset selector
    });
}

// Helper functions
function linearToDb(linear) {
    return 20 * Math.log10(Math.max(0.000001, linear));
}

function dbToLinear(db) {
    return Math.pow(10, db / 20);
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', initializeUI);
