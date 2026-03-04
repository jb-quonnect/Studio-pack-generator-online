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
from modules.lunii_converter import LuniiPackConverter, is_lunii_pack, validate_studio_pack

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
        st.session_state.rss_selected_episodes = None   # Episodes selected in step 1
        st.session_state.rss_chapters = None            # List of {"name": str, "episodes": [...]}
        st.session_state.rss_chapter_mode = False       # True = show chapter editor
        
        # Tree structure for manual building
        st.session_state.tree_nodes = []
        
        # Lunii settings
        st.session_state.lunii_version = "V2"
        st.session_state.lunii_zip_data = None
        st.session_state.lunii_zip_filename = None
        st.session_state.lunii_conversion_complete = False
        
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

    # Lunii options (always visible if expert mode)
    with st.sidebar.expander("🎧 Lunii", expanded=False):
        lunii_versions = [
            ("V2", "V2 — XXTEA (compatible tous appareils)"),
            ("V3", "V3 — AES-CBC (appareils récents)"),
        ]
        st.selectbox(
            "Version de chiffrement",
            options=[v[0] for v in lunii_versions],
            format_func=lambda x: next((v[1] for v in lunii_versions if v[0] == x), x),
            key='lunii_version',
            help="V2 est compatible avec la majorité des appareils Lunii. V3 nécessite des clés spécifiques."
        )


