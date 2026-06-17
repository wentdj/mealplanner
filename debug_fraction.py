import re
qty_re = re.compile(r"^([\d.,]+)\s*(.*)$")

def parse_qty(q):
    if not isinstance(q, str):
        return None
    m = qty_re.match(q.strip())
    if not m:
        return None
    v = float(m.group(1).replace(',','.'))
    unit = m.group(2).strip().lower()
    return v, unit


def parse_multi_qty(q):
    parts = [parse_qty(part) for part in re.split(r"\s*/\s*", q)]
    return parts

print(parse_multi_qty('1/2 / 1/4'))
print(parse_multi_qty('1/2'))
print(parse_multi_qty('1/4'))
