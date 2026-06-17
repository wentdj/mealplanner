#!/usr/bin/env python3
import argparse
import html
import json
import re
from pathlib import Path

SECTION_ORDER = ['Breakfasts', 'Lunches', 'Dinners', 'Snacks', 'Batch']
SECTION_PREFIX = {
    'Breakfasts': 'b',
    'Lunches': 'l',
    'Dinners': 'd',
    'Snacks': 's',
    'Batch': 'bc',
}

RECIPE_IDS_RE = re.compile(r"(const\s+recipeIds\s*=\s*\[)(.*?)(\];)", re.S)
MEALS_OBJ_RE = re.compile(r"(const\s+meals\s*=\s*\{)(.*?)(\n\};\n\nconst\s+selectedMeals)", re.S)
RECIPE_ID_RE = re.compile(r"^([blsd]|bc)(\d+)$")


def parse_ingredient_line(line: str) -> tuple[str, str]:
    item = line.strip()
    item = item.lstrip('+').strip()
    match = re.match(r"^([\d¼½¾/.,]+(?:\s*[a-zA-Z%°]+)*)\s+(.+)$", item)
    if match:
        qty = match.group(1).strip()
        text = match.group(2).strip()
        return text, qty
    return item, 'check stock'


def normalize_item_key(item: str) -> str:
    # Preserve slashes (fractions like '1/2') and spaces when creating the key
    # but remove other punctuation. Keep title-casing for display consistency.
    return re.sub(r"[^A-Za-z0-9 /]+", "", item).strip().title()


def build_shopping_items(data: dict) -> dict:
    categories = {cat: {} for cat in data['ingredients'].keys()}
    item_category = {}
    for cat, items in data['ingredients'].items():
        for line in items:
            name, qty = parse_ingredient_line(line)
            key = normalize_item_key(name)
            if key not in item_category:
                item_category[key] = cat

    def find_category_for_item(key: str) -> str:
        best = None
        for known, cat in item_category.items():
            if known.lower() in key.lower() or key.lower() in known.lower():
                return cat
            if best is None and known.split()[0].lower() == key.split()[0].lower():
                best = cat
        return best or 'Misc'

    for person, lines in data['portions'].items():
        for line in lines:
            name, qty = parse_ingredient_line(line)
            if not name:
                continue
            key = normalize_item_key(name)
            cat = find_category_for_item(key)
            if cat not in categories:
                categories[cat] = {}
            existing = categories[cat].get(key)
            if existing:
                categories[cat][key] = existing + ' / ' + qty
            else:
                categories[cat][key] = qty

    if 'Misc' in categories and not categories['Misc']:
        categories.pop('Misc')
    return categories


def extract_recipe_ids(index_text: str) -> list[str]:
    match = RECIPE_IDS_RE.search(index_text)
    if not match:
        raise ValueError('Could not locate recipeIds array in index.html')
    return re.findall(r"'([^']+)'", match.group(2))


def get_recipe_fragment_ids(recipe_dir: Path) -> set[str]:
    if not recipe_dir.exists():
        return set()
    return {p.stem for p in recipe_dir.glob('*.html') if RECIPE_ID_RE.match(p.stem)}


def remove_meal_entries(meals_body: str, recipe_ids: set[str]) -> str:
    def remove_entry(body: str, key: str) -> str:
        pattern = re.compile(rf"\n\s*{re.escape(key)}\s*:\s*\{{")
        match = pattern.search(body)
        if not match:
            return body
        start = match.start()
        i = match.end()
        depth = 1
        while i < len(body) and depth > 0:
            if body[i] == '{':
                depth += 1
            elif body[i] == '}':
                depth -= 1
            i += 1
        # consume trailing comma if present
        while i < len(body) and body[i] in ' \t':
            i += 1
        if i < len(body) and body[i] == ',':
            i += 1
        return body[:start] + body[i:]

    for recipe_id in list(recipe_ids):
        meals_body = remove_entry(meals_body, recipe_id)
    return meals_body


def remove_deleted_recipe_references(index_text: str, recipe_dir: Path) -> str:
    # Keep recipeIds and meals in sync with actual recipe fragment files.
    fragment_ids = get_recipe_fragment_ids(recipe_dir)
    ids = extract_recipe_ids(index_text)
    missing = [id_ for id_ in ids if id_ not in fragment_ids]
    if not missing:
        return index_text

    # remove missing from recipeIds list
    kept = [id_ for id_ in ids if id_ in fragment_ids]
    match = RECIPE_IDS_RE.search(index_text)
    formatted = ',\n      '.join(f"'{item}'" for item in kept)
    replacement = f"{match.group(1)}\n      {formatted}\n    {match.group(3)}"
    cleaned = index_text[:match.start()] + replacement + index_text[match.end():]

    # remove corresponding meals entries
    meals_match = MEALS_OBJ_RE.search(cleaned)
    if not meals_match:
        return cleaned
    body = meals_match.group(2)
    cleaned_body = remove_meal_entries(body, set(missing))
    return cleaned[:meals_match.start(2)] + cleaned_body + cleaned[meals_match.end(2):]


