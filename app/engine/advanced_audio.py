"""
Advanced Audio Engine Components for Bullen
============================================

This module implements sophisticated signal processing and intelligent audio management
features that elevate the Bullen Audio Router beyond basic routing to a comprehensive
audio processing platform.
"""

import numpy as np
import time
from typing import Dict, List, Tuple
from dataclasses import dataclass
from collections import deque
from scipy import signal, fft
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# PSYCHOACOUSTIC MODELS
# ============================================================================

class PsychoacousticProcessor:
    """
    Implements psychoacoustic models for perceptually-weighted audio processing.
    Based on ITU-R BS.1387 (PEAQ) and ISO 532B loudness models.
    """
    
    def __init__(self, samplerate: int = 48000):
        self.sr = samplerate
        self.bark_bands = self._init_bark_bands()
        self.equal_loudness_curves = self._init_equal_loudness()
        self.masking_threshold = np.zeros(24)  # 24 Bark bands
        
    def _init_bark_bands(self) -> np.ndarray:
        """Initialize critical band (Bark scale) frequency boundaries."""
        # Bark scale: 24 critical bands of human hearing
        return np.array([
            0, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270,
            1480, 1720, 2000, 2320, 2700, 3150, 3700, 4400, 5300,
            6400, 7700, 9500, 12000, 15500
        ])
    
    def _init_equal_loudness(self) -> Dict[int, np.ndarray]:
        """Initialize ISO 226 equal-loudness contours."""
        # Simplified 40, 60, 80 phon curves
        return {
            40: np.array([45, 35, 26, 19, 14, 10, 8, 6, 5, 4, 3, 2, 2, 1, 0, -1, -2, -3, -4, -4, -3, -1, 2, 7]),
            60: np.array([65, 55, 46, 39, 34, 30, 28, 26, 25, 24, 23, 22, 22, 21, 20, 19, 18, 17, 16, 16, 17, 19, 22, 27]),
            80: np.array([85, 75, 66, 59, 54, 50, 48, 46, 45, 44, 43, 42, 42, 41, 40, 39, 38, 37, 36, 36, 37, 39, 42, 47])
        }
    
    def compute_loudness(self, audio: np.ndarray) -> float:
        """
        Compute perceptual loudness using Zwicker's model.
        Returns loudness in sones.
        """
        # FFT to frequency domain
        spectrum = np.abs(fft.rfft(audio))
        freqs = fft.rfftfreq(len(audio), 1/self.sr)
        
        # Map to Bark bands
        bark_power = np.zeros(24)
        for i in range(24):
            mask = (freqs >= self.bark_bands[i]) & (freqs < self.bark_bands[i+1])
            bark_power[i] = np.sum(spectrum[mask] ** 2)
        
        # Apply spreading function (simplified)
        spread = signal.convolve(bark_power, [0.15, 0.7, 0.15], mode='same')
        
        # Convert to specific loudness
        specific_loudness = np.power(spread / 1e-12, 0.23)
        
        # Integrate to total loudness
        return np.sum(specific_loudness) * 0.11
    
    def apply_masking(self, audio: np.ndarray, masker: np.ndarray) -> np.ndarray:
        """
        Apply psychoacoustic masking to reduce perceived noise.
        Uses simultaneous and temporal masking models.
        """
        # Compute masking threshold from masker signal
        masker_spectrum = np.abs(fft.rfft(masker))
        freqs = fft.rfftfreq(len(masker), 1/self.sr)
        
        # Update masking threshold with temporal decay
        decay_factor = 0.95
        self.masking_threshold *= decay_factor
        
        for i in range(24):
            mask = (freqs >= self.bark_bands[i]) & (freqs < self.bark_bands[i+1])
            band_power = np.mean(masker_spectrum[mask] ** 2)
            self.masking_threshold[i] = max(self.masking_threshold[i], band_power)
        
        # Apply masking to target audio
        audio_spectrum = fft.rfft(audio)
        
        for i in range(24):
            mask = (freqs >= self.bark_bands[i]) & (freqs < self.bark_bands[i+1])
            threshold = self.masking_threshold[i] * 0.1  # Masking ratio
            audio_spectrum[mask] *= np.exp(-threshold)
        
        return fft.irfft(audio_spectrum, n=len(audio))


