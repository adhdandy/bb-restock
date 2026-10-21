#!/usr/bin/env python3
"""
BB Restock - Bloodborne (shadPS4 / bbport / PS4 dump)

Generates a mod that tops up the Blood Vials and Quicksilver Bullets in the
inventory to the maximum on respawn after death and on lantern travel, and
leaves the save alone: booting the game and loading the game (quit -> Continue)
do NOT refill. It gives only what is missing: nothing goes to Storage.

What the script changes:
  event/common.emevd.dcx
    -> adds event 9990000, initialized in event 0 (which the game runs on
       every load). In-game tests showed the game gives initialized events an
       initial run on EVERY load (boot, death respawn, lamp warp AND
       quit -> Continue), no matter their rest behavior -- the rest bit alone
       cannot tell a reload apart from a respawn. What does change between a
       respawn and a reload is process lifetime: death respawn and lamp warps
       happen inside the same session, while quit -> Continue starts a fresh
       process with all runtime event values reset to 0. The event uses a
       runtime event value as a session marker:
       - first run in a session (boot or load): increment the marker to 1,
         end quietly -- no refill.
       - later runs (deaths and warps in that session): marker is 2, refill.
       For each item, the event reads the amount the player carries,
       calculates (maximum - amount) and gives the difference straight to
       the inventory. It is the same instruction pattern the game uses in
       event 12101021 of m21_00_00_00.emevd.

The original files are never modified (unless you use --install, which
creates a .bak backup first). Requires only Python 3.8+ (standard library).

Usage:
  python bb_restock.py "C:\\path\\CUSA03173"
  python bb_restock.py "C:\\path\\CUSA03173" --vials 20 --bullets 20 --install
"""

import argparse
import os
import shutil
import struct
import sys
import zlib

# ----------------------------------------------------------------------------
# Bloodborne constants
# ----------------------------------------------------------------------------

BLOOD_VIAL_ID = 1000          # EquipParamGoods (maxNum 20)
QUICKSILVER_BULLET_ID = 900   # EquipParamGoods (maxNum 20)
EMEVD_GOODS = 3               # ItemType "Goods" in event instructions
RESTOCK_EVENT_ID = 9990000    # RestartOnRest event: value-gated top-up
PLAYER_ID = 10000

# Event values (8 flags each) used as temporary variables. The top-up values
# are reused for both items because the two top-ups never overlap.
# Range 6950-6999: not used by any event or dialog script in the game.
VALUE_BITS = 8
REFRESH_MARK = 6950           # 0/1 on the first run of a session, then 2
WANT, HELD = 6960, 6970

COMMON_EMEVD_REL = ("event", "common.emevd.dcx")


class PatchError(Exception):
    pass


# ----------------------------------------------------------------------------
# DCX (DFLT / zlib)
# ----------------------------------------------------------------------------

DCX_HEADER_SIZE = 0x4C


def dcx_decompress(data):
    """Returns (header, payload). header is None if the file is not DCX."""
    if data[:4] != b"DCX\0":
        return None, data
    if data[0x18:0x1C] != b"DCS\0" or data[0x24:0x28] != b"DCP\0" or data[0x44:0x48] != b"DCA\0":
        raise PatchError("Unexpected DCX header.")
    if data[0x28:0x2C] != b"DFLT":
        raise PatchError("DCX compression %r not supported (expected DFLT)." % data[0x28:0x2C])
    usize, csize = struct.unpack_from(">II", data, 0x1C)
    payload = zlib.decompress(data[DCX_HEADER_SIZE:DCX_HEADER_SIZE + csize])
    if len(payload) != usize:
        raise PatchError("DCX decompressed size does not match.")
    return data[:DCX_HEADER_SIZE], payload


def dcx_compress(header, payload):
    if header is None:
        return payload
    comp = zlib.compress(payload, 9)  # 78 DA ... adler32, same as the game
    h = bytearray(header)
    struct.pack_into(">II", h, 0x1C, len(payload), len(comp))
    return bytes(h) + comp


# ----------------------------------------------------------------------------
# EMEVD (Bloodborne format: little-endian, 64-bit, version 0xCC)
# ----------------------------------------------------------------------------

class Instr:
    def __init__(self, bank, idx, args=b"", layer_rel=-1):
        self.bank, self.id, self.args, self.layer_rel = bank, idx, args, layer_rel

    def key(self):
        return (self.bank, self.id, self.args, self.layer_rel)


