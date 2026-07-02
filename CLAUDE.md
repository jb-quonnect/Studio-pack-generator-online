# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Le projet

Application web Streamlit (Python 3.11+) pour créer des packs audio au format [Studio](https://github.com/marian-m12l/studio) destinés aux boîtes à histoires (Lunii, Telmi, Conty…). Fork fonctionnel de [jersou/studio-pack-generator](https://github.com/jersou/studio-pack-generator) (Deno/CLI) réécrit en Python/Streamlit. Documentation et échanges en **français**.

## Workflow git & déploiement — IMPORTANT

- Commits **directement sur `main`**, poussés au fil de l'eau sur `origin` (GitHub `jb-quonnect/Studio-pack-generator-online`).
- **Chaque push sur `main` déclenche un déploiement automatique en production via Coolify (Nixpacks, port 8501).** Toujours tester localement avant de pousser.
- Style de commit : conventional commits en anglais (`feat:`, `fix:`, `chore:`), éventuellement avec scope (`fix(nav): …`).

## Commandes

```powershell
# Lancer l'app en local (Windows, venv dans .venv/)
.venv\Scripts\streamlit run app.py    # → http://localhost:8501

# Installer les dépendances
.venv\Scripts\pip install -r requirements.txt
```

- Prérequis système : **FFmpeg** dans le PATH (conversion audio).
- En production (Docker/`start.sh`) : Linux avec ffmpeg + espeak-ng ; Piper TTS n'est fonctionnel que sous Linux — en local Windows, le TTS bascule automatiquement sur gTTS (nécessite internet).
- Pas de suite de tests ni de linter configurés à ce jour.

## Architecture

Flux global : **entrée (RSS / fichiers / ZIP) → arbre de navigation → conversion des médias → story.json → ZIP Studio → (optionnel) format Lunii natif / transfert sur l'appareil**.

- [app.py](app.py) — Monolithe Streamlit (~1750 lignes) : configuration de page, onglets d'entrée (`render_input_tabs` : RSS, upload fichiers, import ZIP, extraction), options expertes, simulateur/éditeur, gestionnaire Lunii. Point d'entrée `main()`.
- [modules/pack_builder.py](modules/pack_builder.py) — Orchestrateur de la génération : parse la structure en `TreeNode`, convertit les médias, génère les audios de navigation manquants (TTS), construit le story.json. C'est ici que se joue la logique des `stageNodes`/`actionNodes` (controlSettings, optionIndex, transitions home) — zone sensible, plusieurs bugs de navigation y ont été corrigés (voir historique git).
- [modules/story_generator.py](modules/story_generator.py) — Génération/chargement du story.json (structure de navigation Studio).
- [modules/lunii_converter.py](modules/lunii_converter.py) — Conversion du pack Studio vers le **format Lunii natif** : BMP 4-bit grayscale RLE, MP3 mono 44100Hz 64kbps sans ID3, chiffrement XXTEA (V2) ou AES-CBC (V3), fichiers d'index binaires (.ni, .li, .ri, .si, .bt). Spécifications dans [SPECIFICATIONS-lunii-admin-web.md](SPECIFICATIONS-lunii-admin-web.md).
- [static/lunii_manager.js](static/lunii_manager.js) — Gestionnaire d'appareil Lunii côté navigateur (File System Access API), injecté via `components.html` depuis app.py : détection V2/V3, liste/réordonnancement/suppression/installation des packs. Basé sur l'architecture d'olup/lunii-admin-web. Le chiffrement XXTEA y est réimplémenté en JS et doit rester cohérent avec la version Python de lunii_converter.py.
- [modules/rss_handler.py](modules/rss_handler.py) + [modules/radiofrance_api.py](modules/radiofrance_api.py) + [modules/podcast_search.py](modules/podcast_search.py) — Import podcast : parsing RSS (feedparser), API interne Radio France (historique complet des épisodes au-delà de la limite RSS), recherche unifiée iTunes + Radio France. Les épisodes sont triés chronologiquement et numérotés.
- [modules/audio_processor.py](modules/audio_processor.py) / [modules/image_processor.py](modules/image_processor.py) — Formats cibles : MP3 44100Hz mono normalisé (FFmpeg) ; PNG 320x240 avec padding noir (Pillow).
- [modules/tts_engine.py](modules/tts_engine.py) — TTS pour les audios de navigation : Piper (voix française HD, Linux) avec fallback gTTS ; cache dans `.tts_cache/`.
- [modules/zip_handler.py](modules/zip_handler.py) — Import/export/extraction/agrégation de ZIP de packs.
- [modules/session_manager.py](modules/session_manager.py) — Sessions éphémères : dossier temporaire par utilisateur, nettoyé après usage.
- [ui/](ui/) — Composants Streamlit : simulateur de navigation (`simulator.py`), éditeur de pack (renommer/réordonner/supprimer, `editor.py`), éditeur d'images (`image_editor.py`).

## Références du domaine

- [SPECIFICATIONS.md](SPECIFICATIONS.md) — Spécifications fonctionnelles héritées du projet jersou : format du story.json, algorithmes audio/images, logique TTS, extraction inverse.
- [SPECIFICATIONS-lunii-admin-web.md](SPECIFICATIONS-lunii-admin-web.md) — Format Lunii natif et transfert vers l'appareil.
- **[docs/LUNII-FORMAT.md](docs/LUNII-FORMAT.md) — Référence technique complète du format Lunii natif** (issue de l'étude croisée code actuel / STUdio Java / lunii-admin-web / analyse d'une vraie Lunii V2). À lire avant toute modification de [lunii_converter.py](modules/lunii_converter.py) ou [lunii_manager.js](static/lunii_manager.js).
- Structure d'un pack Studio : `story.json` + `thumbnail.png` + `assets/` (images 320x240, MP3). La cohérence des `controlSettings` et `optionIndex` entre nœuds de menu, nœuds d'annonce et nœuds d'histoire est critique pour la navigation réelle sur Lunii (le simulateur ne reproduit pas tous les comportements de l'appareil).

