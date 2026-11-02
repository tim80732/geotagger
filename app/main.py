import csv
import hashlib
import io
import json
import os
import time

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import matcher, track
from .db import DB
from .exif import ExifTool
from .jobs import JPG_EXT, RAW_EXT, Runner

os.umask(0o000)          # new sidecars get Unraid's usual 666, like the photos themselves

DATA = os.environ.get('DATA_DIR', '/data')
ROOTS = [r.rstrip('/') for r in os.environ.get('PHOTO_ROOTS', '/photos').split(',') if r.strip()]
STATIC = os.path.join(os.path.dirname(__file__), '..', 'static')
os.makedirs(os.path.join(DATA, 'gpx'), exist_ok=True)
os.makedirs(os.path.join(DATA, 'thumbs'), exist_ok=True)

DEFAULTS = {
    'source': 'dawarich',
    'dawarich_url': os.environ.get('DAWARICH_URL', ''),
    'dawarich_key': os.environ.get('DAWARICH_API_KEY', ''),
    'photon_url': os.environ.get('PHOTON_URL', 'https://photon.komoot.io'),
    'max_gap_min': 10, 'nearest_min': 2, 'max_accuracy_m': 100,
    'warn_km': 1, 'frozen_min': 30,
    'types': 'both', 'subfolders': True, 'has_gps': 'replace',
    'no_tz': 'skip', 'assume_offset': '+02:00',
}
OPTION_KEYS = ['source', 'subfolders', 'max_gap_min', 'nearest_min', 'max_accuracy_m', 'warn_km', 'frozen_min',
               'has_gps', 'no_tz', 'assume_offset']

db = DB(os.path.join(DATA, 'geotagger.db'))
exif = ExifTool()          # jobs
exif_ui = ExifTool()       # thumbnails, so they don't wait behind a running job


def settings():
    return db.get_settings(DEFAULTS)


runner = Runner(db, exif, settings, DATA)
app = FastAPI(title='Geotagger')


@app.on_event('shutdown')
def stop_writing():
    runner.shutdown()


@app.middleware('http')
async def revalidate_app_files(request, call_next):
    """The page and its own script/style are checked for a newer version on every load, so updates show at once."""
    response = await call_next(request)
    p = request.url.path
    if p == '/' or p.startswith('/static/app.'):
        response.headers['Cache-Control'] = 'no-cache'
    return response


def inside_roots(path):
    real = os.path.realpath(path)
    for r in ROOTS:
        if real == os.path.realpath(r) or real.startswith(os.path.realpath(r) + os.sep):
            return real
    raise HTTPException(400, 'That folder is outside the photo libraries.')


# ---------------------------------------------------------------- settings
@app.get('/api/settings')
def get_settings():
    s = settings()
    s['dawarich_key_set'] = bool(s.pop('dawarich_key'))
    return s


@app.put('/api/settings')
async def put_settings(request: Request):
    body = await request.json()
    values = {k: v for k, v in body.items() if k in DEFAULTS}
    if not values.get('dawarich_key'):
        values.pop('dawarich_key', None)          # empty = keep the current key
    db.set_settings(values)
    return get_settings()


@app.post('/api/settings/test')
async def test_connection(request: Request):
    body = await request.json()
    s = settings()
    url = body.get('dawarich_url') or s['dawarich_url']
    key = body.get('dawarich_key') or s['dawarich_key']
    if not url or not key:
        return {'ok': False, 'message': 'Enter the Dawarich URL and API key first.'}
    try:
        return track.test_dawarich(url, key)
    except Exception as e:                                   # noqa: BLE001
        return {'ok': False, 'message': str(e)}


@app.get('/api/libraries')
def libraries():
    return [{'path': r, 'exists': os.path.isdir(r), 'writable': os.access(r, os.W_OK)} for r in ROOTS]


@app.get('/api/info')
def info():
    return {'exiftool': exif_ui.version(), 'roots': ROOTS}


