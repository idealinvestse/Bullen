import pytest
import numpy as np
from unittest.mock import Mock, patch
import queue
from pathlib import Path
import importlib.util

# Import gain conversion functions directly
from app.engine.audio_engine import db_to_linear, linear_to_db


class TestGainConversions:
    """Test gain conversion functions."""

    def test_db_to_linear_conversion(self):
        """Test dB to linear gain conversion."""
        # Test known values
        assert abs(db_to_linear(0.0) - 1.0) < 1e-6
        assert abs(db_to_linear(-6.0) - 0.501187) < 1e-6  # Approximately -6 dB
        assert abs(db_to_linear(20.0) - 10.0) < 1e-6
        
        # Test that 0 dB returns 1.0
        assert db_to_linear(0.0) == 1.0

    def test_linear_to_db_conversion(self):
        """Test linear to dB gain conversion."""
        # Test known values
        assert abs(linear_to_db(1.0) - 0.0) < 1e-6
        assert abs(linear_to_db(0.5) - (-6.0206)) < 1e-4  # Approximately -6 dB
        assert abs(linear_to_db(10.0) - 20.0) < 1e-6
        
        # Test zero floor protection
        assert linear_to_db(0.0) < -200.0  # Should be a very negative value, not infinity

    def test_roundtrip_conversion(self):
        """Test that dB -> linear -> dB conversion is accurate."""
        test_values_db = [-60.0, -20.0, -6.0, 0.0, 6.0, 12.0, 20.0]
        for db_val in test_values_db:
            linear_val = db_to_linear(db_val)
            db_val_back = linear_to_db(linear_val)
            assert abs(db_val - db_val_back) < 1e-6


# Tests that require JACK - these will be skipped if JACK is not available
# We need to handle the import carefully to avoid ImportError when JACK is not available
JACK_AVAILABLE = importlib.util.find_spec("jack") is not None