def render_input_tabs():
    """Affiche les onglets de sélection du type d'entrée."""
    tab_rss, tab_files, tab_zip, tab_extract = st.tabs([
        "📡 Flux RSS",
        "📁 Fichiers", 
        "📦 Import ZIP", 
        "🔄 Extraction"
    ])
    
    with tab_rss:
        render_rss_input()
        
    with tab_files:
        render_file_upload()
    
    with tab_zip:
        render_zip_upload()
    
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
    
    # Initialize session state for search (guard — init_session_state handles the rest)
    if 'rss_search_results' not in st.session_state:
        st.session_state.rss_search_results = None
    if 'rss_chapter_mode' not in st.session_state:
        st.session_state.rss_chapter_mode = False
    if 'rss_chapters' not in st.session_state:
        st.session_state.rss_chapters = None
    if 'rss_selected_episodes' not in st.session_state:
        st.session_state.rss_selected_episodes = None

    # ─── CHAPTER EDITOR (Step 2) ─────────────────────────────────────────────
    if st.session_state.rss_chapter_mode and st.session_state.rss_chapters is not None:
        _render_chapter_editor()
        return

    # ─── SEARCH BAR ──────────────────────────────────────────────────────────
    with st.form("search_form"):
        col_search, col_btn = st.columns([4, 1])
        
        with col_search:
            search_query = st.text_input(
                "Recherche / RSS",
                placeholder="Ex: France Inter, Les Odyssées, ou https://...",
                key='rss_input',
                label_visibility="collapsed"
            )
        
        with col_btn:
            search_submitted = st.form_submit_button("🔎 Rechercher", use_container_width=True)
    
    # Handle Input (Search vs URL)
    if search_submitted and search_query:
        from urllib.parse import urlparse
        parsed = urlparse(search_query)
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            with st.spinner("Chargement du flux RSS..."):
                feed = parse_rss_feed(search_query)
                if feed:
                    st.session_state.rss_feed = feed
                    st.session_state.rss_search_results = None
                    st.success(f"✅ {len(feed.episodes)} épisodes trouvés")
                    st.rerun()
                else:
                    st.error("❌ Impossible de charger le flux RSS")
        else:
            from modules.podcast_search import unified_search
            with st.spinner(f"Recherche de '{search_query}'..."):
                results = unified_search(search_query)
            if results:
                st.session_state.rss_search_results = results
            else:
                st.session_state.rss_search_results = []
                st.warning("Aucun podcast trouvé. Essayez une autre recherche ou une URL directe.")

    # Display Search Results (Persisted)
    if st.session_state.rss_search_results:
        st.markdown(f"**{len(st.session_state.rss_search_results)} résultats trouvés :**")
        cols = st.columns(3)
        for idx, res in enumerate(st.session_state.rss_search_results):
            with cols[idx % 3]:
                with st.container(border=True):
                    if res.image_url:
                        st.image(res.image_url, use_container_width=True)
                    st.markdown(f"**{res.title}**")
                    st.caption(res.author)
                    if st.button("Choisir", key=f"sel_{idx}", use_container_width=True):
                        with st.spinner("Chargement..."):
                            feed = parse_rss_feed(
                                res.feed_url,
                                existing_title=res.title,
                                existing_image_url=res.image_url
                            )
                            if feed:
                                st.session_state.rss_feed = feed
                                st.session_state.rss_search_results = None
                                st.rerun()
                            else:
                                st.error(f"Erreur lors du chargement : {res.feed_url}")
        st.markdown("---")

    # ─── FEED DISPLAY (Step 1 : episode selection) ────────────────────────────
    if st.session_state.get('rss_feed'):
        feed = st.session_state.rss_feed
        
        col_title, col_reset = st.columns([4, 1])
        with col_title:
            st.markdown(f"### 🎙️ {feed.title}")
        with col_reset:
            if st.button("↩ Changer", use_container_width=True):
                st.session_state.rss_feed = None
                st.session_state.rss_chapter_mode = False
                st.session_state.rss_chapters = None
                st.rerun()

        if feed.description:
            st.caption(feed.description[:200] + "..." if len(feed.description) > 200 else feed.description)

        total = len(feed.episodes)
        st.markdown(f"#### 📋 Sélection des épisodes ({total} disponibles)")

        # Select / Deselect All
        col_sa, col_da = st.columns(2)
        with col_sa:
            if st.button("✅ Tout sélectionner", use_container_width=True, key='rss_select_all'):
                for i in range(total):
                    st.session_state[f'ep_{i}'] = True
                st.rerun()
        with col_da:
            if st.button("⬜ Tout désélectionner", use_container_width=True, key='rss_deselect_all'):
                for i in range(total):
                    st.session_state[f'ep_{i}'] = False
                st.rerun()

        # Show all episodes (no limit)
        selected_episodes = []
        with st.container():
            for i, ep in enumerate(feed.episodes):
                duration_str = f" ({ep.duration // 60:.0f} min)" if ep.duration else ""
                selected = st.checkbox(
                    f"{ep.title}{duration_str}",
                    value=st.session_state.get(f'ep_{i}', True),
                    key=f'ep_{i}'
                )
                if selected:
                    selected_episodes.append(ep)

        n_sel = len(selected_episodes)
        st.caption(f"**{n_sel}** épisode(s) sélectionné(s) sur {total}")

        # Pack settings
        st.markdown("---")
        st.session_state.pack_title = feed.title
        render_pack_settings(key_prefix="rss")

        # Action buttons
        st.markdown("---")
        col_chap, col_gen = st.columns(2)

        with col_chap:
            if st.button(
                "📚 Organiser en chapitres →",
                type="secondary",
                use_container_width=True,
                key='goto_chapters',
                disabled=(n_sel == 0)
            ):
                # Default: one chapter per N episodes (10 by default)
                eps_per_chap = 10
                chapters = []
                for chunk_start in range(0, len(selected_episodes), eps_per_chap):
                    chunk = selected_episodes[chunk_start:chunk_start + eps_per_chap]
                    chap_num = len(chapters) + 1
                    chapters.append({
                        "name": f"Chapitre {chap_num}",
                        "episodes": list(chunk)
                    })
                st.session_state.rss_selected_episodes = selected_episodes
                st.session_state.rss_chapters = chapters
                st.session_state.rss_chapter_mode = True
                st.rerun()

        with col_gen:
            if st.button(
                "🚀 Générer le Pack RSS (sans chapitres)",
                type="primary",
                use_container_width=True,
                key='gen_rss',
                disabled=(n_sel == 0)
            ):
                generate_pack_from_rss(feed, selected_episodes)


