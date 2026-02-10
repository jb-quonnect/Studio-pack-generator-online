/**
 * Lunii Device Manager — Web File System Access API
 * 
 * Full pack management for Lunii devices:
 * - Connect to device via directory picker
 * - Auto-detect V2/V3 from .md binary
 * - List installed packs (read .pi + .content/REF/md YAML)
 * - Reorder packs (↑/↓)
 * - Delete packs
 * - Install new packs (copy ZIP content to device + generate BT)
 * 
 * Based on olup/lunii-admin-web architecture.
 */

(function () {
    'use strict';

    // ─── State ───────────────────────────────────────────────────────────────
    let deviceHandle = null;
    let deviceInfo = null;
    let packs = [];
    let isInstalling = false;

    // ─── XXTEA Encryption ────────────────────────────────────────────────────
    // Matching reference crypto/xxtea.ts exactly

    const XXTEA_DELTA = 0x9E3779B9;

    const V2_COMMON_KEY = new Uint8Array([
        0x91, 0xBD, 0x7A, 0x0A, 0xA7, 0x54, 0x40, 0xA9,
        0xBB, 0xD4, 0x9D, 0x6C, 0xE0, 0xDC, 0xC0, 0xE3
    ]);

    function toUint32Array(bytes, littleEndian = true) {
        const length = bytes.length;
        let n = length >> 2;
        if ((length & 3) !== 0) n++;
        const v = new Uint32Array(n);

        if (littleEndian) {
            for (let i = 0; i < length; i++) {
                v[i >> 2] |= bytes[i] << ((i & 3) << 3);
            }
        } else {
            for (let i = 0; i < length; i++) {
                v[i >> 2] |= bytes[length - 1 - i] << ((i & 3) << 3);
            }
            v.reverse();
        }
        return v;
    }

    function fromUint32Array(v) {
        const n = v.length << 2;
        const bytes = new Uint8Array(n);
        for (let i = 0; i < n; i++) {
            bytes[i] = (v[i >> 2] >> ((i & 3) << 3)) & 0xFF;
        }
        return bytes;
    }

    function xxteaMx(sum, y, z, p, e, k) {
        return (
            (((z >>> 5) ^ (y << 2)) + ((y >>> 3) ^ (z << 4))) ^
            ((sum ^ y) + (k[(p & 3) ^ e] ^ z))
        );
    }

    function xxteaEncryptUint32(v, k) {
        const length = v.length;
        const n = length - 1;
        let z = v[n];
        let sum = 0;
        const q = Math.floor(1 + 52 / length);

        for (let round = 0; round < q; round++) {
            sum = (sum + XXTEA_DELTA) >>> 0;
            const e = (sum >>> 2) & 3;
            for (let p = 0; p < n; p++) {
                const y = v[p + 1];
                const mx = xxteaMx(sum, y, z, p, e, k);
                v[p] = (v[p] + mx) >>> 0;
                z = v[p];
            }
            const y = v[0];
            const mx = xxteaMx(sum, y, z, n, e, k);
            v[n] = (v[n] + mx) >>> 0;
            z = v[n];
        }
        return v;
    }

    function xxteaDecryptUint32(v, k) {
        const length = v.length;
        const n = length - 1;
        const q = Math.floor(1 + 52 / length);
        let sum = (q * XXTEA_DELTA) >>> 0;
        let y = v[0];

        for (let round = 0; round < q; round++) {
            const e = (sum >>> 2) & 3;
            for (let p = n; p > 0; p--) {
                const z = v[p - 1];
                const mx = xxteaMx(sum, y, z, p, e, k);
                v[p] = (v[p] - mx) >>> 0;
                y = v[p];
            }
            const z = v[n];
            const mx = xxteaMx(sum, y, z, 0, e, k);
            v[0] = (v[0] - mx) >>> 0;
            y = v[0];
            sum = (sum - XXTEA_DELTA) >>> 0;
        }
        return v;
    }

    function encryptXxtea(block, key) {
        if (block.length < 8) return block;
        const dataInt = toUint32Array(block, true);   // data: LE
        const keyInt = toUint32Array(key, false);      // key: BE
        const encrypted = xxteaEncryptUint32(dataInt, keyInt);
        return fromUint32Array(encrypted);
    }

    function decryptXxtea(block, key) {
        if (block.length < 8) return block;
        const dataInt = toUint32Array(block, true);
        const keyInt = toUint32Array(key, false);
        const decrypted = xxteaDecryptUint32(dataInt, keyInt);
        return fromUint32Array(decrypted);
    }

    // ─── V2 Key Derivation ──────────────────────────────────────────────────

    function encryptFirstBlock(data, encryptFn, size = 512) {
        const firstBlockLength = Math.min(size, data.length);
        const firstBlock = data.subarray(0, firstBlockLength);
        const encryptedBlock = encryptFn(firstBlock);
        if (encryptedBlock.length > data.length) return encryptedBlock;
        const output = new Uint8Array(data);
        output.set(encryptedBlock);
        return output;
    }

    function v2ComputeSpecificKey(uuidBytes) {
        const decrypted = decryptXxtea(uuidBytes, V2_COMMON_KEY);
        return new Uint8Array([
            decrypted[11], decrypted[10], decrypted[9], decrypted[8],
            decrypted[15], decrypted[14], decrypted[13], decrypted[12],
            decrypted[3], decrypted[2], decrypted[1], decrypted[0],
            decrypted[7], decrypted[6], decrypted[5], decrypted[4],
        ]);
    }

    function v2GenerateBt(riEncrypted, specificKey) {
        const firstBlockLength = Math.min(64, riEncrypted.length);
        const firstBlock = riEncrypted.subarray(0, firstBlockLength);
        return encryptXxtea(firstBlock, specificKey);
    }

    // ─── UUID Utilities ──────────────────────────────────────────────────────

    function bytesToUUID(bytes) {
        const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    function uuidToBytes(uuid) {
        const hex = uuid.replace(/-/g, '');
        const bytes = new Uint8Array(16);
        for (let i = 0; i < 16; i++) {
            bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
        }
        return bytes;
    }

    function uuidToRef(uuid) {
        return uuid.replace(/-/g, '').slice(-8).toUpperCase();
    }

    // ─── File System Helpers ─────────────────────────────────────────────────

    async function getFileHandle(dirHandle, path) {
        const parts = path.split('/').filter(p => p);
        let current = dirHandle;
        for (let i = 0; i < parts.length - 1; i++) {
            current = await current.getDirectoryHandle(parts[i]);
        }
        return current.getFileHandle(parts[parts.length - 1]);
    }

    async function readFileAsBytes(fileHandle) {
        const file = await fileHandle.getFile();
        return new Uint8Array(await file.arrayBuffer());
    }

    async function readFileAsText(fileHandle) {
        const file = await fileHandle.getFile();
        return file.text();
    }

    async function writeFile(dirHandle, name, data, create = false) {
        const handle = await dirHandle.getFileHandle(name, { create });
        const writable = await handle.createWritable();
        await writable.write(data);
        await writable.close();
    }

    async function writeFileAtPath(rootHandle, path, data) {
        const parts = path.split('/').filter(p => p);
        let current = rootHandle;
        for (let i = 0; i < parts.length - 1; i++) {
            current = await current.getDirectoryHandle(parts[i], { create: true });
        }
        await writeFile(current, parts[parts.length - 1], data, true);
    }

    // ─── Simple YAML Parser (for pack metadata) ─────────────────────────────

    function parseSimpleYaml(text) {
        const result = {};
        for (const line of text.split('\n')) {
            const match = line.match(/^(\w+):\s*(.*)$/);
            if (match) {
                result[match[1]] = match[2].replace(/^["']|["']$/g, '');
            }
        }
        return result;
    }

    // ─── Device Detection ────────────────────────────────────────────────────

    function getDeviceModel(mdBytes) {
        const view = new DataView(mdBytes.buffer, mdBytes.byteOffset, mdBytes.byteLength);
        const version = view.getUint16(0, true);
        if (version === 1) return '1';
        if (version === 3) return '2';
        if (version === 6 || version === 7) return '3';
        throw new Error(`Version appareil inconnue: ${version}`);
    }

    function getDeviceInfoV2(mdBytes) {
        const view = new DataView(mdBytes.buffer);
        const fwMajor = view.getInt16(6, true);
        const fwMinor = view.getInt16(8, true);
        const highBits = view.getInt32(10, false);
        const lowBits = view.getInt32(14, false);
        const serialRaw = (BigInt(highBits) << 32n) + BigInt(lowBits);

        // Extract UUID bytes (offset 256, 256 bytes) for specific key computation
        // Reference: const uuid = new Uint8Array(mdFile.slice(256, 256 + 256));
        const uuidBytes = mdBytes.slice(256, 256 + 256);
        const specificKey = v2ComputeSpecificKey(uuidBytes);

        return {
            version: 'V2',
            firmwareVersion: `${fwMajor}.${fwMinor}`,
            serialNumber: serialRaw.toString().padStart(14, '0'),
            uuidBytes: uuidBytes,
            specificKey: specificKey
        };
    }

    function getDeviceInfoV3(mdBytes) {
        const view = new DataView(mdBytes.buffer, mdBytes.byteOffset, mdBytes.byteLength);
        const mdVersion = view.getUint16(0, true);
        const fw = new TextDecoder().decode(mdBytes.slice(2, 8)).replace(/\0/g, '').trim();
        const serialRaw = new TextDecoder().decode(mdBytes.slice(26, 40)).replace(/\0/g, '');
        const serialDigits = serialRaw.match(/\d+/)?.[0] ?? '';
        return {
            version: 'V3',
            firmwareVersion: fw,
            serialNumber: serialDigits.padStart(14, '0'),
            mdVersion
        };
    }

    async function detectDevice(handle) {
        try {
            const mdHandle = await handle.getFileHandle('.md');
            const mdBytes = await readFileAsBytes(mdHandle);
            const model = getDeviceModel(mdBytes);
            if (model === '1' || model === '2') {
                return getDeviceInfoV2(mdBytes);
            } else {
                return getDeviceInfoV3(mdBytes);
            }
        } catch (e) {
            throw new Error('Fichier .md non trouvé — ce dossier n\'est pas un appareil Lunii valide');
        }
    }

    // ─── Pack Index Management (.pi file) ────────────────────────────────────

    async function getPackUuids(handle) {
        try {
            const piHandle = await handle.getFileHandle('.pi');
            const bytes = await readFileAsBytes(piHandle);
            const uuids = [];
            for (let i = 0; i < bytes.length; i += 16) {
                const chunk = bytes.slice(i, i + 16);
                if (chunk.length === 16) uuids.push(bytesToUUID(chunk));
            }
            return uuids;
        } catch {
            return [];
        }
    }

    async function writePackUuids(handle, uuids) {
        const piHandle = await handle.getFileHandle('.pi', { create: true });
        const writable = await piHandle.createWritable();
        for (const uuid of uuids) {
            await writable.write(uuidToBytes(uuid));
        }
        await writable.close();
    }

    // ─── Pack Metadata ───────────────────────────────────────────────────────

    async function getPackMetadata(handle, uuid) {
        try {
            const ref = uuidToRef(uuid);
            const mdHandle = await getFileHandle(handle, `.content/${ref}/md`);
            const text = await readFileAsText(mdHandle);
            return parseSimpleYaml(text);
        } catch {
            return null;
        }
    }

    async function loadAllPacks(handle) {
        const uuids = await getPackUuids(handle);
        const results = [];
        for (const uuid of uuids) {
            const metadata = await getPackMetadata(handle, uuid);
            results.push({
                uuid,
                ref: uuidToRef(uuid),
                title: metadata?.title || 'Pack sans titre',
                description: metadata?.description || '',
                packType: metadata?.packType || 'unknown'
            });
        }
        return results;
    }

    // ─── Pack Operations ─────────────────────────────────────────────────────

    async function movePackUp(index) {
        if (index <= 0) return;
        const uuids = await getPackUuids(deviceHandle);
        [uuids[index], uuids[index - 1]] = [uuids[index - 1], uuids[index]];
        await writePackUuids(deviceHandle, uuids);
        await refreshPacks();
    }

    async function movePackDown(index) {
        const uuids = await getPackUuids(deviceHandle);
        if (index >= uuids.length - 1) return;
        [uuids[index], uuids[index + 1]] = [uuids[index + 1], uuids[index]];
        await writePackUuids(deviceHandle, uuids);
        await refreshPacks();
    }

    async function movePackToTop(index) {
        const uuids = await getPackUuids(deviceHandle);
        const uuid = uuids.splice(index, 1)[0];
        uuids.unshift(uuid);
        await writePackUuids(deviceHandle, uuids);
        await refreshPacks();
    }

    async function deletePack(index) {
        const pack = packs[index];
        if (!confirm(`Supprimer "${pack.title}" ?\n\nCette action est irréversible.`)) return;

        const uuids = await getPackUuids(deviceHandle);
        uuids.splice(index, 1);
        await writePackUuids(deviceHandle, uuids);

        // Remove content directory
        try {
            const contentDir = await deviceHandle.getDirectoryHandle('.content');
            await contentDir.removeEntry(pack.ref, { recursive: true });
        } catch (e) {
            console.warn('Could not remove pack content:', e);
        }

        await refreshPacks();
        showNotification(`"${pack.title}" supprimé`);
    }

    async function installPackFromZip(file) {
        isInstalling = true;
        renderUI();

        try {
            updateInstallStatus('Extraction du ZIP...');

            if (typeof JSZip === 'undefined') {
                await loadScript('https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js');
            }

            const zip = await JSZip.loadAsync(file);
            const entries = Object.keys(zip.files).filter(e => !zip.files[e].dir);

            // Normalize path separators (Windows ZIPs may use backslashes)
            const normalizedEntries = entries.map(e => e.replace(/\\/g, '/'));

            // Find the .content directory structure in the ZIP
            const contentEntries = normalizedEntries.filter(e => e.includes('.content/'));

            if (contentEntries.length === 0) {
                throw new Error('ZIP invalide: pas de dossier .content/ trouvé');
            }

            // Detect pack ref from the ZIP structure
            const refMatch = contentEntries[0].match(/\.content\/([A-F0-9]{8})\//i);
            if (!refMatch) {
                throw new Error('ZIP invalide: référence de pack non trouvée');
            }
            const packRef = refMatch[1];

            // Read metadata to get UUID
            const mdEntry = contentEntries.find(e => e.endsWith(`/${packRef}/md`));
            let packUuid = null;
            let packTitle = 'Pack installé';

            if (mdEntry) {
                // Use original entry name from ZIP (might have backslashes)
                const origEntry = entries[normalizedEntries.indexOf(mdEntry)];
                const mdText = await zip.files[origEntry].async('text');
                const metadata = parseSimpleYaml(mdText);
                packUuid = metadata.uuid;
                packTitle = metadata.title || packTitle;
            }

            updateInstallStatus(`Installation de "${packTitle}"...`);

            // Copy files to device
            const contentDir = await deviceHandle.getDirectoryHandle('.content', { create: true });
            const packDir = await contentDir.getDirectoryHandle(packRef, { create: true });

            let count = 0;
            let riData = null;

            for (let idx = 0; idx < contentEntries.length; idx++) {
                const entry = contentEntries[idx];
                const origEntry = entries[normalizedEntries.indexOf(entry)];
                const relativePath = entry.split(`${packRef}/`).slice(1).join(`${packRef}/`);
                if (!relativePath) continue;

                // Skip BT from ZIP — we'll generate it ourselves
                if (relativePath === 'bt') {
                    count++;
                    continue;
                }

                const data = await zip.files[origEntry].async('arraybuffer');

                // Capture the RI data for BT generation
                if (relativePath === 'ri') {
                    riData = new Uint8Array(data);
                }

                await writeFileAtPath(packDir, relativePath, data);

                count++;
                const pct = Math.round(count / contentEntries.length * 100);
                updateInstallStatus(`Copie ${count}/${contentEntries.length} (${pct}%)...`);
            }

            // Generate BT file on-device for V2
            updateInstallStatus('Génération du BT...');
            if (deviceInfo.version === 'V2' && riData) {
                const encryptV2 = (block) => encryptXxtea(block, V2_COMMON_KEY);
                const btBinary = v2GenerateBt(riData, deviceInfo.specificKey);
                await writeFile(packDir, 'bt', btBinary, true);
                console.log('BT generated:', btBinary.length, 'bytes');
            } else if (deviceInfo.version === 'V3') {
                // V3: read existing bt from device and copy it
                try {
                    const deviceBtHandle = await deviceHandle.getFileHandle('.bt');
                    const deviceBt = await readFileAsBytes(deviceBtHandle);
                    await writeFile(packDir, 'bt', deviceBt, true);
                } catch (e) {
                    console.warn('V3 BT not found, skipping:', e);
                }
            }

            // Add UUID to pack index
            if (packUuid) {
                const uuids = await getPackUuids(deviceHandle);
                if (!uuids.includes(packUuid)) {
                    uuids.push(packUuid);
                    await writePackUuids(deviceHandle, uuids);
                }
            }

            showNotification(`"${packTitle}" installé avec succès !`);
            await refreshPacks();

        } catch (e) {
            console.error('Install error:', e);
            showNotification(`Erreur: ${e.message}`, true);
        } finally {
            isInstalling = false;
            renderUI();
        }
    }

    // ─── UI Helpers ──────────────────────────────────────────────────────────

    let notificationTimeout = null;
    function showNotification(msg, isError = false) {
        const el = document.getElementById('lm-notification');
        if (!el) return;
        el.textContent = msg;
        el.className = 'lm-notification ' + (isError ? 'lm-error' : 'lm-success');
        el.style.display = 'block';
        clearTimeout(notificationTimeout);
        notificationTimeout = setTimeout(() => { el.style.display = 'none'; }, 4000);
    }

    function updateInstallStatus(msg) {
        const el = document.getElementById('lm-install-status');
        if (el) el.textContent = msg;
    }

    async function refreshPacks() {
        packs = await loadAllPacks(deviceHandle);
        renderUI();
    }

    function loadScript(src) {
        return new Promise((resolve, reject) => {
            if (document.querySelector(`script[src="${src}"]`)) { resolve(); return; }
            const s = document.createElement('script');
            s.src = src;
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    // ─── Connect Flow ────────────────────────────────────────────────────────

    async function connectDevice() {
        try {
            deviceHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
            deviceInfo = await detectDevice(deviceHandle);
            packs = await loadAllPacks(deviceHandle);
            renderUI();
        } catch (e) {
            if (e.name !== 'AbortError') {
                showNotification(`Erreur: ${e.message}`, true);
            }
        }
    }

    // ─── Install from file input ─────────────────────────────────────────────

    function handleFileSelect(e) {
        const files = e.target.files;
        if (files.length > 0) {
            installPackFromZip(files[0]);
        }
    }

    // ─── Render ──────────────────────────────────────────────────────────────

    function renderUI() {
        const container = document.getElementById('lunii-manager');
        if (!container) return;

        // Check browser support
        if (!('showDirectoryPicker' in window)) {
            container.innerHTML = `
        <div class="lm-unsupported">
          <div class="lm-icon">⚠️</div>
          <h3>Navigateur non compatible</h3>
          <p>Le gestionnaire Lunii nécessite <strong>Chrome</strong> ou <strong>Edge</strong> pour accéder au système de fichiers de votre appareil.</p>
        </div>`;
            return;
        }

        // Not connected
        if (!deviceHandle) {
            container.innerHTML = `
        <div class="lm-connect">
          <div class="lm-icon">🎧</div>
          <h3>Gérer ma Lunii</h3>
          <p>Branchez votre Lunii en USB, puis sélectionnez son dossier racine</p>
          <button class="lm-btn lm-btn-primary" onclick="LuniiManager.connect()">
            🔌 Connecter mon appareil
          </button>
        </div>`;
            return;
        }

        // Connected — show device info + pack list
        const versionBadge = deviceInfo.version === 'V3'
            ? '<span class="lm-badge lm-badge-v3">V3</span>'
            : '<span class="lm-badge lm-badge-v2">V2</span>';

        const packsHTML = packs.map((pack, i) => `
      <div class="lm-pack" data-index="${i}">
        <div class="lm-pack-arrows">
          <button class="lm-arrow" onclick="LuniiManager.moveUp(${i})" ${i === 0 ? 'disabled' : ''} title="Monter">↑</button>
          <button class="lm-arrow" onclick="LuniiManager.moveDown(${i})" ${i === packs.length - 1 ? 'disabled' : ''} title="Descendre">↓</button>
        </div>
        <div class="lm-pack-info">
          <div class="lm-pack-uuid">${pack.ref}</div>
          <div class="lm-pack-title">${escapeHtml(pack.title)}</div>
          ${pack.description ? `<div class="lm-pack-desc">${escapeHtml(pack.description).substring(0, 120)}${pack.description.length > 120 ? '...' : ''}</div>` : ''}
        </div>
        <div class="lm-pack-actions">
          <button class="lm-menu-btn" onclick="LuniiManager.toggleMenu(${i})" title="Actions">⋯</button>
          <div class="lm-menu" id="lm-menu-${i}" style="display:none;">
            <button onclick="LuniiManager.showDetails(${i})">ℹ️ Détails</button>
            <button onclick="LuniiManager.moveToTop(${i})">⬆️ Mettre en premier</button>
            <button class="lm-danger" onclick="LuniiManager.remove(${i})">🗑️ Supprimer</button>
          </div>
        </div>
      </div>
    `).join('');

        const installSection = isInstalling
            ? `<div class="lm-installing">
           <div class="lm-spinner"></div>
           <span id="lm-install-status">Installation en cours...</span>
         </div>`
            : `<div class="lm-install-bar">
           <label class="lm-btn lm-btn-primary lm-btn-install">
             📦 Installer un pack
             <input type="file" accept=".zip" onchange="LuniiManager.handleFile(event)" style="display:none">
           </label>
           <span class="lm-install-hint">${packs.length} pack${packs.length > 1 ? 's' : ''} installé${packs.length > 1 ? 's' : ''}</span>
         </div>`;

        container.innerHTML = `
      <div id="lm-notification" class="lm-notification" style="display:none"></div>
      <div class="lm-header">
        <div class="lm-device-info">
          ${versionBadge}
          <span class="lm-sn">S/N: ${deviceInfo.serialNumber}</span>
          <span class="lm-fw">FW: ${deviceInfo.firmwareVersion}</span>
        </div>
        <button class="lm-btn lm-btn-sm" onclick="LuniiManager.refresh()" title="Actualiser">🔄</button>
      </div>
      ${installSection}
      <div class="lm-pack-list">
        ${packsHTML || '<div class="lm-empty">Aucun pack installé</div>'}
      </div>
    `;
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function toggleMenu(index) {
        document.querySelectorAll('.lm-menu').forEach(m => { m.style.display = 'none'; });
        const menu = document.getElementById(`lm-menu-${index}`);
        if (menu) menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
    }

    function showDetails(index) {
        const pack = packs[index];
        toggleMenu(index);
        alert(
            `🎧 Détails du pack\n\n` +
            `Titre: ${pack.title}\n` +
            `Description: ${pack.description || '(aucune)'}\n` +
            `UUID: ${pack.uuid}\n` +
            `Référence: ${pack.ref}\n` +
            `Type: ${pack.packType}`
        );
    }

    // Close menus on outside click
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.lm-menu-btn') && !e.target.closest('.lm-menu')) {
            document.querySelectorAll('.lm-menu').forEach(m => { m.style.display = 'none'; });
        }
    });

    // ─── Public API ──────────────────────────────────────────────────────────

    window.LuniiManager = {
        connect: connectDevice,
        refresh: refreshPacks,
        moveUp: movePackUp,
        moveDown: movePackDown,
        moveToTop: movePackToTop,
        remove: deletePack,
        toggleMenu: toggleMenu,
        showDetails: showDetails,
        handleFile: handleFileSelect,
        init: renderUI
    };

    // Auto-init
    if (document.getElementById('lunii-manager')) {
        renderUI();
    }

})();
