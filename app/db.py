import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    created REAL, folders TEXT, types TEXT, options TEXT,
    status TEXT, phase TEXT, done INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
    started REAL, finished REAL, error TEXT, result TEXT
);

-- one row per file in a job. m_* = what the track says, t_* = what will be written.
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY, job_id INTEGER,
    path TEXT, name TEXT, kind TEXT, target TEXT,
    dto TEXT, tz TEXT, utc REAL,
    cam_lat REAL, cam_lon REAL, side_lat REAL, side_lon REAL,
    has_xmp_gps INTEGER DEFAULT 0, ours INTEGER DEFAULT 0,
    m_status TEXT, m_lat REAL, m_lon REAL, m_alt REAL, gap_s REAL, bracket_s REAL,
    frozen INTEGER DEFAULT 0, cam_diff_m REAL,
    manual INTEGER DEFAULT 0, action TEXT, t_lat REAL, t_lon REAL, t_alt REAL, reason TEXT,
    written INTEGER DEFAULT 0, write_error TEXT,
    mtime REAL              -- the target's Date modified just before it was written
);
CREATE INDEX IF NOT EXISTS ix_photos_job ON photos (job_id, name);

-- positions placed by hand; later runs never overwrite these
CREATE TABLE IF NOT EXISTS manual (path TEXT PRIMARY KEY, lat REAL, lon REAL, excluded INTEGER DEFAULT 0, created REAL);

-- what Geotagger wrote, so a later run can tell its own GPS from the camera's
CREATE TABLE IF NOT EXISTS written (path TEXT PRIMARY KEY, lat REAL, lon REAL, job_id INTEGER, at REAL);

CREATE TABLE IF NOT EXISTS gpx (id INTEGER PRIMARY KEY, name TEXT, points INTEGER, first REAL, last REAL, added REAL);
"""


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.executescript(SCHEMA)
            # columns added after 1.0.0
            cols = {r[1] for r in self.conn.execute('PRAGMA table_info(photos)')}
            if 'mtime' not in cols:
                self.conn.execute('ALTER TABLE photos ADD COLUMN mtime REAL')

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    def many(self, sql, rows):
        with self.lock:
            self.conn.execute('BEGIN')
            try:
                self.conn.executemany(sql, rows)
                self.conn.execute('COMMIT')
            except Exception:
                self.conn.execute('ROLLBACK')
                raise

    # ---- settings
    def get_settings(self, defaults):
        s = dict(defaults)
        for r in self.q('SELECT key, value FROM settings'):
            s[r['key']] = json.loads(r['value'])
        return s

    def set_settings(self, values):
        self.many('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                  [(k, json.dumps(v)) for k, v in values.items()])

    # ---- jobs
    def job(self, job_id):
        j = self.one('SELECT * FROM jobs WHERE id = ?', (job_id,))
        if j:
            for k in ('folders', 'options', 'result'):
                j[k] = json.loads(j[k]) if j[k] else None
        return j

    def update_job(self, job_id, **fields):
        if 'result' in fields and not isinstance(fields['result'], str):
            fields['result'] = json.dumps(fields['result'])
        cols = ', '.join(f'{k} = ?' for k in fields)
        self.x(f'UPDATE jobs SET {cols} WHERE id = ?', (*fields.values(), job_id))