def find_next_recipe_id(index_text: str, section: str) -> str:
    ids = extract_recipe_ids(index_text)
    prefix = SECTION_PREFIX[section]
    nums = sorted(int(m.group(2)) for id in ids if (m := RECIPE_ID_RE.match(id)) and m.group(1) == prefix)
    n = 1
    for value in nums:
        if value != n:
            break
        n += 1
    return f"{prefix}{n}"
    match = RECIPE_IDS_RE.search(index_text)
    if not match:
        raise ValueError('Could not locate recipeIds array in index.html')
    ids = re.findall(r"'([^']+)'", match.group(2))
    prefix = SECTION_PREFIX[section]
    nums = [int(m.group(2)) for id in ids if (m := RECIPE_ID_RE.match(id)) and m.group(1) == prefix]
    next_num = max(nums, default=0) + 1
    return f"{prefix}{next_num}"


def parse_recipe_file(path: Path) -> dict:
    text = path.read_text(encoding='utf-8')
    lines = text.splitlines()
    data = {
        'id': None,
        'section': None,
        'name': None,
        'tags': [],
        'ingredients': {},
        'portions': {},
        'method': [],
    }
    state = None
    current_cat = None
    current_person = None

    for raw in lines:
        line = raw.rstrip('\n')
        if not line.strip():
            continue

        kv = re.match(r'^(ID|Section|Name|Tags):\s*(.*)$', line)
        if kv:
            key = kv.group(1).lower()
            value = kv.group(2).strip()
            if key == 'id':
                data['id'] = value
            elif key == 'section':
                data['section'] = value
            elif key == 'name':
                data['name'] = value
            elif key == 'tags':
                data['tags'] = [t.strip() for t in value.split(',') if t.strip()]
            continue

        if line.startswith('Ingredients:'):
            state = 'ingredients'
            current_cat = None
            continue
        if line.startswith('Portions:'):
            state = 'portions'
            current_person = None
            continue
        if line.startswith('Method:'):
            state = 'method'
            continue

        if state == 'ingredients':
            section_header = re.match(r'^\s{2,}([^:]+):\s*$', line)
            bullet = re.match(r'^\s*[-*]\s*(.*)$', line)
            if section_header:
                current_cat = section_header.group(1).strip()
                data['ingredients'][current_cat] = []
                continue
            if bullet and current_cat is not None:
                data['ingredients'][current_cat].append(bullet.group(1).strip())
                continue
            if line.strip() and current_cat is not None and not line.lstrip().startswith('#'):
                data['ingredients'][current_cat].append(line.strip())
                continue
            continue

        if state == 'portions':
            person_header = re.match(r'^\s{2,}([^:]+):\s*$', line)
            bullet = re.match(r'^\s*[-*]\s*(.*)$', line)
            if person_header:
                current_person = person_header.group(1).strip()
                data['portions'][current_person] = []
                continue
            if bullet and current_person is not None:
                data['portions'][current_person].append(bullet.group(1).strip())
                continue
            if line.strip() and current_person is not None:
                data['portions'][current_person].append(line.strip())
                continue
            continue

        if state == 'method':
            bullet = re.match(r'^\s*[-*]\s*(.*)$', line)
            numbered = re.match(r'^\s*\d+\.\s*(.*)$', line)
            if bullet:
                data['method'].append(bullet.group(1).strip())
                continue
            if numbered:
                data['method'].append(numbered.group(1).strip())
                continue
            if line.strip():
                data['method'].append(line.strip())
            continue

    if not data['section']:
        raise ValueError('Recipe file must include a Section header, for example: Section: Breakfasts')
    if data['section'] not in SECTION_ORDER:
        raise ValueError(f"Section must be one of: {', '.join(SECTION_ORDER)}")
    if not data['name']:
        raise ValueError('Recipe file must include a Name header')
    if not data['ingredients']:
        raise ValueError('Recipe file must include an Ingredients section')
    if not data['portions']:
        raise ValueError('Recipe file must include a Portions section')
    if not data['method']:
        raise ValueError('Recipe file must include a Method section')

    return data