def _render_chapter_editor():
    """Affiche l'éditeur de chapitres (étape 2 du flux RSS)."""
    feed = st.session_state.rss_feed
    chapters: list = st.session_state.rss_chapters  # List of {"name": str, "episodes": [...]}
    
    # Collect all assigned episodes to detect unassigned ones
    all_selected = st.session_state.rss_selected_episodes or []
    assigned_guids = {ep.guid or ep.title for ch in chapters for ep in ch["episodes"]}
    unassigned = [ep for ep in all_selected if (ep.guid or ep.title) not in assigned_guids]

    st.markdown(f"### 📚 Organiser en chapitres — *{feed.title}*")

    # Auto-organize toolbar
    with st.container(border=True):
        st.caption("**Auto-organisation**")
        col_slider, col_auto = st.columns([3, 1])
        with col_slider:
            eps_per_chap = st.slider(
                "Épisodes par chapitre",
                min_value=1, max_value=50,
                value=st.session_state.get('rss_episodes_per_part', 10),
                key='rss_auto_chap_size',
                label_visibility="collapsed"
            )
        with col_auto:
            if st.button("🔀 Auto-organiser", use_container_width=True, key='auto_organize'):
                new_chapters = []
                for i, chunk_start in enumerate(range(0, len(all_selected), eps_per_chap)):
                    chunk = all_selected[chunk_start:chunk_start + eps_per_chap]
                    new_chapters.append({
                        "name": f"Chapitre {i + 1}",
                        "episodes": list(chunk)
                    })
                st.session_state.rss_chapters = new_chapters
                st.rerun()

    st.markdown("---")

    # ── Chapter list ──────────────────────────────────────────────────────────
    action_needed = None  # Tuple describing a pending action to apply after loop

    for ch_idx, chapter in enumerate(chapters):
        with st.container(border=True):
            # Chapter header
            col_name, col_del = st.columns([5, 1])
            with col_name:
                new_name = st.text_input(
                    f"Nom du chapitre {ch_idx + 1}",
                    value=chapter["name"],
                    key=f"chap_name_{ch_idx}",
                    label_visibility="collapsed",
                    placeholder=f"Chapitre {ch_idx + 1}"
                )
                if new_name != chapter["name"]:
                    chapter["name"] = new_name
                    st.session_state.rss_chapters = chapters
            with col_del:
                if st.button("🗑", key=f"del_chap_{ch_idx}", help="Supprimer ce chapitre (les épisodes deviennent non-assignés)"):
                    action_needed = ("del_chap", ch_idx)

            # Episode list in this chapter
            eps = chapter["episodes"]
            if not eps:
                st.caption("*(Aucun épisode)*")
            else:
                # Build list of other chapter names for the move selector
                other_chapters = [f"ch_{i}" for i in range(len(chapters)) if i != ch_idx]
                other_chapter_labels = {
                    f"ch_{i}": f"→ {chapters[i]['name']}"
                    for i in range(len(chapters)) if i != ch_idx
                }
                if unassigned:
                    other_chapter_labels["__unassign__"] = "→ Non-assignés"
                    other_chapters.append("__unassign__")

                for ep_idx, ep in enumerate(eps):
                    col_ep, col_up, col_down, col_move, col_del = st.columns([4, 1, 1, 2, 1])
                    with col_ep:
                        dur = f" ({ep.duration // 60:.0f}m)" if ep.duration else ""
                        st.markdown(f"<small>📄 {ep.title[:60]}{dur}</small>", unsafe_allow_html=True)
                    with col_up:
                        if ep_idx > 0 and st.button("↑", key=f"up_{ch_idx}_{ep_idx}", help="Monter"):
                            action_needed = ("move_ep_up", ch_idx, ep_idx)
                    with col_down:
                        if ep_idx < len(eps) - 1 and st.button("↓", key=f"dn_{ch_idx}_{ep_idx}", help="Descendre"):
                            action_needed = ("move_ep_dn", ch_idx, ep_idx)
                    with col_move:
                        if other_chapters:
                            dest = st.selectbox(
                                "Déplacer vers",
                                options=[""] + other_chapters,
                                format_func=lambda x: "Déplacer…" if x == "" else other_chapter_labels.get(x, x),
                                key=f"mv_{ch_idx}_{ep_idx}",
                                label_visibility="collapsed"
                            )
                            if dest:
                                action_needed = ("move_ep_to", ch_idx, ep_idx, dest)
                    with col_del:
                        if st.button("🗑️", key=f"del_ep_{ch_idx}_{ep_idx}", help="Retirer cet épisode du pack"):
                            action_needed = ("del_ep", ch_idx, ep_idx)

    # ── Unassigned pool ───────────────────────────────────────────────────────
    if unassigned:
        st.markdown("---")
        with st.container(border=True):
            st.markdown("**🗂 Épisodes non-assignés**")
            chap_options = [f"ch_{i}" for i in range(len(chapters))]
            chap_labels = {f"ch_{i}": chapters[i]["name"] for i in range(len(chapters))}
            for ep_idx, ep in enumerate(unassigned):
                col_ep, col_mv, col_del = st.columns([4, 2, 1])
                with col_ep:
                    dur = f" ({ep.duration // 60:.0f}m)" if ep.duration else ""
                    st.markdown(f"<small>📄 {ep.title[:60]}{dur}</small>", unsafe_allow_html=True)
                with col_mv:
                    if chap_options:
                        dest = st.selectbox(
                            "Assigner à",
                            options=[""] + chap_options,
                            format_func=lambda x: "Assigner…" if x == "" else chap_labels.get(x, x),
                            key=f"ua_mv_{ep_idx}",
                            label_visibility="collapsed"
                        )
                        if dest:
                            action_needed = ("assign_unassigned", ep_idx, dest)
                with col_del:
                    if st.button("🗑️", key=f"del_ua_{ep_idx}", help="Retirer cet épisode du pack"):
                        action_needed = ("del_unassigned", ep_idx)

    # ── Add chapter button ────────────────────────────────────────────────────
    st.markdown("---")
    if st.button("➕ Ajouter un chapitre vide", key='add_chapter'):
        chapters.append({"name": f"Chapitre {len(chapters) + 1}", "episodes": []})
        st.session_state.rss_chapters = chapters
        st.rerun()

    # ── Back / Generate ───────────────────────────────────────────────────────
    col_back, col_gen = st.columns(2)
    with col_back:
        if st.button("← Retour à la sélection", use_container_width=True, key='back_to_sel'):
            st.session_state.rss_chapter_mode = False
            st.rerun()
    with col_gen:
        n_chapters = len([ch for ch in chapters if ch["episodes"]])
        total_eps = sum(len(ch["episodes"]) for ch in chapters)
        if st.button(
            f"🚀 Générer ({n_chapters} chapitres, {total_eps} épisodes)",
            type="primary",
            use_container_width=True,
            key='gen_chaptered',
            disabled=(total_eps == 0)
        ):
            generate_pack_from_rss(feed, all_selected, chapters=chapters)

    # ── Apply deferred actions (after rendering, to avoid mid-loop mutations) ─
    if action_needed:
        op = action_needed[0]

        if op == "del_chap":
            _, ci = action_needed
            chapters.pop(ci)
        elif op == "move_ep_up":
            _, ci, ei = action_needed
            eps = chapters[ci]["episodes"]
            eps[ei - 1], eps[ei] = eps[ei], eps[ei - 1]
            chapters[ci]["episodes"] = eps
        elif op == "move_ep_dn":
            _, ci, ei = action_needed
            eps = chapters[ci]["episodes"]
            eps[ei], eps[ei + 1] = eps[ei + 1], eps[ei]
            chapters[ci]["episodes"] = eps
        elif op == "move_ep_to":
            _, ci, ei, dest = action_needed
            ep = chapters[ci]["episodes"].pop(ei)
            if dest == "__unassign__":
                pass  # Just remove from chapter; it'll appear in unassigned
            else:
                di = int(dest.split("_")[1])
                chapters[di]["episodes"].append(ep)
        elif op == "assign_unassigned":
            _, ui, dest = action_needed
            di = int(dest.split("_")[1])
            ep = unassigned[ui]
            chapters[di]["episodes"].append(ep)
        elif op in ("del_ep", "del_unassigned"):
            if op == "del_ep":
                _, ci, ei = action_needed
                ep = chapters[ci]["episodes"].pop(ei)
            else:
                _, ui = action_needed
                ep = unassigned[ui]
            
            # Remove from selected episodes
            ep_id = ep.guid or ep.title
            all_selected = st.session_state.rss_selected_episodes
            st.session_state.rss_selected_episodes = [
                e for e in all_selected if (e.guid or e.title) != ep_id
            ]
            
            # Uncheck it in the global list so it doesn't reappear if we go back
            feed = st.session_state.rss_feed
            if feed:
                for idx, feed_ep in enumerate(feed.episodes):
                    if (feed_ep.guid or feed_ep.title) == ep_id:
                        st.session_state[f'ep_{idx}'] = False

        st.session_state.rss_chapters = chapters
        st.rerun()


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


