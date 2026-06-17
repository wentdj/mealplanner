import re, json, sys
html = open('index.html','r',encoding='utf-8').read()
m = re.search(r"const\s+meals\s*=\s*({[\s\S]*?});\s*\n", html)
if not m:
    print('meals block not found', file=sys.stderr); sys.exit(1)
obj = m.group(1)
# Quote top-level keys like:  b1: {  -> "b1": {
obj = re.sub(r"(?m)^(\s*)([a-zA-Z][a-zA-Z0-9_]*)\s*:", r"\1\"\2\" :", obj)
# Replace single quotes with double quotes
obj = obj.replace("'", '"')
# Remove trailing commas before closing braces/brackets
obj = re.sub(r",\s*(\}|\])", r"\1", obj)
# fix escaped quotes if present
obj = obj.replace('\\"', '"')
try:
    data = json.loads(obj)
except Exception as e:
    print('json parse error:', e, file=sys.stderr)
    open('meals_debug.json','w',encoding='utf-8').write(obj)
    print('Wrote meals_debug.json for inspection', file=sys.stderr)
    sys.exit(1)

# aggregate for selected ids (testing eggs aggregation with mixed units)
selected = ['b1','b2']
combined = {}
for id_ in selected:
    if id_ not in data:
        print('missing', id_)
        continue
    cats = data[id_].get('items',{})
    for cat, items in cats.items():
        combined.setdefault(cat, {})
        for item, qty in items.items():
            if isinstance(qty, str):
                combined[cat].setdefault(item, {'generic': [], 'persons': {}})
                combined[cat][item]['generic'].append(qty)
            elif isinstance(qty, dict):
                combined[cat].setdefault(item, {'generic': [], 'persons': {}})
                for person, v in qty.items():
                    combined[cat][item]['persons'].setdefault(person,[]).append(v)

# Debug: show raw combined entries for inspection
import pprint
print('--- RAW COMBINED ---')
pprint.pprint(combined)
print('--------------------')

# formatting function like JS
import math
qty_re = re.compile(r"^([\d.,]+)\s*(.*)$")

def parse_qty(q):
    if not isinstance(q, str):
        return None
    s = q.strip()
    # mixed number e.g. '1 1/2 cup'
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)\s*(.*)$", s)
    if m:
        whole = int(m.group(1))
        num = int(m.group(2))
        den = int(m.group(3))
        if den == 0:
            return None
        v = whole + num / den
        unit = m.group(4).strip().lower()
        return v, unit
    # simple fraction e.g. '1/2 avocado'
    m = re.match(r"^(\d+)/(\d+)\s*(.*)$", s)
    if m:
        num = int(m.group(1))
        den = int(m.group(2))
        if den == 0:
            return None
        v = num / den
        unit = m.group(3).strip().lower()
        return v, unit
    # decimal or integer
    m = qty_re.match(s)
    if not m:
        return None
    v = float(m.group(1).replace(',','.'))
    unit = m.group(2).strip().lower()
    return v, unit


def parse_multi_qty(q):
    # split only on separators with spaces around slash to avoid breaking fractions like '1/2'
    parts = [parse_qty(part) for part in re.split(r"\s+/\s+", q)]
    if any(p is None for p in parts):
        return None
    return parts


def format_quantities(qtys):
    parsed_groups = [parse_multi_qty(q) for q in qtys]
    if any(p is None for p in parsed_groups):
        return ' / '.join(sorted(set(qtys), key=qtys.index))
    flattened = [part for group in parsed_groups for part in group]
    if not flattened:
        return ''
    units = [part[1] for part in flattened]
    first_non_empty_unit = next((u for u in units if u), None)
    normalized_units = [u if u else first_non_empty_unit for u in units]
    first_unit = normalized_units[0]
    if not all(unit == first_unit for unit in normalized_units):
        return ' / '.join(sorted(set(qtys), key=qtys.index))
    total = sum(part[0] for part in flattened)
    if abs(total - round(total)) < 1e-9:
        total = int(round(total))
    else:
        total = round(total, 2)
    return f"{total} {first_unit}" if first_unit else str(total)

out = {}
for cat, items in combined.items():
    out[cat] = {}
    for item, entry in items.items():
        all_qtys = list(entry['generic'])
        for p_vals in entry['persons'].values():
            all_qtys.extend(p_vals)
        out[cat][item] = format_quantities(all_qtys)

print(json.dumps(out, indent=2))
