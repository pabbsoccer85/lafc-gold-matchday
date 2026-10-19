#!/usr/bin/env python3
"""LAFC Gold Matchday - merge the official ECNL feed into the LIVE site's data.

Usage:
  python3 -I refresh.py feed.txt index.html [--firestore fs.json] --out-page new.html --out-data new.json

feed.txt   : id|date|time|homeTid|awayTid|hg|ag|venue lines, then '#TEAMS', then tid|name lines
index.html : the live site's index.html (from the GitHub repo)
fs.json    : optional {"json": "<team/data json string>", "lastChecked": "..."} read from Firestore
             team/data (what admin mode publishes). Whichever of the two is newer is the starting point,
             so nothing published in admin mode is ever lost.

Rules (decided by Pablo, Oct 18 2026):
- Other teams' games: the league feed wins (new finals, corrected scores, moved/new/removed games).
- LAFC Gold games: admin is the source. A score admin already entered is never changed; if the league
  shows a different score it is reported (gold_mismatch). Gold dates/times/fields are never changed by the
  feed (TeamSnap/manager wins); differences are reported (gold_schedule_diff). Gold games the league stops
  listing are kept and reported. A Gold score is filled from the feed only when the app has none yet.
- Blank scores stay blank (never 0-0); a score already in the app is never blanked by the feed.
- Kids' last names stay initials; team-manager phone/email stay off the public data.
Prints a JSON summary; "changed" is true when the site needs publishing.
"""
import json, sys, re, datetime, argparse
from zoneinfo import ZoneInfo

ap = argparse.ArgumentParser()
ap.add_argument('feed'); ap.add_argument('page')
ap.add_argument('--firestore'); ap.add_argument('--out-page', required=True); ap.add_argument('--out-data', required=True)
args = ap.parse_args()

TAG = '<script type="application/json" id="gold-data">'
page = open(args.page, encoding='utf-8').read()
a = page.index(TAG) + len(TAG); b = page.index('</script>', a)
page_data = json.loads(page[a:b].replace('<\\/', '</'))
base, base_src = page_data, 'repo'
if args.firestore:
    try:
        fs = json.load(open(args.firestore))
        if fs and fs.get('json') and str(fs.get('lastChecked', '')) > str(page_data['meta'].get('lastChecked', '')):
            base, base_src = json.loads(fs['json']), 'admin-published (Firestore)'
    except Exception as e:
        print(f'note: could not use Firestore data ({e}); using the repo copy', file=sys.stderr)
data = base
GOLD = data['meta'].get('gold', 7)
PT = ZoneInfo('America/Los_Angeles')
now = datetime.datetime.now(PT)
today = now.date().isoformat()

teams_by_name = {t['name'].strip().lower(): t['id'] for t in data['teams']}
short = {t['id']: t['short'] for t in data['teams']}
lines = [l for l in open(args.feed, encoding='utf-8').read().strip().split('\n') if l.strip()]
sep = lines.index('#TEAMS')
tid_map = {}
for l in lines[sep + 1:]:
    tid, name = l.split('|', 1)
    key = name.strip().lower()
    if key not in teams_by_name:
        sys.exit(f'STOP: unknown team in feed: {name!r}. Nothing was changed. Add it to data.teams first.')
    tid_map[tid] = teams_by_name[key]
fixture_lines = lines[:sep]
if len(fixture_lines) < 50:
    sys.exit(f'STOP: feed has only {len(fixture_lines)} fixtures (expected about 150). Nothing was changed.')

def venue_fmt(v):
    v = re.sub(r'\s+', ' ', v or '').strip()
    return v.replace(' - ', ' · ') if v else None
def vs(h, a): return f'{short.get(h, h)} vs {short.get(a, a)}'

old = {m['id']: m for m in data['matches']}
ch = {k: [] for k in ['new_finals', 'changed_scores', 'new_fixtures', 'moved', 'removed',
                      'gold_new_result', 'gold_mismatch', 'gold_schedule_diff', 'gold_not_listed']}
