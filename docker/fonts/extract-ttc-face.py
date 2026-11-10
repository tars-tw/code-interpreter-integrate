"""Write one face of a TrueType Collection as a standalone font file.

pdf-lib's fontkit and some other embedders reject collections outright, and
only the Taiwan face of a multi-region collection is wanted. Table data is
copied unchanged, so only the directory offsets and head.checkSumAdjustment
need recomputing.
"""
import struct
import sys


def checksum(data):
    data += b'\0' * (-len(data) % 4)
    return sum(struct.unpack(f'>{len(data) // 4}L', data)) & 0xFFFFFFFF


def extract(source, index, target):
    with open(source, 'rb') as file:
        blob = file.read()
    tag, _, count = struct.unpack_from('>4sLL', blob)
    if tag != b'ttcf':
        raise SystemExit(f'{source}: not a TrueType Collection')
    if not 0 <= index < count:
        raise SystemExit(f'{source}: face {index} out of range (collection has {count})')
    (offset,) = struct.unpack_from('>L', blob, 12 + 4 * index)
    sfnt_version, num_tables = struct.unpack_from('>LH', blob, offset)
    records = [struct.unpack_from('>4sLLL', blob, offset + 12 + 16 * i) for i in range(num_tables)]
    records.sort(key=lambda record: record[0])

    header_size = 12 + 16 * num_tables
    entry_selector = num_tables.bit_length() - 1
    search_range = 16 << entry_selector
    out = bytearray(struct.pack('>LHHHH', sfnt_version, num_tables, search_range,
                                entry_selector, num_tables * 16 - search_range))
    out += b'\0' * (header_size - len(out))
    head_offset = None
    for i, (table_tag, table_sum, table_offset, length) in enumerate(records):
        position = len(out)
        data = blob[table_offset:table_offset + length]
        if table_tag == b'head':
            head_offset = position
            data = data[:8] + b'\0\0\0\0' + data[12:]
            table_sum = checksum(data)
        struct.pack_into('>4sLLL', out, 12 + 16 * i, table_tag, table_sum, position, length)
        out += data + b'\0' * (-length % 4)
    if head_offset is None:
        raise SystemExit(f'{source}: face {index} has no head table')
    struct.pack_into('>L', out, head_offset + 8, (0xB1B0AFBA - checksum(bytes(out))) & 0xFFFFFFFF)
    with open(target, 'wb') as file:
        file.write(out)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('usage: extract-ttc-face.py SOURCE.ttc INDEX TARGET')
    extract(sys.argv[1], int(sys.argv[2]), sys.argv[3])