def generate_pack_from_rss(feed: RssFeed, selected_episodes: list, chapters: list = None):
    """Génère un pack à partir d'un flux RSS et des épisodes sélectionnés.
    
    Args:
        feed: Le flux RSS parsé
        selected_episodes: Liste fusionnée de tous les épisodes sélectionnés
        chapters: (optionnel) Liste de {"name": str, "episodes": [...]} pour un pack chapitré.
                  Si None ou vide, comportement flat (épisodes directement sous la racine).
    """
    from modules.rss_handler import (
        download_episode_audio, download_episode_image,
        download_feed_image
    )
    
    if not selected_episodes:
        st.error("❌ Aucun épisode sélectionné")
        return
    
    session = get_session_manager()
    input_folder = os.path.join(session.session.input_dir, "podcast")
    ensure_dir(input_folder)
    
    # Determine episode list to download
    # When using chapters, download all episodes present in the chapters
    if chapters:
        eps_to_download = [ep for ch in chapters for ep in ch["episodes"]]
    else:
        eps_to_download = selected_episodes
    
    # Progress tracking
    total_steps = len(eps_to_download) + 5
    current_step = 0
    progress_bar = st.progress(0, text="Préparation...")
    
    def update_progress(step: int, message: str):
        nonlocal current_step
        current_step = step
        progress_bar.progress(min(current_step / total_steps, 0.99), text=message)
    
    # Download feed image
    update_progress(1, "Téléchargement de l'image du podcast...")
    feed_image_path = download_feed_image(feed, input_folder)
    
    # Download all episode audio + images
    st.info(f"📥 Téléchargement de {len(eps_to_download)} épisode(s)...")
    
    for i, ep in enumerate(eps_to_download):
        update_progress(i + 2, f"Téléchargement: {ep.title[:40]}...")
        if not download_episode_audio(ep, input_folder):
            st.warning(f"⚠️ Impossible de télécharger: {ep.title}")
        else:
            download_episode_image(ep, input_folder)
    
    update_progress(len(eps_to_download) + 2, "Construction de la structure...")
    
    # ── Build tree ────────────────────────────────────────────────────────────
    pack_title = st.session_state.pack_title or feed.title
    root = TreeNode(
        name=pack_title,
        path=input_folder,
        is_folder=True,
        item_image=feed_image_path
    )

    def make_story_node(ep):
        """Return a story TreeNode for a downloaded episode, or None if not downloaded."""
        if not ep.audio_path:
            return None
        return TreeNode(
            name=clean_name(ep.title),
            path=ep.audio_path,
            is_folder=False,
            audio_file=ep.audio_path,
            item_image=ep.image_path
        )

    if chapters:
        # ── Chaptered mode ────────────────────────────────────────────────────
        for ch in chapters:
            ch_eps = [ep for ep in ch["episodes"] if ep.audio_path]
            if not ch_eps:
                continue  # Skip empty chapters
            
            # Chapter folder node: use first episode image if available, else feed image
            ch_image = ch_eps[0].image_path if ch_eps[0].image_path else feed_image_path
            ch_folder = os.path.join(input_folder, clean_name(ch["name"]))
            ensure_dir(ch_folder)
            
            ch_node = TreeNode(
                name=ch["name"],
                path=ch_folder,
                is_folder=True,
                item_image=ch_image
            )
            for ep in ch_eps:
                story = make_story_node(ep)
                if story:
                    ch_node.children.append(story)
            
            if ch_node.children:
                root.children.append(ch_node)
    else:
        # ── Flat mode (no chapters) ───────────────────────────────────────────
        for ep in eps_to_download:
            story = make_story_node(ep)
            if story:
                root.children.append(story)
    
    if not root.children:
        st.error("❌ Aucun épisode n'a pu être téléchargé")
        return
    
    # ── Build options ─────────────────────────────────────────────────────────
    options = BuildOptions(
        title=pack_title,
        description=feed.description[:200] if feed.description else "",
        normalize_audio=st.session_state.get('normalize_audio', True),
        add_delay=st.session_state.get('add_delay', False),
        night_mode=st.session_state.get('night_mode', False),
        tts_model=st.session_state.get('tts_model', 'fr_FR-siwis-medium')
    )
    
    def build_progress(progress: float, message: str):
        update_progress(
            len(eps_to_download) + 3 + int(progress * 2),
            message
        )
    
    options.progress_callback = build_progress
    
    # ── Generate pack ─────────────────────────────────────────────────────────
    update_progress(len(eps_to_download) + 3, "Génération du pack...")
    builder = PackBuilder(options)
    
    if builder.build_from_tree(root):
        progress_bar.progress(1.0, text="Terminé!")
        
        zip_path = builder.get_output_zip_path()
        st.session_state.generation_complete = True
        st.session_state.output_zip_path = zip_path
        
        with open(zip_path, 'rb') as f:
            st.session_state.output_zip_data = f.read()
        st.session_state.output_pack_filename = f"{clean_name(feed.title)}_pack.zip"
        
        # Reset chapter mode
        st.session_state.rss_chapter_mode = False
        st.session_state.rss_chapters = None
        
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


