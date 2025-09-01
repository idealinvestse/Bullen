#!/usr/bin/env python3
"""
Audio Output Test Script for Bullen Audio Router
Automatically tests all 8 output channels with clear, audible tones
"""

import sys
import time
import argparse
import numpy as np
import soundfile as sf
from pathlib import Path
from typing import List
import requests

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

def generate_test_tone(frequency: float, duration: float, samplerate: int = 48000, 
                      amplitude: float = 0.3) -> np.ndarray:
    """
    Generate a clear sine wave tone for testing.
    
    Args:
        frequency: Frequency in Hz
        duration: Duration in seconds
        samplerate: Sample rate in Hz
        amplitude: Amplitude (0.0 to 1.0)
    
    Returns:
        Audio data as numpy array
    """
    t = np.linspace(0, duration, int(samplerate * duration), False)
    # Create sine wave with slight envelope to avoid clicks
    tone = amplitude * np.sin(2 * np.pi * frequency * t)
    
    # Apply fade in/out to prevent clicks
    fade_samples = int(0.01 * samplerate)  # 10ms fade
    if len(tone) > 2 * fade_samples:
        # Fade in
        tone[:fade_samples] *= np.linspace(0, 1, fade_samples)
        # Fade out
        tone[-fade_samples:] *= np.linspace(1, 0, fade_samples)
    
    return tone.astype(np.float32)

def generate_channel_announcement(channel_num: int, duration: float = 1.0, 
                                samplerate: int = 48000) -> np.ndarray:
    """
    Generate a distinctive audio pattern for channel identification.
    Uses multiple tones to create a unique "voice" for each channel.
    
    Args:
        channel_num: Channel number (1-8)
        duration: Duration in seconds
        samplerate: Sample rate in Hz
    
    Returns:
        Audio data as numpy array
    """
    # Base frequencies for each channel (musical notes)
    base_frequencies = [
        261.63,  # C4 - Channel 1
        293.66,  # D4 - Channel 2  
        329.63,  # E4 - Channel 3
        349.23,  # F4 - Channel 4
        392.00,  # G4 - Channel 5
        440.00,  # A4 - Channel 6
        493.88,  # B4 - Channel 7
        523.25,  # C5 - Channel 8
    ]
    
    if channel_num < 1 or channel_num > 8:
        channel_num = 1
    
    base_freq = base_frequencies[channel_num - 1]
    
    # Create a distinctive pattern: base tone + harmonic + beeps
    total_samples = int(samplerate * duration)
    audio = np.zeros(total_samples, dtype=np.float32)
    
    # Main tone (70% of duration)
    main_duration = duration * 0.7
    main_tone = generate_test_tone(base_freq, main_duration, samplerate, 0.4)
    audio[:len(main_tone)] += main_tone
    
    # Add harmonic for richness
    harmonic_tone = generate_test_tone(base_freq * 2, main_duration, samplerate, 0.2)
    audio[:len(harmonic_tone)] += harmonic_tone
    
    # Add channel number as beeps (remaining 30% of duration)
    beep_start = int(main_duration * samplerate)
    beep_duration = 0.1  # 100ms per beep
    beep_gap = 0.05     # 50ms gap
    
    for i in range(channel_num):
        beep_start_sample = beep_start + int(i * (beep_duration + beep_gap) * samplerate)
        beep_end_sample = beep_start_sample + int(beep_duration * samplerate)
        
        if beep_end_sample < total_samples:
            beep_tone = generate_test_tone(880, beep_duration, samplerate, 0.3)
            audio[beep_start_sample:beep_start_sample + len(beep_tone)] += beep_tone
    
    return audio

def create_test_audio_files(output_dir: Path, samplerate: int = 48000) -> List[Path]:
    """
    Create test audio files for all 8 channels.
    
    Args:
        output_dir: Directory to save test files
        samplerate: Sample rate for audio files
    
    Returns:
        List of created file paths
    """
    output_dir.mkdir(exist_ok=True)
    created_files = []
    
    print("Generating test audio files...")
    
    for channel in range(1, 9):
        # Create distinctive audio for each channel
        audio_data = generate_channel_announcement(channel, duration=3.0, samplerate=samplerate)
        
        # Save as WAV file
        filename = f"output_test_ch{channel}.wav"
        filepath = output_dir / filename
        sf.write(str(filepath), audio_data, samplerate)
        created_files.append(filepath)
        
        print(f"  Created {filename} - Channel {channel} test audio")
    
    return created_files

