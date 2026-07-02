# Format Lunii natif — Référence technique

Document de référence sur la construction d'un pack Lunii, issu de l'étude croisée de :

- l'algorithme actuel du projet ([modules/lunii_converter.py](../modules/lunii_converter.py) + [static/lunii_manager.js](../static/lunii_manager.js)) ;
- l'implémentation de référence Java **marian-m12l/studio** (`FsStoryPackWriter.java`, `XXTEACipher.java`, `BinaryStoryPackReader.java`) ;
- **olup/lunii-admin-web** (chiffrement, `bt`, index) ;
- l'**analyse en lecture seule d'une vraie Lunii V2** (54 packs : 2 officiels natifs, ~22 officiels transférés, ~29 communautaires, 1 généré par cette appli).

> Résumé exécutif : le convertisseur actuel produit un pack **techniquement valide** (vérifié octet par octet sur l'appareil). Le format ci-dessous est correct. Les points d'attention restants sont des **divergences avec la référence** (dédoublonnage, silence ajouté, blank MP3) qui ne cassent pas le pack mais méritent d'être connues. Voir [§7](#7-diagnostic-du-pack-généré-par-lappli) et [§8](#8-divergences-connues-avec-la-référence).

---

## 1. Vue d'ensemble

Un pack Lunii vit sous deux formes :

| Forme | Où | Assets | Chiffrement |
|-------|-----|--------|-------------|
| **Bibliothèque (STUdio local / archive)** | disque STUdio | BMP + MP3 en clair | aucun (fichier marqueur `.cleartext`) |
| **Appareil (FS format, firmware ≥ 2.4)** | clé USB Lunii, `/.content/REF/` | idem | **premier bloc (512 o) de chaque fichier chiffré** |

Ce projet génère **directement la forme appareil** (assets déjà chiffrés dans le ZIP livré), puis le composant JS copie sur la clé et régénère le `bt` avec la clé propre à l'appareil.

### Racine de l'appareil

```
F:\ (clé Lunii montée)
├── .md            # métadonnées appareil (version, firmware, série, UUID appareil) — NE PAS modifier
├── .pi            # index des packs : concaténation brute des UUID (16 o chacun)
├── .cfg           # configuration appareil (langue, volume…) — NE PAS modifier
└── .content/
    └── XXXXXXXX/   # un dossier par pack, REF = 8 derniers hex de l'UUID, en MAJUSCULES
```

### Contenu d'un dossier pack

| Fichier | Rôle | Chiffré ? |
|---------|------|-----------|
| `ni` | Node Index — en-tête (512 o) + nœuds de 44 o | **non** (en clair) |
| `li` | List Index — options des menus (int32 = index de stage node) | oui (1er bloc) |
| `ri` | Resource Index — chemins des images `000\XXXXXXXX` (12 o/asset) | oui (1er bloc) |
| `si` | Sound Index — chemins des sons `000\XXXXXXXX` (12 o/asset) | oui (1er bloc) |
| `rf/000/00000000…` | images BMP 4-bit RLE | oui (1er bloc) |
| `sf/000/00000000…` | sons MP3 mono | oui (1er bloc) |
| `bt` | Boot — 1ers 64 o de `ri` (déjà chiffré) re-chiffrés avec la **clé spécifique de l'appareil** | oui (double) |
| `nm` | Night mode (fichier vide, présent seulement si dispo) | — |
| `md` | Métadonnées YAML (title/uuid/ref…) — **lues par les outils, ignorées par le firmware** | non |

> ⚠️ Le `ni` est **en clair** ; seuls `li`, `ri`, `si`, les assets et `bt` ont leur premier bloc chiffré. C'est ce que fait le convertisseur actuel.

---

## 2. Détection de l'appareil (`.md`)

Lu par `getDeviceModel()` dans lunii_manager.js — `uint16` little-endian à l'offset 0 :

| Valeur offset 0 | Modèle | Version chiffrement |
|-----------------|--------|---------------------|
| 1 | Lunii v1 | V2 (XXTEA clé commune) |
| 3 | Lunii v2 | **V2** (XXTEA clé commune) |
| 6 ou 7 | Lunii v3 | V3 (AES-CBC clé appareil) |

**Appareil testé** : offset 0 = `3` → **Lunii V2**, firmware `2.22` (offsets 6/8), UUID appareil aux octets `256..512` du `.md` (256 o) qui sert à calculer la *specific key* pour le `bt`.

---

## 3. Chiffrement

### V2 — XXTEA, clé commune

- Clé commune (16 o) : `91 BD 7A 0A A7 54 40 A9 BB D4 9D 6C E0 DC C0 E3`
- Delta : `0x9E3779B9`
- **Endianness (piège majeur)** : les **données** sont lues en uint32 *little-endian*, mais la **clé** en *big-endian* (voir `_bytes_to_uint32_le` vs `_bytes_to_uint32_be`). Toute réimplémentation doit respecter cette asymétrie, sinon le pack est illisible.
- **Chiffrement partiel** : seul le premier bloc de `min(512, taille)` octets est chiffré (`encrypt_first_block`), le reste du fichier reste en clair. Identique côté olup/lunii-admin-web.

### Specific key (pour le `bt`)

`v2_compute_specific_key(uuid)` = XXTEA-**décrypte** l'UUID (16 o) avec la clé commune, puis réordonne les octets `[11,10,9,8, 15,14,13,12, 3,2,1,0, 7,6,5,4]`. Pour le `bt` d'un pack, l'UUID utilisé est **celui de l'appareil** (pas du pack) — c'est pourquoi le `bt` est régénéré à l'installation par le JS, pas par Python.

### V3 — AES-CBC

Clé + IV propres à chaque appareil (dans le `.md`). Un pack « universel » ne peut pas être pré-chiffré en V3 : la stratégie du projet est de générer en V2 (clé commune), les appareils V3 gérant la conversion à la copie. Le convertisseur Python supporte `aes_cbc_encrypt` (nécessite le paquet `cryptography`) mais le chemin nominal reste V2.

---

## 4. Fichier `ni` (Node Index)

En-tête (512 o, little-endian) — cf. `FsStoryPackWriter.write()` et `generate_ni()` :

| Offset | Type | Champ | Valeur |
|--------|------|-------|--------|
| 0 | uint16 | version format NI | `1` |
| 2 | int16 | version du story pack | `story.version` (souvent 1 ou 2) |
| 4 | int32 | offset du 1er nœud | `512` |
| 8 | int32 | taille d'un nœud | `44` |
| 12 | int32 | nombre de stage nodes | |
| 16 | int32 | nombre d'images (distinctes côté Java) | |
| 20 | int32 | nombre de sons (distincts côté Java) | |
| 24 | int8 | **factory flag** = `1` | évite l'inspection par l'appli Luniistore officielle |

Chaque **stage node** (44 o) :

| Offset | Type | Champ |
|--------|------|-------|
| 0 | int32 | index image dans `ri` (`-1` si aucune) |
| 4 | int32 | index son dans `si` (`-1` si aucun) |
| 8 | int32 | OK transition → position absolue dans `li` (`-1` = aucune) |
| 12 | int32 | OK transition → nombre d'options |
| 16 | int32 | OK transition → optionIndex sélectionné (`-1` = laisser la molette choisir) |
| 20 | int32 | HOME transition → position absolue dans `li` |
| 24 | int32 | HOME transition → nombre d'options |
| 28 | int32 | HOME transition → optionIndex |
| 32 | int16 | WHEEL activée |
| 34 | int16 | OK activée |
| 36 | int16 | HOME activée |
| 38 | int16 | PAUSE activée |
| 40 | int16 | AUTOPLAY (auto-jump) activé |
| 42 | int16 | padding (0) |

### Sémantique des `controlSettings` (observée)

- **Nœud menu / entrée** : `WHEEL=1 OK=1` (+ `HOME=1` sauf au sommet), `PAUSE=0 AUTOPLAY=0`. `optionIndex=-1` sur le menu racine = la molette fait défiler ; `optionIndex≥0` = saut automatique vers cette option.
- **Nœud d'histoire (playback)** : `WHEEL=0 OK=0 HOME=1 PAUSE=1 AUTOPLAY=1`, `okTransition=-1`, `homeTransition` → retour menu.
- **Nœud d'annonce** (titre d'épisode) : comme un nœud menu, `okTransition` → nœud playback.

