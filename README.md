# BB Restock 2.2

A mod for **Bloodborne** on PC ([bbport](https://github.com/Supermedo/bloodborne_pc) or [shadPS4](https://github.com/shadps4-emu/shadPS4)) that fills your Blood Vials
and Quicksilver Bullets to the maximum on respawn after death and on lantern
travel, while leaving booting and loading the save alone.

## Requirements

- Bloodborne **CUSA03173, game version 1.09** (the same one required by bbport).
  **Do not use it with other versions**: the replaced file may break game events.
- bbport or shadPS4.
- To generate the mod with the script: Python 3.8 or newer (standard library
  only) and the extracted game files (the folder containing `dvdroot_ps4`).

## What it does

During a play session, on **respawn after death** and on **lantern travel**,
your **Blood Vials** and **Quicksilver Bullets** are filled to the maximum
(20 each). Only what is missing is added, straight to the inventory, without
the "item received" window. Nothing goes to Storage. Booting the game and
loading the save (quit -> Continue) do **not** refill. No more farming.

## Installation

This repository **does not contain game files**. First generate the mod from
your copy of Bloodborne (see [Generating the mod](#generating-the-mod)); the
result is the `BB_Restock_Mod` folder.

### bbport (recommended: does not modify the game files)

1. Copy the mod folder into bbport's `mods` folder. If it does not exist,
   create it next to `Bloodborne.exe`:

   ```
   bbport-windows\
   ├── Bloodborne.exe
   └── mods\
       └── BB_Restock_Mod\
           ├── dvdroot_ps4\ ...
           └── README.txt
   ```

2. Open `Bloodborne.exe` (launcher) and check on the **Mods & patches** tab that
   the mod appears enabled. Done.

**Uninstall:** delete the mod folder in `mods\` (or disable it in the launcher).

### shadPS4 (or without bbport)

1. Back up this file from the game folder (`CUSA03173`):
   `dvdroot_ps4\event\common.emevd.dcx`.
   If you use a separate update folder (e.g. `CUSA03173-UPDATE`), its file has
   priority: back it up and copy to **it**.
2. Copy the mod's `dvdroot_ps4` folder over the game's `dvdroot_ps4` folder,
   replacing the file.

**Uninstall:** restore the file from the backup (or the `.bak` created by `--install`).

### On other PCs

Just copy the generated mod folder: there is no need to run the script again.
The destination PC must have the **same game version** (1.09).

## Compatibility

- The mod replaces a single file: `dvdroot_ps4\event\common.emevd.dcx`.
  It conflicts with any other mod that replaces that same file; only one of the
  two will work. In bbport, the mod loaded last on the **Mods & patches** tab
  wins.
- Mods that change other files (params, models, maps, textures) work together.

## Notes

- If you have more than 20 (for example with a rune that raises the cap), the
  mod gives nothing of that item. To top up to a different number, generate the
  mod with `--vials`/`--bullets`.
- Items already in Storage stay there; the mod simply no longer adds any.

## Version history

- **2.2**: in-game testing of 2.1 showed the game gives initialized events an
  initial run on every load (boot, respawn, warp AND quit -> Continue), so the
  rest behavior alone cannot keep loads clean. The refill is now guarded by a
  session marker: the first run in a session (boot or loading the save) ends
  without refilling, while later runs in that session (deaths and warps)
  refill. The Dream watcher event was removed.
- **2.1**: refills only on respawn after death and on lantern travel (arrival
  in the Dream); loading the save (quit -> Continue) no longer refills.
- **2.0**: tops up to the maximum instead of giving 20 per load: only what is
  missing, straight to the inventory, without a window and with nothing going
  to Storage. Now only `common.emevd.dcx` is changed (gameparam is no longer
  needed).
- **1.0**: first version: 20 Blood Vials and 20 Quicksilver Bullets per load,
  with the excess going to Storage.

---

## Generating the mod

```
python bb_restock.py "C:\path\to\game\CUSA03173"
```

You can pass the folder containing `dvdroot_ps4` or `dvdroot_ps4` itself. The
original files **are not modified**. The mod is written to `BB_Restock_Mod/`:

```
BB_Restock_Mod/
├── README.txt
└── dvdroot_ps4/
    └── event/common.emevd.dcx
```

| Option | Default | Description |
|---|---|---|
| `--vials N` | 20 | Top up Blood Vials to N (1–99; 20 is the game maximum) |
| `--bullets N` | 20 | Top up Quicksilver Bullets to N (1–99; 20 is the game maximum) |
| `--out FOLDER` | `BB_Restock_Mod` | Output folder |
| `--install` | off | Also installs into the game, saving the original as `.bak` |

If the file has an unexpected format or has already been modified, the script
stops with an error message and writes nothing.

## How it works

The mod adds one event to `common.emevd`, initialized at the start of event
`0`, which the game runs on every area load. In-game tests of 2.1 showed that
the game gives such an event an initial run on **every** load: booting, dying,
warping and quit -> Continue — the rest behavior bit alone cannot tell a load
apart from a respawn. What does differ is process lifetime: deaths and warps
happen inside the running session, while quit -> Continue starts a fresh
process (and so do brand-new games), with all runtime event values reset to 0.

Event `9990000` (RestartOnRest) uses a runtime event value as a session marker:

```
increment marker (0/1 -> 1/2, capped at 2)          2003[31]  Increment Event Value
marker != 1?                                        3[12]     IF Event Value (!=)
if marker == 1 (first run): end without refilling   1000[02]  END IF (condition false)
wait one second                                     1001[00]  WAIT
top up both items (see below)
```

- **First run in a session** (brand-new boot, or the load after quitting):
  the marker goes from 0 to 1, so the event ends quietly - no refill.
- **Later runs in the session** (death respawns, lantern warps): the marker
  goes from 1 to 2 (and stays there), so the run falls through to the refill.
- The refill still waits one second first, so the game's own Storage refill
  on death happens before the top-up.
- The event refuses to run for non-host players (the vanilla human check).

For each item, the event does:

```
want    = maximum                                  2003[41] Event Value Operation (assign)
have    = amount in the inventory                  2003[42] Store Item Amount Held in Event Value
if have >= maximum: skip the next two              3[12] IF Event Value + 1000[01] SKIP
want    = want - have                              2003[41] Event Value Operation (subtract)
give "want" units straight to the inventory        2003[43] Directly Give Player Item
```

It is the same pattern the game itself uses in event `12101021` of
`m21_00_00_00.emevd` to top up Quicksilver Bullets. The `have >= maximum` check
prevents the subtraction from going negative.

The variables live in the 8-bit event values at `6950` (the session marker),
`6960` (amount to give) and `6970` (amount held). The last two are reused for
both items because the two top-ups never overlap. No game event or dialog
script uses the `6950-6999` range, and the script refuses the operation if
`common.emevd` uses any of them.

### Technical details

- **DCX:** decompressed and recompressed with zlib (DFLT), keeping the original header.
- **EMEVD:** the file is parsed and rewritten in the Bloodborne layout
  (64-bit, version `0xCC`). Before any change, the script confirms that the
  rewrite preserves the content. Event 0's parameter indices are adjusted for
  the inserted instruction.

## Tests

`tests/run_tests.sh` runs an end-to-end test:

1. Generates synthetic files in the Bloodborne format with
   [SoulsFormatsNEXT](https://github.com/soulsmods/SoulsFormatsNEXT).
2. Runs `bb_restock.py` on them.
3. Re-reads the result with SoulsFormats and checks that:
   - the original events did not change;
   - the new event, its initialization in event 0 and the session-marker gate
     are correct;
   - running the script on already modified files is refused.

Requires `git`, `python3` and .NET SDK 8 or newer:

```
bash tests/run_tests.sh
```

## Credits

- [SoulsFormatsNEXT](https://github.com/soulsmods/SoulsFormatsNEXT): reference
  for the DCX and EMEVD formats; used in the tests.
- [DarkScript3](https://github.com/AinTunez/DarkScript3): EMEVD instruction definitions.

## License

[MIT](LICENSE). Bloodborne is the property of Sony Interactive Entertainment and
FromSoftware. This project is not affiliated with them and does not distribute
game content.
