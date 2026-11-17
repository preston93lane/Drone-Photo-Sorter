# Drone Pitch Sorter

![Drone Pitch Sorter](docs/screenshot.png)

Sorts DJI drone photos (built for the Mavic 3 Enterprise) into folders based on the
camera's **gimbal pitch**: Tower Flight, Downlook, Uplook, and so on.

**No ExifTool needed.** DJI stores the pitch as plain text inside each photo
(`drone-dji:GimbalPitchDegree="-40.00"`), so the app reads it directly. That makes it
much faster too: hundreds of photos per second instead of ~2–3.

## Updates

The app checks GitHub for a newer version each time it opens. When one exists, a bar
at the top offers **Update now**. It downloads the new version, swaps it in and
restarts. Your `categories.json` settings are kept. If the download fails, nothing
changes.

## Download & run (Windows)

1. Go to the repo's **Releases** page and download `DronePitchSorter.exe`.
2. Double-click it. You don't need Python, ExifTool or any folder setup.
3. Drag a flight folder onto the window (or click **Add folder…**), then click the blue **Copy/Move photos** button.

Every push also builds the exe as a downloadable artifact on the **Actions** tab.

## Options

| Option | What it does |
|---|---|
| **Copy / Move** | Copy keeps the originals (default). Move puts them into the category folders. |
| **Preview only** | Shows what would happen and touches nothing. |
| **Tolerance ±°** | How close the pitch must be to a category's angle (default 0.5°). |
| **Use shot order** | Files in-between photos by shot order (see below). |
| **Edit angles & rules…** | Opens `categories.json` (created next to the exe on first run). |

Photos are sorted into subfolders **inside the folder they came from**, e.g.
`Flight1/Tower Flight/DJI_0001.JPG`. Photos with no pitch, or a pitch that doesn't
match any category, go to `UNSORTED/`.

Running it again on the same folder is safe. It ignores its own output folders and
skips photos that are already sorted. If a *different* file with the same name is
already there, the new one is saved as `name_1.JPG` instead of overwriting it.

## Shrink for upload

An optional switch (off by default). After sorting, each sorted photo is repacked to be
smaller, typically 5–10%, with **exactly the same pixels** and all DJI data kept. It
rebuilds the JPG's internal compression tables (optimized, progressive) using
`jpegtran` from libjpeg-turbo, which is built into the app. The image itself isn't
re-compressed.

Every shrunk photo is decoded and compared pixel by pixel with the original, and its
DJI data is re-read, before it replaces the original. If any check fails, the original
is kept. In Copy mode only the sorted copies are shrunk; originals stay untouched.

**Estimate savings** tries 20 photos from the selected folders without changing anything
and shows how much space the whole set would save.

## Is Move safe?

Yes. Move is built so that a photo can't be lost:

- **Nothing is ever deleted outright.** A photo is moved by giving it a new name on the
  same drive. That's the same thing Windows does when you drag a file between folders.
  On drives that can't do that (some SD cards), the photo is copied, the copy is checked
  byte-for-byte, and only then is the original removed. If anything goes wrong, the
  original stays put.
- **Nothing is ever overwritten.** If a different photo with the same name is already
  there, the new one is saved as `name_1.JPG`. If an *identical* one is already there,
  the original is left where it was.
- **You can undo it.** Every real sort saves a `Sort log <date>.csv` in the flight folder,
  listing where each photo went. Click **Undo a move…**, pick that log, and every moved
  photo goes back to where it came from.

These rules are covered by automated tests (`tests/test_move_safety.py`) that simulate
name clashes, a disk-full error mid-copy, and drives without link support.

## Tower Flight at −40° or −50°

Tower Flight photos can be shot at −40° or, for taller towers, −50°. Both go to
**Tower Flight**. Photos taken during the Compound Flight raise (between the last −45°
and the first −20° shot) still go to Compound Flight, whatever their angle.

## Shot-order rules

Some shots don't have a fixed angle. Because your shot list is always in the same order,
the app uses the order the photos were taken in (DJI file numbers) to file those too:

| Photos taken between… | …and… | go to |
|---|---|---|
| the last **−45°** photo | the first **−20°** photo | **Compound Flight** (the angle raise) |
| the last **−25°** photo | the first **−90°** photo | **Overall** (the safety cable anchor shot) |