def render_lunii_upload():
    """Affiche l'interface d'upload vers Lunii."""
    
    st.markdown("""
    Convertissez votre pack au format natif Lunii pour le charger directement 
    sur votre boîte à histoires.
    """)
    
    # Lunii version selector (inline)
    col_ver, col_info = st.columns([1, 2])
    with col_ver:
        version = st.selectbox(
            "Version Lunii",
            ["V2", "V3"],
            index=0 if st.session_state.get('lunii_version', 'V2') == 'V2' else 1,
            key="lunii_version_selector",
            help="V2 (XXTEA) est compatible avec la majorité des appareils"
        )
    with col_info:
        if version == "V2":
            st.info("🔐 Chiffrement **XXTEA** — Compatible tous appareils Lunii")
        else:
            st.warning("🔐 Chiffrement **AES-CBC** — Appareils V3 uniquement. Nécessite les clés de l'appareil.")
    
    # V3 key inputs (only if V3 selected)
    aes_key = None
    aes_iv = None
    if version == "V3":
        with st.expander("🔑 Clés de chiffrement V3", expanded=True):
            st.caption("Entrez les clés hexadécimales de votre appareil (trouvées dans le fichier .md)")
            key_hex = st.text_input("Clé AES (hex, 32 caractères)", key="aes_key_input")
            iv_hex = st.text_input("IV AES (hex, 32 caractères)", key="aes_iv_input")
            if key_hex and iv_hex:
                try:
                    aes_key = bytes.fromhex(key_hex.strip())
                    aes_iv = bytes.fromhex(iv_hex.strip())
                    if len(aes_key) != 16 or len(aes_iv) != 16:
                        st.error("Les clés doivent faire exactement 16 octets (32 caractères hex)")
                        aes_key = None
                        aes_iv = None
                except ValueError:
                    st.error("Format hexadécimal invalide")
    
    st.markdown("---")
    
    # === Conversion Button ===
    can_convert = version == "V2" or (aes_key is not None and aes_iv is not None)
    
    if st.session_state.get('lunii_conversion_complete') and st.session_state.get('lunii_zip_data'):
        # Already converted — show results
        st.success(f"✅ Pack Lunii prêt: **{st.session_state.lunii_zip_filename}**")
        
        st.download_button(
            "📥 Télécharger le Pack Lunii",
            st.session_state.lunii_zip_data,
            file_name=st.session_state.lunii_zip_filename,
            mime="application/zip",
            type="primary",
            use_container_width=True,
            key="download_lunii"
        )
        st.caption("Téléchargez le pack, puis utilisez le gestionnaire ci-dessous pour l'installer sur votre Lunii.")
        
        # Reconvert button
        if st.button("🔄 Reconvertir", key="reconvert_lunii"):
            st.session_state.lunii_conversion_complete = False
            st.session_state.lunii_zip_data = None
            st.session_state.lunii_zip_filename = None
            st.rerun()
        
        st.markdown("---")
        
        # === Lunii Device Manager ===
        st.subheader("🎧 Gestionnaire Lunii")
        _render_lunii_manager()
    
    elif can_convert:
        if st.button("🎧 Convertir pour Lunii", type="primary", use_container_width=True, key="convert_lunii"):
            _run_lunii_conversion(version, aes_key, aes_iv)
    else:
        st.warning("⚠️ Entrez les clés AES de votre appareil V3 pour continuer")


