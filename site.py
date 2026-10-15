import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
SRC = Path('fx12_output/fx12_screening.json')
SITE = Path('docs')
SITE.mkdir(exist_ok=True)
DATA = json.loads(SRC.read_text(encoding='utf-8')) if SRC.exists() else {'generated_utc': None, 'results': []}


def parse_utc(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def jst(value):
    d = parse_utc(value)
    return d.astimezone(JST).strftime('%m/%d %H:%M') if d else '未取得'