# ---------------------------------------------------------------- GPX
@app.get('/api/gpx')
def list_gpx():
    return db.q('SELECT * FROM gpx ORDER BY first')


@app.post('/api/gpx')
async def upload_gpx(files: list[UploadFile]):
    added = []
    for f in files:
        gid = db.x('INSERT INTO gpx (name, added) VALUES (?, ?)', (f.filename, time.time()))
        path = os.path.join(DATA, 'gpx', f'{gid}.gpx')
        with open(path, 'wb') as out:
            out.write(await f.read())
        try:
            pts = track.parse_gpx(path)
        except Exception as e:                               # noqa: BLE001
            os.remove(path)
            db.x('DELETE FROM gpx WHERE id = ?', (gid,))
            raise HTTPException(400, f'{f.filename}: not a readable GPX file ({e})')
        db.x('UPDATE gpx SET points = ?, first = ?, last = ? WHERE id = ?',
             (len(pts), pts[0][0] if pts else None, pts[-1][0] if pts else None, gid))
        added.append(gid)
    return list_gpx()


@app.delete('/api/gpx/{gid}')
def delete_gpx(gid: int):
    path = os.path.join(DATA, 'gpx', f'{gid}.gpx')
    if os.path.exists(path):
        os.remove(path)
    db.x('DELETE FROM gpx WHERE id = ?', (gid,))
    return list_gpx()


# ---------------------------------------------------------------- browse
def count_files(folder):
    """Files directly in the folder, and in it plus all its subfolders (hidden ones left out, as in a dry run)."""
    here = {'jpg': 0, 'raw': 0, 'mov': 0, 'other': 0}
    deep = dict(here)
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for name in names:
            ext = os.path.splitext(name)[1].lower()
            if ext in JPG_EXT:
                k = 'jpg'
            elif ext in RAW_EXT:
                k = 'raw'
            elif ext in ('.mov', '.mp4', '.m4v', '.avi', '.mts'):
                k = 'mov'
            elif ext not in ('.xmp',):
                k = 'other'
            else:
                continue
            deep[k] += 1
            if root == folder:
                here[k] += 1
    return here, deep


@app.get('/api/browse')
def browse(path: str = ''):
    path = inside_roots(path or ROOTS[0])
    if not os.path.isdir(path):
        raise HTTPException(404, 'Folder not found.')
    dirs = []
    with os.scandir(path) as it:
        for e in sorted(it, key=lambda e: e.name.lower()):
            if e.is_dir() and not e.name.startswith('.'):
                counts, deep = count_files(e.path)
                dirs.append({'name': e.name, 'path': e.path, 'counts': counts, 'deep': deep})
    parent = os.path.dirname(path)
    root = next(r for r in ROOTS if path == os.path.realpath(r) or path.startswith(os.path.realpath(r) + os.sep))
    here, here_deep = count_files(path)
    return {'path': path, 'root': root, 'parent': None if path == os.path.realpath(root) else parent,
            'here': here, 'here_deep': here_deep, 'dirs': dirs}


