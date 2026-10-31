"""Background jobs: a dry run (scan + match) and writing GPS. One job runs at a time."""
import collections
import json
import os
import queue
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import matcher, track

JPG_EXT = {'.jpg', '.jpeg'}
RAW_EXT = {'.raf', '.dng', '.cr2', '.cr3', '.nef', '.arw', '.orf', '.rw2', '.pef', '.srw'}
READ_TAGS = ['EXIF:DateTimeOriginal', 'EXIF:SubSecTimeOriginal', 'EXIF:OffsetTimeOriginal',
             'EXIF:GPSLatitude', 'EXIF:GPSLongitude', 'XMP:GPSLatitude', 'XMP:GPSLongitude',
             'XMP:DateTimeOriginal']
BATCH = 200

PHOTO_COLS = ['job_id', 'path', 'name', 'kind', 'target', 'dto', 'tz', 'utc', 'cam_lat', 'cam_lon',
              'side_lat', 'side_lon', 'has_xmp_gps', 'ours', 'm_status', 'm_lat', 'm_lon', 'm_alt',
              'gap_s', 'bracket_s', 'frozen', 'cam_diff_m', 'manual', 'action', 't_lat', 't_lon', 't_alt',
              'reason', 'written']


def kind_of(name, types):
    ext = os.path.splitext(name)[1].lower()
    if ext in JPG_EXT and types in ('jpg', 'both'):
        return 'jpg'
    if ext in RAW_EXT and types in ('raw', 'both'):
        return 'raw'
    return None


def sidecar_for(path):
    base = os.path.splitext(path)[0]
    for ext in ('.xmp', '.XMP'):
        if os.path.exists(base + ext):
            return base + ext
    return None


def utc_of(dto, sub, tz, no_tz, assume):
    if not dto:
        return None, None, 'notime'
    tz = tz or (assume if no_tz == 'assume' else None)
    if not tz:
        return None, None, 'notz'
    try:
        local = datetime.strptime(str(dto)[:19], '%Y:%m:%d %H:%M:%S')
        sign = -1 if tz.startswith('-') else 1
        hh, mm = tz.lstrip('+-').split(':')
        off = timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
    except ValueError:
        return None, None, 'notime'
    frac = float('0.' + str(sub)) if str(sub or '').isdigit() else 0.0
    return local.replace(tzinfo=off).timestamp() + frac, tz, None