# ============================================================================
# ADAPTIVE SIGNAL PROCESSING
# ============================================================================

@dataclass
class SignalStatistics:
    """Real-time signal statistics for adaptive processing."""
    rms: float = 0.0
    peak: float = 0.0
    crest_factor: float = 0.0
    spectral_centroid: float = 0.0
    zero_crossing_rate: float = 0.0
    spectral_rolloff: float = 0.0
    onset_detected: bool = False
    silence_detected: bool = False
    
    
class AdaptiveProcessor:
    """
    Implements adaptive signal processing that adjusts parameters
    based on real-time signal analysis.
    """
    
    def __init__(self, samplerate: int = 48000, channels: int = 6):
        self.sr = samplerate
        self.channels = channels
        self.stats = [SignalStatistics() for _ in range(channels)]
        self.adaptation_rate = 0.1
        
        # Adaptive parameters per channel
        self.adaptive_gains = np.ones(channels)
        self.noise_gates = np.zeros(channels)
        self.compressor_ratios = np.ones(channels)
        
        # History buffers for temporal analysis
        self.history_size = 10
        self.rms_history = [deque(maxlen=self.history_size) for _ in range(channels)]
        self.onset_detectors = [self._create_onset_detector() for _ in range(channels)]
        
    def _create_onset_detector(self) -> Dict:
        """Create onset detection state for a channel."""
        return {
            'prev_flux': 0.0,
            'threshold': 0.01,
            'adaptation': 0.95
        }
    
    def analyze_signal(self, audio: np.ndarray, channel: int) -> SignalStatistics:
        """
        Perform comprehensive signal analysis for adaptive processing.
        """
        stats = SignalStatistics()
        
        # Time domain analysis
        stats.rms = float(np.sqrt(np.mean(audio ** 2)))
        stats.peak = float(np.max(np.abs(audio)))
        stats.crest_factor = stats.peak / (stats.rms + 1e-10)
        
        # Zero crossing rate (indicates harmonicity)
        zero_crossings = np.sum(np.diff(np.sign(audio)) != 0)
        stats.zero_crossing_rate = zero_crossings / len(audio)
        
        # Frequency domain analysis
        spectrum = np.abs(fft.rfft(audio))
        freqs = fft.rfftfreq(len(audio), 1/self.sr)
        
        # Spectral centroid (brightness indicator)
        magnitude_sum = np.sum(spectrum)
        if magnitude_sum > 0:
            stats.spectral_centroid = np.sum(freqs * spectrum) / magnitude_sum
        
        # Spectral rolloff (high frequency content)
        cumsum = np.cumsum(spectrum)
        rolloff_idx = np.searchsorted(cumsum, 0.85 * cumsum[-1])
        stats.spectral_rolloff = freqs[min(rolloff_idx, len(freqs)-1)]
        
        # Onset detection using spectral flux
        detector = self.onset_detectors[channel]
        flux = np.sum(np.maximum(0, spectrum - detector['prev_flux']))
        threshold = detector['threshold']
        stats.onset_detected = flux > threshold
        
        # Adaptive threshold
        detector['threshold'] = (detector['adaptation'] * threshold + 
                                 (1 - detector['adaptation']) * flux * 2)
        detector['prev_flux'] = spectrum
        
        # Silence detection
        stats.silence_detected = stats.rms < 0.001
        
        # Update history
        self.rms_history[channel].append(stats.rms)
        
        self.stats[channel] = stats
        return stats
    
    def adapt_parameters(self, channel: int):
        """
        Adapt processing parameters based on signal statistics.
        """
        stats = self.stats[channel]
        history = list(self.rms_history[channel])
        
        if not history:
            return
        
        # Adaptive gain control
        target_rms = 0.1  # Target RMS level
        if stats.rms > 0:
            gain_adjustment = target_rms / stats.rms
            # Smooth adaptation to prevent artifacts
            self.adaptive_gains[channel] = (
                (1 - self.adaptation_rate) * self.adaptive_gains[channel] +
                self.adaptation_rate * np.clip(gain_adjustment, 0.1, 10.0)
            )
        
        # Adaptive noise gate
        noise_floor = np.percentile(history, 10) if history else 0
        self.noise_gates[channel] = noise_floor * 2
        
        # Adaptive compression ratio based on crest factor
        if stats.crest_factor > 20:  # High dynamics
            target_ratio = 4.0
        elif stats.crest_factor > 10:
            target_ratio = 2.5
        else:
            target_ratio = 1.5
        
        self.compressor_ratios[channel] = (
            (1 - self.adaptation_rate) * self.compressor_ratios[channel] +
            self.adaptation_rate * target_ratio
        )
    
    def process(self, audio: np.ndarray, channel: int) -> np.ndarray:
        """
        Apply adaptive processing to audio signal.
        """
        # Analyze signal
        stats = self.analyze_signal(audio, channel)
        
        # Adapt parameters
        self.adapt_parameters(channel)
        
        # Apply noise gate
        if stats.rms < self.noise_gates[channel]:
            gate_factor = stats.rms / (self.noise_gates[channel] + 1e-10)
            audio *= gate_factor
        
        # Apply adaptive gain
        audio *= self.adaptive_gains[channel]
        
        # Apply soft-knee compression
        threshold = 0.5
        ratio = self.compressor_ratios[channel]
        
        # Compute envelope
        envelope = np.abs(audio)
        
        # Soft knee compression curve
        knee_width = 0.1
        over_threshold = np.maximum(0, envelope - threshold + knee_width/2)
        knee_factor = over_threshold / (knee_width + 1e-10)
        knee_factor = np.clip(knee_factor, 0, 1)
        
        # Apply compression
        gain_reduction = 1 - knee_factor * (1 - 1/ratio)
        audio *= gain_reduction
        
        return audio