C'est exactement le schéma que produit l'expansion `storyAudio` du convertisseur : chaque épisode devient **annonce (image + court audio) → playback (audio complet, sans image)**. Un `controlSettings` incohérent (ex. AUTOPLAY sur un menu, ou `optionIndex` hors bornes) est la **cause classique de plantage / NPE STUdio** — d'où la série de correctifs de l'historique git.

---

## 5. Fichiers `li`, `ri`, `si`

- **`li`** : pour chaque action node (menu), et pour chaque option, un `int32` = index du stage node cible. La **position absolue** d'un action node = somme des tailles des action nodes précédents. Référence Java : les action nodes sont indexés **dans l'ordre de première référence** par les stage nodes ; le convertisseur Python les indexe **dans l'ordre du tableau `actionNodes`**. Les deux sont *auto-cohérents* (le `ni` pointe correctement dans son propre `li`), donc valides, mais l'ordre peut différer.
- **`ri` / `si`** : concaténation de chaînes ASCII de 12 o, format `000\%08d` (backslash littéral, index **décimal** sur 8 chiffres). Identique Java/Python.

---

## 6. Images et audio

### Image → BMP 4-bit grayscale RLE

- 320×240, 16 niveaux de gris, compression **BI_RLE4** (`comp=2`), palette 16 entrées `(255/16)*i`.
- BMP *bottom-up* → flip vertical avant encodage.
- Le firmware (et le lecteur Java) **rejettent** tout ce qui n'est pas exactement 4-bit / RLE / 320×240.
- Vérifié sur le pack de l'appli : `sig=BM bpp=4 comp=2 320x240` ✓.

