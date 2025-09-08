# Audio Engine Improvements for Bullen

## Performance Optimizations

### 1. Pre-allocated Recording Buffers
Replace memory allocation in RT thread with ring buffer pool:

```python
class AudioEngine:
    def __init__(self, config):
        # ... existing init ...
        
        # Pre-allocated recording buffers (ring buffer pool)
        self._rec_buffer_pool = []
        self._rec_pool_size = 32  # Configurable pool size
        for _ in range(self._rec_pool_size):
            self._rec_buffer_pool.append(np.zeros(self.frames_per_period, dtype=np.float32))
        self._rec_pool_index = 0
        
    def _process(self, frames):
        # ... existing code ...
        
        # Recording with pre-allocated buffers
        if self.record_enabled and self._rec_started:
            for i, inport in enumerate(self.inports):
                # Use pre-allocated buffer from pool
                buf_idx = (self._rec_pool_index + i) % self._rec_pool_size
                self._rec_buffer_pool[buf_idx][:frames] = inport.get_array()
                try:
                    self._rec_queues[i].put_nowait(self._rec_buffer_pool[buf_idx][:frames])
                except queue.Full:
                    with self._lock:
                        self._rec_drop_counts[i] += 1
            self._rec_pool_index = (self._rec_pool_index + 1) % self._rec_pool_size
```

### 2. Lock-Free VU Statistics
Replace lock-based VU accumulation with lock-free ring buffer:

```python
import collections

class AudioEngine:
    def __init__(self, config):
        # ... existing init ...
        
        # Lock-free VU ring buffers
        self._vu_ring_size = 16
        self._vu_peak_ring = [collections.deque(maxlen=self._vu_ring_size) for _ in range(self.num_inputs)]
        self._vu_rms_ring = [collections.deque(maxlen=self._vu_ring_size) for _ in range(self.num_inputs)]
        
    def _process(self, frames):
        # ... existing code ...
        
        # Lock-free VU updates
        for i, inport in enumerate(self.inports):
            buf = inport.get_array()
            g = 0.0 if mutes[i] else gains[i]
            post_gain = buf * g
            
            if frames > 0:
                # Append to lock-free ring buffers
                self._vu_peak_ring[i].append(float(np.max(np.abs(post_gain))))
                self._vu_rms_ring[i].append(float(np.sqrt(np.mean(post_gain ** 2))))
```

### 3. Optimized Multi-Channel Output
Use NumPy broadcasting for efficient multi-channel routing:

```python
def _process(self, frames):
    # ... existing code ...
    
    # Optimized output routing
    if route_buf is not None:
        # Create output array once
        out_array = np.tile(route_buf.reshape(-1, 1), (1, self.num_outputs))
        
        # Write to all outputs efficiently
        for i, outport in enumerate(self.outports):
            outport.get_array()[:] = out_array[:, i % out_array.shape[1]]
```

## Feature Enhancements

### 4. Multi-Channel Mixing to Monitor
Allow mixing multiple channels to the monitor output:

```python
class AudioEngine:
    def __init__(self, config):
        # ... existing init ...
        
        # Multi-channel monitor mix
        self.monitor_mix = np.zeros(self.num_inputs, dtype=np.float32)
        self.monitor_mix[self.selected_ch] = 1.0  # Default: only selected channel
        
    def set_monitor_mix(self, ch_index: int, level: float):
        """Set monitor mix level for a channel (0.0 to 1.0)"""
        with self._lock:
            if 0 <= ch_index < self.num_inputs:
                self.monitor_mix[ch_index] = np.clip(level, 0.0, 1.0)
                
    def _process(self, frames):
        # ... existing code ...
        
        # Multi-channel mixing
        mix_buf = np.zeros(frames, dtype=np.float32)
        for i, inport in enumerate(self.inports):
            buf = inport.get_array()
            g = 0.0 if mutes[i] else gains[i]
            post_gain = buf * g
            
            # Add to mix with monitor level
            mix_buf += post_gain * self.monitor_mix[i]
            
        # Route mix to outputs
        for outport in self.outports:
            outport.get_array()[:] = mix_buf
```

### 5. Post-Gain Recording Option
Add configurable recording point:

```python
class AudioEngine:
    def __init__(self, config):
        # ... existing init ...
        
        # Recording configuration
        self.record_post_gain = bool(config.get('record_post_gain', False))
        
    def _process(self, frames):
        # ... existing code ...
        
        # Flexible recording point
        if self.record_enabled and self._rec_started:
            for i, inport in enumerate(self.inports):
                if self.record_post_gain:
                    # Record post-gain signal
                    g = 0.0 if mutes[i] else gains[i]
                    rec_signal = inport.get_array() * g
                else:
                    # Record raw input (current behavior)
                    rec_signal = inport.get_array()
                    
                # Use pre-allocated buffer
                buf_idx = (self._rec_pool_index + i) % self._rec_pool_size
                self._rec_buffer_pool[buf_idx][:frames] = rec_signal
                # ... rest of recording logic
```

### 6. Input Level Monitoring (Pre-Gain)
Add pre-gain VU for input monitoring:

```python
class AudioEngine:
    def __init__(self, config):
        # ... existing init ...
        
        # Pre-gain VU meters
        self.vu_input_peak = np.zeros(self.num_inputs, dtype=np.float32)
        self.vu_input_rms = np.zeros(self.num_inputs, dtype=np.float32)
        
    def _process(self, frames):
        # ... existing code ...
        
        for i, inport in enumerate(self.inports):
            buf = inport.get_array()
            
            # Pre-gain VU (input monitoring)
            if frames > 0:
                self._vu_input_peak_ring[i].append(float(np.max(np.abs(buf))))
                self._vu_input_rms_ring[i].append(float(np.sqrt(np.mean(buf ** 2))))
            
            # Post-gain processing continues...
            g = 0.0 if mutes[i] else gains[i]
            post_gain = buf * g
```

### 7. Configurable Routing Matrix
Add flexible routing configuration:

```python
class AudioEngine:
    def __init__(self, config):
        # ... existing init ...
        
        # Routing matrix: input x output
        self.routing_matrix = np.zeros((self.num_inputs, self.num_outputs), dtype=np.float32)
        # Default: route selected channel to all outputs
        self.routing_matrix[self.selected_ch, :] = 1.0
        
    def set_route(self, input_ch: int, output_ch: int, level: float):
        """Set routing level from input to output (0.0 to 1.0)"""
        with self._lock:
            if 0 <= input_ch < self.num_inputs and 0 <= output_ch < self.num_outputs:
                self.routing_matrix[input_ch, output_ch] = np.clip(level, 0.0, 1.0)
                
    def _process(self, frames):
        # ... existing code ...
        
        # Matrix-based routing
        output_buffers = np.zeros((frames, self.num_outputs), dtype=np.float32)
        
        for i, inport in enumerate(self.inports):
            buf = inport.get_array()
            g = 0.0 if mutes[i] else gains[i]
            post_gain = buf * g
            
            # Apply routing matrix
            for j in range(self.num_outputs):
                if self.routing_matrix[i, j] > 0:
                    output_buffers[:, j] += post_gain * self.routing_matrix[i, j]
        
        # Write to outputs
        for j, outport in enumerate(self.outports):
            outport.get_array()[:] = output_buffers[:, j]
```

## Implementation Priority

1. **High Priority** (RT-safety critical):
   - Pre-allocated recording buffers
   - Lock-free VU statistics
   - Optimized multi-channel output

2. **Medium Priority** (Feature enhancements):
   - Multi-channel mixing to monitor
   - Post-gain recording option
   - Input level monitoring

3. **Low Priority** (Advanced features):
   - Configurable routing matrix
   - Per-channel output gain
   - Sidechain routing

## Testing Strategy

1. **Performance Testing**:
   - Measure callback execution time
   - Monitor XRUN occurrences
   - Verify memory allocation patterns

2. **Functional Testing**:
   - Multi-channel mix verification
   - Recording quality validation
   - VU meter accuracy

3. **Stress Testing**:
   - Rapid channel switching
   - Full 6-channel recording
   - Maximum gain/routing load

## Configuration Updates

Add to `config.yaml`:
```yaml
# Performance
rec_buffer_pool_size: 32
vu_ring_buffer_size: 16

# Features
record_post_gain: false
enable_multi_mix: false
enable_input_monitoring: true

# Routing
routing_mode: "simple"  # or "matrix"
default_routes:
  - {input: 1, output: [1, 2], level: 1.0}
```