# ============================================================================
# INTELLIGENT AUTO-MIXING SYSTEM
# ============================================================================

class IntelligentMixer:
    """
    Implements intelligent automatic mixing using machine learning-inspired
    algorithms for optimal channel balance.
    """
    
    def __init__(self, channels: int = 6, samplerate: int = 48000):
        self.channels = channels
        self.sr = samplerate
        
        # Mixing weights (learned/adapted)
        self.mix_weights = np.ones(channels) / channels
        self.target_weights = self.mix_weights.copy()
        
        # Correlation matrix for channel relationships
        self.correlation_matrix = np.eye(channels)
        self.correlation_decay = 0.995
        
        # Priority scores based on signal importance
        self.priority_scores = np.ones(channels)
        
        # Spectral masks for frequency-domain mixing
        self.freq_masks = [np.ones(2049) for _ in range(channels)]  # For 4096 FFT
        
        # Scene detection
        self.scene_detector = SceneDetector(channels)
        
    def update_correlation(self, signals: List[np.ndarray]):
        """
        Update correlation matrix between channels for intelligent mixing.
        """
        # Decay old correlations
        self.correlation_matrix *= self.correlation_decay
        
        # Update with new correlations
        for i in range(self.channels):
            for j in range(i+1, self.channels):
                if len(signals[i]) == len(signals[j]):
                    corr = np.corrcoef(signals[i], signals[j])[0, 1]
                    self.correlation_matrix[i, j] = corr
                    self.correlation_matrix[j, i] = corr
    
    def compute_priority_scores(self, stats: List[SignalStatistics]):
        """
        Compute priority scores for each channel based on signal characteristics.
        """
        for i in range(self.channels):
            score = 0.0
            
            # Activity score
            if not stats[i].silence_detected:
                score += 0.3
            
            # Onset bonus (new events)
            if stats[i].onset_detected:
                score += 0.2
            
            # Spectral richness (prefer harmonically rich signals)
            if stats[i].spectral_centroid > 1000:
                score += 0.1
            
            # Crest factor penalty (avoid overly dynamic signals)
            if stats[i].crest_factor < 10:
                score += 0.1
            
            # Consistency bonus (stable RMS)
            score += 0.3 * (1.0 / (1.0 + stats[i].crest_factor / 10))
            
            self.priority_scores[i] = score
    
    def optimize_mix_weights(self):
        """
        Optimize mixing weights using gradient descent-inspired approach.
        """
        # Normalize priority scores
        total_priority = np.sum(self.priority_scores)
        if total_priority > 0:
            self.target_weights = self.priority_scores / total_priority
        
        # Apply correlation-based adjustments
        # Reduce weight for highly correlated channels
        for i in range(self.channels):
            correlation_penalty = 0
            for j in range(self.channels):
                if i != j:
                    correlation_penalty += abs(self.correlation_matrix[i, j])
            
            self.target_weights[i] *= (1.0 - correlation_penalty * 0.1)
        
        # Normalize weights
        self.target_weights /= np.sum(self.target_weights)
        
        # Smooth transition to target weights
        adaptation_rate = 0.05
        self.mix_weights = (
            (1 - adaptation_rate) * self.mix_weights +
            adaptation_rate * self.target_weights
        )
    
    def apply_frequency_masking(self, signals: List[np.ndarray]) -> List[np.ndarray]:
        """
        Apply frequency-domain masking to prevent spectral collisions.
        """
        processed = []
        
        # Convert all signals to frequency domain
        spectra = [fft.rfft(sig, n=4096) for sig in signals]
        
        # Compute spectral masks to minimize overlap
        magnitudes = [np.abs(spec) for spec in spectra]
        
        for i in range(self.channels):
            mask = np.ones_like(magnitudes[i])
            
            for j in range(self.channels):
                if i != j and self.priority_scores[j] > self.priority_scores[i]:
                    # Reduce gain where higher priority channel has energy
                    overlap = np.minimum(magnitudes[i], magnitudes[j])
                    mask *= (1.0 - 0.5 * overlap / (magnitudes[i] + 1e-10))
            
            # Apply mask and convert back to time domain
            masked_spectrum = spectra[i] * mask
            processed.append(fft.irfft(masked_spectrum, n=len(signals[i])))
        
        return processed
    
    def mix(self, signals: List[np.ndarray], stats: List[SignalStatistics]) -> np.ndarray:
        """
        Perform intelligent mixing of input signals.
        """
        # Update correlation matrix
        self.update_correlation(signals)
        
        # Compute priority scores
        self.compute_priority_scores(stats)
        
        # Optimize mixing weights
        self.optimize_mix_weights()
        
        # Apply frequency masking
        processed = self.apply_frequency_masking(signals)
        
        # Mix with optimized weights
        mixed = np.zeros_like(processed[0])
        for i in range(self.channels):
            mixed += processed[i] * self.mix_weights[i]
        
        return mixed


