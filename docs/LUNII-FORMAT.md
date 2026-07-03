# Format Lunii natif — Référence technique

Document de référence sur la construction d'un pack Lunii, issu de l'étude croisée de :

- l'algorithme actuel du projet ([modules/lunii_converter.py](../modules/lunii_converter.py) + [static/lunii_manager.js](../static/lunii_manager.js)) ;
- l'implémentation de référence Java **marian-m12l/studio** (`FsStoryPackWriter.java`, `XXTEACipher.java`, `BinaryStoryPackReader.java`) ;
- **olup/lunii-admin-web** (chiffrement, `bt`, index) ;
- l'**analyse en lecture seule d'une vraie Lunii V2** (54 packs : 2 officiels natifs, ~22 officiels transférés, ~29 communautaires, 1 généré par cette appli).

> Résumé exécutif : le format ci-dessous (structure, index, chiffrement, assets) est correct dans le convertisseur. **Un bug de navigation provoquait un crash (icône erreur) à la fin de chaque épisode** : les nœuds de lecture avaient AUTOPLAY activé mais **aucune transition OK**, or le firmware déclenche automatiquement la transition OK en fin d'audio → transition nulle → crash. Corrigé (voir [§7](#7-diagnostic-du-pack-généré-par-lappli-et-correctif)). Autres points d'attention : **divergences avec la référence** (dédoublonnage, silence ajouté, blank MP3), non bloquantes — voir [§8](#8-divergences-connues-avec-la-référence).
>
> **Règle d'or à ne jamais violer : tout nœud avec `autoplay = true` DOIT avoir une transition OK valide** (offset 8 du nœud `ni` ≠ -1). Vérifié sur tous les packs officiels et communautaires.

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
- Référence Java : 64 kbps recommandé. Le convertisseur exporte désormais en **64 kbps** (aligné sur la référence ; réduit aussi la taille des packs de moitié, ce qui évite la limite de 200 Mo du service statique Streamlit — voir CLAUDE.md « Pièges connus »).
- Vérifié sur le pack de l'appli : sync MPEG valide, `id3=False`, en-tête `FF FB 90 C0` = MPEG1-L3 128k / 44100 / mono ✓.

---

## 7. Diagnostic du pack généré par l'appli, et correctif

Packs concernés : `B14E6B99` (« Les aventures de Tina ») et « La discomobile » — même symptôme : **crash avec icône « error » à la fin de la lecture d'un épisode**.

### Ce qui est correct

- en-tête `ni` cohérent (fmt=1, offset=512, nsize=44, factory=1) ;
- comptages cohérents (`rf = imgcount = ri`, `sf = sndcount = si`), aucun index hors bornes ;
- `bt` identique au recalcul `XXTEA(ri_chiffré[:64], specificKey_appareil)` ✓ ;
- assets BMP (4-bit RLE 320×240) et MP3 (mono 44100Hz sans ID3) au bon format.

### La cause du crash (confirmée)

Les **nœuds de lecture** (playback) issus de l'expansion `storyAudio` avaient :

```
flags : W0 O0 H1 P1 A1   (autoplay activé)
okTransition : -1         ← AUCUNE transition OK
homeTransition : → menu racine
```

Sur l'appareil, **AUTOPLAY signifie « déclencher automatiquement la transition OK à la fin de l'audio »**. Avec `okTransition = -1`, le firmware suit une transition nulle en fin d'épisode → **icône erreur / crash**. Comparaison sans appel (nœuds autoplay `00111` / `01101`) :

| Pack | Nœud de lecture (autoplay) | Transition OK |
|------|----------------------------|---------------|
| Officiel `F3C18541` (Douce Nuit) | oui | `ok(17,1,0)` ✓ |
| Officiel `10880D15` | oui | `ok(43,1,0)`… ✓ |
| Communautaire `03507E09` | oui | `ok(5,1,0)` ✓ |
| **App (avant correctif)** | oui | **`ok(-1,-1,-1)` ✗ → crash** |

> ⚠️ Le premier script de diagnostic ne testait pas cette règle et avait conclu à tort « aucun problème ». Le défaut était bien réel et présent dans les packs générés.

### Correctif appliqué

Dans l'expansion `storyAudio` de [lunii_converter.py](../modules/lunii_converter.py), le nœud de lecture reçoit désormais une **transition OK vers le menu parent** (repris du `homeTransition` d'origine du nœud d'histoire, sinon menu racine), en plus du `homeTransition` — exactement le schéma des packs officiels. Filet de sécurité universel ajouté dans `generate_ni()` : si un nœud a `autoplay` sans transition OK, l'autoplay est **désactivé** (log d'alerte) pour empêcher tout crash, quelle que soit l'origine du story.json.

**Vérification de bout en bout** : conversion réelle d'un pack Studio à 7 épisodes → 8 nœuds autoplay, **tous avec transition OK valide** (`ok(0,1,0)` → menu), 0 violation. ➡️ **Régénérer et réinstaller les packs avec cette version pour corriger le crash.**

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