class Runner:
    def __init__(self, db, exif, settings_fn, data_dir):
        self.db, self.exif, self.settings, self.data_dir = db, exif, settings_fn, data_dir
        self.q = queue.Queue()
        self.flags = {}                    # job_id -> 'pause' | 'cancel'
        self.rates = collections.defaultdict(lambda: collections.deque(maxlen=400))
        # a job that was running when the container stopped
        for j in db.q("SELECT id, status FROM jobs WHERE status IN ('scanning', 'writing', 'queued')"):
            db.update_job(j['id'], status='failed' if j['status'] != 'writing' else 'paused',
                          error='Interrupted by a restart' if j['status'] != 'writing' else None,
                          phase='Interrupted by a restart — resume to continue')
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------------- queue
    def submit(self, job_id, kind):
        self.flags.pop(job_id, None)
        self.db.update_job(job_id, status='queued', phase='Waiting for another job to finish')
        self.q.put((job_id, kind))

    def _loop(self):
        while True:
            job_id, kind = self.q.get()
            try:
                (self._scan if kind == 'scan' else self._write)(job_id)
            except Exception as e:                       # noqa: BLE001
                traceback.print_exc()
                self.db.update_job(job_id, status='failed', error=str(e), finished=time.time())

    def progress(self, job_id, done, total, phase):
        now = time.time()
        self.rates[job_id].append((now, done))
        self.db.update_job(job_id, done=done, total=total, phase=phase)

    def eta(self, job):
        """Seconds left and files per second, from the last two minutes of progress."""
        pts = [p for p in self.rates.get(job['id'], []) if p[0] >= time.time() - 120]
        if len(pts) < 2 or pts[-1][1] <= pts[0][1]:
            return None, None
        rate = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])
        return (job['total'] - job['done']) / rate if rate > 0 else None, rate

    # ---------------- dry run
    def _scan_cancelled(self, job_id):
        if self.flags.get(job_id) != 'cancel':
            return False
        self.flags.pop(job_id, None)
        self.db.x('DELETE FROM photos WHERE job_id = ?', (job_id,))
        self.db.update_job(job_id, status='cancelled', phase='Dry run cancelled', done=0, total=0, finished=time.time())
        return True

    def _scan(self, job_id):
        if self._scan_cancelled(job_id):               # cancelled while it was waiting in the queue
            return
        job = self.db.job(job_id)
        o = job['options']
        s = self.settings()
        self.db.update_job(job_id, status='scanning', started=time.time(), error=None)
        self.db.x('DELETE FROM photos WHERE job_id = ?', (job_id,))

        files = []
        for folder in job['folders']:
            for name in sorted(os.listdir(folder)):
                path = os.path.join(folder, name)
                k = kind_of(name, job['types'])
                if k and os.path.isfile(path):
                    files.append((path, name, k))
        total = len(files)
        self.rates.pop(job_id, None)
        self.progress(job_id, 0, total, 'Reading photo times')

        written = {r['path']: r for r in self.db.q('SELECT path, lat, lon FROM written')}
        photos = []
        for i in range(0, total, BATCH):
            if self._scan_cancelled(job_id):
                return
            chunk = files[i:i + BATCH]
            meta = {m['SourceFile']: m for m in self.exif.read([f[0] for f in chunk], READ_TAGS)}
            sidecars = {f[0]: sidecar_for(f[0]) for f in chunk if f[2] == 'raw'}
            side_meta = {m['SourceFile']: m for m in
                         self.exif.read([p for p in sidecars.values() if p], ['XMP:GPSLatitude', 'XMP:GPSLongitude'])}
            for path, name, k in chunk:
                m = meta.get(path, {})
                utc, tz, bad = utc_of(m.get('EXIF:DateTimeOriginal'), m.get('EXIF:SubSecTimeOriginal'),
                                      m.get('EXIF:OffsetTimeOriginal'), o['no_tz'], o.get('assume_offset'))
                side = sidecars.get(path)
                sm = side_meta.get(side, {}) if side else {}
                ph = dict(job_id=job_id, path=path, name=name, kind=k,
                          target=(side or os.path.splitext(path)[0] + '.xmp') if k == 'raw' else path,
                          dto=m.get('EXIF:DateTimeOriginal'), tz=tz, utc=utc,
                          cam_lat=_num(m.get('EXIF:GPSLatitude')), cam_lon=_num(m.get('EXIF:GPSLongitude')),
                          side_lat=_num(sm.get('XMP:GPSLatitude')), side_lon=_num(sm.get('XMP:GPSLongitude')),
                          has_xmp_gps=1 if m.get('XMP:GPSLatitude') is not None else 0,
                          m_status=bad, frozen=0)
                w = written.get(ph['target'])
                cur_lat = ph['side_lat'] if k == 'raw' else ph['cam_lat']
                cur_lon = ph['side_lon'] if k == 'raw' else ph['cam_lon']
                ph['ours'] = 1 if (w and cur_lat is not None and abs(w['lat'] - cur_lat) < 1e-6
                                   and abs(w['lon'] - cur_lon) < 1e-6) else 0
                photos.append(ph)
            self.progress(job_id, min(i + BATCH, total), total, 'Reading photo times')

        timed = [p['utc'] for p in photos if p['utc'] is not None]
        pts = []
        if timed:
            self.db.update_job(job_id, phase='Loading the track')
            pts = self._track(s, o, min(timed) - 3600, max(timed) + 3600,
                              stop=lambda: self.flags.get(job_id) == 'cancel')
        if self._scan_cancelled(job_id):
            return

        self.db.update_job(job_id, phase='Matching')
        matcher.match(photos, pts, o['max_gap_min'] * 60, o['nearest_min'] * 60)
        matcher.mark_frozen(photos, o['frozen_min'])
        manual = {r['path']: r for r in self.db.q('SELECT * FROM manual')}
        for ph in photos:
            ph['cam_diff_m'] = matcher.camera_diff(ph)
            matcher.decide(ph, o['has_gps'], manual.get(ph['path']))

        self.db.many(f'INSERT INTO photos ({", ".join(PHOTO_COLS)}) VALUES ({", ".join("?" * len(PHOTO_COLS))})',
                     [[ph.get(c) for c in PHOTO_COLS] for ph in photos])
        self._save_track(job_id, pts, o['max_gap_min'] * 60)
        self.db.update_job(job_id, status='ready', phase='Dry run complete', finished=time.time(),
                           result={'track_points': len(pts)})

    def _track(self, s, o, start, end, stop=None):
        if o.get('source', 'dawarich') == 'gpx':
            files = [os.path.join(self.data_dir, 'gpx', f'{g["id"]}.gpx') for g in self.db.q('SELECT id FROM gpx')]
            pts = track.merge(*[track.parse_gpx(f) for f in files if os.path.exists(f)])
            return [p for p in pts if start <= p[0] <= end]
        if not s.get('dawarich_url') or not s.get('dawarich_key'):
            raise RuntimeError('Dawarich URL or API key is not set (Settings).')
        return track.fetch_dawarich(s['dawarich_url'], s['dawarich_key'], start, end, o.get('max_accuracy_m'), stop)

    def _save_track(self, job_id, pts, max_gap_s):
        """A lighter copy of the track for the map, split where it has gaps."""
        segs, seg, last = [], [], None
        for t, lat, lon, _ in pts:
            if last and t - last[0] > max_gap_s:
                if len(seg) > 1:
                    segs.append(seg)
                seg = []
                last = None
            if last is None or matcher.distance_m(last[1], last[2], lat, lon) > 15:
                seg.append([round(lat, 6), round(lon, 6)])
                last = (t, lat, lon)
        if len(seg) > 1:
            segs.append(seg)
        os.makedirs(os.path.join(self.data_dir, 'tracks'), exist_ok=True)
        with open(os.path.join(self.data_dir, 'tracks', f'{job_id}.json'), 'w') as f:
            json.dump(segs, f)

    # ---------------- write
    def _write(self, job_id):
        job = self.db.job(job_id)
        o = job['options']
        todo = self.db.q("SELECT * FROM photos WHERE job_id = ? AND action = 'write' AND written = 0 ORDER BY kind, name",
                         (job_id,))
        already = self.db.one("SELECT COUNT(*) n FROM photos WHERE job_id = ? AND action = 'write' AND written = 1",
                              (job_id,))['n']
        total = len(todo) + already
        result = job['result'] or {}
        result.setdefault('write_started', time.time())
        self.db.update_job(job_id, status='writing', error=None, result=result)
        self.rates.pop(job_id, None)
        common = ['-P'] + ([] if o.get('backups') else ['-overwrite_original_in_place'])
        done = already
        self.progress(job_id, done, total, 'Writing GPS')
        for ph in todo:
            flag = self.flags.get(job_id)
            if flag == 'pause':
                self.db.update_job(job_id, status='paused', phase='Paused')
                return
            if flag == 'cancel':
                self.db.update_job(job_id, status='cancelled', phase='Cancelled', finished=time.time())
                return
            ok, msg = self.exif.write(ph['target'], gps_args(ph), common)
            if ok:
                self.db.x('UPDATE photos SET written = 1, write_error = NULL WHERE id = ?', (ph['id'],))
                self.db.x('INSERT INTO written (path, lat, lon, job_id, at) VALUES (?, ?, ?, ?, ?) '
                          'ON CONFLICT(path) DO UPDATE SET lat = excluded.lat, lon = excluded.lon, '
                          'job_id = excluded.job_id, at = excluded.at',
                          (ph['target'], round(ph['t_lat'], 6), round(ph['t_lon'], 6), job_id, time.time()))
            else:
                self.db.x('UPDATE photos SET write_error = ? WHERE id = ?', (msg[:500], ph['id']))
            done += 1
            self.progress(job_id, done, total, 'Writing GPS')

        self.db.update_job(job_id, phase='Checking what was written')
        result.update(self._verify(job_id))
        self.db.update_job(job_id, status='done', phase='Finished', finished=time.time(), result=result)

    def _verify(self, job_id):
        rows = self.db.q("SELECT id, target, kind, t_lat, t_lon FROM photos WHERE job_id = ? AND action = 'write' "
                         "AND written = 1", (job_id,))
        bad = 0
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            meta = {m['SourceFile']: m for m in self.exif.read([r['target'] for r in chunk],
                                                               ['EXIF:GPSLatitude', 'EXIF:GPSLongitude',
                                                                'XMP:GPSLatitude', 'XMP:GPSLongitude'])}
            for r in chunk:
                m = meta.get(r['target'], {})
                g = 'XMP' if r['kind'] == 'raw' else 'EXIF'
                lat, lon = _num(m.get(f'{g}:GPSLatitude')), _num(m.get(f'{g}:GPSLongitude'))
                if lat is None or abs(lat - round(r['t_lat'], 6)) > 2e-6 or abs(lon - round(r['t_lon'], 6)) > 2e-6:
                    bad += 1
                    self.db.x('UPDATE photos SET written = 0, write_error = ? WHERE id = ?',
                              ('GPS read back does not match', r['id']))
        errors = self.db.one('SELECT COUNT(*) n FROM photos WHERE job_id = ? AND write_error IS NOT NULL',
                             (job_id,))['n']
        return {'verified': len(rows) - bad, 'mismatched': bad, 'errors': errors, 'verified_at': time.time()}