# ============================================================================
# SCENE DETECTION AND CLASSIFICATION
# ============================================================================

class SceneDetector:
    """
    Detects and classifies audio scenes for context-aware processing.
    """
    
    def __init__(self, channels: int = 6):
        self.channels = channels
        self.scene_types = ['speech', 'music', 'ambient', 'mixed']
        self.current_scene = 'mixed'
        self.scene_confidence = 0.0
        
        # Feature buffers for scene analysis
        self.feature_history = deque(maxlen=50)
        
    def extract_features(self, stats: List[SignalStatistics]) -> np.ndarray:
        """
        Extract features for scene classification.
        """
        features = []
        
        # Channel activity pattern
        activity = [0 if s.silence_detected else 1 for s in stats]
        features.extend(activity)
        
        # Average spectral centroid (brightness)
        avg_centroid = np.mean([s.spectral_centroid for s in stats])
        features.append(avg_centroid / 10000)  # Normalize
        
        # Onset density
        onset_count = sum(1 for s in stats if s.onset_detected)
        features.append(onset_count / self.channels)
        
        # Dynamic range indicator
        avg_crest = np.mean([s.crest_factor for s in stats])
        features.append(min(avg_crest / 20, 1.0))
        
        # Zero crossing rate (harmonicity)
        avg_zcr = np.mean([s.zero_crossing_rate for s in stats])
        features.append(avg_zcr)
        
        return np.array(features)
    
    def classify_scene(self, features: np.ndarray) -> Tuple[str, float]:
        """
        Classify audio scene based on features.
        Simple rule-based classifier (could be replaced with ML model).
        """
        # Speech detection rules
        speech_score = 0.0
        if features[-1] > 0.1 and features[-1] < 0.3:  # ZCR in speech range
            speech_score += 0.3
        if features[-3] < 0.5:  # Low onset density
            speech_score += 0.2
        if sum(features[:self.channels]) == 1:  # Single active channel
            speech_score += 0.3
        
        # Music detection rules
        music_score = 0.0
        if features[-3] > 0.3:  # High onset density
            music_score += 0.3
        if features[-2] > 0.5:  # High dynamic range
            music_score += 0.2
        if features[-4] > 0.3:  # High spectral centroid
            music_score += 0.2
        
        # Ambient detection rules
        ambient_score = 0.0
        if features[-2] < 0.3:  # Low dynamic range
            ambient_score += 0.3
        if features[-3] < 0.1:  # Very low onset density
            ambient_score += 0.3
        
        # Determine scene
        scores = {
            'speech': speech_score,
            'music': music_score,
            'ambient': ambient_score,
            'mixed': 0.3  # Default score
        }
        
        scene = max(scores, key=scores.get)
        confidence = scores[scene]
        
        return scene, confidence
    
    def update(self, stats: List[SignalStatistics]) -> str:
        """
        Update scene detection with new statistics.
        """
        features = self.extract_features(stats)
        self.feature_history.append(features)
        
        if len(self.feature_history) >= 5:
            # Average features over time for stability
            avg_features = np.mean(list(self.feature_history)[-5:], axis=0)
            scene, confidence = self.classify_scene(avg_features)
            
            # Hysteresis to prevent rapid switching
            if confidence > self.scene_confidence + 0.1 or scene == self.current_scene:
                self.current_scene = scene
                self.scene_confidence = confidence * 0.9  # Decay factor
        
        return self.current_scene