def _run_lunii_conversion(version: str, aes_key=None, aes_iv=None):
    """Execute the Lunii conversion with progress tracking."""
    import tempfile
    
    progress_bar = st.progress(0, text="Démarrage de la conversion...")
    status_text = st.empty()
    
    def update_progress(progress: float, message: str):
        progress_bar.progress(min(progress, 1.0), text=message)
        status_text.caption(message)
    
    try:
        # Write current pack ZIP to temp file
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            tmp.write(st.session_state.output_zip_data)
            tmp_path = tmp.name
        
        # Run conversion
        converter = LuniiPackConverter(
            zip_path=tmp_path,
            version=version,
            aes_key=aes_key,
            aes_iv=aes_iv
        )
        
        result_path = converter.convert(progress_callback=update_progress)
        
        if result_path:
            # Read result
            with open(result_path, 'rb') as f:
                lunii_data = f.read()
            
            # Store in session
            base_name = os.path.splitext(st.session_state.output_pack_filename or "pack")[0]
            st.session_state.lunii_zip_data = lunii_data
            st.session_state.lunii_zip_filename = f"{base_name}_lunii.zip"
            st.session_state.lunii_conversion_complete = True
            
            # Cleanup temp files
            try:
                os.unlink(tmp_path)
                if result_path != tmp_path:
                    os.unlink(result_path)
            except Exception:
                pass
            
            st.rerun()
        else:
            st.error("❌ La conversion a échoué. Vérifiez les logs pour plus de détails.")
            
    except Exception as e:
        st.error(f"❌ Erreur: {e}")
        logger.error(f"Lunii conversion error: {e}", exc_info=True)