### Audio → MP3 mono 44100 Hz

- Mono, 44100 Hz, **sans tags ID3** (le lecteur Java rejette tout ID3v1/ID3v2).
- Référence Java : 64 kbps recommandé. Le convertisseur actuel exporte en **128 kbps** (le commentaire du code dit encore 64k — incohérence cosmétique).
- Vérifié sur le pack de l'appli : sync MPEG valide, `id3=False`, en-tête `FF FB 90 C0` = MPEG1-L3 128k / 44100 / mono ✓.

---

## 7. Diagnostic du pack généré par l'appli

Pack testé : `B14E6B99` (« Les aventures de Tina », `installedBy: StudioPackGenerator`), 65 nœuds / 35 images / 65 sons.

**Tous les contrôles vérifiables passent :**

- en-tête `ni` cohérent (fmt=1, offset=512, nsize=44, factory=1) ;
- `rf = imgcount = ri = 35` et `sf = sndcount = si = 65` (aucun décalage) ;
- **aucun index hors bornes** (image/son/li/optionIndex tous valides) ;
- structure de navigation cohérente (entrée → menu 3 chapitres → menus 10 histoires → annonce → playback, avec `homeTransition` de retour) ;
- `bt` **identique** au recalcul `XXTEA(ri_chiffré[:64], specificKey_appareil)` ✓ ;
- assets BMP et MP3 au bon format.

**Conclusion** : le pack est techniquement valide et devrait être visible/jouable. Le symptôme « invisible / plantage » rapporté provient très probablement d'un **build antérieur** aux correctifs récents (controlSettings/optionIndex/expansion storyAudio/`optionIndex=-1` sur l'entrypoint). ➡️ **Action recommandée : régénérer et réinstaller le pack avec la version actuelle, puis retester sur l'appareil.**

---

## 8. Divergences connues avec la référence

Ces points ne cassent pas le pack testé mais s'écartent de l'implémentation Java de référence et constituent des risques/dettes :

1. **Pas de dédoublonnage par SHA1.** Java dédoublonne images *et* sons par hash ; le convertisseur écrit un asset **par nœud** (`build_audio_asset_list` : une entrée par stage node, même identique). Conséquence : packs plus lourds, transferts plus longs, risque d'approcher les limites sur de très gros packs. À porter si on veut coller à la référence.
2. **1 seconde de silence ajoutée** en tête de chaque audio (`convert_audio_to_lunii_mp3`) — contournement d'une troncature firmware. Non standard : retarde la lecture et gonfle les fichiers. À réévaluer / rendre optionnel.
3. **`BLANK_MP3` minuscule (~104 o) fait main** pour les nœuds sans audio, là où Java insère un vrai MP3 silencieux validé (mono/44100). Non déclenché sur le pack testé (les 65 sons étaient réels) mais risque si un nœud se retrouve sans audio.
4. **`md` réécrit à l'installation** par le JS en `packType: archive` + `installedBy: StudioPackGenerator`, perdant `description`/`ref` écrits par Python (`packType: custom`). Purement cosmétique (le firmware ignore `md`), mais c'est le marqueur qui identifie l'origine d'un pack.
5. **`nm` et `.cleartext` non générés.** Correct pour la forme appareil (`.cleartext` = forme bibliothèque uniquement ; `nm` optionnel).
6. **Bitrate 128k** au lieu de 64k recommandé (voir §6).

---

## 9. Classification des packs (appareil testé, 54 packs)

| Origine | `packType` (md) | Nommage assets | Exemple |
|---------|-----------------|----------------|---------|
| Officiel natif (Luniistore) | `lunii` | **hash hex** (`0CD1F8A0`) | Douce Nuit, Les Animaux de Versailles |
| Officiel transféré (ancien outil) | `unknown` | séquentiel (`00000000`) | Cornebidouille, J'aime Lire, OLI 1-4 |
| Communautaire (STUdio / lunii-admin) | `custom` | séquentiel | Le Petit Nicolas, Tintin, France Inter… |
| **Généré par cette appli** | `archive` + `installedBy: StudioPackGenerator` | séquentiel | Les aventures de Tina |

Différence notable : les packs **officiels natifs** nomment leurs assets par **hash** et déclarent `packType: lunii` ; tout le reste (dont notre appli) utilise un nommage **séquentiel** `000\00000000…`, ce que le firmware accepte parfaitement (le nom réel vient de `ri`/`si`, pas du nom de fichier).

---

## 10. Reproduire l'analyse (lecture seule)

Scripts jetables utilisés (adapter la lettre de lecteur) : parser `.pi` (UUID 16 o), lister `.content/*/md`, déchiffrer le 1er bloc de `ri`/`si`/`li` à la clé commune V2, parser les nœuds `ni`, recalculer le `bt` avec la specific key issue du `.md`. **Ne jamais écrire sur l'appareil** : toute modification de `.pi`/`.cfg`/`.md` peut le rendre inutilisable.