def html_tag(text: str) -> str:
    return html.escape(text, quote=False)


def build_recipe_fragment(data: dict, include_section_label: bool) -> str:
    tags_html = ' '.join(
        f'<span class="tag {html_tag(t.lower())}">{html_tag(t)}</span>'
        for t in data['tags']
    ) if data['tags'] else ''

    ingredients_html = []
    for cat, items in data['ingredients'].items():
        lines = ['      <div><div class="ing-label">' + html_tag(cat) + '</div>']
        for item in items:
            lines.append('        <div class="ing-item">' + html_tag(item) + '</div>')
        lines.append('      </div>')
        ingredients_html.append('\n'.join(lines))

    portions_html = []
    for person, items in data['portions'].items():
        lines = [
            '      <div class="portion-card"><div class="portion-name">' + html_tag(person) + '</div>'
        ]
        for item in items:
            lines.append('        <div class="portion-item">' + html_tag(item) + '</div>')
        lines.append('      </div>')
        portions_html.append('\n'.join(lines))

    method_html = '\n'.join('      <li>' + html_tag(step) + '</li>' for step in data['method'])

    section_label = f"<div class=\"section-label\">{html_tag(data['section'])}</div>\n\n" if include_section_label else ''
    fragment = f"""{section_label}<div class="meal-card" id="meal-{html_tag(data['id'])}">
  <div class="meal-header" onclick="toggleMeal('{html_tag(data['id'])}')">
    <div class="meal-check" id="check-{html_tag(data['id'])}" onclick="event.stopPropagation();selectMeal('{html_tag(data['id'])}')"><i class="ti ti-check" style="font-size:14px;display:none" id="tick-{html_tag(data['id'])}"></i></div>
    <div class="meal-title-block">
      <div class="meal-name">{html_tag(data['name'])}</div>
      <div class="meal-tags">{tags_html}</div>
    </div>
    <i class="ti ti-chevron-down expand-icon" id="icon-{html_tag(data['id'])}" aria-hidden="true"></i>
  </div>
  <div class="meal-body" id="body-{html_tag(data['id'])}">
    <div class="ingredients-grid">
{chr(10).join(ingredients_html)}
    </div>
    <div class="portion-grid">
{chr(10).join(portions_html)}
    </div>
    <div class="section-label" style="margin-top:.75rem">Method</div>
    <ol class="steps-list">
{method_html}
    </ol>
  </div>
</div>
"""
    return fragment


def update_recipe_ids(index_text: str, recipe_id: str, section: str) -> str:
    match = RECIPE_IDS_RE.search(index_text)
    if not match:
        raise ValueError('Could not locate recipeIds array in index.html')

    ids = re.findall(r"'([^']+)'", match.group(2))
    if recipe_id in ids:
        raise ValueError(f'Recipe ID "{recipe_id}" already exists in index.html')
    prefix = SECTION_PREFIX[section]

    # parse existing ids into (id, prefix, num)
    parsed = []
    for existing in ids:
        m = RECIPE_ID_RE.match(existing)
        if m:
            parsed.append((existing, m.group(1), int(m.group(2))))
        else:
            parsed.append((existing, None, None))

    new_m = RECIPE_ID_RE.match(recipe_id)
    new_num = int(new_m.group(2)) if new_m else None

    # find indices of same-prefix ids
    same_idxs = [i for i, (_id, p, n) in enumerate(parsed) if p == prefix]

    if same_idxs:
        # insert before first same-prefix id with a larger numeric suffix, otherwise after last same-prefix
        insert_at = same_idxs[-1] + 1
        for idx in same_idxs:
            existing_num = parsed[idx][2]
            if existing_num is not None and new_num is not None and existing_num > new_num:
                insert_at = idx
                break
    else:
        # no existing for this prefix: insert before the first id whose prefix comes later in SECTION_ORDER
        order = [SECTION_PREFIX[s] for s in SECTION_ORDER]
        try:
            prefix_pos = order.index(prefix)
        except ValueError:
            prefix_pos = 0
        insert_at = len(ids)
        for i, (_id, p, n) in enumerate(parsed):
            if p is None:
                continue
            try:
                if order.index(p) > prefix_pos:
                    insert_at = i
                    break
            except ValueError:
                continue

    ids.insert(insert_at, recipe_id)
    formatted = ',\n      '.join(f"'{item}'" for item in ids)
    replacement = f"{match.group(1)}\n      {formatted}\n    {match.group(3)}"
    return index_text[:match.start()] + replacement + index_text[match.end():]


