"""Where the GPS track comes from: the Dawarich API or uploaded GPX files."""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx


def _client(key):
    return httpx.Client(timeout=60, headers={'Authorization': f'Bearer {key}'}, follow_redirects=True)


def fetch_dawarich(url, key, start, end, max_accuracy, stop=None):
    """Points as sorted (utc, lat, lon, alt) tuples. stop() is checked between pages."""
    pts = {}
    with _client(key) as c:
        page, pages = 1, 1
        while page <= pages and not (stop and stop()):
            r = c.get(f'{url.rstrip("/")}/api/v1/points',
                      params={'start_at': int(start), 'end_at': int(end), 'order': 'asc',
                              'per_page': 5000, 'page': page})
            r.raise_for_status()
            pages = int(r.headers.get('X-Total-Pages', '1') or 1)
            for p in r.json():
                acc = p.get('accuracy')
                if acc is not None and max_accuracy and acc > max_accuracy:
                    continue
                alt = p.get('altitude_decimal', p.get('altitude'))
                pts[int(p['timestamp'])] = (float(p['latitude']), float(p['longitude']),
                                            float(alt) if alt is not None else None)
            page += 1
    return [(t, *v) for t, v in sorted(pts.items())]


def nearest_dawarich(url, key, t, max_accuracy):
    """The last usable point at or before t and the first one after it, from the whole history."""
    def first(params):
        with _client(key) as c:
            r = c.get(f'{url.rstrip("/")}/api/v1/points', params={**params, 'per_page': 50})
            r.raise_for_status()
            for p in r.json():
                acc = p.get('accuracy')
                if acc is not None and max_accuracy and acc > max_accuracy:
                    continue
                return (int(p['timestamp']), float(p['latitude']), float(p['longitude']), None)
        return None
    before = first({'end_at': int(t), 'order': 'desc'})
    after = first({'start_at': int(t) + 1, 'order': 'asc'})
    return before, after


def nearest_in(pts, t):
    import bisect
    ts = [p[0] for p in pts]
    i = bisect.bisect_right(ts, t) - 1
    return (pts[i] if i >= 0 else None), (pts[i + 1] if i + 1 < len(pts) else None)


def test_dawarich(url, key):
    with _client(key) as c:
        h = c.get(f'{url.rstrip("/")}/api/v1/health')
        version = h.headers.get('X-Dawarich-Version') or h.headers.get('x-dawarich-version')
        r = c.get(f'{url.rstrip("/")}/api/v1/points', params={'per_page': 1, 'order': 'desc', 'slim': 'true'})
        if r.status_code == 401:
            return {'ok': False, 'message': 'The API key was rejected.'}
        r.raise_for_status()
        rows = r.json()
        latest = rows[0]['timestamp'] if rows else None
        return {'ok': True, 'version': version, 'latest': latest}


def _parse_time(s):
    s = s.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:          # GPX times without a zone are UTC by the spec
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def parse_gpx(path):
    pts = {}
    for _, el in ET.iterparse(path):
        tag = el.tag.rsplit('}', 1)[-1]
        if tag in ('trkpt', 'rtept'):
            t = alt = None
            for ch in el:
                ctag = ch.tag.rsplit('}', 1)[-1]
                if ctag == 'time' and ch.text:
                    t = _parse_time(ch.text)
                elif ctag == 'ele' and ch.text:
                    try:
                        alt = float(ch.text)
                    except ValueError:
                        pass
            if t is not None:
                pts[t] = (float(el.get('lat')), float(el.get('lon')), alt)
            el.clear()
    return [(t, *v) for t, v in sorted(pts.items())]


def merge(*tracks):
    pts = {}
    for tr in tracks:
        for p in tr:
            pts[p[0]] = p
    return [pts[t] for t in sorted(pts)]