@pytest.mark.skipif(not JACK_AVAILABLE, reason="JACK library not available")
class TestAudioEngineWithJACK:
    """Test the AudioEngine class functionality with JACK available."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock configuration for testing."""
        return {
            'samplerate': 48000,
            'frames_per_period': 128,
            'nperiods': 2,
            'inputs': 6,
            'outputs': 8,
            'record': True,
            'recordings_dir': 'test_recordings',
            'record_queue_size': 32,
            'rec_buffer_pool_size': 16,
            'auto_connect_capture': True,
            'auto_connect_playback': True,
            'capture_match': 'capture',
            'playback_match': 'playback',
            'selected_channel': 1,
            'enable_advanced_features': False
        }

    @pytest.fixture
    def mock_jack_client(self):
        """Create a mock JACK client for testing."""
        client = Mock()
        client.samplerate = 48000
        client.blocksize = 128
        client.inports.register = Mock(side_effect=lambda name: Mock(name=name))
        client.outports.register = Mock(side_effect=lambda name: Mock(name=name))
        client.set_process_callback = Mock()
        client.activate = Mock()
        client.deactivate = Mock()
        client.close = Mock()
        client.get_ports = Mock(return_value=[])
        client.connect = Mock()
        return client

    def test_audio_engine_initialization(self, mock_config, mock_jack_client):
        """Test AudioEngine initialization with various configurations."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Check that the engine was initialized with correct values
            assert engine.samplerate == 48000
            assert engine.frames_per_period == 128
            assert engine.nperiods == 2
            assert engine.num_inputs == 6
            assert engine.num_outputs == 8
            assert engine.record_enabled is True
            assert engine.record_dir == Path('test_recordings')
            assert engine.record_queue_size == 32
            
            # Check that input ports were registered
            assert len(engine.inports) == 6
            for i, port in enumerate(engine.inports):
                assert port.name == f'in_{i + 1}'
            
            # Check that output ports were registered
            assert len(engine.outports) == 8
            assert engine.outports[0].name == 'out_l'
            assert engine.outports[1].name == 'out_r'
            for i in range(2, 8):
                assert engine.outports[i].name == f'out_{i + 1}'
            
            # Check initial state
            assert engine.selected_ch == 0  # 1-based config converted to 0-based
            assert len(engine.gains) == 6
            assert len(engine.mutes) == 6
            assert len(engine.vu_peak) == 6
            assert len(engine.vu_rms) == 6
            
            # Check that all gains are initially 1.0
            assert np.allclose(engine.gains, np.ones(6))
            # Check that all mutes are initially False
            assert np.allclose(engine.mutes, np.zeros(6, dtype=bool))

    def test_audio_engine_initialization_stereo_mode(self, mock_config, mock_jack_client):
        """Test AudioEngine initialization with stereo output mode."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        mock_config['outputs'] = 2
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Check that only 2 output ports were registered
            assert len(engine.outports) == 2
            assert engine.outports[0].name == 'out_l'
            assert engine.outports[1].name == 'out_r'

    def test_set_selected_channel(self, mock_config, mock_jack_client):
        """Test setting the selected channel."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Test valid channel selection
            engine.set_selected_channel(3)  # 0-based index
            assert engine.selected_ch == 3
            
            # Test channel selection clamping (too high)
            engine.set_selected_channel(10)
            assert engine.selected_ch == 5  # Should clamp to max index (5 for 6 channels)
            
            # Test channel selection clamping (too low)
            engine.set_selected_channel(-5)
            assert engine.selected_ch == 0  # Should clamp to min index (0)

    def test_set_gain_linear(self, mock_config, mock_jack_client):
        """Test setting linear gain values."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Test setting gain on a valid channel
            engine.set_gain_linear(2, 0.5)  # Channel 3, gain 0.5
            assert engine.gains[2] == 0.5
            
            # Test gain clamping (negative gain)
            engine.set_gain_linear(1, -0.3)  # Channel 2, negative gain
            assert engine.gains[1] == 0.0  # Should clamp to 0.0
            
            # Test invalid channel index
            engine.set_gain_linear(10, 0.8)  # Invalid channel
            # Should not change any gains
            assert engine.gains[2] == 0.5
            assert engine.gains[1] == 0.0

    def test_set_gain_db(self, mock_config, mock_jack_client):
        """Test setting gain values in dB."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Test setting gain in dB
            engine.set_gain_db(0, -6.0)  # Channel 1, -6 dB
            expected_linear = db_to_linear(-6.0)
            assert abs(engine.gains[0] - expected_linear) < 1e-6
            
            # Test another dB value
            engine.set_gain_db(3, 3.0)  # Channel 4, +3 dB
            expected_linear = db_to_linear(3.0)
            assert abs(engine.gains[3] - expected_linear) < 1e-6

    def test_set_mute(self, mock_config, mock_jack_client):
        """Test setting mute status."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Test muting a channel
            engine.set_mute(1, True)  # Mute channel 2
            assert engine.mutes[1] is True
            
            # Test unmuting a channel
            engine.set_mute(1, False)  # Unmute channel 2
            assert engine.mutes[1] is False
            
            # Test invalid channel index
            engine.set_mute(10, True)  # Invalid channel
            # Should not change any mutes
            assert engine.mutes[1] is False

    def test_get_state(self, mock_config, mock_jack_client):
        """Test getting the engine state."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Get the state
            state = engine.get_state()
            
            # Check that all expected keys are present
            expected_keys = [
                'samplerate', 'frames_per_period', 'selected_channel',
                'gains_linear', 'gains_db', 'mutes', 'vu_peak',
                'vu_rms', 'recording', 'rec_dropped_buffers'
            ]
            for key in expected_keys:
                assert key in state
            
            # Check specific values
            assert state['samplerate'] == 48000
            assert state['frames_per_period'] == 128
            assert state['selected_channel'] == 1  # 1-based
            assert len(state['gains_linear']) == 6
            assert len(state['gains_db']) == 6
            assert len(state['mutes']) == 6
            assert len(state['vu_peak']) == 6
            assert len(state['vu_rms']) == 6
            assert state['recording'] is True
            assert len(state['rec_dropped_buffers']) == 6
            assert all(d == 0 for d in state['rec_dropped_buffers'])

    def test_init_recording_buffers(self, mock_config, mock_jack_client):
        """Test initialization of recording buffers."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            engine._init_recording_buffers()
            
            # Check that buffer pool was initialized correctly
            assert len(engine._rec_buffer_pool) == 6  # One pool per input channel
            assert len(engine._rec_pool_indices) == 6
            assert engine._rec_buffer_pool_size == 16
            
            # Check that each channel has the correct number of buffers
            for ch_buffers in engine._rec_buffer_pool:
                assert len(ch_buffers) == 16
                
            # Check that buffers are numpy arrays of correct size and type
            for ch_buffers in engine._rec_buffer_pool:
                for buf in ch_buffers:
                    assert isinstance(buf, np.ndarray)
                    assert buf.dtype == np.float32
                    assert len(buf) == 128  # blocksize

    def test_recording_queue_setup(self, mock_config, mock_jack_client):
        """Test that recording queues are set up correctly."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Check that we have the correct number of queues
            assert len(engine._rec_queues) == 6
            
            # Check that each queue has the correct max size
            for q in engine._rec_queues:
                assert isinstance(q, queue.Queue)
                assert q.maxsize == 32

    def test_vu_meter_initialization(self, mock_config, mock_jack_client):
        """Test that VU meters are initialized correctly."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Check VU meter arrays
            assert len(engine.vu_peak) == 6
            assert len(engine.vu_rms) == 6
            assert len(engine._vu_peak_temp) == 6
            assert len(engine._vu_sumsq_temp) == 6
            assert len(engine._vu_count_temp) == 6
            assert len(engine._vu_peak_smooth) == 6
            assert len(engine._vu_rms_smooth) == 6
            
            # Check that all VU values start at 0
            assert np.allclose(engine.vu_peak, np.zeros(6))
            assert np.allclose(engine.vu_rms, np.zeros(6))
            assert np.allclose(engine._vu_peak_temp, np.zeros(6))
            assert np.allclose(engine._vu_sumsq_temp, np.zeros(6))
            assert np.allclose(engine._vu_count_temp, np.zeros(6))
            assert np.allclose(engine._vu_peak_smooth, np.zeros(6))
            assert np.allclose(engine._vu_rms_smooth, np.zeros(6))

    def test_advanced_features_disabled_by_default(self, mock_config, mock_jack_client):
        """Test that advanced features are disabled by default."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        mock_config['enable_advanced_features'] = False
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Check that advanced features are disabled
            assert engine.advanced_processing_enabled is False
            assert engine.advanced_processors is None

    def test_auto_connect_capture_disabled(self, mock_config, mock_jack_client):
        """Test AudioEngine with auto-connect capture disabled."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        mock_config['auto_connect_capture'] = False
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Auto-connect should not be called
            assert engine.auto_connect_capture is False

    def test_auto_connect_playback_disabled(self, mock_config, mock_jack_client):
        """Test AudioEngine with auto-connect playback disabled."""
        # Import AudioEngine inside the test to avoid ImportError when JACK is not available
        from app.engine.audio_engine import AudioEngine
        
        mock_config['auto_connect_playback'] = False
        with patch('app.engine.audio_engine.jack.Client', return_value=mock_jack_client):
            engine = AudioEngine(mock_config)
            
            # Auto-connect should not be called
            assert engine.auto_connect_playback is False


if __name__ == "__main__":
    pytest.main([__file__])
