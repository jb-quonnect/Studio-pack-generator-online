# Suggestions d'amélioration (Sécurité, Performance & Code Health)
*Par Jules (Lovable.ai) - 2026-03-20*

Ce document synthétise les recommandations pour améliorer la base de code du projet "Studio Pack Generator Online". Ces suggestions sont prêtes à être ingérées et appliquées par l'agent Lovable.ai.

## 1. Sécurité : Prévention de la vulnérabilité "Zip Slip"
### Problème
L'extraction de fichiers ZIP dans `modules/zip_handler.py` utilise `zf.extractall(output_dir)` sans vérifier si les chemins des fichiers contenus dans le ZIP tentent de remonter dans l'arborescence (ex: `../../../etc/passwd`). Cela expose l'application à des attaques de type Path Traversal (Zip Slip).

### Fichier concerné
- `modules/zip_handler.py`

### Solution
Implémenter une vérification des chemins avant extraction pour s'assurer que chaque fichier extrait reste confiné dans le dossier de destination.

```python
<<<<<<< SEARCH
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(output_dir)
=======
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Sécurité anti-Zip Slip
            for member in zf.namelist():
                member_path = os.path.abspath(os.path.join(output_dir, member))
                if not member_path.startswith(os.path.abspath(output_dir)):
                    raise Exception(f"Tentative de Path Traversal détectée dans le ZIP: {member}")
            zf.extractall(output_dir)
>>>>>>> REPLACE
```

---

## 2. Robustesse : Exécution des sous-processus sans timeout
### Problème
Dans les fichiers gérant FFmpeg et d'autres binaires locaux (`audio_processor.py`, `tts_engine.py`, `lunii_converter.py`, `app.py`), les appels à `subprocess.run` sont souvent faits sans argument `timeout`. En cas de blocage d'un binaire (par ex., un fichier corrompu qui fait boucler FFmpeg), cela peut bloquer indéfiniment le thread Streamlit.

### Fichiers concernés
- `modules/audio_processor.py`
- `modules/tts_engine.py`

### Solution
Toujours ajouter un `timeout` explicite aux appels `subprocess.run` (et gérer l'exception `subprocess.TimeoutExpired`).

**Exemple (à répliquer dans tous les appels `subprocess.run`) :**

```python
<<<<<<< SEARCH
        result = subprocess.run([
            'ffmpeg', '-y', '-i', source_path,
            '-acodec', 'libmp3lame', '-ar', str(TARGET_SAMPLE_RATE),
            '-ac', str(TARGET_CHANNELS), '-b:a', '128k',
            output_path
        ], capture_output=True, text=True, check=True)
=======
        try:
            result = subprocess.run([
                'ffmpeg', '-y', '-i', source_path,
                '-acodec', 'libmp3lame', '-ar', str(TARGET_SAMPLE_RATE),
                '-ac', str(TARGET_CHANNELS), '-b:a', '128k',
                output_path
            ], capture_output=True, text=True, check=True, timeout=60)
        except subprocess.TimeoutExpired as e:
            logger.error(f"FFmpeg a dépassé le délai imparti pour le fichier {source_path}")
            raise RuntimeError("Le traitement audio a expiré.") from e
>>>>>>> REPLACE
```

---

## 3. Performance & Fuites Mémoire : État binaire stocké dans Streamlit
### Problème
Dans `app.py`, l'application stocke les fichiers ZIP générés directement sous forme de bytes dans `st.session_state` (ex: `st.session_state.output_zip_data = f.read()`). Pour des packs volumineux (plusieurs centaines de Mo), cela consomme beaucoup de RAM côté serveur par utilisateur actif, ce qui peut mener à des crashs de type OOM (Out Of Memory) si le serveur est très sollicité.

### Fichier concerné
- `app.py`

### Solution
Au lieu de stocker les bytes en mémoire dans le Session State, stocker uniquement le chemin absolu du fichier temporaire. Streamlit peut lire le fichier à la volée lorsqu'on clique sur le bouton de téléchargement via `with open(...) as f:`.

```python
<<<<<<< SEARCH
        # Store ZIP data for persistence across tabs
        with open(zip_path, 'rb') as f:
            st.session_state.output_zip_data = f.read()
        st.session_state.output_pack_filename = os.path.basename(zip_path)
=======
        # Store ZIP path for persistence across tabs
        st.session_state.output_zip_path = zip_path
        st.session_state.output_pack_filename = os.path.basename(zip_path)
        # Indique que le pack est généré (la donnée sera lue au moment du téléchargement)
        st.session_state.output_zip_data_ready = True
>>>>>>> REPLACE
```

Et lors du rendu du bouton de téléchargement :

```python
<<<<<<< SEARCH
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
=======
        with col2:
            if os.path.exists(st.session_state.output_zip_path):
                with open(st.session_state.output_zip_path, 'rb') as f:
                    st.download_button(
                        "📥 Télécharger",
                        f.read(),
                        file_name=st.session_state.output_pack_filename,
                        mime="application/zip",
                        type="primary",
                        use_container_width=True,
                        key="download_persistent"
                    )
            else:
                st.error("Le fichier du pack n'existe plus.")
>>>>>>> REPLACE
```

---

## 4. Code Health : Nettoyage automatique des sessions / fichiers temporaires
### Problème
Les dossiers temporaires gérés par `session_manager.py` ne sont pas explicitement purgés sur une base régulière globale. Bien que l'outil recrée des UUID de session, si un utilisateur génère de gros fichiers et part, les fichiers peuvent rester sur le disque du serveur (notamment sous Docker) et remplir l'espace disque.

### Fichier concerné
- `modules/session_manager.py`

### Solution
Mettre en place une tâche asynchrone ou un nettoyage opportuniste lors de la création d'une nouvelle session qui supprime les dossiers de `tmp/` de plus de X heures.

**Exemple d'ajout dans `session_manager.py` (fonction d'initialisation) :**

