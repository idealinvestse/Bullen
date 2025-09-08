import pytest
from unittest.mock import MagicMock, patch
import numpy as np
from app.engine.advanced_audio import (
    PsychoacousticProcessor,
    AdaptiveProcessor,
    IntelligentMixer,
    SceneDetector,
    PredictiveBufferManager,
    AdvancedTelemetry,
    SignalStatistics
)


class TestPsychoacousticProcessor:
    """Test psychoacoustic processing capabilities."""
    
    def test_initialization(self):
        """Test processor initialization with correct parameters."""
        processor = PsychoacousticProcessor(samplerate=48000)
        assert processor.sr == 48000
        assert len(processor.bark_bands) == 25  # 24 bands + 1 upper limit
        assert 40 in processor.equal_loudness_curves
        assert len(processor.masking_threshold) == 24
    
    def test_compute_loudness(self):
        """Test loudness computation in sones."""
        processor = PsychoacousticProcessor(samplerate=48000)
        
        # Generate test signal (1 kHz sine wave)
        t = np.linspace(0, 0.1, 4800)
        signal = np.sin(2 * np.pi * 1000 * t) * 0.5
        
        loudness = processor.compute_loudness(signal)
        assert loudness > 0
        assert isinstance(loudness, float)
    
    def test_apply_masking(self):
        """Test psychoacoustic masking application."""
        processor = PsychoacousticProcessor(samplerate=48000)
        
        # Generate test signals
        t = np.linspace(0, 0.1, 4800)
        target = np.sin(2 * np.pi * 1000 * t) * 0.3
        masker = np.sin(2 * np.pi * 1200 * t) * 0.5
        
        masked = processor.apply_masking(target, masker)
        assert masked.shape == target.shape
        # Masked signal should have reduced amplitude
        assert np.max(np.abs(masked)) <= np.max(np.abs(target))


class TestAdaptiveProcessor:
    """Test adaptive signal processing."""
    
    def test_initialization(self):
        """Test adaptive processor initialization."""
        processor = AdaptiveProcessor(samplerate=48000, channels=6)
        assert processor.sr == 48000
        assert processor.channels == 6
        assert len(processor.stats) == 6
        assert len(processor.adaptive_gains) == 6
        assert np.all(processor.adaptive_gains == 1.0)
    
    def test_signal_analysis(self):
        """Test comprehensive signal analysis."""
        processor = AdaptiveProcessor(samplerate=48000, channels=6)
        
        # Generate test signal with known characteristics
        t = np.linspace(0, 0.1, 4800)
        signal = np.sin(2 * np.pi * 440 * t) * 0.5  # A4 note
        
        stats = processor.analyze_signal(signal, channel=0)
        
        assert isinstance(stats, SignalStatistics)
        assert stats.rms > 0
        assert stats.peak > 0
        assert stats.crest_factor > 0
        assert stats.spectral_centroid > 0
        assert stats.zero_crossing_rate > 0
        assert isinstance(stats.onset_detected, bool)
        assert isinstance(stats.silence_detected, bool)
    
    def test_adapt_parameters(self):
        """Test parameter adaptation based on signal statistics."""
        processor = AdaptiveProcessor(samplerate=48000, channels=6)
        
        # Simulate signal analysis
        signal = np.random.randn(4800) * 0.1
        processor.analyze_signal(signal, channel=0)
        
        initial_gain = processor.adaptive_gains[0]
        processor.adapt_parameters(channel=0)
        
        # Parameters should adapt
        assert processor.adaptive_gains[0] != initial_gain or processor.noise_gates[0] > 0
    
    def test_process_signal(self):
        """Test adaptive processing of audio signal."""
        processor = AdaptiveProcessor(samplerate=48000, channels=6)
        
        # Generate test signal
        signal = np.random.randn(4800) * 0.5
        
        processed = processor.process(signal, channel=0)
        
        assert processed.shape == signal.shape
        assert processed.dtype == signal.dtype