class Event:
    def __init__(self, eid, rest=0):
        self.id, self.rest, self.instrs, self.params = eid, rest, [], []

    def key(self):
        return (self.id, self.rest, [i.key() for i in self.instrs], list(self.params))


class Emevd:
    def __init__(self, data):
        if data[:4] != b"EVD\0":
            raise PatchError("Not an EMEVD file.")
        if data[4:8] != b"\0\xFF\0\0" or struct.unpack_from("<i", data, 8)[0] != 0xCC:
            raise PatchError("EMEVD is not in the Bloodborne format.")
        (event_count, events_off, _ic, instrs_off, _uc, _u3, self.layer_count, layers_off,
         _pc, params_off, self.linked_count, linked_off, _al, args_off, str_len, str_off) = \
            struct.unpack_from("<16q", data, 0x10)
        if _uc:
            raise PatchError("EMEVD uses unknown data; this patcher only supports Bloodborne common.emevd.")
        # Layers, linked files and strings are copied verbatim: this mod never
        # adds any of them. Only instruction layer offsets need adjusting.
        self.layers = data[layers_off:args_off] if self.layer_count else b""
        self.linked = data[linked_off:str_off] if self.linked_count else b""
        self.strings = data[str_off:str_off + str_len]

        self.events = []
        for i in range(event_count):
            p = events_off + i * 48
            eid, icount, ioff, pcount = struct.unpack_from("<4q", data, p)
            poff = struct.unpack_from("<i", data, p + 32)[0]
            rest = struct.unpack_from("<I", data, p + 40)[0]
            ev = Event(eid, rest)
            for k in range(icount):
                q = instrs_off + ioff + k * 32
                bank, iid, alen = struct.unpack_from("<iiq", data, q)
                aoff = struct.unpack_from("<i", data, q + 16)[0]
                loff = struct.unpack_from("<i", data, q + 24)[0]
                layer_rel = -1
                if loff != -1:
                    layer_rel = loff
                    if not 0 <= layer_rel < len(self.layers) or layer_rel % 32:
                        raise PatchError("Invalid EMEVD layer reference.")
                args = data[args_off + aoff:args_off + aoff + alen] if alen > 0 else b""
                ev.instrs.append(Instr(bank, iid, args, layer_rel))
            for k in range(pcount):
                ev.params.append(struct.unpack_from("<3qii", data, params_off + poff + k * 32))
            self.events.append(ev)

    def write(self):
        n_instr = sum(len(e.instrs) for e in self.events)
        n_param = sum(len(e.params) for e in self.events)

        events_off = 0x90
        instrs_off = events_off + 48 * len(self.events)
        layers_off = instrs_off + 32 * n_instr
        args_off = layers_off + len(self.layers)

        args_blob = bytearray()
        arg_rel = []
        for ev in self.events:
            for ins in ev.instrs:
                if ins.args:
                    arg_rel.append(len(args_blob))
                    args_blob += ins.args
                    args_blob += b"\0" * (-(args_off + len(args_blob)) % 4)
                else:
                    arg_rel.append(-1)
        args_blob += b"\0" * (-len(args_blob) % 0x10)
        params_off = args_off + len(args_blob)
        linked_off = params_off + 32 * n_param
        str_off = linked_off + len(self.linked)
        file_size = str_off + len(self.strings)

        out = bytearray(b"EVD\0" + bytes([0, 0xFF, 0, 0]))
        out += struct.pack("<ii", 0xCC, file_size)
        out += struct.pack("<16q", len(self.events), events_off, n_instr, instrs_off,
                           0, layers_off, self.layer_count, layers_off, n_param, params_off,
                           self.linked_count, linked_off, len(args_blob), args_off,
                           len(self.strings), str_off)
        ins_index = 0
        param_index = 0
        for ev in self.events:
            ioff = ins_index * 32 if ev.instrs else -1
            poff = param_index * 32 if ev.params else -1
            out += struct.pack("<4qiiIi", ev.id, len(ev.instrs), ioff, len(ev.params), poff, 0, ev.rest, 0)
            ins_index += len(ev.instrs)
            param_index += len(ev.params)
        k = 0
        for ev in self.events:
            for ins in ev.instrs:
                loff = ins.layer_rel
                out += struct.pack("<iiqiiii", ins.bank, ins.id, len(ins.args), arg_rel[k], 0, loff, 0)
                k += 1
        out += self.layers
        out += args_blob
        for ev in self.events:
            for prm in ev.params:
                out += struct.pack("<3qii", *prm)
        out += self.linked
        out += self.strings
        assert len(out) == file_size
        return bytes(out)

    def key(self):
        return ([e.key() for e in self.events], self.layer_count, self.layers,
                self.linked_count, self.linked, self.strings)


