#!/usr/bin/env python3
"""
season_to_json.py — turn an iRacing "Current Race Schedule" PDF into the JSON
that iRacing Season Planner.html reads.

    pip install pdfplumber
    python3 season_to_json.py SeasonSchedule.pdf -o season.json

On Windows use -o, never "> season.json": PowerShell writes UTF-16 and the
planner cannot read it.

Then open the planner, click "Load season…", and pick season.json.
Your owned cars and tracks are matched by name, so they carry over.

Get the PDF from the iRacing site: Racing → Season Schedule → download PDF.

Only one thing needs hand-editing each season: FREE_CARS / FREE_TRACKS below,
which the planner uses to pre-tick base content on a fresh install. iRacing
publishes the current list at iracing.com/membership (scroll to the bottom).
"""

import argparse
import json
import re
import sys
import datetime
from collections import defaultdict

import pdfplumber

# --- iRacing base (free) content, current as of 2026 Season 3 -----------------
FREE_CARS = {
    "Street Stock - Panther C1", "Mini Stock", "Legends Ford '34 Coupe",
    "[Legacy] NASCAR Truck Chevrolet Silverado - 2008", "BMW M2 Racing (G87)",
    "Toyota GR86", "Global Mazda MX-5 Cup", "Cadillac CTS-V Racecar", "Kia Optima",
    "Pontiac Solstice", "Radical SR8", "SCCA Spec Racer Ford", "Ray FF1600",
    "Formula Vee - Classic", "Dirt Mini Stock", "Dirt Micro Sprint Car - Winged",
    "Dirt Micro Sprint Car - Non-Winged", "Dirt UMP Modified",
    "Dirt Legends Ford '34 Coupe", "Dirt Street Stock", "FIA Cross Car",
    "VW Beetle", "VW Beetle - Lite", "Lucas Oil Off Road Pro 2 Lite",
}
FREE_TRACKS = {
    "Circuito de Navarra", "Circuit de Lédenon", "Virginia International Raceway",
    "Motorsport Arena Oschersleben", "Rudskogen Motorsenter", "Winton Motor Raceway",
    "Lime Rock Park", "Wild West Motorsports Park", "Tsukuba Circuit",
    "Charlotte Motor Speedway", "Snetterton Circuit", "Southern National Motorsports Park",
    "Limaland Motorsports Park", "Lanier National Speedway", "South Boston Speedway",
    "Oran Park Raceway", "Thompson Speedway Motorsports Park", "Oulton Park Circuit",
    "Okayama International Circuit", "Concord Speedway", "Oxford Plains Speedway",
    "USA International Speedway", "Summit Point Raceway", "Langley Speedway",
    "[Legacy] Phoenix Raceway", "Daytona Rallycross and Dirt Road",
}

WEEK_RE = re.compile(r'^Week (\d+) \((\d{4}-\d{2}-\d{2})\)')
TIME_RE = re.compile(r'^\(\d{4}-\d{2}-\d{2} \d{2}:\d{2}')
PAGENUM_RE = re.compile(r'^\d{1,3}$')
CAT_RE = re.compile(r'^(OVAL|SPORTS CAR|FORMULA CAR|DIRT OVAL|DIRT ROAD)$')
CLS_RE = re.compile(r'^([RDCBA]) Class Series \((.+)\)$')
MIN_RE = re.compile(r'^Min entries for official:')
TEMP_RE = re.compile(r'^\d{2,3}°[FC]/')
HEAT_RE = re.compile(r'^[A-Z]:\d+[LM]$')
TYPE = {'OVAL': 'Oval', 'SPORTS CAR': 'Sports Car', 'FORMULA CAR': 'Formula Car',
        'DIRT OVAL': 'Dirt Oval', 'DIRT ROAD': 'Dirt Road', 'UNRANKED': 'Unranked'}


def join_wrapped(parts):
    """Rejoin lines the PDF wrapped. A trailing hyphen mid-word joins tight."""
    out = ''
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not out:
            out = p
        elif out.endswith('-') and len(out) > 1 and out[-2] != ' ':
            out += p
        else:
            out += ' ' + p
    return out