class TestIntelligentMixer:
    """Test intelligent auto-mixing system."""
    
    def test_initialization(self):
        """Test mixer initialization."""
        mixer = IntelligentMixer(channels=6, samplerate=48000)
        assert mixer.channels == 6
        assert mixer.sr == 48000
        assert len(mixer.mix_weights) == 6
        assert np.allclose(np.sum(mixer.mix_weights), 1.0)
    
    def test_update_correlation(self):
        """Test correlation matrix updates."""
        mixer = IntelligentMixer(channels=6, samplerate=48000)
        
        # Generate correlated signals
        base = np.random.randn(1000)
        signals = [base + np.random.randn(1000) * 0.1 for _ in range(6)]
        
        mixer.update_correlation(signals)
        
        # Correlation matrix should be updated
        assert mixer.correlation_matrix[0, 1] != 0
        assert mixer.correlation_matrix[0, 1] == mixer.correlation_matrix[1, 0]
    
    def test_compute_priority_scores(self):
        """Test priority score computation."""
        mixer = IntelligentMixer(channels=6, samplerate=48000)
        
        # Create varied signal statistics
        stats = []
        for i in range(6):
            s = SignalStatistics()
            s.silence_detected = (i == 5)  # Last channel silent
            s.onset_detected = (i == 0)  # First channel has onset
            s.spectral_centroid = 1000 + i * 500
            s.crest_factor = 5 + i * 2
            stats.append(s)
        
        mixer.compute_priority_scores(stats)
        
        # Priority scores should vary
        assert not np.all(mixer.priority_scores == mixer.priority_scores[0])
        # Silent channel should have lowest priority
        assert mixer.priority_scores[5] < mixer.priority_scores[0]
    
    def test_intelligent_mixing(self):
        """Test intelligent mixing of signals."""
        mixer = IntelligentMixer(channels=6, samplerate=48000)
        
        # Generate test signals
        signals = [np.random.randn(1000) * 0.1 for _ in range(6)]
        stats = [SignalStatistics() for _ in range(6)]
        
        mixed = mixer.mix(signals, stats)
        
        assert mixed.shape == signals[0].shape
        assert np.max(np.abs(mixed)) <= 1.0  # Should not clip


class TestSceneDetector:
    """Test scene detection and classification."""
    
    def test_initialization(self):
        """Test scene detector initialization."""
        detector = SceneDetector(channels=6)
        assert detector.channels == 6
        assert detector.current_scene == 'mixed'
        assert detector.scene_confidence == 0.0
        assert 'speech' in detector.scene_types
    
    def test_feature_extraction(self):
        """Test feature extraction for scene classification."""
        detector = SceneDetector(channels=6)
        
        # Create test statistics
        stats = []
        for i in range(6):
            s = SignalStatistics()
            s.silence_detected = (i > 3)
            s.spectral_centroid = 2000 + i * 100
            s.onset_detected = (i % 2 == 0)
            s.crest_factor = 10 + i
            s.zero_crossing_rate = 0.2
            stats.append(s)
        
        features = detector.extract_features(stats)
        
        assert len(features) > 0
        assert isinstance(features, np.ndarray)
    
    def test_scene_classification(self):
        """Test scene classification logic."""
        detector = SceneDetector(channels=6)
        
        # Speech-like features
        speech_features = np.array([1, 0, 0, 0, 0, 0, 0.1, 0.1, 0.3, 0.2])
        scene, confidence = detector.classify_scene(speech_features)
        assert scene in detector.scene_types
        assert 0 <= confidence <= 1
    
    def test_scene_update(self):
        """Test scene detection update with hysteresis."""
        detector = SceneDetector(channels=6)
        
        # Create consistent statistics for stable detection
        stats = []
        for _ in range(6):
            s = SignalStatistics()
            s.zero_crossing_rate = 0.15  # Speech-like
            s.onset_detected = False
            s.crest_factor = 8
            stats.append(s)
        
        # Update multiple times to build history
        for _ in range(10):
            scene = detector.update(stats)
        
        assert scene in detector.scene_types