# ---------------------------------------------------------------- jobs
def job_summary(job):
    jid = job['id']
    s = job['options'] or {}
    warn_m = float(s.get('warn_km', 1)) * 1000

    def n(where, args=()):
        return db.one(f'SELECT COUNT(*) n FROM photos WHERE job_id = ? AND {where}', (jid, *args))['n']

    by_kind = {r['kind']: r['n'] for r in db.q('SELECT kind, COUNT(*) n FROM photos WHERE job_id = ? GROUP BY kind', (jid,))}
    write_kind = {r['kind']: r['n'] for r in db.q(
        "SELECT kind, COUNT(*) n FROM photos WHERE job_id = ? AND action = 'write' AND written = 0 GROUP BY kind", (jid,))}
    tzs = {r['tz'] or 'none': r['n'] for r in db.q(
        'SELECT tz, COUNT(*) n FROM photos WHERE job_id = ? GROUP BY tz ORDER BY n DESC', (jid,))}
    span = db.one('SELECT MIN(utc) a, MAX(utc) b FROM photos WHERE job_id = ?', (jid,))
    first = db.one('SELECT dto, tz FROM photos WHERE job_id = ? AND utc = ?', (jid, span['a'])) if span['a'] else None
    last = db.one('SELECT dto, tz FROM photos WHERE job_id = ? AND utc = ?', (jid, span['b'])) if span['b'] else None
    worst = db.one('SELECT name, cam_diff_m FROM photos WHERE job_id = ? AND cam_diff_m IS NOT NULL '
                   'ORDER BY cam_diff_m DESC LIMIT 1', (jid,))
    frozen_span = db.one('SELECT MIN(dto) a, MAX(dto) b FROM photos WHERE job_id = ? AND frozen = 1', (jid,))
    return {
        'total': sum(by_kind.values()), 'by_kind': by_kind,
        'to_write': sum(write_kind.values()), 'to_write_kind': write_kind,
        'written': n("action = 'write' AND written = 1"),
        'skipped': n("action = 'skip'"),
        'gap': n("action = 'skip' AND m_status IN ('gap', 'outside') AND manual = 0"),
        'frozen': n('frozen = 1'), 'frozen_from': frozen_span['a'], 'frozen_to': frozen_span['b'],
        'off': n('cam_diff_m > ?', (warn_m,)), 'worst': worst,
        'manual': n('manual = 1'),
        'camera_gps': n('cam_lat IS NOT NULL AND ours = 0'),
        'errors': n('write_error IS NOT NULL'),
        'timezones': tzs, 'first': first, 'last': last,
    }


def job_out(job, summary=True):
    out = dict(job)
    left, rate = runner.eta(job) if job['status'] in ('scanning', 'writing') else (None, None)
    out['eta_s'], out['rate'] = left, rate
    if summary:
        out['summary'] = job_summary(job)
    return out


@app.post('/api/jobs')
async def create_job(request: Request):
    body = await request.json()
    folders = [inside_roots(f) for f in body.get('folders', [])]
    if not folders:
        raise HTTPException(400, 'Choose at least one folder.')
    s = settings()
    opts = {k: body.get('options', {}).get(k, s[k]) for k in OPTION_KEYS}
    jid = db.x('INSERT INTO jobs (created, folders, types, options, status) VALUES (?, ?, ?, ?, ?)',
               (time.time(), json.dumps(folders), body.get('types', s['types']), json.dumps(opts), 'queued'))
    runner.submit(jid, 'scan')
    return job_out(db.job(jid), summary=False)


@app.get('/api/jobs')
def list_jobs():
    out = []
    for j in db.q('SELECT id FROM jobs ORDER BY id DESC'):
        job = db.job(j['id'])
        o = job_out(job, summary=False)
        o['counts'] = db.one("SELECT COUNT(*) total, SUM(action = 'write') tag, SUM(action = 'write' AND written = 1) done "
                             "FROM photos WHERE job_id = ?", (job['id'],))
        out.append(o)
    return out


def get_job(jid):
    job = db.job(jid)
    if not job:
        raise HTTPException(404, 'No such job.')
    return job


@app.get('/api/jobs/{jid}')
def read_job(jid: int):
    return job_out(get_job(jid))


@app.delete('/api/jobs/{jid}')
def delete_job(jid: int):
    job = get_job(jid)
    if job['status'] in ('scanning', 'writing', 'queued'):
        raise HTTPException(409, 'Stop the job first.')
    db.x('DELETE FROM photos WHERE job_id = ?', (jid,))
    db.x('DELETE FROM jobs WHERE id = ?', (jid,))
    return {'ok': True}


FILTERS = {
    'all': '1 = 1',
    'write': "action = 'write'",
    'skipped': "action = 'skip'",
    'gap': "action = 'skip' AND m_status IN ('gap', 'outside') AND manual = 0",
    'frozen': 'frozen = 1',
    'off': 'cam_diff_m > ?',
    'manual': 'manual = 1',
    'errors': 'write_error IS NOT NULL',
    'jpg': "kind = 'jpg'",
    'raw': "kind = 'raw'",
}
ROW_COLS = ('id, name, kind, path, dto, tz, utc, m_status, action, reason, t_lat, t_lon, gap_s, bracket_s, '
            'cam_lat, cam_lon, cam_diff_m, frozen, manual, written, write_error, ours')


