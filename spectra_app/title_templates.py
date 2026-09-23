"""Conservative filename fields and a literal-only plot title template language."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re
from string import Formatter

FIELDS = ('voltage', 'field', 'angle', 'valley', 'temperature', 'sample')
BUILTIN_PRESET = {'name': 'Current group', 'pattern': 'E={voltage}V {field}T {valley}',
                  'angle_map': {'85': 'K', '175': 'Kp'}}
_NUMBER = r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)'
_FORMATTER = Formatter()


def manual_settings(title: str | None = None) -> dict:
    return {'mode': 'manual', 'preset': None, 'manual_override': title}


def _angle_key(value: str) -> str:
    if not re.fullmatch(_NUMBER, value):
        raise ValueError('Angles must be plain numbers.')
    number = format(Decimal(value), 'f')
    return number.rstrip('0').rstrip('.') if '.' in number else number


def parse_angle_map(text: str) -> dict[str, str]:
    result = {}
    for part in text.split(';'):
        if not part.strip():
            continue
        pieces = part.split('=')
        if len(pieces) != 2 or not pieces[1].strip():
            raise ValueError('Angle map: use 85=K;175=Kp, or leave blank.')
        key = _angle_key(pieces[0].strip())
        if key in result:
            raise ValueError('Each angle may appear only once.')
        result[key] = pieces[1].strip()
    return result


def template_parts(pattern: str) -> list:
    try:
        parts = list(_FORMATTER.parse(pattern))
    except ValueError as exc:
        raise ValueError('Unmatched braces; use {{ and }} for literal braces.') from exc
    for _, field, spec, conversion in parts:
        if field is not None and (field not in FIELDS or spec or conversion):
            raise ValueError('Use only named fields: ' + ', '.join(FIELDS) + '.')
    return parts


def validate_preset(preset: object) -> dict:
    if (not isinstance(preset, dict) or set(preset) != {'name', 'pattern', 'angle_map'}
            or not isinstance(preset['name'], str) or not preset['name'].strip()
            or not isinstance(preset['pattern'], str) or not isinstance(preset['angle_map'], dict)):
        raise ValueError('Invalid title preset.')
    template_parts(preset['pattern'])
    seen = set()
    for angle, label in preset['angle_map'].items():
        if not isinstance(angle, str) or not isinstance(label, str) or not label.strip():
            raise ValueError('Invalid angle map.')
        normalized = _angle_key(angle)
        if normalized in seen:
            raise ValueError('Each angle may appear only once.')
        seen.add(normalized)
    return preset


def validate_title_settings(settings: object) -> dict:
    if (not isinstance(settings, dict) or set(settings) != {'mode', 'preset', 'manual_override'}
            or settings['mode'] not in ('manual', 'template')
            or (settings['manual_override'] is not None and not isinstance(settings['manual_override'], str))):
        raise ValueError('Invalid saved title settings.')
    if settings['mode'] == 'manual':
        if settings['preset'] is not None:
            raise ValueError('Manual titles cannot contain a preset.')
    else:
        validate_preset(settings['preset'])
    return settings


def filename_fields(filename: str) -> dict[str, str]:
    # Keep decimals intact: only remove a known file extension.
    name = Path(filename).name
    if name.lower().endswith('.dat'):
        name = name[:-4]
    patterns = {
        'voltage': rf'(?<![A-Za-z0-9.])(?:{_NUMBER}TG-BG|E)\s*=\s*({_NUMBER})(?:V)?(?=$|[$~_\s])',
        'field': rf'(?<![A-Za-z0-9.+-])({_NUMBER})T(?![A-Za-z])',
        'angle': rf'(?<![A-Za-z0-9.+-])({_NUMBER})deg(?=$|[$~_\s])',
        'temperature': rf'(?<![A-Za-z0-9.+-])({_NUMBER})K(?=Ref|$|[$~_\s])',
    }
    # Adjacent field/temperature tokens are common: 8T3.6KRef.
    temperature_name = re.sub(rf'(?<![A-Za-z0-9.+-]){_NUMBER}T(?={_NUMBER}K)', '_', name)
    result = {}
    for field, pattern in patterns.items():
        values = re.findall(pattern, temperature_name if field == 'temperature' else name)
        if field == 'voltage':
            values += re.findall(rf'(?<![A-Za-z0-9.=+-])({_NUMBER})V(?=$|[$~_\s])', name)
        unique = set(values)
        if len(unique) == 1:
            result[field] = values[0]
    sample = re.match(r'^\$([A-Za-z][A-Za-z0-9-]*\d[A-Za-z0-9-]*)\$~', name)
    if sample:
        result['sample'] = sample.group(1)
    return result


def render_title(filename: str, preset: dict) -> str:
    validate_preset(preset)
    values = filename_fields(filename)
    if 'angle' in values:
        mapping = {_angle_key(angle): label for angle, label in preset['angle_map'].items()}
        label = mapping.get(_angle_key(values['angle']))
        if label is not None:
            values['valley'] = label
    parts = template_parts(preset['pattern'])
    missing = sorted({field for _, field, _, _ in parts if field is not None and field not in values})
    if missing:
        raise ValueError('Missing or ambiguous filename fields: ' + ', '.join(missing) + '. Edit the title manually.')
    # No format/eval call: field lookup is restricted to the allowlist above.
    return ''.join(literal + (values[field] if field is not None else '') for literal, field, _, _ in parts)