def read_rows(path):
    """Flatten the PDF into rows tagged full / week / cont, with columns split
    on the x-positions taken from each 'Week N (date)' header row."""
    rows = []
    track_x = weather_x = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            lines = defaultdict(list)
            for w in words:
                lines[round(w['top'] / 3.0)].append(w)
            for key in sorted(lines):
                ws = sorted(lines[key], key=lambda w: w['x0'])
                text = ' '.join(w['text'] for w in ws)
                if PAGENUM_RE.match(text.strip()):
                    continue
                m = WEEK_RE.match(text)
                if m:
                    i = next((k for k, w in enumerate(ws)
                              if re.match(r'\(\d{4}-\d{2}-\d{2}\)', w['text'])), 0)
                    if i + 1 < len(ws):
                        track_x = ws[i + 1]['x0']
                    wx = next((w['x0'] for w in ws if TEMP_RE.match(w['text'])), None)
                    if wx:
                        weather_x = wx
                    rows.append({'kind': 'week', 'week': int(m.group(1)), 'date': m.group(2),
                                 'col2': cols(ws, track_x, weather_x), 'tail': tail(ws)})
                    continue
                if track_x and ws[0]['x0'] >= track_x - 3:
                    rows.append({'kind': 'cont', 'col2': cols(ws, track_x, weather_x),
                                 'tail': tail(ws)})
                else:
                    rows.append({'kind': 'full', 'text': text})
    return rows


def cols(ws, track_x, weather_x):
    return ' '.join(w['text'] for w in ws if track_x - 3 <= w['x0'] < weather_x - 3)


def tail(ws):
    return [w['text'] for w in ws]


def race_length(row, following):
    """The rightmost column: '40 mins', '70 laps', or dirt heats 'H:8L C:10L F:50L'.
    It sometimes wraps onto the next line."""
    t = row['tail']
    if len(t) >= 2 and t[-1] in ('laps', 'mins') and t[-2].isdigit():
        return f'{t[-2]} {t[-1]}'
    if t and HEAT_RE.match(t[-1]):
        parts = [t[-1]]
        for nxt in following:
            if nxt['kind'] != 'cont' or not nxt['tail'] or not HEAT_RE.match(nxt['tail'][-1]):
                break
            parts.append(nxt['tail'][-1])
        return ' '.join(parts)
    if t and t[-1].isdigit():
        for nxt in following[:1]:
            if nxt.get('tail') and nxt['tail'][-1] in ('laps', 'mins'):
                return f"{t[-1]} {nxt['tail'][-1]}"
    return ''


def parse_series(rows):
    series, cat, cls, cur, pending = [], None, None, None, []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r['kind'] == 'full':
            t = r['text'].strip()
            if CAT_RE.match(t):
                cat, pending = t, []
                i += 1
                continue
            m = CLS_RE.match(t)
            if m:
                cls = m.group(1)
                if m.group(2) in TYPE or m.group(2) == 'UNRANKED':
                    cat = m.group(2)
                pending = []
                i += 1
                continue
            if MIN_RE.match(t):
                lic_i = next((k for k, s in enumerate(pending) if '-->' in s), None)
                if lic_i is None:
                    pending = []
                    i += 1
                    continue
                cur = {'category': cat, 'class': cls, 'series': pending[0],
                       'cars_raw': join_wrapped(pending[1:lic_i]),
                       'license': pending[lic_i],
                       'races': pending[lic_i + 1] if lic_i + 1 < len(pending) else '',
                       'weeks': []}
                series.append(cur)
                pending = []
                i += 1
                continue
            pending.append(t)
            i += 1
            continue
        if r['kind'] == 'week':
            pending = []
            col2 = [r['col2']]
            j = i + 1
            while j < len(rows) and rows[j]['kind'] == 'cont':
                if rows[j]['col2'].strip():
                    col2.append(rows[j]['col2'].strip())
                j += 1
            if cur is not None:
                cur['weeks'].append({'week': r['week'], 'date': r['date'], 'col2': col2,
                                     'len': race_length(r, rows[i + 1:i + 6])})
            i = j
            continue
        i += 1
    return series


