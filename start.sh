#!/bin/bash
# Script de démarrage pour le conteneur Docker
# S'assure que toutes les variables d'environnement sont prises en compte

echo "Starting Studio Pack Generator..."
echo "Checking dependencies..."
ffmpeg -version | head -n 1
espeak-ng --version | head -n 1
python --version

echo "Launching Streamlit..."
streamlit run app.py --server.port=8501 --server.address=0.0.0.0