# ============================================================================
# PREDICTIVE RESOURCE MANAGEMENT
# ============================================================================

class PredictiveBufferManager:
    """
    Uses statistical models to predict buffer requirements and
    optimize resource allocation.
    """
    
    def __init__(self, channels: int = 6, history_size: int = 100):
        self.channels = channels
        self.history_size = history_size
        
        # Processing time history
        self.proc_times = deque(maxlen=history_size)
        
        # Buffer size predictions
        self.predicted_sizes = np.ones(channels) * 128
        
        # Statistical models
        self.mean_proc_time = 0.0
        self.std_proc_time = 0.0
        self.percentile_95 = 0.0
        
    def update_statistics(self, proc_time: float):
        """
        Update processing time statistics.
        """
        self.proc_times.append(proc_time)
        
        if len(self.proc_times) >= 10:
            times = np.array(self.proc_times)
            self.mean_proc_time = np.mean(times)
            self.std_proc_time = np.std(times)
            self.percentile_95 = np.percentile(times, 95)
    
    def predict_buffer_size(self, target_latency: float, safety_factor: float = 1.5) -> int:
        """
        Predict optimal buffer size based on processing statistics.
        """
        if self.percentile_95 > 0:
            # Calculate buffer size for target latency
            predicted = int(target_latency / self.percentile_95)
            # Apply safety factor
            predicted = int(predicted * safety_factor)
            # Ensure power of 2 for FFT efficiency
            return 2 ** int(np.ceil(np.log2(max(predicted, 64))))
        return 128
    
    def get_resource_allocation(self) -> Dict:
        """
        Get recommended resource allocation based on predictions.
        """
        return {
            'recommended_buffer_size': self.predict_buffer_size(0.005),  # 5ms target
            'processing_headroom': max(0, 1.0 - self.percentile_95 / 0.005),
            'mean_processing_time': self.mean_proc_time,
            'processing_variance': self.std_proc_time,
            'confidence': min(len(self.proc_times) / self.history_size, 1.0)
        }


# ============================================================================
# ADVANCED MONITORING AND TELEMETRY
# ============================================================================

