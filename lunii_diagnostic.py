"""
Lunii Device Diagnostic Script
Usage: python lunii_diagnostic.py D:\
"""

import os
import struct
import sys


def read_pi(device_root):
    pi_path = os.path.join(device_root, '.pi')
    if not os.path.exists(pi_path):
        print("❌ .pi file not found!")
        return []
    with open(pi_path, 'rb') as f:
        data = f.read()
    uuids = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        if len(chunk) == 16:
            hex_str = chunk.hex()
            uuid = f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:]}"
            uuids.append(uuid)
    print(f"\n📋 .pi file: {len(data)} bytes, {len(uuids)} UUIDs")
    return uuids


def uuid_to_ref(uuid):
    return uuid.replace('-', '')[-8:].upper()


def safe_read(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except Exception as e:
        return None


def inspect_pack(device_root, uuid_str, ref, verbose=False):
    content_dir = os.path.join(device_root, '.content', ref)
    if not os.path.exists(content_dir):
        print(f"  ❌ Directory .content/{ref}/ NOT FOUND")
        return

    print(f"  ✅ Directory .content/{ref}/ exists")

    # Check files
    for fname in ['ni', 'li', 'ri', 'si', 'bt', 'md']:
        fpath = os.path.join(content_dir, fname)
        if os.path.exists(fpath):
            try:
                size = os.path.getsize(fpath)
                print(f"    {'✅' if size > 0 else '⚠️'}  {fname}: {size} bytes")
            except:
                print(f"    ⚠️  {fname}: exists (size unknown)")
        else:
            print(f"    ❌ {fname}: MISSING")

    # Read md (metadata)
    md_path = os.path.join(content_dir, 'md')
    if os.path.exists(md_path):
        try:
            with open(md_path, 'r', encoding='utf-8', errors='replace') as f:
                md_text = f.read()
            for line in md_text.strip().split('\n')[:3]:
                print(f"       {line[:80]}")
        except:
            pass

    # Check rf/ and sf/
    for subdir in ['rf/000', 'sf/000']:
        dpath = os.path.join(content_dir, *subdir.split('/'))
        if os.path.exists(dpath):
            try:
                files = sorted(os.listdir(dpath))
                if files:
                    first_size = os.path.getsize(os.path.join(dpath, files[0]))
                    print(f"    ✅ {subdir}/: {len(files)} files (first: {files[0]}, {first_size}B)")
                else:
                    print(f"    ⚠️  {subdir}/: empty")
            except:
                print(f"    ⚠️  {subdir}/: error listing")
        else:
            print(f"    ❌ {subdir}/: MISSING")

    if not verbose:
        return

    # Detailed NI inspection
    ni_data = safe_read(os.path.join(content_dir, 'ni'))
    if ni_data and len(ni_data) >= 25:
        ver = struct.unpack_from('<H', ni_data, 0)[0]
        pv = struct.unpack_from('<h', ni_data, 2)[0]
        off = struct.unpack_from('<i', ni_data, 4)[0]
        ns = struct.unpack_from('<i', ni_data, 8)[0]
        nc = struct.unpack_from('<i', ni_data, 12)[0]
        ic = struct.unpack_from('<i', ni_data, 16)[0]
        sc = struct.unpack_from('<i', ni_data, 20)[0]
        fac = struct.unpack_from('<B', ni_data, 24)[0]
        exp = 512 + nc * 44
        print(f"    NI: ver={ver} packVer={pv} offset={off} nodeSize={ns} nodes={nc} imgs={ic} snds={sc} factory={fac}")
        print(f"        size={len(ni_data)} expected={exp} {'✅' if len(ni_data)==exp else '❌'}")
        if nc > 0 and len(ni_data) >= 512 + 44:
            o = 512
            print(f"    Node[0]: img={struct.unpack_from('<i',ni_data,o)[0]} aud={struct.unpack_from('<i',ni_data,o+4)[0]} "
                  f"okPos={struct.unpack_from('<i',ni_data,o+8)[0]} okCnt={struct.unpack_from('<i',ni_data,o+12)[0]} "
                  f"okOpt={struct.unpack_from('<i',ni_data,o+16)[0]}")

    # RI first bytes
    ri_data = safe_read(os.path.join(content_dir, 'ri'))
    if ri_data:
        print(f"    RI hex[0:48]: {ri_data[:48].hex()}")

    # BT first bytes
    bt_data = safe_read(os.path.join(content_dir, 'bt'))
    if bt_data:
        print(f"    BT hex[0:16]: {bt_data[:16].hex()}")
    elif os.path.exists(os.path.join(content_dir, 'bt')):
        print(f"    BT: file exists but unreadable")

    # SI first bytes
    si_data = safe_read(os.path.join(content_dir, 'si'))
    if si_data:
        print(f"    SI hex[0:48]: {si_data[:48].hex()}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python lunii_diagnostic.py <LUNII_DRIVE_ROOT>")
        sys.exit(1)

    device_root = sys.argv[1]
    print(f"🔬 Lunii Diagnostic — {device_root}")
    print("=" * 60)

    # Device .md
    md_path = os.path.join(device_root, '.md')
    if os.path.exists(md_path):
        data = safe_read(md_path)
        if data:
            print(f"\n🔍 .md: {len(data)} bytes, first 30: {data[:30].hex()}")
            v = struct.unpack_from('<H', data, 0)[0]
            print(f"   Version: {v} → {'V2' if v in (1,3) else 'V3' if v in (6,7) else '?'}")

    # Read .pi
    uuids = read_pi(device_root)
    if not uuids:
        return

    # Show first working pack (verbose) and last 3 packs (to find ours)
    print(f"\n{'─'*60}")
    print("📦 Pack 1 (WORKING REFERENCE — verbose):")
    print(f"   UUID={uuids[0]}, REF={uuid_to_ref(uuids[0])}")
    inspect_pack(device_root, uuids[0], uuid_to_ref(uuids[0]), verbose=True)

    # Show packs 2-51 (brief summary only)
    print(f"\n{'─'*60}")
    print(f"📦 Packs 2-{len(uuids)-2}: (summary)")
    missing_count = 0
    for i in range(1, len(uuids) - 3):
        ref = uuid_to_ref(uuids[i])
        content_dir = os.path.join(device_root, '.content', ref)
        if not os.path.exists(content_dir):
            missing_count += 1

    if missing_count:
        print(f"   ⚠️ {missing_count} packs have missing .content/ dirs")
    else:
        print(f"   ✅ All middle packs have .content/ dirs")

    # Show LAST 3 packs (verbose) — our pack should be here
    start = max(1, len(uuids) - 3)
    for i in range(start, len(uuids)):
        print(f"\n{'─'*60}")
        idx = i + 1
        print(f"📦 Pack {idx} ({'LAST — LIKELY OURS' if i == len(uuids)-1 else 'RECENT'} — verbose):")
        print(f"   UUID={uuids[i]}, REF={uuid_to_ref(uuids[i])}")
        inspect_pack(device_root, uuids[i], uuid_to_ref(uuids[i]), verbose=True)

    # Orphan check
    content_path = os.path.join(device_root, '.content')
    if os.path.exists(content_path):
        try:
            all_dirs = [d for d in os.listdir(content_path) if os.path.isdir(os.path.join(content_path, d))]
            known_refs = {uuid_to_ref(u) for u in uuids}
            orphans = set(all_dirs) - known_refs
            if orphans:
                print(f"\n⚠️  Orphan dirs in .content/: {orphans}")
        except:
            pass

    print(f"\n{'='*60}")
    print("Done. Copy everything above.")


if __name__ == '__main__':
    main()
