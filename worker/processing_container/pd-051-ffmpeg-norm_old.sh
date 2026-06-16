#!/bin/bash

# Description: This script normalizes the RMS level of an audio file and applies dynamic range compression.
# It accepts an input audio file and optional parameters for the output file name, target RMS normalization level,
# and the compression threshold.
# Usage: ./script.sh <input_file> [output_file] [target_RMS_level] [compression_threshold]
# Reference: https://medium.com/@jud.dagnall/dynamic-range-compression-for-audio-with-ffmpeg-and-compand-621fe2b1a892/

# Assigning the first command line argument as the input file
input_file=$1  
echo "Input file: $input_file"

# Check if the second argument (output file name) is provided
if [ -z "$2" ]; then
    # If not provided, generate a new output file name with a timestamp
    output_file="${input_file}-$(date +%Y%m%d%H%M%S).mp3"
else
    # Use the provided output file name
    output_file=$2
fi
echo "Output file: $output_file"

# Check if the third argument (target RMS level) is provided
if [ -z "$3" ]; then
    # If not provided, set a default target RMS level to -20dB
    normalise_RMS_to="-20"
else
    # Use the provided target RMS level
    normalise_RMS_to="$3"
fi

# Check if the fourth argument (compression threshold) is provided
if [ -z "$4" ]; then
    # If not provided, set a default compression threshold to -3.5dB
    compression_threshold="-3.5"
else
    # Use the provided compression threshold
    compression_threshold="$4"
fi

# Getting the mean volume of the input file using ffmpeg
sound_info="$(ffmpeg -hide_banner -i $input_file -af volumedetect -f null -y null  2>&1)"
mean_volume_line="$(echo "$sound_info" | grep 'mean_volume')"
mean_volume="$(echo "$mean_volume_line" | grep -oE 'mean_volume: -?[0-9]+(\.[0-9]+)?'  | grep -oE ' -?[0-9]+(\.[0-9]+)?')"

# Calculating the adjustment needed to reach the target RMS level
adjustment=$(echo "$normalise_RMS_to - $mean_volume" | bc)dB
echo "Adjustment: $adjustment"

# Temporary output file
output_file_tmp=$output_file-tmp.mp3

# Applying volume adjustment
ffmpeg -hide_banner -loglevel quiet -i $input_file -filter:a "volume=$adjustment" $output_file_tmp -y

# Dynamic range compression profile
profile="compand=attacks=0:points=-80/-80|-20/-20|-10/-7|$compression_threshold/$compression_threshold|0/$compression_threshold|20/$compression_threshold"
echo "Applying profile: [$profile]"

# Applying dynamic range compression
ffmpeg -hide_banner -loglevel quiet -i $output_file_tmp -filter_complex "$profile" $output_file -y

# Removing the temporary file
rm $output_file_tmp