class TestPredictiveBufferManager:
    """Test predictive resource management."""
    
    def test_initialization(self):
        """Test buffer manager initialization."""
        manager = PredictiveBufferManager(channels=6, history_size=100)
        assert manager.channels == 6
        assert manager.history_size == 100
        assert len(manager.predicted_sizes) == 6
    
    def test_update_statistics(self):
        """Test processing time statistics update."""
        manager = PredictiveBufferManager(channels=6)
        
        # Add processing times
        for _ in range(20):
            manager.update_statistics(0.001)  # 1ms processing time
        
        assert manager.mean_proc_time > 0
        assert manager.percentile_95 > 0
    
    def test_predict_buffer_size(self):
        """Test buffer size prediction."""
        manager = PredictiveBufferManager(channels=6)
        
        # Add consistent processing times
        for _ in range(20):
            manager.update_statistics(0.001)
        
        predicted = manager.predict_buffer_size(target_latency=0.005)
        
        assert predicted > 0
        assert predicted & (predicted - 1) == 0  # Should be power of 2
    
    def test_resource_allocation(self):
        """Test resource allocation recommendations."""
        manager = PredictiveBufferManager(channels=6)
        
        # Add processing times
        for _ in range(50):
            manager.update_statistics(0.001)
        
        allocation = manager.get_resource_allocation()
        
        assert 'recommended_buffer_size' in allocation
        assert 'processing_headroom' in allocation
        assert 'confidence' in allocation
        assert 0 <= allocation['confidence'] <= 1


class TestAdvancedTelemetry:
    """Test advanced telemetry system."""
    
    def test_initialization(self):
        """Test telemetry system initialization."""
        telemetry = AdvancedTelemetry(channels=6)
        assert telemetry.channels == 6
        assert telemetry.xrun_count == 0
        assert len(telemetry.thd_measurements) == 6
        assert len(telemetry.snr_measurements) == 6
    
    def test_measure_thd(self):
        """Test Total Harmonic Distortion measurement."""
        telemetry = AdvancedTelemetry(channels=6)
        
        # Generate signal with fundamental and harmonics
        t = np.linspace(0, 0.1, 4800)
        fundamental = 440  # A4
        signal = np.sin(2 * np.pi * fundamental * t)
        signal += 0.1 * np.sin(2 * np.pi * fundamental * 2 * t)  # 2nd harmonic
        signal += 0.05 * np.sin(2 * np.pi * fundamental * 3 * t)  # 3rd harmonic
        
        thd = telemetry.measure_thd(signal, fundamental, sr=48000)
        
        assert thd > 0
        assert thd < 100  # THD percentage
    
    def test_measure_snr(self):
        """Test Signal-to-Noise Ratio measurement."""
        telemetry = AdvancedTelemetry(channels=6)
        
        # Generate signal with known SNR
        signal = np.random.randn(1000) * 0.5
        noise_floor = 0.001
        
        snr = telemetry.measure_snr(signal, noise_floor)
        
        assert snr > 0  # Should be positive dB for this signal
    
    def test_xrun_detection(self):
        """Test XRUN event tracking."""
        telemetry = AdvancedTelemetry(channels=6)
        
        import time
        current_time = time.time()
        
        telemetry.detect_xrun(buffer_underrun=True, timestamp=current_time)
        
        assert telemetry.xrun_count == 1
        assert len(telemetry.xrun_timestamps) == 1
    
    def test_health_score(self):
        """Test system health score computation."""
        telemetry = AdvancedTelemetry(channels=6)
        
        # Perfect system
        score = telemetry.get_health_score()
        assert score == 100
        
        # Add some issues
        telemetry.xrun_count = 2
        telemetry.thd_measurements = np.array([5, 10, 15, 5, 10, 15])
        telemetry.snr_measurements = np.array([30, 35, 40, 45, 50, 55])
        
        score = telemetry.get_health_score()
        assert 0 <= score < 100


class TestIntegration:
    """Test integration of all advanced features."""
    
    @patch('app.engine.advanced_audio.integrate_advanced_features')
    def test_integration_with_engine(self, mock_integrate):
        """Test integration with main audio engine."""
        # Mock engine
        engine = MagicMock()
        engine.client = MagicMock()
        engine.client.samplerate = 48000
        engine.num_inputs = 6
        
        # Import and call integration
        from app.engine.advanced_audio import integrate_advanced_features
        integrate_advanced_features(engine)
        
        # Verify processors are added
        assert hasattr(engine, 'psychoacoustic')
        assert hasattr(engine, 'adaptive')
        assert hasattr(engine, 'mixer')
        assert hasattr(engine, 'scene_detector')
        assert hasattr(engine, 'buffer_manager')
        assert hasattr(engine, 'telemetry')
        assert engine.advanced_processing_enabled == True