def _render_lunii_manager():
    """Render the embedded Lunii device manager."""
    import streamlit.components.v1 as components
    
    # Read the JS file
    js_path = os.path.join(os.path.dirname(__file__), "static", "lunii_manager.js")
    try:
        with open(js_path, 'r', encoding='utf-8') as f:
            js_code = f.read()
    except FileNotFoundError:
        st.error("Fichier lunii_manager.js introuvable")
        return
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: transparent; color: #e0e0e0; }}
      
      /* Connect screen */
      .lm-connect, .lm-unsupported {{ text-align: center; padding: 40px 20px; }}
      .lm-connect .lm-icon, .lm-unsupported .lm-icon {{ font-size: 48px; margin-bottom: 12px; }}
      .lm-connect h3, .lm-unsupported h3 {{ font-size: 18px; margin-bottom: 8px; color: #fff; }}
      .lm-connect p, .lm-unsupported p {{ color: #999; font-size: 14px; margin-bottom: 20px; }}
      
      /* Buttons */
      .lm-btn {{ border: none; border-radius: 8px; padding: 10px 20px; font-size: 14px; cursor: pointer; transition: all 0.2s; }}
      .lm-btn-primary {{ background: linear-gradient(135deg, #667eea, #764ba2); color: #fff; }}
      .lm-btn-primary:hover {{ transform: translateY(-1px); box-shadow: 0 4px 12px rgba(102,126,234,0.4); }}
      .lm-btn-sm {{ background: rgba(255,255,255,0.1); color: #ccc; padding: 6px 12px; font-size: 13px; }}
      .lm-btn-sm:hover {{ background: rgba(255,255,255,0.2); }}
      .lm-btn-install {{ display: inline-flex; align-items: center; gap: 8px; }}
      
      /* Header */
      .lm-header {{ display: flex; align-items: center; justify-content: space-between; padding: 8px 0; margin-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.1); }}
      .lm-device-info {{ display: flex; align-items: center; gap: 12px; font-size: 13px; color: #999; }}
      .lm-badge {{ padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; text-transform: uppercase; }}
      .lm-badge-v2 {{ background: #2d5a27; color: #7ed47e; }}
      .lm-badge-v3 {{ background: #1a3a5c; color: #5da8e8; }}
      
      /* Install bar */
      .lm-install-bar {{ display: flex; align-items: center; gap: 16px; padding: 10px 0; margin-bottom: 8px; }}
      .lm-install-hint {{ font-size: 13px; color: #888; }}
      .lm-installing {{ display: flex; align-items: center; gap: 12px; padding: 12px 0; color: #aaa; font-size: 14px; }}
      
      /* Spinner */
      .lm-spinner {{ width: 20px; height: 20px; border: 2px solid rgba(255,255,255,0.2); border-top-color: #667eea; border-radius: 50%; animation: lm-spin 0.8s linear infinite; }}
      @keyframes lm-spin {{ to {{ transform: rotate(360deg); }} }}
      
      /* Pack list */
      .lm-pack-list {{ max-height: 500px; overflow-y: auto; }}
      .lm-pack {{ display: flex; align-items: center; gap: 12px; padding: 12px; margin-bottom: 6px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; transition: border-color 0.2s; position: relative; }}
      .lm-pack:hover {{ border-color: rgba(102,126,234,0.3); }}
      
      /* Arrows */
      .lm-pack-arrows {{ display: flex; flex-direction: column; gap: 2px; }}
      .lm-arrow {{ background: rgba(255,255,255,0.08); border: none; color: #888; padding: 3px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }}
      .lm-arrow:hover:not(:disabled) {{ background: rgba(102,126,234,0.3); color: #fff; }}
      .lm-arrow:disabled {{ opacity: 0.2; cursor: default; }}
      
      /* Pack info */
      .lm-pack-info {{ flex: 1; min-width: 0; }}
      .lm-pack-uuid {{ font-family: monospace; font-size: 11px; color: #667eea; background: rgba(102,126,234,0.12); padding: 2px 8px; border-radius: 4px; display: inline-block; margin-bottom: 4px; }}
      .lm-pack-title {{ font-weight: 600; font-size: 15px; color: #e8e8e8; }}
      .lm-pack-desc {{ font-size: 12px; color: #888; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
      
      /* Actions menu */
      .lm-pack-actions {{ position: relative; }}
      .lm-menu-btn {{ background: none; border: none; color: #888; font-size: 20px; cursor: pointer; padding: 4px 8px; border-radius: 4px; }}
      .lm-menu-btn:hover {{ background: rgba(255,255,255,0.1); color: #fff; }}
      .lm-menu {{ position: absolute; right: 0; top: 100%; background: #2a2a2e; border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding: 4px; z-index: 100; min-width: 180px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }}
      .lm-menu button {{ display: block; width: 100%; text-align: left; background: none; border: none; color: #ddd; padding: 8px 12px; border-radius: 4px; font-size: 13px; cursor: pointer; }}
      .lm-menu button:hover {{ background: rgba(255,255,255,0.08); }}
      .lm-menu .lm-danger {{ color: #e85d5d; }}
      .lm-menu .lm-danger:hover {{ background: rgba(232,93,93,0.12); }}
      
      /* Notification */
      .lm-notification {{ position: fixed; top: 12px; left: 50%; transform: translateX(-50%); padding: 10px 24px; border-radius: 8px; font-size: 14px; z-index: 1000; animation: lm-fade-in 0.2s; }}
      .lm-success {{ background: #1a3d1a; color: #7ed47e; border: 1px solid #2d5a27; }}
      .lm-error {{ background: #3d1a1a; color: #e85d5d; border: 1px solid #5a2727; }}
      @keyframes lm-fade-in {{ from {{ opacity: 0; transform: translateX(-50%) translateY(-10px); }} to {{ opacity: 1; transform: translateX(-50%) translateY(0); }} }}
      
      .lm-empty {{ text-align: center; padding: 30px; color: #666; font-size: 14px; }}
    </style>
    </head>
    <body>
      <div id="lunii-manager"></div>
      <script>{js_code}</script>
    </body>
    </html>
    """
    
    components.html(html, height=600, scrolling=True)


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
        # === SECTION 4: Upload Lunii ===
        with st.expander("🎧 4. Uploader dans ma Lunii", expanded=not st.session_state.get('lunii_conversion_complete')):
            render_lunii_upload()
    else:
        # Show disabled placeholders for sections 2, 3, and 4
        st.markdown("---")
        st.markdown("##### 👁️ 2. Aperçu & Vérification")
        st.info("💡 Générez d'abord un pack pour accéder à l'aperçu")
        
        st.markdown("##### 📥 3. Télécharger")
        st.info("💡 Générez d'abord un pack pour le télécharger")
        
        st.markdown("##### 🎧 4. Uploader dans ma Lunii")
        st.info("💡 Générez d'abord un pack pour l'envoyer vers votre Lunii")
    
    # Legal notice
    render_legal_notice()


if __name__ == "__main__":
    main()