out, seen = [], set()
for l in fixture_lines:
    id_, date, time, h, aw, hg, ag, venue = (l.split('|') + [''] * 8)[:8]
    seen.add(id_)
    home, away = tid_map[h], tid_map[aw]
    fhg = int(hg) if hg.strip() != '' else None
    fag = int(ag) if ag.strip() != '' else None
    ftime = time.strip() or None
    fvenue = venue_fmt(venue)
    prev = old.get(id_)
    gold = GOLD in (home, away)
    m = dict(prev) if prev else {'id': id_, 'note': ''}
    if not prev:
        m.update({'date': date, 'home': home, 'away': away, 'hg': fhg, 'ag': fag, 'time': ftime, 'venue': fvenue})
        ch['new_fixtures'].append(f'{date} {vs(home, away)} ({id_})' + (f' {fhg}–{fag}' if fhg is not None else ''))
    else:
        has_prev = prev.get('hg') is not None and prev.get('ag') is not None
        if fhg is not None and fag is not None:
            if not has_prev:
                m['hg'], m['ag'] = fhg, fag
                (ch['gold_new_result'] if gold else ch['new_finals']).append(f'{date} {short[home]} {fhg}–{fag} {short[away]}')
            elif (prev['hg'], prev['ag']) != (fhg, fag):
                if gold:
                    ch['gold_mismatch'].append(f"{date} {vs(home, away)}: app has {prev['hg']}–{prev['ag']} (kept), league shows {fhg}–{fag}")
                else:
                    m['hg'], m['ag'] = fhg, fag
                    ch['changed_scores'].append(f"{date} {vs(home, away)}: {prev['hg']}–{prev['ag']} → {fhg}–{fag}")
        if gold:
            diffs = []
            if prev.get('date') != date: diffs.append(f"date {date}")
            if ftime and prev.get('time') and prev.get('time') != ftime: diffs.append(f'time {ftime}')
            if diffs and 'league lists' not in (prev.get('note') or ''): ch['gold_schedule_diff'].append(f"{vs(home, away)} ({prev.get('date')} {prev.get('time') or ''}): league lists " + ', '.join(diffs) + ' — app unchanged')
        else:
            if prev.get('date') != date or (ftime and prev.get('time') != ftime):
                ch['moved'].append(f"{vs(home, away)}: {prev.get('date')} {prev.get('time') or ''} → {date} {ftime or ''}".strip())
            m['date'] = date
            if ftime: m['time'] = ftime
            if fvenue: m['venue'] = fvenue
        m['home'], m['away'] = home, away
    # status
    if m.get('hg') is not None and m.get('ag') is not None:
        m['status'] = 'final'
    elif prev and prev.get('status') == 'postponed' and prev.get('date') == m.get('date'):
        m['status'] = 'postponed'
    else:
        m['status'] = 'unreported' if m['date'] < today else 'scheduled'
    out.append(m)
for id_, prev in old.items():
    if id_ in seen: continue
    gold = GOLD in (prev['home'], prev['away'])
    if id_.startswith('local-') or gold:
        out.append(prev)
        if gold and not id_.startswith('local-'):
            ch['gold_not_listed'].append(f"{prev['date']} {vs(prev['home'], prev['away'])} ({id_}) — kept")
    else:
        ch['removed'].append(f"{prev['date']} {vs(prev['home'], prev['away'])} ({id_})")
# double-booked Gold dates note (same as before)
by_date = {}
for m in out:
    if GOLD in (m['home'], m['away']) and m.get('status') != 'postponed':
        by_date.setdefault(m['date'], []).append(m)
for d, ms in by_date.items():
    for m in ms:
        note = m.get('note') or ''
        if len(ms) > 1 and not note:
            m['note'] = 'Two LAFC Gold fixtures are listed on this date. Confirm with your team manager.'
        elif len(ms) == 1 and note.startswith('Two LAFC Gold'):
            m['note'] = ''
out.sort(key=lambda m: (m['date'], m.get('time') is None, m.get('time') or ''))
# privacy (same as the app's own cleaner)
for p in data.get('roster', []):
    if p.get('last'): p['last'] = str(p['last'])[0]
for s in data.get('staff', []):
    if re.search('manager', s.get('role', ''), re.I):
        s.pop('phone', None); s.pop('email', None); s['private'] = True
data['meta']['chatLink'] = ''
changed = any(ch[k] for k in ['new_finals', 'changed_scores', 'new_fixtures', 'moved', 'removed', 'gold_new_result'])
data['matches'] = out
data['meta']['snapshotLabel'] = now.strftime('%b %-d, %Y · %-I:%M %p PT')
data['meta']['snapshotDay'] = today
data['meta']['lastChecked'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
data['meta']['updatedBy'] = 'official ECNL results (auto-refresh)'
blob = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
json.dump(data, open(args.out_data, 'w', encoding='utf-8'), separators=(',', ':'), ensure_ascii=False)
open(args.out_page, 'w', encoding='utf-8').write(page[:a] + blob.replace('</', '<\\/') + page[b:])
gold_line = ''
for m in out:
    if GOLD in (m['home'], m['away']) and m.get('hg') is not None and m['date'] <= today:
        gold_line = f"{m['date']} {short[m['home']]} {m['hg']}–{m['ag']} {short[m['away']]}"
print(json.dumps({'changed': changed, 'base': base_src, 'fixtures': len(out),
                  'finals': sum(1 for m in out if m.get('hg') is not None), 'last_gold_result': gold_line,
                  'label': data['meta']['snapshotLabel'], **ch}, indent=1, ensure_ascii=False))