This applies whatever angle those in-between photos have, even if it happens to match
another category. Each folder is handled on its own, so a set is never linked to photos
in a different folder. As a safety limit, a gap of more than 15 photos is left alone.
In the Activity tab, these photos are marked "shot order". Turn the **Use shot order**
switch off to sort by angle only.

## Center In vs Center Out

Both orbits are shot at 0°, so the app uses your shot pattern instead:

- In each folder, it looks at the camera's compass direction from one 0° photo to the next.
- During an orbit, the direction changes a little with each photo (going from 359° to 1°
  counts as a small step).
- Switching from the Center In orbit to the Center Out orbit is a sudden jump of about
  180°. Everything after the biggest jump goes to **Center Out**.

The jump only has to be more than 120°, so imperfect numbers are fine. If there's no
big jump (only one orbit), everything stays in Center In. GPS isn't used.

## Rooftop sectors

On rooftops each sector is shot as a group: **Downlook (−35°) → Center In and Center Out
(0°) → Uplook (+20°, sometimes skipped)**, then the next sector.

- A new sector starts at a −35° photo when 0° photos have been taken since the
  previous −35° photo. With 2 or more sectors, each sector's downlooks go to their own
  folder: **Downlook 1**, **Downlook 2**, … With only one (a normal tower site), they stay
  in **Downlook**.
- When a folder is recognized as a rooftop (2+ downlook sets), **Compound Flight** is
  named **BSO** instead, and an empty **Cable Run** folder is created (along with Access
  Road). Tower sites are unchanged. Both are configurable under `"rooftop"` in
  `categories.json`.
- All center ins, center outs and uplooks still go to **Center In**, **Center Out** and
  **Uplook**.
- **Center In vs Center Out is decided per sector.** On rooftops the camera keeps turning
  smoothly from the in arc into the out arc, so the app looks for the **pause** while you
  move the drone in front of the antennas: the biggest gap in capture time, if it's at
  least 3 seconds and 3× the normal gap between photos. With the 0.7 s interval timer,
  **wait about 5 seconds** before restarting, because photo times are often stored to the
  whole second. Tower orbits still use the ~180° direction flip.
- If a sector has neither a flip nor a clear pause, its 0° photos stay in Center In and
  the Activity tab flags it in yellow.

**Tower or rooftop?** A tower is occasionally shot twice (two downlook / Center In /
Center Out / Uplook sets). The app tells it apart from a rooftop by the tower pattern:
if every set's 0° photos show either the ~180° direction flip between Center In and Center
Out, **or** a full orbit (about 270° or more of continuous turning without a pause, for
orders with no Center Out), it's a tower. Then the downlooks stay in **Downlook**, and Compound Flight keeps its
name with no Cable Run folder. Rooftop arcs turn smoothly without that flip.

**Pauses only count on rooftops.** On tower sites, a pause during the Center In orbit
never switches photos to Center Out; only the ~180° flip does.

This follows the **Use shot order** switch.

## Access Road folder

Every sorted folder also gets an empty **Access Road** folder for your GoPro photos.
Anything you put in it is never touched by later sorts. Change or add folder names
under `"extra_folders"` in `categories.json`.

## Editing categories

`categories.json`:

```json
{
  "tolerance": 0.5,
  "categories": [
    { "name": "Tower Flight", "pitch": -40 },
    { "name": "Tower Flight", "pitch": -50 },
    { "name": "Uplook", "pitch": 20 },
    { "name": "Wide band example", "min": -30, "max": -10 }
  ],
  "sequence_rules": [
    { "category": "Compound Flight", "after_pitch": -45, "before_pitch": -20 },
    { "category": "Overall", "after_pitch": -25, "before_pitch": -90 }
  ],
  "max_photos_between": 15
}
```

Each entry uses either `pitch` (± tolerance; add `"tolerance"` to override it for that
entry) or an explicit `min`/`max`. The first match wins, and several entries can share a
name (like the two "Compound Flight" angles).

## Running from source

```bash
pip install -r requirements.txt   # optional, enables drag & drop
python main.py                    # opens the app
python main.py "D:\Flights\Site 42" --dry-run   # command-line mode
python -m pytest                  # tests
```

## Publishing a new version

Raise `__version__` in `drone_sorter/__init__.py` and push. GitHub Actions runs the tests,
builds `DronePitchSorter.exe` and publishes it as a new Release. Everyone's app then
offers the update the next time it opens.