def _num(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def gps_args(ph):
    """exiftool arguments for one file. JPG: EXIF GPS (+ XMP GPS when the file already has it).
    RAW: the .xmp sidecar, created if needed; the raw file itself is never touched."""
    lat, lon, alt = round(ph['t_lat'], 6), round(ph['t_lon'], 6), ph['t_alt']
    stamp = datetime.fromtimestamp(ph['utc'], timezone.utc) if ph['utc'] is not None else None
    lat_s = f'{abs(lat)} {"S" if lat < 0 else "N"}'
    lon_s = f'{abs(lon)} {"W" if lon < 0 else "E"}'
    xmp = [f'-XMP-exif:GPSLatitude={lat_s}', f'-XMP-exif:GPSLongitude={lon_s}', '-XMP-exif:GPSMapDatum=WGS-84']
    if alt is not None:
        xmp += [f'-XMP-exif:GPSAltitude={abs(round(alt, 1))}', f'-XMP-exif:GPSAltitudeRef#={1 if alt < 0 else 0}']
    else:
        xmp += ['-XMP-exif:GPSAltitude=', '-XMP-exif:GPSAltitudeRef=']
    if stamp:
        xmp.append(f'-XMP-exif:GPSDateTime={stamp:%Y:%m:%d %H:%M:%S}Z')
    if ph['kind'] == 'raw':
        return xmp
    args = [f'-EXIF:GPSLatitude={abs(lat)}', f'-EXIF:GPSLatitudeRef={"S" if lat < 0 else "N"}',
            f'-EXIF:GPSLongitude={abs(lon)}', f'-EXIF:GPSLongitudeRef={"W" if lon < 0 else "E"}',
            '-EXIF:GPSMapDatum=WGS-84']
    if alt is not None:
        args += [f'-EXIF:GPSAltitude={abs(round(alt, 1))}', f'-EXIF:GPSAltitudeRef#={1 if alt < 0 else 0}']
    else:
        args += ['-EXIF:GPSAltitude=', '-EXIF:GPSAltitudeRef=']
    if stamp:
        args += [f'-EXIF:GPSDateStamp={stamp:%Y:%m:%d}', f'-EXIF:GPSTimeStamp={stamp:%H:%M:%S}']
    if ph.get('has_xmp_gps'):
        args += xmp
    return args
