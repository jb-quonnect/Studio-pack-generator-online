"""
Studio Pack Generator Online

Application web pour générer des packs audio compatibles "Studio Pack"
pour les boîtes à histoires (Lunii, Telmi, etc.)

Fonctionnalités:
- Import de fichiers audio/images ou flux RSS
- Conversion automatique au format cible (MP3 44100Hz Mono, PNG 320x240)
- Synthèse vocale pour les menus de navigation
- Simulateur de navigation avant téléchargement
- Mode éphémère (suppression des fichiers après génération)
"""

import streamlit as st
import os
import tempfile
import shutil
import logging
import zipfile
import subprocess
from pathlib import Path

# Configuration logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import des modules
from modules.session_manager import get_session_manager, reset_session_manager
from modules.audio_processor import is_ffmpeg_available
from modules.utils import is_audio_file, is_image_file, ensure_dir, clean_name
from modules.tts_engine import get_tts_engine, PIPER_FRENCH_MODELS
from modules.pack_builder import PackBuilder, BuildOptions, parse_folder_to_tree, TreeNode
from modules.zip_handler import extract_zip, is_studio_pack, extract_pack_to_folder, get_zip_info
from modules.rss_handler import parse_rss_feed, RssFeed, RssEpisode, download_episode_audio
from modules.story_generator import load_story_pack