@app.get('/api/jobs/{jid}/photos')
def job_photos(jid: int, filter: str = 'all', offset: int = 0, limit: int = 100):
    job = get_job(jid)
    where = FILTERS.get(filter, '1 = 1')
    args = (float(job['options'].get('warn_km', 1)) * 1000,) if '?' in where else ()
    total = db.one(f'SELECT COUNT(*) n FROM photos WHERE job_id = ? AND {where}', (jid, *args))['n']
    rows = db.q(f'SELECT {ROW_COLS} FROM photos WHERE job_id = ? AND {where} '
                f'ORDER BY utc IS NULL, utc, kind, name LIMIT ? OFFSET ?', (jid, *args, limit, offset))
    return {'total': total, 'rows': rows}


@app.get('/api/jobs/{jid}/map')
def job_map(jid: int):
    get_job(jid)
    path = os.path.join(DATA, 'tracks', f'{jid}.json')
    segs = json.load(open(path)) if os.path.exists(path) else []
    # one marker per photo moment (a JPG and its RAW twin share one)
    pts = db.q("SELECT MIN(id) id, MIN(name) name, t_lat, t_lon, MAX(manual) manual, MIN(written) written "
               "FROM photos WHERE job_id = ? AND action = 'write' "
               "GROUP BY round(t_lat, 6), round(t_lon, 6), utc", (jid,))
    frozen = db.q('SELECT round(cam_lat, 6) lat, round(cam_lon, 6) lon, COUNT(*) n, MIN(dto) a, MAX(dto) b '
                  'FROM photos WHERE job_id = ? AND frozen = 1 GROUP BY 1, 2', (jid,))
    return {'segments': segs,
            'photos': [[p['id'], p['t_lat'], p['t_lon'], p['manual'], p['written'], p['name']] for p in pts],
            'frozen': frozen}


def editable(job):
    if job['status'] in ('scanning', 'writing', 'queued'):
        raise HTTPException(409, 'Wait until the job has stopped.')


def twins(jid, ids):
    """The chosen photos plus their JPG/RAW twins (same name stem, same capture time)."""
    if not ids:
        return []
    marks = ','.join('?' * len(ids))
    sel = db.q(f'SELECT name, utc FROM photos WHERE job_id = ? AND id IN ({marks})', (jid, *ids))
    out = set(ids)
    for s in sel:
        stem = os.path.splitext(s['name'])[0]
        for r in db.q('SELECT id, name FROM photos WHERE job_id = ? AND utc IS ? AND name LIKE ?',
                      (jid, s['utc'], stem + '.%')):
            if os.path.splitext(r['name'])[0] == stem:
                out.add(r['id'])
    return list(out)


def redecide(jid, ids):
    job = db.job(jid)
    manual = {r['path']: r for r in db.q('SELECT * FROM manual')}
    marks = ','.join('?' * len(ids))
    for ph in db.q(f'SELECT * FROM photos WHERE job_id = ? AND id IN ({marks})', (jid, *ids)):
        matcher.decide(ph, job['options']['has_gps'], manual.get(ph['path']))
        db.x('UPDATE photos SET manual = ?, action = ?, t_lat = ?, t_lon = ?, t_alt = ?, reason = ?, written = ?, '
             'write_error = NULL WHERE id = ?',
             (ph['manual'], ph['action'], ph['t_lat'], ph['t_lon'], ph['t_alt'], ph['reason'], ph['written'], ph['id']))