### Format Lunii — points critiques (résumé, détails dans docs/LUNII-FORMAT.md)

- **Deux formes** : bibliothèque (assets en clair) vs appareil (premier bloc de 512 o de chaque fichier chiffré). Le convertisseur génère directement la **forme appareil**.
- **Structure pack** : `.content/REF/` avec `ni` (nodes, **en clair**, en-tête 512 o + nœuds 44 o), `li`/`ri`/`si` (index, 1er bloc chiffré), `rf/`/`sf/` (BMP 4-bit RLE 320×240 / MP3 mono 44100Hz sans ID3, 1er bloc chiffré), `bt` (boot). REF = 8 derniers hex de l'UUID en majuscules.
- **Chiffrement V2** : XXTEA clé commune ; piège d'endianness — **données en little-endian, clé en big-endian**. Le `bt` = `XXTEA(ri_chiffré[:64], specificKey_de_l'appareil)` → il est **régénéré à l'installation par le JS** (clé propre à l'appareil, pas au pack). Appareils : `.md` offset 0 = 1/3 → V2, 6/7 → V3 (AES).
- **`.pi`** à la racine = liste brute des UUID (16 o chacun) des packs installés ; un pack absent du `.pi` est invisible. **`factory=1`** dans l'en-tête `ni` évite l'inspection par l'appli officielle.
- **État vérifié (juillet 2026)** : le convertisseur actuel produit un pack **techniquement valide** (contrôlé octet par octet sur une Lunii V2 — index, BMP, MP3, bt tous corrects). Les plantages historiques venaient de `controlSettings`/`optionIndex` incohérents, corrigés. Divergences résiduelles avec la référence Java (non bloquantes) : pas de dédoublonnage SHA1 des assets, 1 s de silence ajoutée à chaque audio, `BLANK_MP3` minuscule non validé, bitrate 128k au lieu de 64k.
- **⚠️ Ne jamais écrire sur `.pi`/`.cfg`/`.md` d'un appareil branché** hors du flux d'installation testé : risque de brique.

## Conventions locales

- Machine de dev : Windows 11 ; la prod tourne sous Linux (Docker/Nixpacks). Attention aux différences de chemins et à la disponibilité de Piper.
- Les fichiers de test locaux (packs .zip, story.json extraits, scripts de test à la racine, `studio_repo_tmp/`) sont ignorés par git — ne pas les committer.
