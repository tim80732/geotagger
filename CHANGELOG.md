# Changelog

What changed in each release. The Docker image is
`ghcr.io/tim80732/geotagger`; release pages are on the
[Releases page](https://github.com/tim80732/geotagger/releases).

## 1.1.2 — 2026-11-12

### Security
- *Test connection* only sends the saved Dawarich API key to the saved
  address. Testing another address needs the key typed in.
- Changes sent by another website open in your browser are refused, so a
  web page can't start a write or change settings.
- A file name with a line break in it could add exiftool options. Such
  files are left out of a dry run, and exiftool refuses any argument with a
  line break.
- A photo or sidecar that is a symlink to somewhere outside the photo
  folders is left out, so a write can't reach files elsewhere.

### Fixed
- A RAW file deleted after the dry run no longer fails the whole write, or
  leaves a `.xmp` behind on its own. That photo gets an error.
- A job whose write was cut off in the middle of a photo can't be deleted
  or re-run while the exiftool temporary file is still there, so the note
  that names the photo isn't lost.
- Pausing and resuming a write that was waiting in the queue no longer runs
  it twice. A waiting job that was paused or cancelled is no longer started.
- A dry run that didn't finish can't be written. One interrupted by a
  restart now says to run it again. A write that was waiting in the queue
  when the container restarted carries on by itself.
- Settings and job options are checked: numbers sent as text are converted,
  and negative numbers or unknown choices are refused.
- When Dawarich doesn't say how many pages of points there are (a proxy
  may drop the header), every page is loaded instead of only the first.
- *Nearest GPS* looks past inaccurate points, up to 10,000 in each direction.
- A photo with half a GPS position (latitude but no longitude) no longer
  stops the dry run.
- Interpolation takes the short way across the ±180° line.
- An unreadable timezone is reported as that, not as "No capture time".
- The map no longer shows gaps in the track while you were standing still.
- A photo with a wrong date (a reset camera clock) no longer downloads the
  whole location history in between: the track is loaded around each
  group of photos.
- Deleting a job also deletes its map track.
- GPX uploads are written straight to disk; thumbnails are saved whole
  before they are served.
- Listing the folders at the start of a dry run is much faster.

## 1.1.1 — 2026-11-02

### Fixed
- **Date modified is kept exactly.** Writing GPS used to round each photo's
  Date modified to the whole second; it's now put back to the nanosecond,
  also for a photo that was cut off part-way.
- The folder list counts only photos, not videos.
- An exiftool command that hangs is stopped after two minutes. During a
  write, that photo gets an error and the job carries on.
- *Test connection* and GPX import no longer freeze the other pages when
  Dawarich is slow or unreachable.
- The dry run, the write and the read-back check use the same rule for "this
  photo already holds this position".

## 1.1.0 — 2026-11-02

### Fixed
- Photos south of the equator or west of Greenwich failed the read-back check
  and were rewritten every time a job was resumed.

### Changed
- **Stopping is safe.** When the container stops, the photo being written is
  finished first, then the job pauses, and it carries on by itself when the
  container starts again. A photo cut off by a hard kill is named in the job
  and gets its Date modified back on resume.
- Files that already hold the position are left alone, so re-running an old
  job rewrites nothing.
- The container runs as root, because only a file's owner or root can set
  its Date modified and a library often has photos owned by several users.
  New `.xmp` sidecars get the owner of their photo. A write that couldn't
  keep the date is listed as an error.

### Added
- *Include subfolders* (on by default) for dry runs and folder counts.

### Removed
- The Backups option. It put a full-size copy next to every photo and
  replaced the file, which changed its owner and Date created.

## 1.0.0 — 2026-11-01

First release: writes GPS from a Dawarich location history or GPX files
into photos, matching each photo by its own EXIF timezone. Dry run, map
review with manual pins, then write (JPG in place, RAW as `.xmp` sidecars).
Docker image on ghcr.io with an Unraid template.