# Configuration de la page
st.set_page_config(
    page_title="Studio Pack Generator Online",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS personnalisé
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        background: linear-gradient(90deg, #FF6B35, #F7C59F);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-align: center;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1.2rem;
        color: #888;
        text-align: center;
        margin-bottom: 2rem;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 20px;
        border-radius: 8px;
    }
    .legal-notice {
        font-size: 0.75rem;
        color: #666;
        text-align: center;
        padding: 1rem;
        border-top: 1px solid #333;
        margin-top: 2rem;
        background: rgba(0,0,0,0.2);
        border-radius: 8px;
    }
    .success-card {
        background: linear-gradient(135deg, #1a472a, #2d5a3d);
        padding: 1.5rem;
        border-radius: 12px;
        border: 1px solid #28A745;
    }
    .warning-card {
        background: linear-gradient(135deg, #4a3f00, #5c4d00);
        padding: 1rem;
        border-radius: 8px;
        border: 1px solid #FFC107;
    }
</style>
""", unsafe_allow_html=True)


def init_session_state():
    """Initialise l'état de session Streamlit."""
    if 'initialized' not in st.session_state:
        st.session_state.initialized = True
        st.session_state.mode = 'basic'
        st.session_state.input_type = 'files'
        st.session_state.pack_title = "Mon Pack"
        st.session_state.pack_description = ""
        st.session_state.generation_complete = False
        st.session_state.output_zip_path = None
        st.session_state.output_zip_data = None  # Store ZIP binary for persistence
        st.session_state.output_pack_filename = None  # Store filename
        
        # Options de traitement
        st.session_state.normalize_audio = True
        st.session_state.add_delay = False
        st.session_state.night_mode = False
        
        # TTS settings
        st.session_state.tts_model = "fr_FR-siwis-medium"
        
        # RSS settings
        st.session_state.rss_episodes_per_part = 10
        st.session_state.rss_feed = None
        
        # Tree structure for manual building
        st.session_state.tree_nodes = []
        
        # Create a new session
        reset_session_manager()
        
        logger.info("Session state initialized")


def render_header():
    """Affiche l'en-tête de l'application."""
    st.markdown('<div class="main-header">📦 Studio Pack Generator Online</div>', 
                unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Créez des packs audio pour votre boîte à histoires</div>', 
                unsafe_allow_html=True)


def render_mode_selector():
    """Affiche le sélecteur de mode dans la sidebar."""
    st.sidebar.markdown("## ⚙️ Mode")
    mode = st.sidebar.radio(
        "Choisissez votre mode:",
        options=['basic', 'expert'],
        format_func=lambda x: "🎯 Basique" if x == 'basic' else "🔧 Expert",
        key='mode',
        horizontal=True
    )
    
    if mode == 'basic':
        st.sidebar.info("Mode simplifié avec les options par défaut.")
    else:
        st.sidebar.info("Accès à toutes les options avancées.")
    
    return mode


def render_expert_options():
    """Affiche les options avancées (mode expert)."""
    if st.session_state.mode != 'expert':
        return
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("## 🔧 Options avancées")
    
    # Audio options
    with st.sidebar.expander("🎵 Audio", expanded=True):
        st.checkbox(
            "Normaliser le volume",
            value=True,
            key='normalize_audio',
            help="Applique une normalisation dynamique du volume"
        )
        st.checkbox(
            "Ajouter silence (1s)",
            value=False,
            key='add_delay',
            help="Ajoute 1 seconde de silence au début et à la fin"
        )
    
    # Navigation options
    with st.sidebar.expander("🧭 Navigation", expanded=True):
        st.checkbox(
            "Mode Nuit",
            value=False,
            key='night_mode',
            help="Active les transitions entre histoires"
        )
    
    # TTS options
    with st.sidebar.expander("🗣️ Synthèse vocale", expanded=True):
        tts_models = [
            ("fr_FR-siwis-medium", "Siwis Medium (Femme) ⭐"),
            ("fr_FR-siwis-low", "Siwis Low (Femme, léger)"),
            ("fr_FR-gilles-low", "Gilles Low (Homme)"),
            ("fr_FR-tom-medium", "Tom Medium (Homme)"),
            ("fr_FR-upmc-medium", "UPMC Medium"),
        ]
        st.selectbox(
            "Modèle TTS",
            options=[m[0] for m in tts_models],
            format_func=lambda x: next((m[1] for m in tts_models if m[0] == x), x),
            key='tts_model'
        )
        
        # Check TTS availability
        tts = get_tts_engine()
        status = tts.get_engine_status()
        
        if status['piper']:
            st.success("✅ Piper TTS disponible")
        elif status['gtts']:
            st.warning("⚠️ Utilisation de gTTS (fallback)")
        else:
            st.error("❌ Aucun moteur TTS disponible")
            
    # Debug Section
    with st.sidebar.expander("🛠️ Diagnostic", expanded=False):
        if st.button("Lancer le diagnostic"):
            health = check_system_health()
            
            st.write("FFmpeg:", "✅" if health["ffmpeg"] else "❌")
            st.write("Piper Module:", "✅" if health["piper_module"] else "❌")
            st.write("Piper Bin:", "✅" if health["piper_bin"] else "❌")
            st.write("Espeak-ng:", "✅" if health["espeak"] else "❌")
            st.write("Écriture:", "✅" if health["write_access"] else "❌")
            
            if not health["piper_module"] and not health["piper_bin"]:
                st.warning("Piper n'est pas détecté. Le système utilisera gTTS si internet est disponible.")

            st.markdown("---")
            st.caption(f"OS: {health['details'].get('distro', 'N/A')}")
            with st.expander("Voir Message Erreur FFmpeg"):
                st.code(health['details'].get('ffmpeg_error', 'Pas d\'erreur'))


def render_input_tabs():
    """Affiche les onglets de sélection du type d'entrée."""
    tab_files, tab_zip, tab_rss, tab_extract = st.tabs([
        "📁 Fichiers", 
        "📦 Import ZIP", 
        "📡 Flux RSS",
        "🔄 Extraction"
    ])
    
    with tab_files:
        render_file_upload()
    
    with tab_zip:
        render_zip_upload()
    
    with tab_rss:
        render_rss_input()
    
    with tab_extract:
        render_extract_mode()


def check_system_health():
    """Diagnostique l'état du système."""
    health = {
        "ffmpeg": False,
        "piper_module": False,
        "piper_bin": False,
        "espeak": False,
        "write_access": False,
        "details": {}
    }
    
    # 0. Environment Fingerprint
    import platform
    health["details"]["os"] = platform.system() + " " + platform.release()
    health["details"]["path"] = os.environ.get("PATH", "")
    try:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release") as f:
                health["details"]["distro"] = f.read().splitlines()[0] # First line usually NAME="..."
    except: 
        health["details"]["distro"] = "Unknown"

    # 1. Check FFmpeg
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        health["ffmpeg"] = True
    except Exception as e:
        health["details"]["ffmpeg_error"] = str(e)
        
    # 2. Check Piper Module
    try:
        import piper
        health["piper_module"] = True
    except ImportError as e:
        health["details"]["piper_module_error"] = str(e)
        
    # 3. Check Piper Binary
    try:
        subprocess.run(["piper", "--help"], capture_output=True, timeout=2)
        health["piper_bin"] = True
    except Exception as e:
         health["details"]["piper_bin_error"] = str(e)

    # 4. Check Espeak
    try:
        subprocess.run(["espeak-ng", "--version"], capture_output=True, check=True)
        health["espeak"] = True
    except Exception as e:
        health["details"]["espeak_error"] = str(e)
        
    # 5. Write Access
    try:
        test_file = "test_write.txt"
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        health["write_access"] = True
    except Exception as e:
        health["details"]["write_error"] = str(e)
        
    return health


def render_generation_result():
    """Affiche le résultat de génération s'il existe."""
    if st.session_state.get('generation_complete') and st.session_state.get('output_zip_data'):
        st.markdown("---")
        st.markdown("### ✅ Pack généré")
        
        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.success(f"Pack prêt: **{st.session_state.output_pack_filename}**")
        
        with col2:
            st.download_button(
                "📥 Télécharger",
                st.session_state.output_zip_data,
                file_name=st.session_state.output_pack_filename,
                mime="application/zip",
                type="primary",
                use_container_width=True,
                key="download_persistent"
            )
        
        st.info("💡 Allez dans l'onglet 'Aperçu' pour tester la navigation avant de télécharger.")


def render_file_upload():
    """Affiche l'interface d'upload de fichiers."""
    st.markdown("### 📁 Upload de fichiers audio")
    st.markdown("Uploadez vos fichiers audio pour créer un pack simple.")
    
    # Audio files upload
    audio_files = st.file_uploader(
        "Glissez vos fichiers audio ici",
        type=['mp3', 'ogg', 'opus', 'wav', 'm4a', 'flac'],
        accept_multiple_files=True,
        key='audio_files',
        help="Formats supportés: MP3, OGG, OPUS, WAV, M4A, FLAC"
    )
    
    if audio_files:
        st.success(f"✅ {len(audio_files)} fichier(s) audio chargé(s)")
        
        with st.expander("Voir les fichiers", expanded=False):
            for f in audio_files:
                st.text(f"  • {f.name}")
    
    # Optional images
    st.markdown("---")
    st.markdown("#### 🖼️ Images (optionnel)")
    
    image_files = st.file_uploader(
        "Ajoutez des images pour personnaliser les menus",
        type=['png', 'jpg', 'jpeg', 'bmp', 'gif', 'webp'],
        accept_multiple_files=True,
        key='image_files'
    )
    
    if image_files:
        st.info(f"📷 {len(image_files)} image(s) chargée(s)")
    
    # Pack settings
    st.markdown("---")
    render_pack_settings(key_prefix="files")
    
    # Generate button
    if audio_files:
        st.markdown("---")
        if st.button("🚀 Générer le Pack", type="primary", use_container_width=True, key='gen_files'):
            generate_pack_from_files(audio_files, image_files)


def render_zip_upload():
    """Affiche l'interface d'upload de ZIP."""
    st.markdown("### 📦 Import d'un fichier ZIP")
    st.markdown("Importez un dossier zippé contenant votre arborescence de fichiers.")
    
    st.info("""
    **Structure attendue du ZIP:**
    ```
    📂 Mon Pack/
    ├── 📂 Menu 1/
    │   ├── 🎵 histoire1.mp3
    │   └── 🎵 histoire2.mp3
    └── 📂 Menu 2/
        └── 🎵 histoire3.mp3
    ```
    """)
    
    uploaded_zip = st.file_uploader(
        "Glissez votre fichier ZIP",
        type=['zip'],
        key='zip_file'
    )
    
    if uploaded_zip:
        st.success(f"✅ ZIP chargé: {uploaded_zip.name}")
        
        # Show ZIP info
        session = get_session_manager()
        temp_zip = os.path.join(session.session.temp_dir, uploaded_zip.name)
        
        with open(temp_zip, 'wb') as f:
            f.write(uploaded_zip.getbuffer())
        
        info = get_zip_info(temp_zip)
        if info:
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Fichiers", info['file_count'])
            with col2:
                size_mb = info['total_size'] / (1024 * 1024)
                st.metric("Taille", f"{size_mb:.1f} MB")
        
        # Pack settings
        st.markdown("---")
        render_pack_settings(key_prefix="zip")
        
        # Generate button
        st.markdown("---")
        if st.button("🚀 Générer le Pack depuis ZIP", type="primary", use_container_width=True, key='gen_zip'):
            generate_pack_from_zip(temp_zip)


def render_rss_input():
    """Affiche l'interface d'import RSS avec moteur de recherche unifié."""
    st.markdown("### 📡 Import de Podcast")
    st.markdown("Recherchez un podcast ou collez directement l'URL d'un flux RSS.")
    
    # Search/URL Input
    col_search, col_btn = st.columns([4, 1])
    
    with col_search:
        search_query = st.text_input(
            "Recherche / RSS",
            placeholder="Ex: France Inter, Les Odyssées, ou https://...",
            key='rss_input',
            label_visibility="collapsed"
        )
    
    with col_btn:
        search_clicked = st.button("🔎 Rechercher", use_container_width=True)
    
    # Handle Input (Search vs URL)
    if search_clicked and search_query:
        # Check if it's a URL
        from urllib.parse import urlparse
        parsed = urlparse(search_query)
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            # It's a direct URL
            with st.spinner("Chargement du flux RSS..."):
                feed = parse_rss_feed(search_query)
                if feed:
                    st.session_state.rss_feed = feed
                    st.success(f"✅ {len(feed.episodes)} épisodes trouvés")
                    st.rerun() # Force refresh to show episodes
                else:
                    st.error("❌ Impossible de charger le flux RSS")
        else:
            # It's a search term
            from modules.podcast_search import unified_search
            
            with st.spinner(f"Recherche de '{search_query}'..."):
                results = unified_search(search_query)
                
            if results:
                st.markdown(f"**{len(results)} résultats trouvés :**")
                
                # Display results in a grid
                cols = st.columns(3)
                for idx, res in enumerate(results):
                    with cols[idx % 3]:
                        with st.container(border=True):
                            # Image
                            if res.image_url:
                                st.image(res.image_url, use_container_width=True)
                            
                            # Info
                            st.markdown(f"**{res.title}**")
                            st.caption(res.author)
                            
                            # Select Button
                            if st.button("Choisir", key=f"sel_{idx}"):
                                with st.spinner("Chargement..."):
                                    feed = parse_rss_feed(res.feed_url)
                                    if feed:
                                        st.session_state.rss_feed = feed
                                        st.rerun()
                                    else:
                                        st.error(f"Erreur lors du chargement : {res.feed_url}")
            else:
                st.warning("Aucun podcast trouvé. Essayez une autre recherche ou une URL directe.")

    # Display loaded feed (Common for both Search and Direct URL)
    if st.session_state.get('rss_feed'):
        # Divider if we have search results above? 
        # Actually standard flow clears search results on rerun so we are good.
        
        feed = st.session_state.rss_feed
        
        st.markdown("---")
        st.markdown(f"### 🎙️ {feed.title}")
        if feed.description:
            st.caption(feed.description[:200] + "..." if len(feed.description) > 200 else feed.description)
        
        # Episodes per part slider
        st.slider(
            "Épisodes par partie",
            min_value=1,
            max_value=50,
            value=10,
            key='rss_episodes_per_part',
            help="Les épisodes seront regroupés en parties de cette taille"
        )
        
        # Episode selection
        st.markdown("#### 📋 Sélection des épisodes")
        
        selected_episodes = []
        for i, ep in enumerate(feed.episodes[:50]):  # Limit display
            duration_str = f"({ep.duration // 60:.0f} min)" if ep.duration else ""
            selected = st.checkbox(
                f"{ep.title} {duration_str}",
                value=True,
                key=f'ep_{i}'
            )
            if selected:
                selected_episodes.append(ep)
        
        if len(feed.episodes) > 50:
            st.warning(f"⚠️ Seuls les 50 premiers épisodes sont affichés ({len(feed.episodes)} au total)")
        
        # Pack settings
        st.markdown("---")
        st.session_state.pack_title = feed.title
        render_pack_settings(key_prefix="rss")
        
        # Generate button
        st.markdown("---")
        if st.button("🚀 Générer le Pack RSS", type="primary", use_container_width=True, key='gen_rss'):
            generate_pack_from_rss(feed, selected_episodes)


def render_extract_mode():
    """Affiche l'interface du mode extraction."""
    st.markdown("### 🔄 Extraction de Pack")
    st.markdown("Extrayez un pack existant vers une structure de dossiers éditable.")
    
    uploaded_pack = st.file_uploader(
        "Glissez un fichier pack (.zip)",
        type=['zip'],
        key='extract_zip'
    )
    
    if uploaded_pack:
        session = get_session_manager()
        temp_zip = os.path.join(session.session.temp_dir, uploaded_pack.name)
        
        with open(temp_zip, 'wb') as f:
            f.write(uploaded_pack.getbuffer())
        
        # Check if it's a valid pack
        if is_studio_pack(temp_zip):
            st.success("✅ Pack Studio valide détecté")
            
            info = get_zip_info(temp_zip)
            if info and info.get('pack_title'):
                st.info(f"📦 Titre du pack: {info['pack_title']}")
            
            if st.button("📂 Extraire vers dossier", type="primary", use_container_width=True):
                extract_output = os.path.join(session.session.temp_dir, "extracted")
                
                with st.spinner("Extraction en cours..."):
                    if extract_pack_to_folder(temp_zip, extract_output):
                        st.success("✅ Pack extrait avec succès!")
                        
                        # Create ZIP of extracted folder for download
                        extracted_zip = os.path.join(session.session.temp_dir, "extracted_folder.zip")
                        shutil.make_archive(
                            extracted_zip.replace('.zip', ''),
                            'zip',
                            extract_output
                        )
                        
                        with open(extracted_zip, 'rb') as f:
                            st.download_button(
                                "📥 Télécharger le dossier extrait",
                                f.read(),
                                file_name=f"{info.get('pack_title', 'pack')}_extracted.zip",
                                mime="application/zip"
                            )
                    else:
                        st.error("❌ Erreur lors de l'extraction")
        else:
            st.warning("⚠️ Ce fichier ne semble pas être un pack Studio valide.")


def render_pack_settings(key_prefix: str = "files"):
    """Affiche les paramètres du pack.
    
    Args:
        key_prefix: Préfixe unique pour les clés Streamlit (évite les doublons entre onglets)
    """
    st.markdown("#### 📝 Informations du pack")
    
    col1, col2 = st.columns(2)
    
    title_key = f'{key_prefix}_title_input'
    desc_key = f'{key_prefix}_desc_input'
    
    with col1:
        new_title = st.text_input(
            "Titre du pack",
            value=st.session_state.pack_title,
            key=title_key
        )
        if new_title != st.session_state.pack_title:
            st.session_state.pack_title = new_title
    
    with col2:
        new_desc = st.text_input(
            "Description (optionnel)",
            value=st.session_state.pack_description,
            key=desc_key
        )
        if new_desc != st.session_state.pack_description:
            st.session_state.pack_description = new_desc


def generate_pack_from_files(audio_files, image_files):
    """Génère un pack à partir des fichiers uploadés."""
    session = get_session_manager()
    
    with st.spinner("Préparation des fichiers..."):
        # Save uploaded files
        input_folder = os.path.join(session.session.input_dir, "stories")
        ensure_dir(input_folder)
        
        for audio in audio_files:
            path = os.path.join(input_folder, audio.name)
            with open(path, 'wb') as f:
                f.write(audio.getbuffer())
        
        if image_files:
            for img in image_files:
                path = os.path.join(input_folder, img.name)
                with open(path, 'wb') as f:
                    f.write(img.getbuffer())
    
    # Parse folder to tree
    tree = parse_folder_to_tree(input_folder)
    if not tree:
        st.error("❌ Erreur lors de la création de la structure")
        return
    
    # Wrap in a root node with pack title
    root = TreeNode(
        name=st.session_state.pack_title,
        path=input_folder,
        is_folder=True,
        children=[tree] if tree.is_folder else [tree]
    )
    
    # Build options
    options = BuildOptions(
        title=st.session_state.pack_title,
        description=st.session_state.get('desc_input', ''),
        normalize_audio=st.session_state.get('normalize_audio', True),
        add_delay=st.session_state.get('add_delay', False),
        night_mode=st.session_state.get('night_mode', False),
        tts_model=st.session_state.get('tts_model', 'fr_FR-siwis-medium')
    )
    
    # Progress bar
    progress_bar = st.progress(0, text="Démarrage...")
    
    def update_progress(progress: float, message: str):
        progress_bar.progress(progress, text=message)
    
    options.progress_callback = update_progress
    
    # Build pack
    builder = PackBuilder(options)
    
    if builder.build_from_tree(root):
        progress_bar.progress(1.0, text="Terminé!")
        
        zip_path = builder.get_output_zip_path()
        st.session_state.generation_complete = True
        st.session_state.output_zip_path = zip_path
        
        # Store ZIP data for persistence across tabs
        with open(zip_path, 'rb') as f:
            st.session_state.output_zip_data = f.read()
        st.session_state.output_pack_filename = os.path.basename(zip_path)
        # Rerun to show updated layout with preview/download sections
        st.rerun()
    
    else:
        st.error("❌ Erreur lors de la génération du pack")


def generate_pack_from_zip(zip_path: str):
    """Génère un pack à partir d'un ZIP uploadé."""
    session = get_session_manager()
    
    with st.spinner("Extraction du ZIP..."):
        extract_dir = os.path.join(session.session.input_dir, "extracted")
        if not extract_zip(zip_path, extract_dir):
            st.error("❌ Erreur lors de l'extraction du ZIP")
            return
    
    # Find the root folder
    contents = os.listdir(extract_dir)
    if len(contents) == 1 and os.path.isdir(os.path.join(extract_dir, contents[0])):
        root_dir = os.path.join(extract_dir, contents[0])
    else:
        root_dir = extract_dir
    
    # Parse folder to tree
    tree = parse_folder_to_tree(root_dir)
    if not tree:
        st.error("❌ Erreur lors de la création de la structure")
        return
    
    # Update title if not set
    if st.session_state.pack_title == "Mon Pack":
        st.session_state.pack_title = tree.display_name
    
    # Build options
    options = BuildOptions(
        title=st.session_state.pack_title,
        description=st.session_state.get('desc_input', ''),
        normalize_audio=st.session_state.get('normalize_audio', True),
        add_delay=st.session_state.get('add_delay', False),
        night_mode=st.session_state.get('night_mode', False),
        tts_model=st.session_state.get('tts_model', 'fr_FR-siwis-medium')
    )
    
    # Progress bar
    progress_bar = st.progress(0, text="Démarrage...")
    
    def update_progress(progress: float, message: str):
        progress_bar.progress(progress, text=message)
    
    options.progress_callback = update_progress
    
    # Build pack
    builder = PackBuilder(options)
    
    if builder.build_from_tree(tree):
        progress_bar.progress(1.0, text="Terminé!")
        
        zip_path = builder.get_output_zip_path()
        st.session_state.generation_complete = True
        st.session_state.output_zip_path = zip_path
        
        # Store ZIP data for persistence across tabs
        with open(zip_path, 'rb') as f:
            st.session_state.output_zip_data = f.read()
        st.session_state.output_pack_filename = os.path.basename(zip_path)
        # Rerun to show updated layout with preview/download sections
        st.rerun()
    else:
        st.error("❌ Erreur lors de la génération du pack")


def generate_pack_from_rss(feed: RssFeed, selected_episodes: list):
    """Génère un pack à partir d'un flux RSS et des épisodes sélectionnés."""
    from modules.rss_handler import (
        download_episode_audio, download_episode_image, 
        split_episodes_into_parts, download_feed_image
    )
    
    if not selected_episodes:
        st.error("❌ Aucun épisode sélectionné")
        return
    
    session = get_session_manager()
    
    # Create folder structure
    input_folder = os.path.join(session.session.input_dir, "podcast")
    ensure_dir(input_folder)
    
    # Get episodes per part setting
    episodes_per_part = st.session_state.get('rss_episodes_per_part', 10)
    
    # Split episodes into parts
    parts = split_episodes_into_parts(selected_episodes, episodes_per_part)
    
    # Progress tracking
    total_steps = len(selected_episodes) + len(parts) + 3  # +1 for feed image
    current_step = 0
    progress_bar = st.progress(0, text="Préparation...")
    
    def update_progress(step: int, message: str):
        nonlocal current_step
        current_step = step
        progress_bar.progress(current_step / total_steps, text=message)
    
    # Download feed image first
    update_progress(1, "Téléchargement de l'image du podcast...")
    feed_image_path = download_feed_image(feed, input_folder)
    
    # Download episodes
    st.info(f"📥 Téléchargement de {len(selected_episodes)} épisode(s)...")
    
    for i, ep in enumerate(selected_episodes):
        update_progress(i + 2, f"Téléchargement: {ep.title[:40]}...")
        
        # Download audio
        if not download_episode_audio(ep, input_folder):
            st.warning(f"⚠️ Impossible de télécharger: {ep.title}")
            continue
        
        # Download image if available
        download_episode_image(ep, input_folder)
    
    # Build tree structure
    update_progress(len(selected_episodes) + 2, "Construction de la structure...")
    
    # Create root node with feed image
    root = TreeNode(
        name=st.session_state.pack_title or feed.title,
        path=input_folder,
        is_folder=True,
        item_image=feed_image_path  # Use feed image for root
    )
    
    # If only one part, add episodes directly
    if len(parts) == 1:
        for ep in parts[0]:
            if ep.audio_path:
                child = TreeNode(
                    name=clean_name(ep.title),
                    path=ep.audio_path,
                    is_folder=False,
                    audio_file=ep.audio_path,
                    item_image=ep.image_path
                )
                root.children.append(child)
    else:
        # Multiple parts - create sub-menus
        for part_idx, part in enumerate(parts):
            part_name = f"Partie {part_idx + 1}"
            part_folder = os.path.join(input_folder, part_name)
            ensure_dir(part_folder)
            
            part_node = TreeNode(
                name=part_name,
                path=part_folder,
                is_folder=True
            )
            
            for ep in part:
                if ep.audio_path:
                    child = TreeNode(
                        name=clean_name(ep.title),
                        path=ep.audio_path,
                        is_folder=False,
                        audio_file=ep.audio_path,
                        item_image=ep.image_path
                    )
                    part_node.children.append(child)
            
            if part_node.children:
                root.children.append(part_node)
    
    if not root.children:
        st.error("❌ Aucun épisode n'a pu être téléchargé")
        return
    
    # Build options
    options = BuildOptions(
        title=st.session_state.pack_title or feed.title,
        description=feed.description[:200] if feed.description else "",
        normalize_audio=st.session_state.get('normalize_audio', True),
        add_delay=st.session_state.get('add_delay', False),
        night_mode=st.session_state.get('night_mode', False),
        tts_model=st.session_state.get('tts_model', 'fr_FR-siwis-medium')
    )
    
    def build_progress(progress: float, message: str):
        update_progress(
            len(selected_episodes) + 1 + int(progress * len(parts)),
            message
        )
    
    options.progress_callback = build_progress
    
    # Build pack
    update_progress(len(selected_episodes) + len(parts), "Génération du pack...")
    builder = PackBuilder(options)
    
    if builder.build_from_tree(root):
        progress_bar.progress(1.0, text="Terminé!")
        
        zip_path = builder.get_output_zip_path()
        st.session_state.generation_complete = True
        st.session_state.output_zip_path = zip_path
        
        # Store ZIP data for persistence across tabs
        with open(zip_path, 'rb') as f:
            st.session_state.output_zip_data = f.read()
        st.session_state.output_pack_filename = f"{clean_name(feed.title)}_pack.zip"
        # Rerun to show updated layout with preview/download sections
        st.rerun()
    else:
        st.error("❌ Erreur lors de la génération du pack")


def render_simulator_tab():
    """Affiche l'onglet simulateur et éditeur."""
    if not st.session_state.get('generation_complete'):
        st.info("📦 Générez d'abord un pack pour pouvoir le tester ici.")
        return
    
    session = get_session_manager()
    
    # Sub-tabs for simulator and editor
    tab_sim, tab_edit = st.tabs(["🎮 Simulateur", "✏️ Modifier"])
    
    with tab_sim:
        # Import and render simulator
        from ui.simulator import render_simulator_tab as render_sim
        render_sim(session.session.output_dir)
    
    with tab_edit:
        # Import and render editor
        from ui.editor import render_pack_editor
        from modules.story_generator import load_story_pack
        
        story_json_path = os.path.join(session.session.output_dir, "story.json")
        pack = load_story_pack(story_json_path)
        
        if pack:
            render_pack_editor(pack, story_json_path)
        else:
            st.error("❌ Impossible de charger le pack pour l'édition")


def render_legal_notice():
    """Affiche les mentions légales."""
    st.markdown("""
    <div class="legal-notice">
        ⚖️ <strong>Mentions légales</strong><br>
        Cet outil est réservé à un usage strictement personnel et privé.<br>
        L'utilisateur est seul responsable du respect des droits d'auteur 
        des fichiers qu'il traite avec cette application.<br>
        Les fichiers uploadés sont automatiquement supprimés après la génération du pack.
    </div>
    """, unsafe_allow_html=True)


def check_dependencies():
    """Vérifie les dépendances système."""
    issues = []
    
    if not is_ffmpeg_available():
        issues.append("⚠️ FFmpeg non détecté. La conversion audio peut ne pas fonctionner.")
    
    return issues


def main():
    """Point d'entrée principal de l'application."""
    # Initialisation
    init_session_state()
    
    # En-tête
    render_header()
    
    # Vérification des dépendances
    issues = check_dependencies()
    for issue in issues:
        st.warning(issue)
    
    # Sidebar
    render_mode_selector()
    render_expert_options()
    
    # Determine current phase
    pack_ready = st.session_state.get('generation_complete') and st.session_state.get('output_zip_data')
    
    # === SECTION 1: Import ===
    with st.expander("📥 1. Importer votre contenu", expanded=not pack_ready):
        render_input_tabs()
    
    # === SECTION 2: Aperçu & Vérification ===
    if pack_ready:
        with st.expander("👁️ 2. Aperçu & Vérification", expanded=True):
            render_simulator_tab()
        
        # === SECTION 3: Download ===
        with st.expander("📥 3. Télécharger le pack", expanded=True):
            col1, col2 = st.columns([2, 1])
            with col1:
                st.success(f"✅ Pack prêt: **{st.session_state.output_pack_filename}**")
                st.caption("Votre pack Studio est prêt à être utilisé !")
            with col2:
                st.download_button(
                    "📥 Télécharger le Pack",
                    st.session_state.output_zip_data,
                    file_name=st.session_state.output_pack_filename,
                    mime="application/zip",
                    type="primary",
                    use_container_width=True,
                    key="download_main"
                )
    else:
        # Show disabled placeholders for sections 2 and 3
        st.markdown("---")
        st.markdown("##### 👁️ 2. Aperçu & Vérification")
        st.info("💡 Générez d'abord un pack pour accéder à l'aperçu")
        
        st.markdown("##### 📥 3. Télécharger")
        st.info("💡 Générez d'abord un pack pour le télécharger")
    
    # Legal notice
    render_legal_notice()


if __name__ == "__main__":
    main()
