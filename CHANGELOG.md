# Changelog

What changed in each release. The Docker image is
`ghcr.io/tim80732/geotagger`; release pages are on the
[Releases page](https://github.com/tim80732/geotagger/releases).

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