def test_output_channel(channel: int, test_file: Path, server_url: str = "http://localhost:8000",
                       duration: float = 3.0) -> bool:
    """
    Test a specific output channel using the Bullen API.
    
    Args:
        channel: Input channel to use (1-6)
        test_file: Path to test audio file
        server_url: Bullen server URL
        duration: How long to play the test
    
    Returns:
        True if test was successful
    """
    try:
        # Upload the test file
        print(f"  Uploading test file: {test_file.name}")
        with open(test_file, 'rb') as f:
            files = {'file': f}
            response = requests.post(f"{server_url}/api/upload/audio", files=files)
        
        if not response.ok:
            print(f"    Failed to upload: {response.text}")
            return False
        
        upload_result = response.json()
        uploaded_filename = upload_result['filename']
        
        # Start playback on the specified input channel
        print(f"  Starting playback on input channel {channel}")
        playback_data = {
            "file": upload_result['path'],
            "input": channel,
            "loop": False,
            "gain_db": 0
        }
        
        response = requests.post(f"{server_url}/api/tools/feed/start", 
                               json=playback_data,
                               headers={'Content-Type': 'application/json'})
        
        if not response.ok:
            print(f"    Failed to start playback: {response.text}")
            return False
        
        playback_result = response.json()
        print(f"    Playback started (PID: {playback_result.get('pid', 'unknown')})")
        
        # Select the input channel (routes to all 8 outputs)
        response = requests.post(f"{server_url}/api/select/{channel}")
        if response.ok:
            print(f"    Selected input channel {channel} → All 8 outputs")
        
        # Wait for playback duration
        time.sleep(duration)
        
        # Stop playback
        stop_data = {"input": channel}
        response = requests.post(f"{server_url}/api/tools/feed/stop",
                               json=stop_data,
                               headers={'Content-Type': 'application/json'})
        
        # Clean up uploaded file
        requests.delete(f"{server_url}/api/upload/{uploaded_filename}")
        
        return True
        
    except Exception as e:
        print(f"    Error testing channel {channel}: {e}")
        return False

def run_comprehensive_output_test(server_url: str = "http://localhost:8000",
                                test_duration: float = 3.0,
                                pause_between: float = 1.0) -> None:
    """
    Run comprehensive test of all 8 output channels.
    
    Args:
        server_url: Bullen server URL
        test_duration: Duration to play each test
        pause_between: Pause between tests
    """
    print("🔊 BULLEN AUDIO ROUTER - OUTPUT CHANNEL TEST")
    print("=" * 50)
    print("Testing all 8 output channels with distinctive audio")
    print(f"Each test plays for {test_duration} seconds")
    print(f"Server: {server_url}")
    print()
    
    # Create test audio files
    script_dir = Path(__file__).parent
    test_dir = script_dir / "output_test_audio"
    test_files = create_test_audio_files(test_dir)
    
    print()
    print("Starting output channel tests...")
    print("Listen to each output channel to verify audio routing:")
    print()
    
    success_count = 0
    total_tests = 8  # We have 8 output channels to test
    
    for i, (channel, filename) in enumerate(test_files, 1):
        print(f"\n--- TEST {i}/8: Channel {channel} ---")
        success = test_output_channel(channel, filename, server_url, test_duration)
        if success:
            success_count += 1
            print("   ✅ Test completed successfully")
        else:
            print("   ❌ Test failed")
        
        if i < total_tests:
            print(f"   Pausing {pause_between}s before next test...")
            time.sleep(pause_between)
        
        print()
    
    # Summary
    print("=" * 50)
    print("🎯 TEST SUMMARY")
    print(f"Successful tests: {success_count}/{total_tests}")
    
    if success_count == total_tests:
        print("✅ ALL TESTS PASSED - All output channels should be working")
        print("   Each input channel routes to all 8 output channels simultaneously")
    else:
        print("⚠️  SOME TESTS FAILED - Check server logs and hardware connections")
    
    print()
    print("🔧 TROUBLESHOOTING:")
    print("- Ensure Audio Injector Octo is properly installed")
    print("- Check JACK is running: 'jack_lsp' should show audioinjector ports")
    print("- Verify physical connections to output channels")
    print("- Check volume levels on connected devices")
    
    # Cleanup
    print(f"\nCleaning up test files in {test_dir}")
    for file in test_files:
        file.unlink(missing_ok=True)
    test_dir.rmdir()

def main():
    """Main entry point for the audio output test script."""
    parser = argparse.ArgumentParser(description='Test Audio Injector Octo output channels')
    parser.add_argument('--server', default='http://localhost:8000', 
                       help='Bullen server URL (default: http://localhost:8000)')
    parser.add_argument('--duration', type=float, default=3.0,
                       help='Test duration per channel in seconds (default: 3.0)')
    parser.add_argument('--pause', type=float, default=1.0,
                       help='Pause between tests in seconds (default: 1.0)')
    parser.add_argument('--generate-only', action='store_true',
                       help='Only generate test audio files, do not run tests')
    
    args = parser.parse_args()
    
    try:
        if args.generate_only:
            # Only generate test files
            script_dir = Path(__file__).parent
            test_dir = script_dir / "output_test_audio"
            test_files = create_test_audio_files(test_dir)
            print(f"✅ Generated {len(test_files)} test audio files in {test_dir}")
            for channel, filename in test_files:
                print(f"  Channel {channel}: {filename}")
        else:
            # Check if server is running
            response = requests.get(f"{args.server}/api/state", timeout=5)
            if not response.ok:
                print(f"❌ Cannot connect to Bullen server at {args.server}")
                print("   Make sure the server is running: python3 Bullen.py")
                return
            
            run_comprehensive_output_test(args.server, args.duration, args.pause)
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Cannot connect to Bullen server at {args.server}")
        print("   Make sure the server is running: python3 Bullen.py")
    except KeyboardInterrupt:
        print("\n⏹️  Test interrupted by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main()
