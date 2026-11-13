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
GPS_TAGS = ['EXIF:GPSLatitude', 'EXIF:GPSLatitudeRef', 'EXIF:GPSLongitude', 'EXIF:GPSLongitudeRef',
            'XMP:GPSLatitude', 'XMP:GPSLongitude']
READ_TAGS = ['EXIF:DateTimeOriginal', 'EXIF:SubSecTimeOriginal', 'EXIF:OffsetTimeOriginal', *GPS_TAGS,
             'XMP:DateTimeOriginal']
BATCH = 200
TMP_SUFFIX = '_exiftool_tmp'      # exiftool's temporary file while it rewrites a photo

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


def utc_of(dto, sub, tz, no_tz, assume):
    if not dto:
        return None, None, 'notime'
    tz = tz or (assume if no_tz == 'assume' else None)
    if not tz:
        return None, None, 'notz'
    try:
        local = datetime.strptime(str(dto)[:19], '%Y:%m:%d %H:%M:%S')
    except ValueError:
        return None, None, 'notime'
    try:
        sign = -1 if tz.startswith('-') else 1
        hh, mm = tz.lstrip('+-').split(':')
        off = timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
    except ValueError:
        return None, None, 'badtz'
    frac = float('0.' + str(sub)) if str(sub or '').isdigit() else 0.0
    return local.replace(tzinfo=off).timestamp() + frac, tz, None


def time_windows(times, pad=3600, join=86400):
    """(start, end) spans of track to load: photos taken within a day of each other share one, so a single
    photo with a wrong date doesn't pull in the whole history in between."""
    out = []
    for t in sorted(times):
        if out and t - out[-1][1] <= join + 2 * pad:
            out[-1][1] = t + pad
        else:
            out.append([t - pad, t + pad])
    return [tuple(w) for w in out]


