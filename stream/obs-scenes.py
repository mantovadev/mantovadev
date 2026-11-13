#!/usr/bin/env python3
"""Generate an OBS scene collection for Mantova Dev streams.

Writes a JSON file to import in OBS (Scene Collection > Import). Video slots come from
overlay/layouts.js, the same file the overlay pages read, so sources and frames line up.

  python3 stream/obs-scenes.py -o ~/Desktop/mantovadev-obs.json \
      --devices ~/Library/Application\\ Support/obs-studio/basic/scenes/Mantova_Dev.json

--devices copies capture device settings from a collection this script generated before:
"Schermo speaker", "Camera", "Microfono", "Audio speaker". Without it (first time on a Mac),
pick the devices in OBS after importing.
"""
import argparse
import json
import pathlib
import uuid

HERE = pathlib.Path(__file__).resolve().parent
OVERLAY = HERE / "overlay"
DEVICE_NAMES = {"screen": "Schermo speaker", "camera": "Camera", "mic": "Microfono", "screen_audio": "Audio speaker"}


def source(name, sid, settings, **extra):
    return {
        "name": name, "uuid": str(uuid.uuid4()), "id": sid, "versioned_id": sid, "settings": settings,
        "mixers": 255, "sync": 0, "flags": 0, "volume": 1.0, "balance": 0.5, "enabled": True, "muted": False,
        "monitoring_type": 0, "private_settings": {}, **extra,
    }


def filt(name, fid, settings):
    return {"name": name, "id": fid, "versioned_id": fid, "settings": settings, "enabled": True}


def item(src, item_id, box=None, outer=False):
    """Place src in box [x, y, w, h]: fit inside it, or fill it and crop the excess (outer)."""
    x, y, w, h = box or (0, 0, 0, 0)
    return {
        "name": src["name"], "source_uuid": src["uuid"], "id": item_id, "visible": True, "locked": False,
        "rot": 0.0, "pos": {"x": x, "y": y}, "scale": {"x": 1.0, "y": 1.0}, "align": 5,
        "bounds_type": (3 if outer else 2) if box else 0, "bounds_align": 0, "bounds": {"x": w, "y": h},
        "bounds_crop": outer, "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0,
        "group_item_backup": False, "scale_filter": "area" if box else "disable",
        "blend_method": "default", "blend_type": "normal", "private_settings": {},
    }


def scene(name, sources):
    """sources: (source, box, outer) tuples, bottom layer first."""
    items = [item(s, i + 1, box, outer) for i, (s, box, outer) in enumerate(sources)]
    return source(name, "scene", {"id_counter": len(items), "custom_size": False, "items": items})


def browser(name, page):
    return source(name, "browser_source", {
        "is_local_file": True, "local_file": str(OVERLAY / page), "width": 1920, "height": 1080, "css": "",
    })


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", required=True, type=pathlib.Path)
    ap.add_argument("--devices", type=pathlib.Path, help="existing OBS scene collection to copy device settings from")
    args = ap.parse_args()

    devices = {}
    if args.devices:
        print(f"Reading device settings from {args.devices}")
        old_collection = json.loads(args.devices.read_text())
        # global audio devices live in their own keys, not in "sources"
        old_sources = old_collection["sources"] + [v for k, v in old_collection.items() if "AudioDevice" in k]
        old = {s["name"]: s["settings"] for s in old_sources}
        devices = {k: old.get(v, {}) for k, v in DEVICE_NAMES.items()}
        print("  matched: " + (", ".join(k for k, v in devices.items() if v) or "none"))

    layouts = json.loads((OVERLAY / "layouts.js").read_text().split("window.LAYOUTS =", 1)[1].strip().rstrip(";"))

    screen = source("Schermo speaker", "macos-avcapture-fast", devices.get("screen", {}))
    camera = source("Camera", "macos-avcapture", devices.get("camera", {}))
    # tested on a past recording: Speex lowers the hiss without touching the voice, gain and limiter
    # bring it near YouTube's loudness. RNNoise and the compressor clipped words: don't use them.
    mic = source("Microfono", "coreaudio_input_capture", devices.get("mic", {}) or {"device_id": "default"}, filters=[
        filt("Noise suppression", "noise_suppress_filter_v2", {"method": "speex", "suppress_level": -10}),
        filt("Gain", "gain_filter", {"db": 4.0}),
        filt("Limiter", "limiter_filter", {"threshold": -2.0, "release_time": 60}),
    ])
    # the speaker laptop's audio, for videos in a talk: unmute when needed
    screen_audio = source("Audio speaker", "coreaudio_input_capture", devices.get("screen_audio", {}) or {"device_id": "default"}, muted=True)

    pages = {name: browser(f"Overlay {name}", page) for name, page in [
        ("Inizio", "inizio.html"), ("Talk", "talk.html"), ("Talk 4:3", "talk-4x3.html"), ("Live", "live.html"),
        ("Big camera", "big-camera.html"), ("Panel", "panel.html"), ("Pausa", "pausa.html"), ("Fine", "fine.html"),
    ]}

    def talk_scene(name, layout):
        L = layouts[layout]
        return scene(name, [(pages[name], None, False), (screen, L["screen"], False), (camera, L["camera"], True)])

    scenes = [
        scene("Inizio", [(pages["Inizio"], None, False)]),
        talk_scene("Talk", "talk"),
        talk_scene("Talk 4:3", "talk-4x3"),
        scene("Demo", [(pages["Live"], None, False), (screen, layouts["live"]["screen"], False)]),
        scene("Solo camera", [(pages["Live"], None, False), (camera, layouts["live"]["screen"], True)]),
        scene("Big camera", [(pages["Big camera"], None, False), (camera, layouts["big-camera"]["camera"], True), (screen, layouts["big-camera"]["screen"], False)]),
        scene("Panel", [(pages["Panel"], None, False), (camera, layouts["panel"]["camera"], True)]),
        scene("Pausa", [(pages["Pausa"], None, False)]),
        scene("Fine", [(pages["Fine"], None, False)]),
    ]

    collection = {
        "name": "Mantova Dev",
        "AuxAudioDevice1": mic,
        "AuxAudioDevice2": screen_audio,
        "sources": [screen, camera, *pages.values(), *scenes],
        "scene_order": [{"name": s["name"]} for s in scenes],
        "current_scene": "Inizio",
        "version": 2,
    }
    print(f"Writing {args.output}")
    args.output.write_text(json.dumps(collection, indent=2))


if __name__ == "__main__":
    main()
