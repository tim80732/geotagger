"""Match photos to the track and decide what gets written."""
import bisect
import math


SAME_SPOT = 2e-6     # degrees (about 20 cm): a position written to 6 decimals reads back within this


def same_spot(cur, target):
    """Whether a position read from a file is the one Geotagger writes for target (rounded to 6 decimals).
    The dry run, the write and the read-back check all use this one rule."""
    return (None not in (cur[0], cur[1], target[0], target[1])
            and abs(cur[0] - round(target[0], 6)) <= SAME_SPOT and abs(cur[1] - round(target[1], 6)) <= SAME_SPOT)


def distance_m(lat1, lon1, lat2, lon2):
    r = math.pi / 180
    a = (math.sin((lat2 - lat1) * r / 2) ** 2 +
         math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin((lon2 - lon1) * r / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


def match(photos, pts, max_gap_s, nearest_s):
    """Same rule as Lightroom (straight line between the points around the photo),
    except gaps longer than max_gap_s are not bridged."""
    ts = [p[0] for p in pts]
    for ph in photos:
        t = ph.get('utc')
        if t is None or ph.get('m_status'):
            continue
        if not pts:
            ph.update(m_status='outside')
            continue
        i = bisect.bisect_right(ts, t) - 1
        before = pts[i] if i >= 0 else None
        after = pts[i + 1] if i + 1 < len(pts) else None
        if before and after and after[0] - before[0] <= max_gap_s:
            span = after[0] - before[0]
            k = (t - before[0]) / span if span else 0
            alt = None
            if before[3] is not None and after[3] is not None:
                alt = before[3] + (after[3] - before[3]) * k
            ph.update(m_status='interp',
                      m_lat=before[1] + (after[1] - before[1]) * k,
                      m_lon=before[2] + (after[2] - before[2]) * k,
                      m_alt=alt, gap_s=min(t - before[0], after[0] - t), bracket_s=span)
            continue
        near = min((p for p in (before, after) if p), key=lambda p: abs(p[0] - t))
        gap = abs(near[0] - t)
        if gap <= nearest_s:
            ph.update(m_status='nearest', m_lat=near[1], m_lon=near[2], m_alt=near[3], gap_s=gap)
        elif before and after:
            ph.update(m_status='gap', gap_s=gap, bracket_s=after[0] - before[0])
        else:
            ph.update(m_status='outside', gap_s=gap)


def mark_frozen(photos, frozen_min):
    """Camera GPS that stays on the exact same spot for a long run of photos."""
    for kind in {p['kind'] for p in photos}:
        seq = sorted((p for p in photos if p['kind'] == kind and p.get('cam_lat') is not None
                      and p.get('utc') is not None and not p.get('ours')), key=lambda p: p['utc'])
        run = []
        for p in seq + [None]:
            key = (round(p['cam_lat'], 6), round(p['cam_lon'], 6)) if p else None
            if run and key == run[0][0]:
                run.append((key, p))
                continue
            if len(run) >= 3 and run[-1][1]['utc'] - run[0][1]['utc'] >= frozen_min * 60:
                for _, q in run:
                    q['frozen'] = 1
            run = [(key, p)] if p else []


def camera_diff(ph):
    if ph.get('cam_lat') is None or ph.get('ours') or ph.get('m_lat') is None:
        return None
    return distance_m(ph['cam_lat'], ph['cam_lon'], ph['m_lat'], ph['m_lon'])


GAP_TEXT = {'outside': 'Outside the track', 'notime': 'No capture time', 'notz': 'No timezone in EXIF'}


def decide(ph, has_gps_policy, manual):
    """Fill action/t_*/reason. manual = row from the manual table or None."""
    ph.update(manual=0, t_lat=None, t_lon=None, t_alt=None)
    existing = ph.get('side_lat') if ph['kind'] == 'raw' else ph.get('cam_lat')
    if ph['kind'] == 'raw' and existing is None:
        existing = ph.get('cam_lat')
    if manual:
        ph['manual'] = 1
        if manual['excluded']:
            ph.update(action='skip', reason='Excluded by you')
        else:
            ph.update(action='write', t_lat=manual['lat'], t_lon=manual['lon'], reason='Placed by you')
    elif ph.get('m_status') in ('interp', 'nearest'):
        if existing is not None and not ph.get('ours') and has_gps_policy == 'keep':
            ph.update(action='skip', reason='Has GPS (kept)')
        else:
            ph.update(action='write', t_lat=ph['m_lat'], t_lon=ph['m_lon'], t_alt=ph.get('m_alt'),
                      reason='Interpolated' if ph['m_status'] == 'interp' else 'Nearest point')
    elif ph.get('m_status') == 'gap':
        ph.update(action='skip', reason=f'In a {fmt_dur(ph.get("bracket_s") or 0)} track gap')
    else:
        ph.update(action='skip', reason=GAP_TEXT.get(ph.get('m_status'), 'Not matched'))

    # what is in the file now already equals the target: nothing to write
    current = ph.get('side_lat') if ph['kind'] == 'raw' else ph.get('cam_lat')
    current_lon = ph.get('side_lon') if ph['kind'] == 'raw' else ph.get('cam_lon')
    ph['written'] = 1 if ph['action'] == 'write' and same_spot((current, current_lon), (ph['t_lat'], ph['t_lon'])) else 0
    return ph


def fmt_dur(s):
    if s >= 3600:
        return f'{s / 3600:.1f} h'
    if s >= 60:
        return f'{round(s / 60)} min'
    return f'{round(s)} s'