# ----------------------------------------------------------------------------
# Patch
# ----------------------------------------------------------------------------

CALC_SUB, CALC_ASSIGN = 1, 5        # Event Value Operation: calculation type
CMP_GE, CMP_NE = 4, 1               # Comparison ">=" / "!="


def topup_instrs(item_id, maximum, group):
    """Tops up item_id to `maximum`:
        want = maximum
        held = amount the player carries
        if held >= maximum: skip the next two (avoids a negative value)
        want = want - held
        give `want` units straight to the inventory"""
    if not 1 <= maximum <= 99:
        raise PatchError("Maximum must be between 1 and 99.")
    return [
        Instr(2003, 41, struct.pack("<iIiiIb3x", WANT, VALUE_BITS, maximum, 0, 1, CALC_ASSIGN)),
        Instr(2003, 42, struct.pack("<iiiI", EMEVD_GOODS, item_id, HELD, VALUE_BITS)),
        Instr(3, 12, struct.pack("<b3xiBB2xI", group, HELD, VALUE_BITS, CMP_GE, maximum)),
        skip_if_true(2, group),
        Instr(2003, 41, struct.pack("<iIiiIb3x", WANT, VALUE_BITS, 0, HELD, VALUE_BITS, CALC_SUB)),
        Instr(2003, 43, struct.pack("<iiiI", EMEVD_GOODS, item_id, WANT, VALUE_BITS)),
    ]


# Raw instruction encodings, all matching how the vanilla common.emevd uses
# them (events 3500, 5500, 3503, 7200, 9030, 9700).

def instr_disable_network_sync():
    return Instr(2000, 2, struct.pack("<i", 0))


def instr_character_is_human(group):
    """AND_<group>.Add(CharacterIsHuman(PLAYER))."""
    return Instr(4, 3, struct.pack("<iii", group, PLAYER_ID, 0))


def instr_end_if_condition_false(group):
    """Ends the event while AND_<group> is false ("if not: return").
    Vanilla events 3500/3503/5500 use instruction 1000[2] for this."""
    return Instr(1000, 2, struct.pack("<2xBx", group))


def instr_increment(value, maximum):
    """EventValue(value, VALUE_BITS) += 1, capped at `maximum`."""
    return Instr(2003, 31, struct.pack("<iII", value, VALUE_BITS, maximum))


def instr_value_not_equal(value, number, group):
    """AND_<group>: EventValue(value, VALUE_BITS) != `number`."""
    return Instr(3, 12, struct.pack("<b3xiBB2xI", group, value, VALUE_BITS, CMP_NE, number))


def instr_wait(seconds):
    return Instr(1001, 0, struct.pack("<f", seconds))


def skip_if_true(count, group):
    """Skips the next `count` instructions while AND_<group> is true."""
    return Instr(1000, 1, struct.pack("<BBbx", count, 1, group))


def respawn_event(vials, bullets):
    """Event 9990000 (RestartOnRest): tops up vials/bullets on death respawn
    and on lamp travel (any in-process reload), but NOT on boot or on
    loading a save (quit -> Continue).

    The gate is a runtime event value (REFRESH_MARK). The engine keeps event
    values alive while the process runs (death respawn and lamp warps all
    re-run this event within the same session), but a fresh process (new
    game, quit -> Continue loading screen) starts with the value at 0, so
    the first run only bumps the marker to 1 and ends quietly."""
    ev = Event(RESTOCK_EVENT_ID, rest=1)
    ev.instrs.append(instr_disable_network_sync())
    ev.instrs.append(instr_character_is_human(15))
    ev.instrs.append(instr_end_if_condition_false(15))          # if not human: return
    ev.instrs.append(instr_increment(REFRESH_MARK, 2))          # 0/1 -> 1/2 (stays 2)
    ev.instrs.append(instr_value_not_equal(REFRESH_MARK, 1, 3)) # first run: marker == 1
    ev.instrs.append(instr_end_if_condition_false(3))           # first run: no refill
    ev.instrs.append(instr_wait(1.0))  # let the game's own Storage refill finish first
    ev.instrs += topup_instrs(BLOOD_VIAL_ID, vials, 1)
    ev.instrs += topup_instrs(QUICKSILVER_BULLET_ID, bullets, 2)
    return ev