def min_licence(lic):
    m = re.match(r'^(Rookie|Class ([A-D])|Pro/WC|Pro)\s*([\d.]+)?', lic)
    if not m:
        return '?'
    cls = 'R' if m.group(1) == 'Rookie' else (m.group(2) or 'P')
    return f'{cls} {m.group(3)}' if m.group(3) else cls


def build(series):
    cars, tracks, ci, ti = [], [], {}, {}

    def cid(n):
        if n not in ci:
            ci[n] = len(cars)
            cars.append(n)
        return ci[n]

    def tid(n):
        if n not in ti:
            ti[n] = len(tracks)
            tracks.append(n)
        return ti[n]

    out_series, out_rows = [], []
    for s in series:
        per_week_cars = s['cars_raw'].lower().startswith('see race week')
        sidx = len(out_series)
        out_series.append({
            'n': s['series'], 'c': s['class'] or 'R',
            't': TYPE.get(s['category'], s['category']),
            'l': min_licence(s['license']), 'r': s['races'],
            'f': 1 if re.search(r'fixed', s['series'], re.I) else 0,
            'm': 1 if 'Team racing' in s['license'] else 0,
        })
        weeks = []
        for w in s['weeks']:
            parts, session = [], ''
            for p in w['col2']:
                if TIME_RE.match(p):
                    session = p
                elif not session:
                    parts.append(p)
            if per_week_cars and len(parts) > 1:
                track, carstr = parts[0], join_wrapped(parts[1:])
            else:
                track, carstr = join_wrapped(parts), s['cars_raw']
            if carstr.lower().startswith('see race week'):
                carstr = ''
            weeks.append({'week': w['week'], 'date': w['date'], 'track': track,
                          'cars': carstr, 'len': w['len']})
        weeks.sort(key=lambda x: x['date'])
        for i, w in enumerate(weeks):
            start = datetime.date.fromisoformat(w['date'])
            if i + 1 < len(weeks):
                end = datetime.date.fromisoformat(weeks[i + 1]['date'])
            else:
                gap = (start - datetime.date.fromisoformat(weeks[i - 1]['date'])).days if i else 7
                end = start + datetime.timedelta(days=max(gap, 7))
            base = w['track'].split(' - ')[0].strip()
            out_rows.append({
                's': sidx, 'w': w['week'], 'a': str(start), 'b': str(end),
                't': tid(base), 'y': w['track'][len(base):].lstrip(' -').strip(),
                'c': [cid(c.strip()) for c in w['cars'].split(', ') if c.strip()],
                'd': w['len'],
            })
    return {
        'built': str(datetime.date.today()),
        'season': season_label(out_rows),
        'cars': cars, 'tracks': tracks, 'series': out_series, 'rows': out_rows,
        'freeCars': sorted(ci[c] for c in FREE_CARS if c in ci),
        'freeTracks': sorted(ti[t] for t in FREE_TRACKS if t in ti),
    }


def season_label(rows):
    if not rows:
        return 'Unknown season'
    tally = defaultdict(int)
    for r in rows:
        if r['w'] == 1:
            tally[r['a']] += 1
    first = datetime.date.fromisoformat(max(tally, key=tally.get))
    q = (first.month - 12) % 12 // 3 + 1
    year = first.year + 1 if first.month == 12 else first.year
    return f'{year} Season {q}'


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pdf', help='the iRacing season schedule PDF')
    ap.add_argument('-o', '--out', help='write here as UTF-8 (recommended on Windows)')
    args = ap.parse_args()

    data = build(parse_series(read_rows(args.pdf)))
    print(f"{len(data['series'])} series · {len(data['rows'])} series-weeks · "
          f"{len(data['cars'])} cars · {len(data['tracks'])} tracks · "
          f"{len(data['freeCars'])}/{len(data['freeTracks'])} free matched",
          file=sys.stderr)
    unmatched = (FREE_CARS - set(data['cars'])) | (FREE_TRACKS - set(data['tracks']))
    if unmatched:
        print('free content not used by any series this season: '
              + ', '.join(sorted(unmatched)), file=sys.stderr)
    if args.out:
        with open(args.out, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(',', ':'))
        print(f'wrote {args.out}', file=sys.stderr)
    else:
        json.dump(data, sys.stdout, ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    main()
