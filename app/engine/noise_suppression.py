"""
Call Center Noise Suppression Module for Bullen
================================================

Advanced noise suppression algorithms optimized for call center environments
where multiple operators work in close proximity.
"""

import numpy as np
from typing import Optional, Dict
from collections import deque
from scipy import fft
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# VOICE ACTIVITY DETECTION (VAD)
# ============================================================================

class VoiceActivityDetector:
    """
    Detects when voice is present in audio signal.
    Essential for noise estimation during silence periods.
    """
    
    def __init__(self, samplerate: int = 48000):
        self.sr = samplerate
        self.frame_size = int(0.02 * samplerate)  # 20ms frames
        
        # Energy-based VAD parameters
        self.energy_threshold = 0.01
        self.energy_history = deque(maxlen=50)
        
        # Zero-crossing rate for speech detection
        self.zcr_threshold_low = 0.1
        self.zcr_threshold_high = 0.25
        
        # Spectral features
        self.spectral_flatness_threshold = 0.5
        
        # Hangover to prevent cutting speech
        self.hangover_frames = 10
        self.hangover_counter = 0
        
    def detect(self, frame: np.ndarray) -> bool:
        """
        Detect if voice is present in frame.
        Uses multiple features for robust detection.
        """
        # Energy detection
        energy = np.mean(frame ** 2)
        self.energy_history.append(energy)
        
        if len(self.energy_history) < 10:
            return False
        
        # Dynamic threshold based on noise floor
        noise_floor = np.percentile(list(self.energy_history), 20)
        dynamic_threshold = max(self.energy_threshold, noise_floor * 3)
        
        energy_vad = energy > dynamic_threshold
        
        # Zero-crossing rate
        zcr = np.sum(np.diff(np.sign(frame)) != 0) / len(frame)
        zcr_vad = self.zcr_threshold_low < zcr < self.zcr_threshold_high
        
        # Spectral flatness (distinguish speech from noise)
        spectrum = np.abs(fft.rfft(frame))
        geometric_mean = np.exp(np.mean(np.log(spectrum + 1e-10)))
        arithmetic_mean = np.mean(spectrum)
        spectral_flatness = geometric_mean / (arithmetic_mean + 1e-10)
        
        spectral_vad = spectral_flatness < self.spectral_flatness_threshold
        
        # Combine detectors
        voice_detected = energy_vad and (zcr_vad or spectral_vad)
        
        # Apply hangover
        if voice_detected:
            self.hangover_counter = self.hangover_frames
            return True
        elif self.hangover_counter > 0:
            self.hangover_counter -= 1
            return True
        
        return False


# ============================================================================
# SPECTRAL SUBTRACTION
# ============================================================================