def patch_common_emevd(raw, vials, bullets):
    header, data = dcx_decompress(raw)
    emevd = Emevd(data)

    # The re-serialization must preserve the content before we change anything.
    rewritten = emevd.write()
    if Emevd(rewritten).key() != emevd.key():
        raise PatchError("Check failed: EMEVD does not survive rewriting.")

    ids = [e.id for e in emevd.events]
    if RESTOCK_EVENT_ID in ids:
        raise PatchError("Event %d already exists. Were the files already modified? "
                         "Use the original game files." % RESTOCK_EVENT_ID)
    if 0 not in ids:
        raise PatchError("Event 0 not found in common.emevd.")
    used = set(ids)
    for ev in emevd.events:
        for ins in ev.instrs:
            for o in range(0, len(ins.args) - 3, 4):
                used.add(struct.unpack_from("<I", ins.args, o)[0])
    for base in (REFRESH_MARK, WANT, HELD):
        if used & set(range(base, base + VALUE_BITS)):
            raise PatchError("Flags %d-%d already used by common.emevd; different game version?"
                             % (base, base + VALUE_BITS - 1))

    emevd.events.append(respawn_event(vials, bullets))

    ev0 = emevd.events[ids.index(0)]
    # InitializeEvent(slot 0, event, 0) at the start of event 0
    ev0.instrs.insert(0, Instr(2000, 0, struct.pack("<iIi", 0, RESTOCK_EVENT_ID, 0)))
    ev0.params = [(p[0] + 1,) + tuple(p[1:]) for p in ev0.params]

    new_data = emevd.write()
    check = Emevd(new_data)
    if check.key() != emevd.key():
        raise PatchError("Check failed: EMEVD modified.")
    return dcx_compress(header, new_data)


def find_file(root, rel_parts):
    """Looks for <root>/dvdroot_ps4/<rel> or <root>/<rel>."""
    for base in (root, os.path.join(root, "dvdroot_ps4")):
        path = os.path.join(base, *rel_parts)
        if os.path.isfile(path):
            return path
    raise PatchError("Could not find %s in %s. Pass the game folder (the one containing dvdroot_ps4)."
                     % ("/".join(rel_parts), root))


def write_readme(out_dir, vials, bullets):
    text = """BB Restock - mod for Bloodborne

Effect: during a play session your Blood Vials are topped up to {v} and your
Quicksilver Bullets to {b} on every death respawn and lantern warp. Booting
the game and loading the save (quit -> Continue) do not refill: the session
marker in a fresh process keeps the game quiet. Only what is missing is
added, straight to the inventory: nothing goes to Storage.

Installation (on any PC with the SAME game version):
  bbport:  copy this folder into bbport-windows\\mods\\
  shadPS4: back up the game's dvdroot_ps4\\event\\common.emevd.dcx and copy
           this mod's dvdroot_ps4 folder over the game's.

Uninstall: delete the mod folder (bbport) or restore the original file.
""".format(v=vials, b=bullets)
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser(description="Generates the BB Restock mod for Bloodborne.")
    ap.add_argument("game_dir", help="Game folder (contains dvdroot_ps4) or dvdroot_ps4 itself")
    ap.add_argument("--out", default="BB_Restock_Mod", help="Output folder (default: BB_Restock_Mod)")
    ap.add_argument("--vials", type=int, default=20,
                    help="Top up Blood Vials to this number (default 20, the game maximum)")
    ap.add_argument("--bullets", type=int, default=20,
                    help="Top up Quicksilver Bullets to this number (default 20, the game maximum)")
    ap.add_argument("--install", action="store_true",
                    help="Also installs directly into the game (creates a .bak of the original)")
    args = ap.parse_args()

    try:
        ev_path = find_file(args.game_dir, COMMON_EMEVD_REL)
        print("common.emevd: %s" % ev_path)
        with open(ev_path, "rb") as f:
            new_ev = patch_common_emevd(f.read(), args.vials, args.bullets)
        print("common.emevd: event %d added (tops up vials to %d and bullets to %d)."
              % (RESTOCK_EVENT_ID, args.vials, args.bullets))

        dst = os.path.join(args.out, "dvdroot_ps4", *COMMON_EMEVD_REL)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(new_ev)
        write_readme(args.out, args.vials, args.bullets)
        print("Mod generated in: %s" % os.path.abspath(args.out))

        if args.install:
            bak = ev_path + ".bak"
            if not os.path.exists(bak):
                shutil.copy2(ev_path, bak)
            with open(ev_path, "wb") as f:
                f.write(new_ev)
            print("Installed into the game (original saved as .bak).")
    except PatchError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
