# Clipivo

Clipivo is a local-first desktop video cutter for Fedora Linux. The current
manual-first build opens a single source video, provides precise frame and
keyframe navigation, supports editable A/B clip ranges, and exports separate
clips or a stitched compilation.

Clipivo also includes reusable and per-video character setup, automatic
appearance finding, and a background Batch workflow for turning every detected
appearance into separate clips or a per-source character compilation.

## Characters

The People tab supports two local character types:

- Reusable characters are available across imported videos.
- One ad-hoc character can be attached to a specific imported video without
  adding it to the reusable library.

Create a character, then add face photos, import an image folder, or capture the
current video frame. Clipivo rejects unreadable images and images without a
face, warns about multiple faces and inconsistent outliers, and provides a
still-photo match test before video analysis. Five to fifteen varied references
are recommended. Character selections are remembered per source video.

`Batch import` accepts a character-library root where every immediate subfolder
is a reusable character name. Images inside each character folder are discovered
recursively, validated, and encrypted. A folder matching an existing character
merges non-duplicate references instead of replacing it; loose images directly
inside the selected root are ignored.

Reference metadata and image blobs use a Clipivo-only AES-256-GCM store; its key
is held by the Linux OS keyring. Clipivo does not read or write Castivo's
character store. Ad-hoc identities are separated by source-video project ID.

After choosing `Use for this video`, the selected target appears above the clip
list. `Find character` searches the complete video and jumps to the first
confident face match. It begins with a sparse 320px keyframe-only locator; when
that pass misses, Clipivo retries keyframes with the configured high-resolution
detector and only then uses the original careful sequential cadence. A match is
refined locally against normal frames. A successful result is cached
against the video, reference set, and strictness setting, so repeated use and
reopening jump immediately. A not-found message is only shown after all passes
have checked the whole video. After the first hit, the control becomes `Find
next`. Clipivo samples forward until the character has been absent for a
confirmed gap, then searches for the next re-entry; each distinct appearance is
marked on the timeline. This appearance navigator remains available for quick
manual work alongside the automatic Batch range generator.

## Batch workflow

The Batch tab automates the character-clipping workflow across one or many
videos:

1. Add individual videos or recursively add every supported video under a
   folder.
2. Check one or more reusable characters. Clipivo decodes each source once per
   analysis pass and compares detected faces with every selected character.
3. Choose separate appearance clips or one compilation per source and
   character, plus the encoder and audio setting.
4. Optionally check `Review` on a queue row. Clipivo opens the generated A/B
   ranges in the normal Video editor and waits for approval before exporting.
5. Start, pause, or safely cancel the background queue. Foreground playback and
   scrubbing temporarily pause face analysis so the editor stays responsive.

Outputs use `source_YYMMDD_HHMM_SSS[_NNN]_Character.mp4`. Queue state and
settings survive a restart. The shared coarse scan is followed by small local
200 ms boundary scans, so automatic clips start and end at matching frames
without invisible lead-in/out padding. Overlapping ranges are merged before
export instead of duplicating footage.

Automatic exact exports use standard H.264/AAC with a bitrate ceiling derived
from the source video. This prevents compact AV1 sources from expanding several
times in size merely because H.264 was selected. Clipivo checks expected
duration and decodes frames from the end of every result before reporting
success; a failed result is removed and its source is kept.

`Move source to Trash` is off by default and uses
the Linux Trash; it runs only after every selected character produced an output
and every file passed FFprobe validation. No-match, failed, cancelled, partial,
or unreviewed jobs always keep the source.

## Video filters

The Filters tab opens a dedicated filter window with an ordered
Avidemux-style filter chain:

- Transformation: Crop, Rotate, Vertical flip, and Frame rate.
- Colors: Contrast and Color balance.

The popup opens as a compact manager with Categories, Available filters, and
the ordered Active filters chain. Double-clicking an available filter opens its
dedicated settings-and-preview dialog; `OK` adds it to the Active chain and
`Cancel` leaves the chain untouched. Double-clicking an active filter edits it
with the same commit behavior. Preview is not shown in the manager itself. Crop provides
a shaded on-picture selection with draggable edge and corner handles, a movable
frame, guides, and synchronized numeric margins. The active chain can also be
previewed explicitly as Input, Filtered output, or Compare at any source
position. Contrast exposes 50–150% contrast, -127–127 brightness, individual
luma/chroma processing, an MPEG2-to-PC preset, and a live histogram. Color
Balance provides independent shadow, midtone, and highlight hue, chroma, luma,
and saturation controls. Every filter editor can play the filtered preview from
its current position or play only the Video tab's A–B selection; active playback
buttons switch to Pause. Resample FPS keeps duration; Change speed creates synchronized
slow/fast motion. Active filters automatically use exact H.264/AAC export
because stream-copy cannot alter video frames.

