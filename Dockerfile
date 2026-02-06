FROM python:3.11-slim

# Install system dependencies
# ffmpeg: required for audio processing (mp3 conversion)
# espeak-ng: required for Piper TTS phoneme handling
RUN apt-get update && apt-get install -y \
    ffmpeg \
    espeak-ng \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
# Ensure Piper is installed
RUN pip install piper-tts

# Expose Streamlit port
EXPOSE 8501

# Copy start script
COPY start.sh .
RUN chmod +x start.sh

# Healthcheck to verify system dependencies
HEALTHCHECK CMD ffmpeg -version && espeak-ng --version || exit 1

# Run the application
CMD ["./start.sh"]