class AdvancedTelemetry:
    """
    Comprehensive telemetry system for performance monitoring and optimization.
    """
    
    def __init__(self, channels: int = 6):
        self.channels = channels
        
        # Performance metrics
        self.xrun_count = 0
        self.xrun_timestamps = deque(maxlen=100)
        
        # Quality metrics
        self.thd_measurements = np.zeros(channels)  # Total Harmonic Distortion
        self.snr_measurements = np.zeros(channels)  # Signal-to-Noise Ratio
        self.channel_correlation = np.eye(channels)
        
        # Processing metrics
        self.cpu_usage_history = deque(maxlen=100)
        self.memory_usage_history = deque(maxlen=100)
        
        # Spectral metrics
        self.spectral_peaks = [[] for _ in range(channels)]
        self.frequency_masks = [np.ones(2049) for _ in range(channels)]
        
    def measure_thd(self, signal: np.ndarray, fundamental: float, sr: int) -> float:
        """
        Measure Total Harmonic Distortion.
        """
        spectrum = np.abs(fft.rfft(signal))
        freqs = fft.rfftfreq(len(signal), 1/sr)
        
        # Find fundamental peak
        fund_idx = np.argmin(np.abs(freqs - fundamental))
        fund_power = spectrum[fund_idx] ** 2
        
        # Sum harmonic powers
        harmonic_power = 0
        for n in range(2, 6):  # Up to 5th harmonic
            harm_idx = np.argmin(np.abs(freqs - n * fundamental))
            harmonic_power += spectrum[harm_idx] ** 2
        
        # THD percentage
        return np.sqrt(harmonic_power / fund_power) * 100 if fund_power > 0 else 0
    
    def measure_snr(self, signal: np.ndarray, noise_floor: float = 1e-6) -> float:
        """
        Measure Signal-to-Noise Ratio.
        """
        signal_power = np.mean(signal ** 2)
        return 10 * np.log10(signal_power / noise_floor) if signal_power > 0 else -np.inf
    
    def detect_xrun(self, buffer_underrun: bool, timestamp: float):
        """
        Track XRUN events for reliability monitoring.
        """
        if buffer_underrun:
            self.xrun_count += 1
            self.xrun_timestamps.append(timestamp)
    
    def get_health_score(self) -> float:
        """
        Compute overall system health score (0-100).
        """
        score = 100.0
        
        # Penalize XRUNs
        recent_xruns = sum(1 for t in self.xrun_timestamps if t > time.time() - 60)
        score -= recent_xruns * 10
        
        # Penalize high THD
        avg_thd = np.mean(self.thd_measurements)
        score -= min(avg_thd, 20)
        
        # Penalize low SNR
        avg_snr = np.mean(self.snr_measurements)
        if avg_snr < 40:
            score -= (40 - avg_snr) * 0.5
        
        return max(0, score)


# ============================================================================
# INTEGRATION WITH MAIN ENGINE
# ============================================================================

def integrate_advanced_features(engine):
    """
    Integrate advanced features into the main AudioEngine.
    """
    # Add advanced processors
    engine.psychoacoustic = PsychoacousticProcessor(engine.client.samplerate)
    engine.adaptive = AdaptiveProcessor(engine.client.samplerate, engine.num_inputs)
    engine.mixer = IntelligentMixer(engine.num_inputs, engine.client.samplerate)
    engine.scene_detector = SceneDetector(engine.num_inputs)
    engine.buffer_manager = PredictiveBufferManager(engine.num_inputs)
    engine.telemetry = AdvancedTelemetry(engine.num_inputs)
    
    # Add call center noise suppression
    try:
        from app.engine.noise_suppression import CallCenterNoiseSuppressor
        engine.noise_suppressor = CallCenterNoiseSuppressor(engine.client.samplerate, engine.num_inputs)
        logger.info("Call center noise suppression initialized")
    except ImportError:
        logger.warning("Noise suppression module not available")
        engine.noise_suppressor = None
    
    # Enable advanced processing flag
    engine.advanced_processing_enabled = True
    
    return engine
