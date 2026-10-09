"""Scrape the Tokyo Red Cross blood donation bus schedule.

Writes:
  site/data/stops.json   all stops: today onwards, plus the last HISTORY_DAYS days
  site/data/meta.json    when the page was last checked (not committed)
  site/feeds/<ward>.ics  one iCalendar feed per ward, city, town and village
  site/feeds/all.ics     every stop in Tokyo

Usage:
  python3 scraper/kenbus.py                    # fetch the live page
  python3 scraper/kenbus.py --html page.html   # parse a saved copy instead
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

SOURCE_URL = 'https://www.bs.jrc.or.jp/ktks/tokyo/place/m1_03_kenbus.html'
USER_AGENT = 'kenketsu-bus/1.0 (+https://github.com/drdrongo/kenketsu-bus)'
JST = dt.timezone(dt.timedelta(hours=9), 'JST')
HISTORY_DAYS = 90
ROOT = Path(__file__).resolve().parent.parent

# Every municipality in Tokyo, so each feed URL exists even in a month with no bus there.
WARDS = {
    '千代田区': 'chiyoda', '中央区': 'chuo', '港区': 'minato', '新宿区': 'shinjuku',
    '文京区': 'bunkyo', '台東区': 'taito', '墨田区': 'sumida', '江東区': 'koto',
    '品川区': 'shinagawa', '目黒区': 'meguro', '大田区': 'ota', '世田谷区': 'setagaya',
    '渋谷区': 'shibuya', '中野区': 'nakano', '杉並区': 'suginami', '豊島区': 'toshima',
    '北区': 'kita', '荒川区': 'arakawa', '板橋区': 'itabashi', '練馬区': 'nerima',
    '足立区': 'adachi', '葛飾区': 'katsushika', '江戸川区': 'edogawa',
    '八王子市': 'hachioji', '立川市': 'tachikawa', '武蔵野市': 'musashino', '三鷹市': 'mitaka',
    '青梅市': 'ome', '府中市': 'fuchu', '昭島市': 'akishima', '調布市': 'chofu',
    '町田市': 'machida', '小金井市': 'koganei', '小平市': 'kodaira', '日野市': 'hino',
    '東村山市': 'higashimurayama', '国分寺市': 'kokubunji', '国立市': 'kunitachi',
    '福生市': 'fussa', '狛江市': 'komae', '東大和市': 'higashiyamato', '清瀬市': 'kiyose',
    '東久留米市': 'higashikurume', '武蔵村山市': 'musashimurayama', '多摩市': 'tama',
    '稲城市': 'inagi', '羽村市': 'hamura', 'あきる野市': 'akiruno', '西東京市': 'nishitokyo',
    '瑞穂町': 'mizuho', '日の出町': 'hinode', '檜原村': 'hinohara', '奥多摩町': 'okutama',
    '大島町': 'oshima', '利島村': 'toshima-village', '新島村': 'niijima',
    '神津島村': 'kozushima', '三宅村': 'miyake', '御蔵島村': 'mikurajima',
    '八丈町': 'hachijo', '青ヶ島村': 'aogashima', '小笠原村': 'ogasawara',
}

DAY_RE = re.compile(r'<p>\s*(\d{1,2})月\s*(\d{1,2})日\s*\([^)]*\)\s*</p>(.*?)</table>', re.S)
OPTION_DATE_RE = re.compile(r'<option value="(\d{4})(\d{2})(\d{2})"')
ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
LI_RE = re.compile(r'<li[^>]*>(.*?)</li>', re.S)
HREF_RE = re.compile(r'href="([^"]+)"')
TAG_RE = re.compile(r'<[^>]+>')
SESSION_RE = re.compile(r'(\d{1,2}):(\d{2})\s*[~〜\-‐‑–—−ー]\s*(\d{1,2}):(\d{2})')


def clean(fragment: str) -> str:
    """HTML fragment -> plain text, with full-width digits and spaces made half-width."""
    text = html.unescape(TAG_RE.sub('', fragment))
    return ' '.join(unicodedata.normalize('NFKC', text).split())


def municipality(cell: str) -> str:
    # '西多摩郡　日の出町' -> '日の出町'
    return clean(cell).split(' ')[-1]


def slug_for(city: str) -> str:
    return WARDS.get(city) or 'area-' + hashlib.sha1(city.encode()).hexdigest()[:8]


def guess_year(month: int, day: int, today: dt.date) -> int:
    year = today.year
    if month < today.month - 6:
        year += 1
    elif month > today.month + 6:
        year -= 1
    return year


def parse_sessions(hours: str) -> list[list[str]]:
    sessions = []
    for h1, m1, h2, m2 in SESSION_RE.findall(hours):
        if int(h1) < 24 and int(h2) < 24 and (int(h1), int(m1)) < (int(h2), int(m2)):
            sessions.append([f'{int(h1):02d}:{m1}', f'{int(h2):02d}:{m2}'])
    return sessions


def parse_venue(cell: str) -> dict:
    items = LI_RE.findall(cell) or [cell]
    venue = {'venue': clean(items[0]), 'address': '', 'map': None, 'reserve': None, 'notes': []}
    for item in items[1:]:
        hrefs = [html.unescape(h) for h in HREF_RE.findall(item)]
        if any('kenketsu.jp' in h for h in hrefs):
            venue['reserve'] = next(h for h in hrefs if 'kenketsu.jp' in h)
        elif any('map' in h for h in hrefs) or clean(item).startswith('東京都'):
            venue['address'] = clean(item).removesuffix('[MAP]').strip()
            venue['map'] = next((h for h in hrefs if 'map' in h), None)
        elif clean(item):
            venue['notes'].append(clean(item))
    return venue


def parse(page: str, today: dt.date) -> list[dict]:
    """Return one dict per bus stop on the schedule page."""
    # The date picker has full yyyymmdd values; the table headings only have month and day.
    years = {(int(m), int(d)): int(y) for y, m, d in OPTION_DATE_RE.findall(page)}
    stops = []
    seen = set()
    for month, day, table in DAY_RE.findall(page):
        month, day = int(month), int(day)
        year = years.get((month, day)) or guess_year(month, day, today)
        date = dt.date(year, month, day).isoformat()
        for row in ROW_RE.findall(table):
            cells = CELL_RE.findall(row)
            if len(cells) != 3:
                continue  # header row
            city = municipality(cells[0])
            hours = [clean(h) for h in re.split(r'<br\s*/?>', cells[2])]
            stop = {
                'id': '',
                'date': date,
                'city': city,
                'slug': slug_for(city),
                **parse_venue(cells[1]),
                'hours': [h for h in hours if h],
                'sessions': parse_sessions(' '.join(hours)),
            }
            base = hashlib.sha1(f'{date}|{city}|{stop["venue"]}'.encode()).hexdigest()[:12]
            stop['id'] = base
            n = 2
            while stop['id'] in seen:
                stop['id'] = f'{base}-{n}'
                n += 1
            seen.add(stop['id'])
            stops.append(stop)
    return stops


def merge(previous: list[dict], scraped: list[dict], today: dt.date, now: str) -> list[dict]:
    """The page is the truth from its first date onwards. Older stops come from history."""
    start = min(s['date'] for s in scraped)
    cutoff = (today - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    first_seen = {s['id']: s.get('first_seen') for s in previous}
    kept = [s for s in previous if cutoff <= s['date'] < start]
    for stop in scraped:
        stop['first_seen'] = first_seen.get(stop['id']) or now
    return sorted(kept + scraped, key=lambda s: (s['date'], s['city'], s['venue'], s['id']))


# --- iCalendar -------------------------------------------------------------------------

def ics_text(value: str) -> str:
    return (value.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
            .replace('\n', '\\n'))


def fold(line: str) -> str:
    """Fold at 75 octets (RFC 5545 3.1), never inside a multi-byte character."""
    parts, current, size = [], '', 0
    for ch in line:
        n = len(ch.encode())
        if size + n > 75:
            parts.append(current)
            current, size = ' ', 1
        current += ch
        size += n
    parts.append(current)
    return '\r\n'.join(parts)


def utc(date: str, hhmm: str) -> str:
    local = dt.datetime.fromisoformat(f'{date}T{hhmm}:00').replace(tzinfo=JST)
    return local.astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def vevent(stop: dict) -> list[str]:
    stamp = dt.datetime.fromisoformat(stop['first_seen']).astimezone(dt.timezone.utc)
    lines = [
        'BEGIN:VEVENT',
        f'UID:{stop["id"]}@kenketsu-bus',
        f'DTSTAMP:{stamp.strftime("%Y%m%dT%H%M%SZ")}',
    ]
    if stop['sessions']:
        lines.append(f'DTSTART:{utc(stop["date"], stop["sessions"][0][0])}')
        lines.append(f'DTEND:{utc(stop["date"], stop["sessions"][-1][1])}')
    else:
        day = dt.date.fromisoformat(stop['date'])
        lines.append(f'DTSTART;VALUE=DATE:{day.strftime("%Y%m%d")}')
        lines.append(f'DTEND;VALUE=DATE:{(day + dt.timedelta(days=1)).strftime("%Y%m%d")}')
    description = ['受付: ' + ' / '.join(stop['hours'])]
    description += stop['notes']
    if stop['reserve']:
        description.append('Web予約 (ラブラッド): ' + stop['reserve'])
    if stop['map']:
        description.append('地図: ' + stop['map'])
    description.append('出典: ' + SOURCE_URL)
    lines += [
        f'SUMMARY:{ics_text("献血バス " + stop["venue"])}',
        f'LOCATION:{ics_text(stop["address"] or stop["venue"])}',
        f'DESCRIPTION:{ics_text(chr(10).join(description))}',
        f'URL:{stop["reserve"] or SOURCE_URL}',
        'END:VEVENT',
    ]
    return lines


def calendar(name: str, stops: list[dict]) -> str:
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//drdrongo//kenketsu-bus//JA',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:{ics_text(name)}',
        'X-WR-TIMEZONE:Asia/Tokyo',
        'REFRESH-INTERVAL;VALUE=DURATION:PT6H',
        'X-PUBLISHED-TTL:PT6H',
    ]
    for stop in stops:
        lines += vevent(stop)
    lines.append('END:VCALENDAR')
    return ''.join(fold(line) + '\r\n' for line in lines)


# --- Main ------------------------------------------------------------------------------

def fetch() -> str:
    request = urllib.request.Request(SOURCE_URL, headers={'User-Agent': USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode('utf-8')
        except OSError as error:
            if attempt == 2:
                raise
            print(f'fetch failed ({error}), retrying', file=sys.stderr)
            time.sleep(15 * (attempt + 1))
    raise AssertionError('unreachable')


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8', newline='')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--html', type=Path, help='parse this saved page instead of fetching')
    parser.add_argument('--out', type=Path, default=ROOT / 'site')
    args = parser.parse_args()

    now = dt.datetime.now(JST).replace(microsecond=0)
    page = args.html.read_text(encoding='utf-8') if args.html else fetch()
    scraped = parse(page, now.date())
    if not scraped:
        print('No stops found. The page layout probably changed.', file=sys.stderr)
        return 1

    data_path = args.out / 'data' / 'stops.json'
    previous = json.loads(data_path.read_text(encoding='utf-8')) if data_path.exists() else {}
    stops = merge(previous.get('stops', []), scraped, now.date(), now.isoformat())
    updated_at = previous.get('updated_at') if stops == previous.get('stops') else now.isoformat()

    known = dict(WARDS)
    known.update({s['city']: s['slug'] for s in stops})
    data = {
        'source': SOURCE_URL,
        # Stops first seen on the first run are not "new", so the site does not badge them.
        'first_run': previous.get('first_run') or now.isoformat(),
        'updated_at': updated_at,
        'wards': [{'name': name, 'slug': slug} for name, slug in known.items()],
        'stops': stops,
    }
    write(data_path, json.dumps(data, ensure_ascii=False, indent=1) + '\n')
    write(args.out / 'data' / 'meta.json', json.dumps({'checked_at': now.isoformat()}) + '\n')

    write(args.out / 'feeds' / 'all.ics', calendar('献血バス 東京都', stops))
    for name, slug in known.items():
        write(args.out / 'feeds' / f'{slug}.ics',
              calendar(f'献血バス {name}', [s for s in stops if s['slug'] == slug]))

    print(f'{len(scraped)} stops on the page, {len(stops)} in total, '
          f'{scraped[0]["date"]} to {scraped[-1]["date"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