class SpectralSubtractor:
    """
    Removes stationary background noise using spectral subtraction.
    Effective for consistent background sounds like air conditioning, fans.
    """
    
    def __init__(self, samplerate: int = 48000, fft_size: int = 2048):
        self.sr = samplerate
        self.fft_size = fft_size
        self.hop_size = fft_size // 2
        
        # Noise spectrum estimation
        self.noise_spectrum = np.zeros(fft_size // 2 + 1)
        self.noise_estimation_frames = 0
        self.noise_update_rate = 0.98  # Slow adaptation
        
        # Subtraction parameters
        self.subtraction_factor = 2.0  # Over-subtraction factor
        self.spectral_floor = 0.1  # Minimum spectral value
        
        # Musical noise reduction
        self.smoothing_factor = 0.98
        self.prev_gain = np.ones(fft_size // 2 + 1)
        
    def estimate_noise(self, frame: np.ndarray, is_speech: bool):
        """
        Update noise spectrum estimate during non-speech periods.
        """
        if not is_speech:
            spectrum = np.abs(fft.rfft(frame, n=self.fft_size))
            
            if self.noise_estimation_frames == 0:
                self.noise_spectrum = spectrum
            else:
                # Exponential averaging
                self.noise_spectrum = (self.noise_update_rate * self.noise_spectrum + 
                                      (1 - self.noise_update_rate) * spectrum)
            
            self.noise_estimation_frames += 1
    
    def process(self, frame: np.ndarray, is_speech: bool) -> np.ndarray:
        """
        Apply spectral subtraction to remove noise.
        """
        # Update noise estimate
        self.estimate_noise(frame, is_speech)
        
        if self.noise_estimation_frames < 10:
            return frame  # Not enough noise samples yet
        
        # FFT
        spectrum = fft.rfft(frame, n=self.fft_size)
        magnitude = np.abs(spectrum)
        phase = np.angle(spectrum)
        
        # Spectral subtraction with over-subtraction
        if is_speech:
            # Less aggressive during speech
            subtracted = magnitude - self.subtraction_factor * self.noise_spectrum
        else:
            # More aggressive during silence
            subtracted = magnitude - (self.subtraction_factor * 1.5) * self.noise_spectrum
        
        # Apply spectral floor
        subtracted = np.maximum(subtracted, self.spectral_floor * magnitude)
        
        # Smooth gain to reduce musical noise
        gain = subtracted / (magnitude + 1e-10)
        gain = self.smoothing_factor * self.prev_gain + (1 - self.smoothing_factor) * gain
        self.prev_gain = gain
        
        # Apply gain and reconstruct
        enhanced_spectrum = magnitude * gain * np.exp(1j * phase)
        enhanced = fft.irfft(enhanced_spectrum, n=self.fft_size)
        
        return enhanced[:len(frame)]


# ============================================================================
# ADAPTIVE NOISE CANCELLATION
# ============================================================================

class AdaptiveNoiseCanceller:
    """
    Uses adaptive filtering to cancel correlated noise from multiple channels.
    Ideal for canceling cross-talk from nearby operators.
    """
    
    def __init__(self, filter_length: int = 256, adaptation_rate: float = 0.01):
        self.filter_length = filter_length
        self.mu = adaptation_rate  # LMS step size
        
        # Adaptive filter coefficients
        self.weights = np.zeros(filter_length)
        
        # Reference signal buffer
        self.reference_buffer = np.zeros(filter_length)
        
        # RLS parameters for faster convergence
        self.use_rls = True
        self.lambda_rls = 0.999  # Forgetting factor
        self.delta = 0.01  # Regularization
        self.P = np.eye(filter_length) / self.delta  # Inverse correlation matrix
        
    def cancel(self, primary: np.ndarray, reference: np.ndarray) -> np.ndarray:
        """
        Cancel noise from primary signal using reference signal.
        Primary: Main microphone with speech + noise
        Reference: Noise reference (e.g., from adjacent channel)
        """
        output = np.zeros_like(primary)
        
        for i in range(len(primary)):
            # Shift reference buffer
            self.reference_buffer[1:] = self.reference_buffer[:-1]
            self.reference_buffer[0] = reference[i]
            
            # Filter reference to estimate noise
            noise_estimate = np.dot(self.weights, self.reference_buffer)
            
            # Subtract estimated noise from primary
            output[i] = primary[i] - noise_estimate
            
            # Update filter coefficients
            if self.use_rls:
                # Recursive Least Squares (faster convergence)
                k = self.P @ self.reference_buffer
                k = k / (self.lambda_rls + self.reference_buffer @ k)
                
                self.weights += k * output[i]
                self.P = (self.P - np.outer(k, self.reference_buffer) @ self.P) / self.lambda_rls
            else:
                # Normalized LMS
                norm = np.dot(self.reference_buffer, self.reference_buffer) + 1e-10
                self.weights += self.mu * output[i] * self.reference_buffer / norm
        
        return output


# ============================================================================
# WIENER FILTERING
# ============================================================================

class WienerFilter:
    """
    Optimal linear filter for noise reduction.
    Minimizes mean square error between desired and actual signal.
    """
    
    def __init__(self, fft_size: int = 2048):
        self.fft_size = fft_size
        self.noise_psd = np.zeros(fft_size // 2 + 1)  # Noise power spectral density
        self.speech_psd = np.zeros(fft_size // 2 + 1)  # Speech PSD
        self.alpha_noise = 0.98  # Noise PSD smoothing
        self.alpha_speech = 0.95  # Speech PSD smoothing
        
    def update_psd(self, frame: np.ndarray, is_speech: bool):
        """
        Update power spectral density estimates.
        """
        spectrum = np.abs(fft.rfft(frame, n=self.fft_size)) ** 2
        
        if is_speech:
            # Update speech PSD
            self.speech_psd = self.alpha_speech * self.speech_psd + (1 - self.alpha_speech) * spectrum
        else:
            # Update noise PSD
            self.noise_psd = self.alpha_noise * self.noise_psd + (1 - self.alpha_noise) * spectrum
    
    def filter(self, frame: np.ndarray, is_speech: bool) -> np.ndarray:
        """
        Apply Wiener filtering for optimal noise reduction.
        """
        self.update_psd(frame, is_speech)
        
        # Compute Wiener gain
        spectrum = fft.rfft(frame, n=self.fft_size)
        
        # Wiener gain: H = SNR / (1 + SNR)
        snr = self.speech_psd / (self.noise_psd + 1e-10)
        wiener_gain = snr / (1 + snr)
        
        # Apply gain
        filtered_spectrum = spectrum * wiener_gain
        
        # Inverse FFT
        filtered = fft.irfft(filtered_spectrum, n=self.fft_size)
        
        return filtered[:len(frame)]


# ============================================================================
# CALL CENTER NOISE SUPPRESSOR
# ============================================================================

class CallCenterNoiseSuppressor:
    """
    Comprehensive noise suppression system for call center environments.
    Combines multiple techniques for optimal results.
    """
    
    def __init__(self, samplerate: int = 48000, channels: int = 6):
        self.sr = samplerate
        self.channels = channels
        
        # Initialize components
        self.vad = VoiceActivityDetector(samplerate)
        self.spectral_subtractor = SpectralSubtractor(samplerate)
        self.wiener_filter = WienerFilter()
        self.adaptive_cancellers = [AdaptiveNoiseCanceller() for _ in range(channels)]
        
        # Processing parameters
        self.aggressiveness = 0.5  # 0 = light, 1 = heavy suppression
        self.enable_cross_channel = True  # Use other channels for noise reference
        
        # Comfort noise generation
        self.comfort_noise_level = 0.01
        self.noise_generator = np.random.RandomState(42)
        
    def process_channel(self, audio: np.ndarray, channel: int, 
                        reference_channels: Optional[Dict[int, np.ndarray]] = None) -> np.ndarray:
        """
        Process single channel with comprehensive noise suppression.
        
        Args:
            audio: Input audio from channel
            channel: Channel index
            reference_channels: Audio from other channels for cross-talk cancellation
        """
        processed = audio.copy()
        
        # Step 1: Voice Activity Detection
        is_speech = self.vad.detect(audio)
        
        # Step 2: Adaptive noise cancellation (cross-talk from other channels)
        if self.enable_cross_channel and reference_channels:
            for ref_ch, ref_audio in reference_channels.items():
                if ref_ch != channel:
                    # Use other channels as noise reference
                    processed = self.adaptive_cancellers[ref_ch].cancel(processed, ref_audio)
        
        # Step 3: Spectral subtraction
        processed = self.spectral_subtractor.process(processed, is_speech)
        
        # Step 4: Wiener filtering
        processed = self.wiener_filter.filter(processed, is_speech)
        
        # Step 5: Comfort noise injection during silence
        if not is_speech:
            comfort_noise = self.noise_generator.randn(len(processed)) * self.comfort_noise_level
            processed = processed * 0.1 + comfort_noise  # Heavily attenuate + comfort noise
        
        # Step 6: Automatic gain control to maintain consistent levels
        target_level = 0.1
        current_level = np.sqrt(np.mean(processed ** 2))
        if current_level > 0:
            gain = target_level / current_level
            gain = np.clip(gain, 0.5, 2.0)  # Limit gain range
            processed *= gain
        
        return processed
    
    def process_multi_channel(self, channels_audio: Dict[int, np.ndarray]) -> Dict[int, np.ndarray]:
        """
        Process multiple channels with cross-channel noise cancellation.
        
        Args:
            channels_audio: Dictionary of channel index to audio data
        
        Returns:
            Dictionary of processed audio for each channel
        """
        processed_channels = {}
        
        for ch, audio in channels_audio.items():
            # Get reference channels (all except current)
            reference = {k: v for k, v in channels_audio.items() if k != ch}
            
            # Process with cross-channel cancellation
            processed_channels[ch] = self.process_channel(audio, ch, reference)
        
        return processed_channels
    
    def set_aggressiveness(self, level: float):
        """
        Set noise suppression aggressiveness (0.0 to 1.0).
        """
        self.aggressiveness = np.clip(level, 0.0, 1.0)
        
        # Adjust component parameters
        self.spectral_subtractor.subtraction_factor = 1.0 + 2.0 * self.aggressiveness
        self.adaptive_cancellers[0].mu = 0.005 + 0.02 * self.aggressiveness
        self.comfort_noise_level = 0.02 * (1 - self.aggressiveness)
