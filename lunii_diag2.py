import os, struct, sys
device_root = 'D:\\'

out = []

def p(s):
    out.append(str(s))

# Read .md
md_data = open(os.path.join(device_root, '.md'), 'rb').read()
v = struct.unpack_from('<H', md_data, 0)[0]
p('Device .md: ' + str(len(md_data)) + ' bytes, ver=' + str(v))

# Read .pi
pi_data = open(os.path.join(device_root, '.pi'), 'rb').read()
uuids = []
for i in range(0, len(pi_data), 16):
    c = pi_data[i:i+16]
    if len(c)==16:
        h = c.hex()
        uuids.append(h[:8]+'-'+h[8:12]+'-'+h[12:16]+'-'+h[16:20]+'-'+h[20:])
p('Packs: ' + str(len(uuids)))

def uref(u):
    return u.replace('-','')[-8:].upper()

def safe_read(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except:
        return None

def show_pack(idx):
    u = uuids[idx]
    r = uref(u)
    d = os.path.join(device_root, '.content', r)
    tag = 'LAST' if idx==len(uuids)-1 else ('FIRST/REF' if idx==0 else 'RECENT')
    p('--- Pack ' + str(idx+1) + ' (' + tag + '): UUID=' + u + ' REF=' + r)
    if not os.path.exists(d):
        p('  DIR MISSING')
        return
    p('  dir: OK')
    for fn in ['ni','li','ri','si','bt','md']:
        fp = os.path.join(d, fn)
        if os.path.exists(fp):
            try:
                p('  ' + fn + ': ' + str(os.path.getsize(fp)) + ' B')
            except:
                p('  ' + fn + ': exists')
        else:
            p('  ' + fn + ': MISSING')

    ni = safe_read(os.path.join(d,'ni'))
    if ni and len(ni)>=25:
        p('  NI: ver=' + str(struct.unpack_from('<H',ni,0)[0])
          + ' pv=' + str(struct.unpack_from('<h',ni,2)[0])
          + ' off=' + str(struct.unpack_from('<i',ni,4)[0])
          + ' ndsz=' + str(struct.unpack_from('<i',ni,8)[0])
          + ' ndcnt=' + str(struct.unpack_from('<i',ni,12)[0])
          + ' imgs=' + str(struct.unpack_from('<i',ni,16)[0])
          + ' snds=' + str(struct.unpack_from('<i',ni,20)[0])
          + ' fac=' + str(ni[24]))
        if len(ni)>=556:
            o=512
            p('  Node0: img=' + str(struct.unpack_from('<i',ni,o)[0])
              + ' aud=' + str(struct.unpack_from('<i',ni,o+4)[0])
              + ' okP=' + str(struct.unpack_from('<i',ni,o+8)[0])
              + ' okC=' + str(struct.unpack_from('<i',ni,o+12)[0])
              + ' okO=' + str(struct.unpack_from('<i',ni,o+16)[0]))

    ri = safe_read(os.path.join(d,'ri'))
    if ri:
        p('  RI hex: ' + ri[:48].hex())
        p('  RI len: ' + str(len(ri)))

    bt = safe_read(os.path.join(d,'bt'))
    if bt:
        p('  BT hex: ' + bt[:16].hex() + ' len=' + str(len(bt)))
    else:
        p('  BT: unreadable or missing data')

    for sub in [('rf','000'),('sf','000')]:
        sp = os.path.join(d, sub[0], sub[1])
        if os.path.exists(sp):
            fs = sorted(os.listdir(sp))
            nm = fs[0] if fs else 'none'
            p('  ' + sub[0] + '/' + sub[1] + ': ' + str(len(fs)) + ' files, first=' + nm)

    md = safe_read(os.path.join(d,'md'))
    if md:
        txt = md.decode('utf-8', errors='replace')
        for line in txt.strip().split('\n')[:5]:
            p('  md> ' + line[:80])
    p('')

show_pack(0)
for i in range(max(1,len(uuids)-3), len(uuids)):
    show_pack(i)

cpath = os.path.join(device_root, '.content')
if os.path.exists(cpath):
    all_d = set(x for x in os.listdir(cpath) if os.path.isdir(os.path.join(cpath,x)))
    known = set(uref(u) for u in uuids)
    orphans = all_d - known
    if orphans:
        p('Orphan dirs: ' + str(orphans))

# Write to file
with open('diag_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('Done - wrote diag_result.txt')
