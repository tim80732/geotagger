# Geotagger

Writes GPS positions into your photos from a [Dawarich](https://github.com/Freika/dawarich) location history, or from GPX files. Runs as a small web app in Docker (made for Unraid). Nothing is written until you have looked at the dry run and clicked **Write GPS**.

**Built by AI.** The code and docs in this repository were written by Claude (Anthropic's AI model), directed and tested by [@tim80732](https://github.com/tim80732). It's used on a real photo library, but review it yourself before trusting it with files you can't replace, and keep backups.

## Why

Lightroom and most other tools match a GPS track by the photo's clock time, in your computer's timezone, and ignore the timezone the camera wrote into the photo. Shoot abroad and every photo lands hours away from where it was taken unless you set an offset by hand, per trip, and again when you cross a timezone.

Geotagger reads each photo's own `OffsetTimeOriginal` (`+02:00`, `+08:00` …), turns its time into UTC and matches that against the track, which is stored in UTC. A trip through several timezones needs no offsets at all.

## What it does

- **Dry run first.** Reads every photo's capture time, loads the track from Dawarich (or your GPX files) and shows what would be written: on a map, in a table and as totals.
- **Interpolates** between the two track points around each photo when they are close enough in time (default 10 min); otherwise uses a single point within 2 min. Points worse than 100 m accuracy are ignored. All three are options per job.
- **Flags problems** in the existing camera GPS: a phone-to-camera GPS link that froze and kept stamping the same spot, and camera positions that disagree with the track by more than 1 km.
- **Track gaps** are left untagged. The **Nearest GPS** button shows the last and next track point around such a photo, so you can place it from there.
- **Manual pins.** Select photos and click the map, search a place, or drag a pin. Hand-placed positions are kept by later runs. A JPG and its RAW twin are placed together.
- **Writes** GPS into JPG files, and into a `.xmp` sidecar next to each RAW file. The RAW file itself is never changed. File dates, owner and permissions are kept. Files that already hold the position are left alone, so running an old job again rewrites nothing. Every written file is read back to check it.
- **Long writes** show progress and time left, and can be paused, resumed or cancelled. A dry run can be cancelled.
- **Safe to stop.** When the container stops (an update, or a backup plugin stopping all containers), the photo being written is finished first, and the job carries on by itself when the container starts again.
- **Full-size viewer** with next/previous (arrow keys or swipe), CSV export, phone-friendly layout.

Videos are not tagged.

## Install on Unraid

1. In a terminal on the server, download the template:

   ```bash
   wget -O /boot/config/plugins/dockerMan/templates-user/my-Geotagger.xml https://raw.githubusercontent.com/tim80732/geotagger/main/unraid/geotagger.xml
   ```

2. Docker tab → **Add Container** → pick **Geotagger** from the template list.
3. Set **Photo library** to the share with your photos. Fill in the **Dawarich URL** and **API key** here, or later in the app's Settings page.
4. Apply, then open the WebUI.

The template is kept on the flash drive, so it survives reboots. Updates show up on the Docker tab like any other container. Settings, jobs and manual pins are in the app data folder and are kept. An update during a write pauses it after the current photo, and the job carries on when the new version starts.

## Any Docker host

```bash
docker run -d --name geotagger -p 8790:8000 \
  -v /path/to/photos:/photos -v /path/to/appdata:/data \
  -e DAWARICH_URL=https://dawarich.example.com -e DAWARICH_API_KEY=... \
  ghcr.io/tim80732/geotagger:latest
```

The container runs as root, because only a file's owner or root may set its Date modified, and photos copied in by different users have different owners. New `.xmp` sidecars get the owner of their photo. You can run it as a normal user with `--user <uid>:<gid>` (the app data folder must be writable by that user), but then photos owned by anyone else get GPS with today's Date modified; those are listed under Errors.

The web UI has no login: keep it on your LAN and don't forward the port.

| Variable | Default | |
|---|---|---|
| `DAWARICH_URL` | | Base URL of Dawarich. Use the HTTPS address if it redirects HTTP to HTTPS. |
| `DAWARICH_API_KEY` | | Dawarich → Account → API key |
| `PHOTO_ROOTS` | `/photos` | Comma-separated container paths the folder picker may open |
| `PHOTON_URL` | `https://photon.komoot.io` | Place search. Falls back to OpenStreetMap's Nominatim when Photon doesn't answer. |

Dawarich URL and key can also be set (and tested) in the Settings page, which also takes GPX uploads.

## Using it

1. **New job:** pick one or more folders (with their subfolders, unless you untick **Include subfolders**), choose JPG, RAW or both, adjust the options if needed.
2. **Review the dry run.** Check the warnings, place photos that fell into track gaps, exclude photos you don't want tagged.
3. **Write GPS.**
4. **Lightroom:** select the photos → **Metadata → Read Metadata from Files**. No timezone offset needed. If you edited the photos' metadata in Lightroom, save it to the files (Ctrl+S) before writing, or reading the files back replaces Lightroom's unsaved changes.

Photos without a timezone in their EXIF are skipped by default, or can be read with an offset you choose.

Writing rewrites each JPG in place, so a big batch on slow or nearly full storage takes a while. Tagging RAW files only writes small `.xmp` sidecars and is quick. Geotagger keeps no copies of your photos: have a backup of the library.

If the container is killed in the middle of a photo (power loss, or a stop that takes longer than Docker waits), the job stays paused when it starts again and names the photo. Its Date modified is put back when the job resumes. Next to it is an exiftool temporary file ending in `_exiftool_tmp`. If the photo looks right all the way to the bottom, delete the temporary file. If not, replace the photo with the temporary file (remove `_exiftool_tmp` from its name). Then resume.

## How it's built

| Path | What |
|---|---|
| `app/main.py` | FastAPI app and JSON API |
| `app/jobs.py` | Background queue: dry run (read times, load track, match) and write |
| `app/matcher.py` | Interpolation, frozen-GPS detection, what to write per photo |
| `app/track.py` | Dawarich API and GPX reader |
| `app/exif.py` | A long-running ExifTool process (`-stay_open`) |
| `app/db.py` | SQLite: jobs, photos, manual pins, what was written |
| `static/` | Web UI (plain JavaScript, Leaflet, OpenStreetMap tiles) |
| `unraid/` | Unraid template |

Uses [ExifTool](https://exiftool.org/) by Phil Harvey for all reading and writing of metadata.

To build the image yourself: `docker build -t geotagger .` in this folder. Releases are built by GitHub Actions: pushing a tag like `v1.0.0` pushes the image to `ghcr.io/tim80732/geotagger` (`:1.0.0` and `:latest`).

## License

MIT, see [LICENSE](LICENSE).