## Current manual workflow

1. Open an MP4, MKV, WebM, MOV, AVI, or M4V file.
2. Seek with the timeline or use frame/keyframe navigation.
3. Press `A` at the desired start and `B` at the desired end.
4. Press `Ctrl+Enter` to add the A–B range.
5. Enable or disable ranges in the right pane.
6. `Exact-cut MP4` is selected by default for frame-accurate boundaries. Choose
   `Copy MP4` only when preserving the source stream without re-encoding is more
   important than an exact non-keyframe start.
7. Choose separate clips or a stitched compilation, decide whether to include
   audio, then save.

Single clips and stitched compilations open a normal save-file dialog so the
output filename can be chosen directly. Separate multi-clip exports ask for a
destination folder.

The speaker button and volume slider control preview playback only. The
`Include audio` checkbox controls whether exported files contain an audio track.

Timeline dragging uses coalesced native seeking: Clipivo previews the
newest requested position through the native VLC surface while the mouse moves.
VLC is briefly resumed with audio muted for each request so its paused X11
surface actually repaints, then Clipivo synchronizes one exact PyAV frame when
the mouse is released.

Exact-cut exports use the encoder selected under Settings → Export performance.
`Auto` prefers NVIDIA NVENC and falls back to CPU libx264 if CUDA encoding is
unavailable. Export progress follows encoded media time instead of remaining at
zero until an entire clip completes.

Generated export names use `filename_YYMMDD_HHMM_SSS.mp4`; separate multi-clip
exports append their sequence number. Ctrl+P frame captures use
`filename_YYMMDD_HHMM_SSS--HH:MM:SS.mmm.png`, where the final timestamp is the
position inside the source video.

The source video is never modified. Reviewed clip ranges are stored under the
user's local Clipivo data directory rather than beside the source file.

## Run from source

Requires Python 3.11, FFmpeg/ffprobe, and VLC 3 on Fedora Linux. Clipivo uses
libVLC's dav1d decoder for smooth AV1 playback and PyAV for exact paused-frame
navigation.

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh
```

Optionally open a video directly:

```bash
./run.sh /path/to/video.mkv
```

On Fedora, install the system playback dependency once with `sudo dnf install
vlc`. The Python binding is installed by `requirements.txt` inside Clipivo's
virtual environment.

## Private Fedora RPM

The current private Fedora 44 x86_64 test package is self-contained: it vendors
the tested Python, PySide6, CUDA/ONNX Runtime, and InsightFace environment plus
the local `buffalo_l` models. It still uses Fedora's installed FFmpeg and VLC.
The package is intentionally large and must not be redistributed while the face
model licensing remains unresolved.

Install the local build:

```bash
sudo dnf install ./dist/clipivo-0.2.0-1.local.20260724.fc44.x86_64.rpm
```

Launch it from the application menu or run `clipivo`. Remove only the installed
application with `sudo dnf remove clipivo`; user characters, settings, and queue
state under the normal XDG data/config directories are preserved.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Space` | Play / pause |
| `Shift+Space` | Play only the marked A–B selection |
| `K` | Stop/pause |
| `Left` / `Right` | Previous / next frame |
| `Down` / `Up` | Previous / next keyframe |
| `Home` / `End` | Jump to start / end |
| `A` / `B` | Set A / B marker |
| Double-click `A` / `B` | Also set the opposite marker at the playhead |
| `Ctrl+Enter` | Add the current A–B range |
| `Ctrl+F` | Find the selected character's first appearance |
| `Ctrl+P` | Save the currently displayed source-resolution frame as PNG |
| `Ctrl+W` | Close the Clipivo window |
| `Delete` | Remove the selected clip |

Clipivo performs no cloud processing, account access, or telemetry.

The private development build uses InsightFace `buffalo_l`. Its pretrained
model licensing must be resolved before public or commercial distribution.