class Runner:
    def __init__(self, db, exif, settings_fn, data_dir, roots):
        self.db, self.exif, self.settings, self.data_dir = db, exif, settings_fn, data_dir
        self.roots = [os.path.realpath(r) for r in roots]
        self.q = queue.Queue()
        self.pending = set()               # (job_id, kind) waiting in the queue
        self.flags = {}                    # job_id -> 'pause' | 'cancel' | 'shutdown'
        self.current = None                # (job_id, kind) while a job runs
        self.rates = collections.defaultdict(lambda: collections.deque(maxlen=400))
        self._recover()
        threading.Thread(target=self._loop, daemon=True).start()

    def _recover(self):
        """Jobs that were running when the container stopped. A write that was stopped cleanly, or killed
        between two files, carries on by itself; one killed in the middle of a file waits for you."""
        for j in self.db.q("SELECT id, status, result FROM jobs WHERE status IN ('scanning', 'writing', 'queued', 'paused')"):
            result = json.loads(j['result']) if j['result'] else {}
            if j['status'] == 'paused' and not result.pop('resume_on_start', False):
                continue                                # paused by you
            if j['status'] == 'scanning' or (j['status'] == 'queued' and result.get('track_points') is None):
                self.db.update_job(j['id'], status='failed', error='Interrupted by a restart',
                                   phase='Interrupted by a restart — run the dry run again')
                continue
            left = self._leftovers(j['id'])
            # a photo cut off while it was being copied back has lost its Date modified; the next write puts it back
            result['cut_off'] = sorted(set(result.get('cut_off', [])) | {p[:-len(TMP_SUFFIX)] for p in left})
            if left:
                names = ', '.join(os.path.basename(p)[:-len(TMP_SUFFIX)] for p in left[:5])
                more = f' and {len(left) - 5} more' if len(left) > 5 else ''
                self.db.update_job(
                    j['id'], status='paused', result=result, phase='Stopped in the middle of a photo — check it before resuming',
                    error=f'Writing was cut off in the middle of {names}{more}. Next to each is an exiftool temporary file '
                          f'(name ending in {TMP_SUFFIX}). If the photo looks right all the way to the bottom, delete the '
                          f'temporary file. If not, delete the photo and remove {TMP_SUFFIX} from the temporary file\'s name. '
                          f'Then resume.')
            else:
                self.db.update_job(j['id'], result=result)
                self.submit(j['id'], 'write')

    def _leftovers(self, job_id):
        """exiftool temporary files next to this job's photos: a write that was cut off part-way."""
        dirs = {os.path.dirname(r['target']) for r in
                self.db.q("SELECT DISTINCT target FROM photos WHERE job_id = ? AND action = 'write'", (job_id,))}
        out = []
        for d in dirs:
            try:
                out += [os.path.join(d, n) for n in os.listdir(d) if n.endswith(TMP_SUFFIX)]
            except OSError:
                pass
        return sorted(out)

    def unresolved(self, job_id):
        """This job's photos that still have a temporary file next to them from a write that was cut off."""
        targets = {r['target'] for r in self.db.q("SELECT target FROM photos WHERE job_id = ? AND action = 'write'",
                                                  (job_id,))}
        return [p[:-len(TMP_SUFFIX)] for p in self._leftovers(job_id) if p[:-len(TMP_SUFFIX)] in targets]

    def safe(self, path):
        """exiftool takes one argument per line, and a file must really be inside a photo library
        (not a symlink to somewhere else)."""
        if '\n' in path or '\r' in path:
            return False
        real = os.path.realpath(path)
        return any(real == r or real.startswith(r + os.sep) for r in self.roots)

    def _photo_files(self, folder, types, subfolders, unsafe):
        """(path, name, kind, existing sidecar) for each photo in folder, and in its subfolders (hidden ones left
        out). Works from directory entries, so a big library costs one listing per folder; only a symlink needs
        the full safe() check, since the folders walked are real ones inside a library."""
        stack = [folder]
        while stack:
            with os.scandir(stack.pop()) as it:
                entries = sorted(it, key=lambda e: e.name)
            by_name = {e.name: e for e in entries}
            subdirs = []
            for e in entries:
                if e.is_dir(follow_symlinks=False):
                    if subfolders and not e.name.startswith('.'):
                        subdirs.append(e.path)
                    continue
                k = kind_of(e.name, types)
                if not k or not e.is_file():
                    continue
                side = None
                if k == 'raw':
                    base = os.path.splitext(e.name)[0]
                    side = next((by_name[base + x] for x in ('.xmp', '.XMP') if base + x in by_name), None)
                if any((x.is_symlink() or '\n' in x.path or '\r' in x.path) and not self.safe(x.path)
                       for x in (e, side) if x):
                    unsafe.append(e.path)
                    continue
                yield e.path, e.name, k, side.path if side else None
            stack.extend(reversed(subdirs))

    def shutdown(self, timeout=8):
        """The container is stopping: finish the photo being written, then stop. Docker kills everything
        10 s after asking, and a photo cut off half-way through being rewritten is damaged."""
        if not self.current or self.current[1] != 'write':
            return
        self.flags[self.current[0]] = 'shutdown'
        end = time.time() + timeout
        while self.current and time.time() < end:
            time.sleep(0.1)

    # ---------------- queue
    def submit(self, job_id, kind):
        self.flags.pop(job_id, None)
        self.db.update_job(job_id, status='queued', phase='Waiting for another job to finish')
        if (job_id, kind) not in self.pending:          # paused and resumed while waiting: it's queued already
            self.pending.add((job_id, kind))
            self.q.put((job_id, kind))

    def _loop(self):
        while True:
            job_id, kind = self.q.get()
            self.pending.discard((job_id, kind))
            job = self.db.job(job_id)
            if not job or job['status'] != 'queued':    # paused, cancelled or deleted while it waited
                self.flags.pop(job_id, None)
                continue
            self.current = (job_id, kind)
            try:
                (self._scan if kind == 'scan' else self._write)(job_id)
            except Exception as e:                       # noqa: BLE001
                traceback.print_exc()
                self.db.update_job(job_id, status='failed', error=str(e), finished=time.time())
            finally:
                self.current = None

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

        files, seen, unsafe = [], set(), []
        for folder in job['folders']:
            for f in self._photo_files(folder, job['types'], o.get('subfolders'), unsafe):
                if f[0] not in seen:
                    seen.add(f[0])
                    files.append(f)
                if len(files) % 2000 == 0 and self._scan_cancelled(job_id):
                    return
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
            side_meta = {m['SourceFile']: m for m in
                         self.exif.read([f[3] for f in chunk if f[3]], ['XMP:GPSLatitude', 'XMP:GPSLongitude'])}
            for path, name, k, side in chunk:
                m = meta.get(path, {})
                utc, tz, bad = utc_of(m.get('EXIF:DateTimeOriginal'), m.get('EXIF:SubSecTimeOriginal'),
                                      m.get('EXIF:OffsetTimeOriginal'), o['no_tz'], o.get('assume_offset'))
                sm = side_meta.get(side, {}) if side else {}
                cam_lat, cam_lon = exif_gps(m)
                side_lat, side_lon = _pair(sm.get('XMP:GPSLatitude'), sm.get('XMP:GPSLongitude'))
                ph = dict(job_id=job_id, path=path, name=name, kind=k,
                          target=(side or os.path.splitext(path)[0] + '.xmp') if k == 'raw' else path,
                          dto=m.get('EXIF:DateTimeOriginal'), tz=tz, utc=utc,
                          cam_lat=cam_lat, cam_lon=cam_lon, side_lat=side_lat, side_lon=side_lon,
                          has_xmp_gps=1 if m.get('XMP:GPSLatitude') is not None else 0,
                          m_status=bad, frozen=0)
                w = written.get(ph['target'])
                cur_lat = ph['side_lat'] if k == 'raw' else ph['cam_lat']
                cur_lon = ph['side_lon'] if k == 'raw' else ph['cam_lon']
                ph['ours'] = 1 if w and matcher.same_spot((cur_lat, cur_lon), (w['lat'], w['lon'])) else 0
                photos.append(ph)
            self.progress(job_id, min(i + BATCH, total), total, 'Reading photo times')

        windows = time_windows(p['utc'] for p in photos if p['utc'] is not None)
        pts = []
        if windows:
            self.db.update_job(job_id, phase='Loading the track')
            pts = self._track(s, o, windows, stop=lambda: self.flags.get(job_id) == 'cancel')
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
        result = {'track_points': len(pts)}
        if unsafe:
            result['unsafe'] = len(unsafe)
            result['unsafe_names'] = [os.path.basename(p) for p in unsafe[:20]]
        self.db.update_job(job_id, status='ready', phase='Dry run complete', finished=time.time(), result=result)

    def _track(self, s, o, windows, stop=None):
        if o.get('source', 'dawarich') == 'gpx':
            files = [os.path.join(self.data_dir, 'gpx', f'{g["id"]}.gpx') for g in self.db.q('SELECT id FROM gpx')]
            pts = track.merge(*[track.parse_gpx(f) for f in files if os.path.exists(f)])
            return [p for p in pts if any(a <= p[0] <= b for a, b in windows)]
        if not s.get('dawarich_url') or not s.get('dawarich_key'):
            raise RuntimeError('Dawarich URL or API key is not set (Settings).')
        return track.fetch_windows(s['dawarich_url'], s['dawarich_key'], windows, o.get('max_accuracy_m'), stop)

    def _save_track(self, job_id, pts, max_gap_s):
        """A lighter copy of the track for the map, split where it has gaps."""
        segs, seg, last, prev_t = [], [], None, None
        for t, lat, lon, _ in pts:
            # a gap is measured between real samples, not between the points kept for the map
            # (standing still keeps no new points)
            if prev_t is not None and t - prev_t > max_gap_s:
                if len(seg) > 1:
                    segs.append(seg)
                seg = []
                last = None
            prev_t = t
            if last is None or matcher.distance_m(last[0], last[1], lat, lon) > 15:
                seg.append([round(lat, 6), round(lon, 6)])
                last = (lat, lon)
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
        if not already:
            result['unchanged'] = 0
        self.db.update_job(job_id, status='writing', error=None, result=result)
        self.rates.pop(job_id, None)
        common = ['-P', '-overwrite_original_in_place']
        done = already
        self.progress(job_id, done, total, 'Writing GPS')
        cut_off = set(result.get('cut_off', []))
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            # the files as they are now, not as the dry run saw them: another job may have written them since
            meta = {m['SourceFile']: m for m in
                    self.exif.read([ph['target'] for ph in chunk
                                    if os.path.exists(ph['target']) and self.safe(ph['target'])], GPS_TAGS)}
            # remember each Date modified, to the nanosecond, before touching the file
            # (a cut-off photo keeps the one from before it was cut off)
            for ph in chunk:
                if ph['target'] not in cut_off or ph.get('mtime_ns') is None:
                    ph['mtime_ns'] = os.stat(ph['target']).st_mtime_ns if os.path.exists(ph['target']) else None
            self.db.many('UPDATE photos SET mtime_ns = ? WHERE id = ?', [(ph['mtime_ns'], ph['id']) for ph in chunk])
            for ph in chunk:
                flag = self.flags.get(job_id)
                if flag == 'pause':
                    self.db.update_job(job_id, status='paused', phase='Paused', result=result)
                    return
                if flag == 'shutdown':
                    result['resume_on_start'] = True
                    self.db.update_job(job_id, status='paused', result=result,
                                       phase='Stopped because the container stopped — carries on when it starts')
                    return
                if flag == 'cancel':
                    self.db.update_job(job_id, status='cancelled', phase='Cancelled', finished=time.time(), result=result)
                    return
                cur = current_gps(meta.get(ph['target'], {}), ph['kind'])
                if not os.path.exists(ph['path']):
                    ok, msg = False, 'The photo no longer exists'   # (a RAW's sidecar would be left on its own)
                elif not (self.safe(ph['path']) and self.safe(ph['target'])):
                    ok, msg = False, 'Left out: its name has a line break, or it points outside the photo folders'
                elif matcher.same_spot(cur, (ph['t_lat'], ph['t_lon'])):
                    ok, msg = True, ''                  # already has this position: rewriting it changes nothing
                    result['unchanged'] = result.get('unchanged', 0) + 1
                else:
                    new = not os.path.exists(ph['target'])
                    try:
                        ok, msg = self.exif.write(ph['target'], gps_args(ph), common)
                    except (TimeoutError, RuntimeError, ValueError) as e:  # stuck or crashed on this photo: note it, go on
                        ok, msg = False, str(e)
                        if os.path.exists(ph['target'] + TMP_SUFFIX):
                            cut_off.add(ph['target'])
                            result['cut_off'] = sorted(cut_off)
                            msg += (f'. It stopped in the middle of this photo: a temporary file ending in {TMP_SUFFIX} '
                                    f'is next to it. If the photo looks right all the way to the bottom, delete the '
                                    f'temporary file; if not, replace the photo with it. Then write again.')
                    if ok and new and os.geteuid() == 0:     # a new sidecar belongs to whoever owns the photo
                        try:
                            st = os.stat(ph['path'])
                            os.chown(ph['target'], st.st_uid, st.st_gid)
                        except OSError:
                            pass
                # exiftool -P keeps Date modified only to the second, and a cut-off photo has lost it: put back
                # the exact time recorded before the write
                if ok and ph.get('mtime_ns') is not None:
                    try:
                        st = os.stat(ph['target'])
                        if st.st_mtime_ns != ph['mtime_ns']:
                            os.utime(ph['target'], ns=(st.st_atime_ns, ph['mtime_ns']))
                    except OSError:
                        msg += '\nError setting file time'
                if ok and ph['target'] in cut_off:
                    cut_off.discard(ph['target'])
                    result['cut_off'] = sorted(cut_off)
                if ok and 'Error setting file time' in msg:
                    self.db.x('UPDATE photos SET written = 1, write_error = ? WHERE id = ?',
                              ('GPS written, but its Date modified could not be kept', ph['id']))
                elif ok:
                    self.db.x('UPDATE photos SET written = 1, write_error = NULL WHERE id = ?', (ph['id'],))
                if ok:
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
            meta = {m['SourceFile']: m for m in self.exif.read([r['target'] for r in chunk if self.safe(r['target'])],
                                                                GPS_TAGS)}
            for r in chunk:
                cur = current_gps(meta.get(r['target'], {}), r['kind'])
                if not matcher.same_spot(cur, (r['t_lat'], r['t_lon'])):
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


def _pair(lat, lon):
    """A position, or (None, None) when either half is missing or unreadable."""
    lat, lon = _num(lat), _num(lon)
    return (lat, lon) if lat is not None and lon is not None else (None, None)


def exif_gps(m):
    """Signed EXIF GPS from an exiftool -n read. EXIF stores the numbers without a sign;
    south and west are only in GPSLatitudeRef / GPSLongitudeRef."""
    lat, lon = _pair(m.get('EXIF:GPSLatitude'), m.get('EXIF:GPSLongitude'))
    if lat is None:
        return None, None
    if str(m.get('EXIF:GPSLatitudeRef', '')).upper().startswith('S'):
        lat = -lat
    if str(m.get('EXIF:GPSLongitudeRef', '')).upper().startswith('W'):
        lon = -lon
    return lat, lon


def current_gps(m, kind):
    """What a target holds now: XMP for a RAW's sidecar (already signed), EXIF for a JPG."""
    if kind == 'raw':
        return _pair(m.get('XMP:GPSLatitude'), m.get('XMP:GPSLongitude'))
    return exif_gps(m)



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
