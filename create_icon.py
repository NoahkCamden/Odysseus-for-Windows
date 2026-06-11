import struct
import zlib
import os

# Create minimal 1x1 PNG
def create_minimal_png():
    # PNG signature
    png_sig = b'\x89PNG\r\n\x1a\n'
    
    # IHDR chunk (13 bytes of data)
    ihdr_data = struct.pack('>IIBBBBB', 
        1,      # width
        1,      # height
        8,      # bit depth
        2,      # color type (2 = RGB)
        0,      # compression
        0,      # filter
        0)      # interlace
    ihdr = make_png_chunk(b'IHDR', ihdr_data)
    
    # IDAT chunk (minimal image data - 1 red pixel)
    # Filter byte + RGB pixel
    img_data = b'\x00\xff\x00\x00'  # Filter 0, then RGB (red)
    compressed = zlib.compress(img_data)
    idat = make_png_chunk(b'IDAT', compressed)
    
    # IEND chunk (empty)
    iend = make_png_chunk(b'IEND', b'')
    
    return png_sig + ihdr + idat + iend

def make_png_chunk(ctype, data):
    crc = zlib.crc32(ctype + data) & 0xffffffff
    return struct.pack('>I', len(data)) + ctype + data + struct.pack('>I', crc)

png_data = create_minimal_png()
os.makedirs('src-tauri/icons', exist_ok=True)
with open('src-tauri/icons/icon.png', 'wb') as f:
    f.write(png_data)
print(f"Created minimal PNG ({len(png_data)} bytes)")

# Also create ICO as fallback
ico_data = bytes.fromhex(
    '00000100010001002000000016000000'  # ICO header + dir entry
    '28000000010000000200000001002000'  # DIB header
    '0000000000000000000000000000ff0000ff'  # Pixel data (red)
    '00000000000000000000000000000000'  # Padding
)
with open('src-tauri/icons/icon.ico', 'wb') as f:
    f.write(ico_data)
print(f"Created minimal ICO ({len(ico_data)} bytes)")
