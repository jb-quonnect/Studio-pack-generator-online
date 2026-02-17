"""Comprehensive diagnostic: BT integrity + NI node structure comparison."""
import os, struct, sys
sys.path.insert(0, '.')
from modules.lunii_converter import xxtea_decrypt, xxtea_encrypt, XXTEA_KEY_V2

out = []
def p(s): out.append(str(s))

device = 'D:\\'

# ─── Device UUID and Specific Key ───
md_data = open(os.path.join(device, '.md'), 'rb').read()
uuid_bytes = md_data[256:512]  # 256 bytes
p('Device UUID bytes[0:16]: ' + uuid_bytes[:16].hex())

# Compute specific key (same as JS v2ComputeSpecificKey)
# Step 1: XXTEA decrypt the full 256-byte UUID with common key
dec_uuid = xxtea_decrypt(uuid_bytes)
p('Decrypted UUID[0:16]: ' + dec_uuid[:16].hex())

# Step 2: Reorder bytes [11,10,9,8, 15,14,13,12, 3,2,1,0, 7,6,5,4]
specific_key = bytes([
    dec_uuid[11], dec_uuid[10], dec_uuid[9], dec_uuid[8],
    dec_uuid[15], dec_uuid[14], dec_uuid[13], dec_uuid[12],
    dec_uuid[3], dec_uuid[2], dec_uuid[1], dec_uuid[0],
    dec_uuid[7], dec_uuid[6], dec_uuid[5], dec_uuid[4],
])
p('Specific key: ' + specific_key.hex())

# ─── Find our pack ───
pi = open(os.path.join(device, '.pi'), 'rb').read()
uuids = []
for i in range(0, len(pi), 16):
    c = pi[i:i+16]
    if len(c)==16: uuids.append(c.hex())

last_hex = uuids[-1]
ref = last_hex[-8:].upper()
base = os.path.join(device, '.content', ref)
p('Our pack REF: ' + ref + ' exists: ' + str(os.path.exists(base)))

# ─── BT Verification ───
p('')
p('=== BT VERIFICATION ===')
bt = open(os.path.join(base, 'bt'), 'rb').read()
ri_enc = open(os.path.join(base, 'ri'), 'rb').read()
p('BT size: ' + str(len(bt)))
p('RI size: ' + str(len(ri_enc)))
p('BT hex: ' + bt.hex())
p('RI first 64 hex: ' + ri_enc[:64].hex())

# Decrypt BT with specific key → should equal first 64 bytes of encrypted RI
bt_decrypted = xxtea_decrypt(bt, specific_key)
p('BT decrypted hex: ' + bt_decrypted.hex())

# Padded RI first 64 (pad with zeros if shorter)
ri_padded = ri_enc[:64] + b'\x00' * max(0, 64 - len(ri_enc))
p('RI padded 64 hex: ' + ri_padded.hex())
p('BT_dec == RI_first64: ' + str(bt_decrypted[:64] == ri_padded[:64]))

# Now verify using encrypt: BT should = encrypt(RI_first64, specific_key)
expected_bt = xxtea_encrypt(ri_padded, specific_key)
p('Expected BT hex: ' + expected_bt.hex())
p('BT matches expected: ' + str(bt == expected_bt))

# ─── Verify working pack 52 BT ───
p('')
p('=== WORKING PACK 52 BT VERIFICATION ===')
ref52 = '07C26EA8'
base52 = os.path.join(device, '.content', ref52)
bt52 = open(os.path.join(base52, 'bt'), 'rb').read()
ri52_enc = open(os.path.join(base52, 'ri'), 'rb').read()
p('BT52 size: ' + str(len(bt52)))
p('RI52 size: ' + str(len(ri52_enc)))
bt52_dec = xxtea_decrypt(bt52, specific_key)
p('BT52 dec hex: ' + bt52_dec.hex())
p('RI52 first 64: ' + ri52_enc[:64].hex())
p('BT52_dec == RI52_first64: ' + str(bt52_dec[:64] == ri52_enc[:64]))

# ─── Full NI dump for Pack 52 ───
p('')
p('=== WORKING PACK 52 - ALL NODES ===')
ni52 = open(os.path.join(base52, 'ni'), 'rb').read()
nc52 = struct.unpack_from('<i', ni52, 12)[0]
p('Node count: ' + str(nc52))
for idx in range(nc52):
    o = 512 + idx*44
    vals = struct.unpack_from('<iiiiiiiihhhhhh', ni52, o)
    p('  N' + str(idx) + ': img=' + str(vals[0]) + ' aud=' + str(vals[1])
      + ' okP=' + str(vals[2]) + ' okC=' + str(vals[3]) + ' okO=' + str(vals[4])
      + ' hmP=' + str(vals[5]) + ' hmC=' + str(vals[6]) + ' hmO=' + str(vals[7])
      + ' whl=' + str(vals[8]) + ' ok=' + str(vals[9]) + ' hm=' + str(vals[10])
      + ' pse=' + str(vals[11]) + ' auto=' + str(vals[12]) + ' pad=' + str(vals[13]))

# ─── Full NI dump for our pack ───
p('')
p('=== OUR PACK - ALL NODES ===')
ni = open(os.path.join(base, 'ni'), 'rb').read()
nc = struct.unpack_from('<i', ni, 12)[0]
ic = struct.unpack_from('<i', ni, 16)[0]
sc = struct.unpack_from('<i', ni, 20)[0]
pv = struct.unpack_from('<h', ni, 2)[0]
fac = ni[24]
p('Header: pv=' + str(pv) + ' nodes=' + str(nc) + ' imgs=' + str(ic) + ' snds=' + str(sc) + ' fac=' + str(fac))
for idx in range(nc):
    o = 512 + idx*44
    vals = struct.unpack_from('<iiiiiiiihhhhhh', ni52, o)
    p('  N' + str(idx) + ': img=' + str(vals[0]) + ' aud=' + str(vals[1])
      + ' okP=' + str(vals[2]) + ' okC=' + str(vals[3]) + ' okO=' + str(vals[4])
      + ' hmP=' + str(vals[5]) + ' hmC=' + str(vals[6]) + ' hmO=' + str(vals[7])
      + ' whl=' + str(vals[8]) + ' ok=' + str(vals[9]) + ' hm=' + str(vals[10])
      + ' pse=' + str(vals[11]) + ' auto=' + str(vals[12]) + ' pad=' + str(vals[13]))

# LI dump
p('')
p('=== OUR LI DECRYPTED ===')
li = open(os.path.join(base, 'li'), 'rb').read()
dec_li = xxtea_decrypt(li[:min(512,len(li))])
for i in range(0, len(dec_li), 4):
    p('  li[' + str(i//4) + '] = ' + str(struct.unpack_from('<I', dec_li, i)[0]))

p('')
p('=== PACK 52 LI DECRYPTED ===')
li52 = open(os.path.join(base52, 'li'), 'rb').read()
dec_li52 = xxtea_decrypt(li52[:min(512,len(li52))])
for i in range(0, min(40, len(dec_li52)), 4):
    p('  li[' + str(i//4) + '] = ' + str(struct.unpack_from('<I', dec_li52, i)[0]))

with open('diag4.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('Done - see diag4.txt')
