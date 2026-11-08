"""Component typography must use the shared type styles rather than local sizes."""

from pathlib import Path
import re


TEMPLATES = Path(__file__).resolve().parents[1] / 'skills/dnd/display/templates'


def test_component_typography_uses_shared_styles():
    violations = []
    for name in ('index.html', 'campaign_portal.html', 'typography.html'):
        source = (TEMPLATES / name).read_text(encoding='utf-8')
        source = re.sub(r'/\*.*?\*/|<!--.*?-->', '', source, flags=re.S)
        for declaration in re.finditer(r'\b(font-size|font-family|font)\s*:\s*([^;}\n"<>]+)', source):
            kind, value = declaration.groups()
            value = value.strip()
            if value == 'inherit':
                continue
            if kind == 'font-size':
                valid = value.startswith('var(--type-')
            elif kind == 'font-family':
                valid = value in ('var(--font-app)', 'var(--font-heading)')
            else:
                valid = 'var(--type-' in value and 'var(--font-' in value
            if not valid:
                violations.append(f'{name}: {kind}: {value}')
    assert not violations, 'Use the existing shared typography styles:\n' + '\n'.join(violations)
