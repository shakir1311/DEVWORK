# Default application configuration

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "ecg/full_record"  # Full-record ECG data topic

# Sampling rate configuration - PhysioNet CinC 2017 dataset
# Source: https://archive.physionet.org/pn3/challenge/2017/
# "ECG recordings were sampled at 300 Hz and band-pass filtered by the AliveCor device"
# 
# FIXED at 300 Hz - No downsampling
# All recordings use 300 Hz sampling rate (verified from .hea files)
# Values are converted to actual voltage in mV (not normalized)
SAMPLING_RATE = 300  # Hz - Fixed sampling rate (original dataset rate)

# Real-time streaming mode: Data is sent continuously at 300 Hz
# Sample interval: 3.33 ms per sample
# No artificial delays - maintains temporal accuracy for heart rate calculation
# Values are in millivolts (mV) - converted from ADC units using .hea file metadata