```python
<<<<<<< SEARCH
def reset_session_manager():
    """Reset the current session with a new ID and directory."""
    if 'session_manager' in st.session_state:
        # Clean up old directory if it exists
        old_dir = st.session_state.session_manager.session.base_dir
        if os.path.exists(old_dir):
            try:
                shutil.rmtree(old_dir)
            except Exception:
                pass

    st.session_state.session_manager = SessionManager()
=======
import time

def cleanup_old_sessions(base_tmp_dir="/tmp/studio_pack_sessions", max_age_hours=24):
    """Supprime les dossiers de session obsolètes pour libérer de l'espace."""
    if not os.path.exists(base_tmp_dir):
        return
    now = time.time()
    for item in os.listdir(base_tmp_dir):
        item_path = os.path.join(base_tmp_dir, item)
        if os.path.isdir(item_path):
            try:
                if os.stat(item_path).st_mtime < now - (max_age_hours * 3600):
                    shutil.rmtree(item_path)
            except Exception as e:
                logger.warning(f"Impossible de supprimer le vieux dossier de session {item_path}: {e}")

def reset_session_manager():
    """Reset the current session with a new ID and directory."""
    # Nettoyage opportuniste des vieilles sessions
    cleanup_old_sessions()

    if 'session_manager' in st.session_state:
        # Clean up old directory if it exists
        old_dir = st.session_state.session_manager.session.base_dir
        if os.path.exists(old_dir):
            try:
                shutil.rmtree(old_dir)
            except Exception:
                pass

    st.session_state.session_manager = SessionManager()
>>>>>>> REPLACE
```

---

## 5. Bonnes Pratiques : Gestion propre du path `os.path.join` pour Linux/Windows
### Problème
La base de code utilise beaucoup `os.path.join` et des manipulations de chaînes, ce qui est acceptable mais le module standard `pathlib.Path` est plus sûr, plus lisible et plus pythonique.

### Solution
Poursuivre la migration entamée dans certains modules de `os.path` vers `pathlib.Path` pour assurer la compatibilité multiplateforme et éviter les pièges des slash/antislash. C'est une recommandation "Code Health" générale (pas de diff spécifique, mais un point d'attention pour les prochains développements de Lovable.ai).