@app.post('/api/jobs/{jid}/place')
async def place(jid: int, request: Request):
    editable(get_job(jid))
    body = await request.json()
    ids = twins(jid, [int(i) for i in body['ids']])
    marks = ','.join('?' * len(ids))
    paths = [r['path'] for r in db.q(f'SELECT path FROM photos WHERE job_id = ? AND id IN ({marks})', (jid, *ids))]
    mode = body.get('mode', 'set')
    for p in paths:
        if mode == 'set':
            db.x('INSERT INTO manual (path, lat, lon, excluded, created) VALUES (?, ?, ?, 0, ?) '
                 'ON CONFLICT(path) DO UPDATE SET lat = excluded.lat, lon = excluded.lon, excluded = 0',
                 (p, float(body['lat']), float(body['lon']), time.time()))
        elif mode == 'exclude':
            db.x('INSERT INTO manual (path, excluded, created) VALUES (?, 1, ?) '
                 'ON CONFLICT(path) DO UPDATE SET excluded = 1', (p, time.time()))
        else:                                                # 'track': back to the track position
            db.x('DELETE FROM manual WHERE path = ?', (p,))
    redecide(jid, ids)
    if db.job(jid)['status'] == 'done':
        db.update_job(jid, status='ready', phase='Changed since the last write')
    return job_out(get_job(jid))


@app.post('/api/jobs/{jid}/write')
def start_write(jid: int):
    job = get_job(jid)
    if job['status'] not in ('ready', 'done', 'paused', 'cancelled', 'failed'):
        raise HTTPException(409, f'The job is {job["status"]}.')
    if job['status'] != 'paused':
        result = job['result'] or {}
        result.pop('write_started', None)
        db.update_job(jid, result=result)
    runner.submit(jid, 'write')
    return job_out(get_job(jid), summary=False)


@app.post('/api/jobs/{jid}/{action}')
def control(jid: int, action: str):
    job = get_job(jid)
    if action in ('pause', 'cancel'):
        scan = job['status'] == 'scanning' or (job['status'] == 'queued' and not job['total'])
        if action == 'pause' and scan:
            raise HTTPException(409, 'A dry run can only be cancelled.')
        if job['status'] == 'queued' and scan:
            db.update_job(jid, status='cancelled', phase='Dry run cancelled', done=0, total=0)
        elif job['status'] == 'queued':
            db.update_job(jid, status='paused' if action == 'pause' else 'cancelled', phase=action.title() + 'd')
        runner.flags[jid] = action
    elif action == 'resume':
        return start_write(jid)
    elif action == 'rescan':
        editable(job)
        db.x('DELETE FROM photos WHERE job_id = ?', (jid,))
        db.update_job(jid, result=None, done=0, total=0)
        runner.submit(jid, 'scan')
    else:
        raise HTTPException(404)
    return job_out(get_job(jid), summary=False)


@app.get('/api/jobs/{jid}/csv')
def job_csv(jid: int):
    get_job(jid)
    rows = db.q('SELECT name, kind, dto, tz, utc, reason, action, t_lat, t_lon, t_alt, gap_s, bracket_s, '
                'cam_lat, cam_lon, cam_diff_m, frozen, manual, written, write_error, path FROM photos '
                'WHERE job_id = ? ORDER BY utc, kind, name', (jid,))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ['name'])
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="geotagger-job-{jid}.csv"'})


# ---------------------------------------------------------------- full-size view, nearest GPS
@app.get('/api/photo/{pid}/full')
def full_image(pid: int):
    """The photo itself for a JPG; the largest embedded preview for a RAW file."""
    ph = db.one('SELECT path, kind FROM photos WHERE id = ?', (pid,))
    if not ph:
        raise HTTPException(404)
    if ph['kind'] == 'jpg':
        return FileResponse(ph['path'], media_type='image/jpeg', headers={'Cache-Control': 'max-age=3600'})
    for tag in ('JpgFromRaw', 'PreviewImage', 'ThumbnailImage'):
        data = exif_ui.binary(ph['path'], tag)
        if data:
            return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'max-age=3600'})
    raise HTTPException(404, 'No preview inside this raw file.')


