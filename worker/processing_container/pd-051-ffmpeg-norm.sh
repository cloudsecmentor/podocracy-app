#!/bin/bash

# ----------------------------------------------
# Description:
#   This script now does the following, in order:
#     1) Applies a limiter at -4 dB (default).
#     2) Applies dynamic range compression (compand).
#     3) Measures the peak volume after compression.
#     4) Increases loudness so that the new peak is -2 dB, if needed.
#
# Usage:
#   ./script.sh <input_file> [output_file] [compression_threshold]
#
# The [compression_threshold] defaults to -3.5 dB if not supplied.
# ----------------------------------------------

# ========== 1) Parse Inputs ==========

# Input file
input_file="$1"
if [ -z "$input_file" ]; then
  echo "No input file specified."
  exit 1
fi
echo "Input file: $input_file"

# Output file (either given or generate a name with timestamp)
if [ -z "$2" ]; then
  output_file="${input_file}-$(date +%Y%m%d%H%M%S).mp3"
else
  output_file="$2"
fi
echo "Output file: $output_file"

# Compression threshold (used in compand)
if [ -z "$3" ]; then
  compression_threshold="-3.5"
else
  compression_threshold="$3"
fi

# ========== 2) Define Constants ==========

# Limiter threshold (default: -4 dB)
limiter_threshold="-4"

# Final peak target after compression (default: -3.5 dB)
final_peak="-3.5"

# Compand profile for dynamic range compression
profile="compand=attacks=0:points=-80/-80|-20/-20|-10/-7|$compression_threshold/$compression_threshold|0/$compression_threshold|20/$compression_threshold"
echo "Compand profile: [$profile]"

# Temporary filenames
tmp_limited="${output_file%.mp3}-limited.mp3"
tmp_compressed="${output_file%.mp3}-compressed.mp3"

# ========== 3) Apply Limiter at -4 dB ==========

echo "Applying limiter at $limiter_threshold dB..."
ffmpeg -hide_banner -loglevel quiet \
  -i "$input_file" \
  -filter:a "alimiter=limit=${limiter_threshold}dB" \
  -y "$tmp_limited"

# ========== 4) Apply Dynamic Range Compression ==========

echo "Applying dynamic range compression..."
ffmpeg -hide_banner -loglevel quiet \
  -i "$tmp_limited" \
  -filter_complex "$profile" \
  -y "$tmp_compressed"

# ========== 5) Measure Peak After Compression ==========

sound_info_compressed="$(
  ffmpeg -hide_banner -i "$tmp_compressed" \
  -af volumedetect -f null -y /dev/null 2>&1
)"

# Extract just the numeric peak from the "max_volume: -XX dB" line using awk + sed.
peak_volume="$(
  echo "$sound_info_compressed" \
    | awk -F': ' '/max_volume/ {print $2}' \
    | sed 's/ dB//'
)"

# If parsing fails, fall back to a safe value.
if [ -z "$peak_volume" ]; then
  echo "Warning: Could not parse peak volume. Setting peak_volume to -99 dB..."
  peak_volume="-99"
fi

echo "Peak volume after compression: ${peak_volume} dB"

# ========== 6) Boost Audio Until Peak is -2 dB ==========

# difference = (target peak) - (current peak)
difference="$(echo "$final_peak - $peak_volume" | bc 2>/dev/null || echo 0)"

if (( $(echo "$difference > 0" | bc 2>/dev/null) )); then
  echo "Peak is below ${final_peak} dB. Boosting audio by ${difference} dB..."
  ffmpeg -hide_banner -loglevel quiet \
    -i "$tmp_compressed" \
    -filter:a "volume=${difference}dB" \
    -y "$output_file"
else
  echo "Peak is already at or above ${final_peak} dB. No boost needed."
  cp "$tmp_compressed" "$output_file"
fi

# ========== 7) Cleanup Temporary Files ==========

rm -f "$tmp_limited" "$tmp_compressed"

echo "Done!"