def format_js_object(data: dict, indent: int = 2) -> str:
    lines = ['{']
    prefix = ' ' * indent
    for idx, (cat, items) in enumerate(data.items()):
        lines.append(f"{prefix}{json.dumps(cat)}: {{")
        inner_prefix = prefix + '  '
        for jdx, (item, value) in enumerate(items.items()):
            if isinstance(value, dict):
                lines.append(f"{inner_prefix}{json.dumps(item)}: {{")
                for kdx, (sub_key, sub_value) in enumerate(value.items()):
                    comma = ',' if kdx < len(value) - 1 else ''
                    lines.append(f"{inner_prefix}  {json.dumps(sub_key)}: {json.dumps(sub_value)}{comma}")
                end_comma = ',' if jdx < len(items) - 1 else ''
                lines.append(f"{inner_prefix}}}{end_comma}")
            else:
                comma = ',' if jdx < len(items) - 1 else ''
                lines.append(f"{inner_prefix}{json.dumps(item)}: {json.dumps(value)}{comma}")
        end_comma = ',' if idx < len(data) - 1 else ''
        lines.append(f"{prefix}}}{end_comma}")
    lines.append('}')
    return '\n'.join(lines)


def update_meals_object(index_text: str, recipe_id: str, name: str, items: dict) -> str:
    match = MEALS_OBJ_RE.search(index_text)
    if not match:
        raise ValueError('Could not locate meals object in index.html')
    items_text = format_js_object(items, indent=4)
    new_entry = f"\n  {recipe_id}: {{\n    name: {json.dumps(name)},\n    items: {items_text}\n  }},"
    prefix = match.group(1)
    body = match.group(2)
    suffix = match.group(3)
    return index_text[:match.start()] + prefix + body + new_entry + suffix + index_text[match.end():]


def find_recipe_text_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(path.glob('*.txt'))
    if path.is_file():
        return [path]
    raise FileNotFoundError(f'No recipe file or directory found at: {path}')


def process_recipe_file(recipe_file: Path, index_text: str, recipes_dir: Path) -> tuple[str, str]:
    recipe = parse_recipe_file(recipe_file)
    if not recipe['id']:
        recipe['id'] = find_next_recipe_id(index_text, recipe['section'])
    else:
        match = RECIPE_ID_RE.match(recipe['id'])
        if not match or SECTION_PREFIX[recipe['section']] != match.group(1):
            raise ValueError(f"Recipe ID '{recipe['id']}' does not match section prefix '{SECTION_PREFIX[recipe['section']]}'")

    section_ids = extract_recipe_ids(index_text)
    include_section_label = not any(id.startswith(SECTION_PREFIX[recipe['section']]) for id in section_ids)
    fragment = build_recipe_fragment(recipe, include_section_label)

    target_file = recipes_dir / f"{recipe['id']}.html"
    if target_file.exists():
        raise FileExistsError(f'Recipe fragment already exists: {target_file}')
    target_file.write_text(fragment, encoding='utf-8')
    print(f'Created fragment: {target_file}')

    shopping_items = build_shopping_items(recipe)
    index_text = update_recipe_ids(index_text, recipe['id'], recipe['section'])
    index_text = update_meals_object(index_text, recipe['id'], recipe['name'], shopping_items)
    return index_text, recipe['id']


def main() -> None:
    parser = argparse.ArgumentParser(description='Process a recipe text file or directory of recipe files into the meal planner page.')
    parser.add_argument('input_path', type=Path, help='Path to a recipe text file or a directory containing recipe .txt files')
    parser.add_argument('--index', type=Path, default=Path('index.html'), help='Path to index.html')
    parser.add_argument('--recipes-dir', type=Path, default=Path('recipes'), help='Directory to write recipe fragments')
    args = parser.parse_args()

    recipe_files = find_recipe_text_files(args.input_path)
    args.recipes_dir.mkdir(parents=True, exist_ok=True)

    original_index_text = args.index.read_text(encoding='utf-8')
    index_text = remove_deleted_recipe_references(original_index_text, args.recipes_dir)
    index_changed = index_text != original_index_text

    if recipe_files:
        for recipe_file in recipe_files:
            print(f'Processing {recipe_file}...')
            index_text, recipe_id = process_recipe_file(recipe_file, index_text, args.recipes_dir)
            print(f'Added {recipe_id} from {recipe_file}')
        args.index.write_text(index_text, encoding='utf-8')
        print(f'Updated index file: {args.index}')
    elif index_changed:
        args.index.write_text(index_text, encoding='utf-8')
        print(f'Cleaned deleted recipes and updated index file: {args.index}')
    else:
        print('No recipe files found to add. Index file already in sync.')


if __name__ == '__main__':
    main()