@app.get('/api/photo/{pid}/nearby')
def nearby(pid: int):
    """Closest GPS points in time on either side of the photo, for placing it by hand."""
    ph = db.one('SELECT p.utc, j.options FROM photos p JOIN jobs j ON j.id = p.job_id WHERE p.id = ?', (pid,))
    if not ph or ph['utc'] is None:
        raise HTTPException(404, 'This photo has no capture time.')
    o = json.loads(ph['options'])
    s = settings()
    if o.get('source') == 'gpx':
        files = [os.path.join(DATA, 'gpx', f'{g["id"]}.gpx') for g in db.q('SELECT id FROM gpx')]
        before, after = track.nearest_in(track.merge(*[track.parse_gpx(f) for f in files if os.path.exists(f)]), ph['utc'])
    else:
        before, after = track.nearest_dawarich(s['dawarich_url'], s['dawarich_key'], ph['utc'], o.get('max_accuracy_m'))
    out = lambda p: {'utc': p[0], 'lat': p[1], 'lon': p[2], 'diff_s': p[0] - ph['utc']} if p else None
    return {'utc': ph['utc'], 'before': out(before), 'after': out(after)}


# ---------------------------------------------------------------- thumbnails, place search
@app.get('/api/thumb/{pid}')
def thumb(pid: int):
    ph = db.one('SELECT path FROM photos WHERE id = ?', (pid,))
    if not ph:
        raise HTTPException(404)
    cache = os.path.join(DATA, 'thumbs', hashlib.sha1(ph['path'].encode()).hexdigest() + '.jpg')
    if not os.path.exists(cache):
        data = exif_ui.binary(ph['path'], 'ThumbnailImage') or exif_ui.binary(ph['path'], 'PreviewImage')
        if not data:
            raise HTTPException(404)
        with open(cache, 'wb') as f:
            f.write(data)
    return FileResponse(cache, media_type='image/jpeg', headers={'Cache-Control': 'max-age=86400'})


UA = {'User-Agent': 'Geotagger/1.0 (self-hosted photo geotagging)'}


def photon(q):
    url = settings()['photon_url'].rstrip('/') + '/api/'
    r = httpx.get(url, params={'q': q, 'limit': 6}, timeout=4, headers=UA)
    r.raise_for_status()
    out = []
    for f in r.json().get('features', []):
        p = f.get('properties', {})
        lon, lat = f['geometry']['coordinates'][:2]
        parts = [p.get(k) for k in ('street', 'district', 'city', 'state', 'country')
                 if p.get(k) and p.get(k) != p.get('name')]
        name = p.get('name') or (parts[0] if parts else '')
        out.append({'name': name, 'detail': ', '.join(dict.fromkeys(parts)), 'lat': lat, 'lon': lon})
    return out


def nominatim(q, lang=None):
    r = httpx.get('https://nominatim.openstreetmap.org/search', timeout=8,
                  headers={**UA, **({'Accept-Language': lang} if lang else {})},
                  params={'q': q, 'format': 'jsonv2', 'limit': 6})
    r.raise_for_status()
    out = []
    for f in r.json():
        name = f.get('name') or f['display_name'].split(',')[0]
        detail = ', '.join(p.strip() for p in f['display_name'].split(',')[1:4])
        out.append({'name': name, 'detail': detail, 'lat': float(f['lat']), 'lon': float(f['lon'])})
    return out


photon_down_until = 0.0


@app.get('/api/geocode')
def geocode(q: str, request: Request):
    """Photon (same service as Dawarich) first; OpenStreetMap's Nominatim if Photon doesn't answer.
    After a Photon failure, go straight to Nominatim for 10 minutes."""
    global photon_down_until
    if time.time() >= photon_down_until:
        try:
            return photon(q)
        except Exception:                                    # noqa: BLE001
            photon_down_until = time.time() + 600
    return nominatim(q, request.headers.get('accept-language'))


app.mount('/static', StaticFiles(directory=STATIC), name='static')


@app.get('/')
def index():
    return FileResponse(os.path.join(STATIC, 'index.html'))
