# Changelog

## [Unreleased]

## [0.6.1] - 2026-10-21

### Added
- Videos that carry a cover picture, such as YouTube downloads, pass it on: every clip and stitched video Clipivo saves gets the same thumbnail, and the Batch list shows it beside each video.
- Save to has a new choice, a "Clipivo done" folder beside each video, so results collect in one place per folder.

### Changed
- Finished originals now move into a "Clipivo source" folder instead of "Clipivo done", so originals and results never share a folder.
- Add folder and dropped folders skip "Clipivo source" and "Clipivo done", so Clipivo never queues its own results or originals it already finished.

## [0.6.0] - 2026-10-21

### Fixed
- Follow person now follows the person. 0.5.0 chose one crop for a whole clip, so a clip that cut from a split screen to a full-screen shot was cropped between the two, often onto someone else or onto nothing. Each clip is now tracked: the face is checked every 0.4 seconds, camera cuts are found frame by frame, and every shot gets its own crop that pans smoothly when the person moves.
- News tickers, headline boxes and social bars at the bottom are kept out of framed output. Clipivo finds the band of the picture that stays still through a shot and keeps the crop above it, with room for the scrolling crawl on top, as long as head and shoulders still fit.
- Framed output is no longer blown up: it takes the size of the typical crop, capped by Size, so a 1080p source gives a sharp 468x832 portrait rather than a soft 1080x1920 one.
- Clipivo shows its own icon and name in the title bar, task bar and window switcher instead of a generic icon and "python".
- Temporary export segments are kept in Clipivo's cache folder instead of a hidden folder next to your videos, and leftovers from an interrupted export are removed the next time Clipivo starts.

### Added
- Export photos for one saved person, and Export all, one folder per person in the layout Batch import reads. Saved photos are stored encrypted, so this is how to get plain images back out.
- The Video tab shows one progress window with Cancel while it scans or saves, instead of progress in the pane and the status bar at once.

## [0.5.0] - 2026-10-21

### Added
- Size: keep the original size, or limit output to 1440p, 1080p or 720p. The limit applies to the shorter side, so a 16:9 4K video becomes 1920x1080, a 4:3 4K video 1440x1080 and a vertical video 1080x1920. Smaller videos are never enlarged.
- Frame: Whole picture, or Follow person in landscape, portrait or square. Each clip is cropped steadily around where the person's face was found, such as their half of a split screen, and every clip is scaled to the same size so a stitched video never changes shape. Clips marked by hand are cropped from the centre.
- Quality slider from 50% to 100%. 100% copies the original untouched; lower values re-encode on the exact frames into smaller files. Resizing and following a person re-encode at 95% when the slider is at 100%.
- The same Size, Frame and Quality options on the Video tab and in Batch, with one line explaining what will happen.

### Changed
- Face matches now remember where the face was in the frame, and found clips keep a summary of it, so framing can be changed after a scan and saved again.

## [0.4.1] - 2026-10-21

### Added
- Settings has a Default save location. The Video tab and Batch start there each time Clipivo opens, and you can still pick another folder for one run. Until you set it, they start on "Same folder as video".
- The Video tab has the same Original option as Batch: leave the video, move it into "Clipivo done", move it to a folder, or move it to the Trash once the result is saved and checked. A moved video is closed in the editor.

### Known issues
- Original quality starts each clip at the keyframe before the person appears. On files with a keyframe every few seconds, such as YouTube downloads, a stitched video can open with a few seconds of footage before she is on screen. Exact frames avoids this at the cost of re-encoding.

## [0.4.0] - 2026-10-21

### Added
- Original quality is now the default: clips are copied from the source untouched, frame for frame, instead of being re-encoded. Each clip starts at the keyframe just before the person appears, usually a second or a few early, and clips that then overlap are merged so no footage repeats. It is also several times faster to save.
- Each Batch video has its own Who column. Only the people listed there are looked for in that video, and Start refuses to run while any video has nobody chosen. The summary counts videos per person.
- Batch accepts videos and folders dragged in from the file manager. The Video tab opens a video dropped onto it.
- What happens to each original once its outputs are saved and checked: leave it, move it into a "Clipivo done" folder beside it, move it to a folder you choose, or move it to the Trash. Nothing is moved when a video failed, was cancelled, or when someone was not found.
- File naming: video name + date, same as the video, custom name + video ID, or custom name + video ID + date, with a live example. The ID is read from downloader names such as "Title-gvj96ngMgwY" or "Title [id]". With "Same as video" and the original moved away, the output takes the original's name.
- Close video (Ctrl+W), like Avidemux. Close window stays in the File menu; Ctrl+Q quits.
- Rename and Delete person buttons beside a saved person's name, and the same on right-click.

### Changed
- Exact frames now re-encodes at high quality (about VMAF 98 against the source) instead of matching the source's bitrate, which visibly softened the picture (about VMAF 94). Files are larger.
- Videos open at once. The keyframe index used for keyframe stepping was read from the whole file first, which took 15 seconds for an uncached file on a hard drive; it is now built in the background.
- The Video tab no longer picks a saved person for you: it starts on "Choose who to find…".
- Original quality is off while video filters are active, and comes back on when they are removed, without changing your saved choice.


## [0.3.0] - 2026-10-21

### Added
- One simple panel on the Video tab: pick who to find, choose how to save, press Start. Clipivo scans the whole video, saves the result and checks it on its own, while the window stays free to use. Progress, the number of clips found and an Open folder button appear right in the pane.
- Save to "Same folder as video" or a folder you choose, on both the Video tab and Batch. Files are named after the source video with the date and time added, plus the person's name for saved people.
- One-time people: add a few screenshots or grab the frame on screen, use them in Video or Batch, and they are gone when Clipivo closes. Nothing is written to disk.
- Found clips get thumbnails in the clip list, and the timeline marks every appearance.
- Batch shows overall progress in the same panel and can clear finished videos from the list.

### Changed
- Batch now uses the same Who, Output and Start panel as the Video tab. Encoder choice lives in Settings only.
- People is now just your library of saved people. The per-video ad-hoc box and "Use for this video" are gone; old ad-hoc characters stay on disk but are no longer shown.
- Appearance scans check faces at 1280 px, so small faces in wide studio shots are found. A presenter seen only from across the studio was missed at 640 px.
- Reading the video and checking faces now overlap instead of taking turns, which shortens scans without changing what they find.
- Fast copy and exact cut are named for what they do. Clip rows say Found, Found · unsure or Manual.

### Removed
- The Batch "Review" column and the approve-in-editor hand-off. Start does the whole job; open a finished video in the editor if you want to adjust clips and save again.

## [0.2.0] - 2026-07-24

- Added a reproducible private Fedora x86_64 RPM build with a vendored tested runtime, local face models, desktop entry, application icon, and system launcher.
- Removed hidden Batch lead-in/out padding, added 200 ms local entrance/exit refinement, and merged overlapping automatic ranges to prevent duplicate footage.
- Added source-aware H.264 bitrate ceilings so compact AV1 inputs no longer balloon into disproportionately large Batch outputs.
- Added normalized export timestamps, expected-duration verification, and actual tail-frame decoding; invalid generated files are removed and their sources are always kept.
- Added a compact Batch tab for individual videos and recursive folder queues, with persistent per-video enable/review state.
- Added one-pass multi-character face analysis with Rapid, Balanced, and Thorough quality presets plus background progress, pause, and safe cancellation.
- Added automatic appearance grouping, optional manual A/B review handoff, per-appearance or per-source/character compilation exports, and character-aware filenames.
- Added FFprobe output validation and opt-in Linux Trash cleanup that never runs after no-match, partial success, failure, cancellation, or pending review.
- Added Phase 1 reusable characters with encrypted Clipivo-only storage and reference management.
- Added one ad-hoc character target per imported video, isolated from the reusable library.
- Added face-reference validation, outlier warnings, still-photo match testing, GPU preference, and CPU fallback settings.
- Added cancellable local face tasks and a replaceable InsightFace engine interface.
- Added comprehensive tooltips plus visible Clipivo 0.2.0 and Long Weekend Labs branding/About details.
- Consolidated scattered People-page controls into compact contextual toolbars and overflow menus.
- Added recursive batch character import using immediate subfolders as reusable character names, with safe merging and face validation.
- Added a cancellable, cached `Find character` scan using fast coarse sampling plus local refinement across the complete video.
- Added automatic high-resolution and careful fallbacks so a fast-search miss is never reported as a final character result.
- Replaced Qt Multimedia playback on Linux with embedded libVLC/dav1d playback, fixing AV1 video on GPUs without AV1 hardware decode.
- Preserved requested playhead/A-marker positions while VLC opens a source, preventing playback from restarting at zero.
- Suppressed noisy InsightFace provider diagnostics and known dependency deprecation warnings in normal source runs.
- Added coalesced native VLC timeline previews, with one exact PyAV synchronization on mouse release.
- Show overlapping A and B points as one explicit `A/B` timeline marker instead of allowing B to hide A.
- Removed aggressive VLC frame skipping and synchronized seeks to the timestamp of the frame actually displayed.
- Made long-video character location genuinely sparse by decoding keyframes only in both rapid passes, with local normal-frame refinement after a match.
- Added Auto/NVIDIA NVENC/CPU exact-cut export settings, NVENC-to-CPU fallback, continuously drained FFmpeg output, and encoded-time progress.
- Added application-wide Ctrl+P source-frame PNG capture and Ctrl+W window-close shortcuts, with a compact Settings reference.
- Made native VLC scrubbing repaint continuously while dragging by briefly resuming muted playback for each coalesced seek.
- Added distinct `Find next` character appearances by confirming the end of the current presence before searching for re-entry.
- Replaced generated `_clip` names with `YYMMDD_HHMM_SSS` export tags and VLC-style date-plus-video-time PNG names.
- Made timeline scrubbing lightweight by deferring compressed-video seeks until mouse release and avoiding hidden filter-preview frame copies.

- Created the independent Clipivo project and private repository.
- Added the approved Avidemux-style visual shell.
- Added MP4/MKV/WebM/MOV/AVI/M4V probing and keyframe indexing.
- Added playback, timeline seeking, exact frame stepping, and keyframe jumps.
- Added manual A/B ranges, persistent clip review, and source-safe deletion.
- Added visible separate/stitched export selection, source-copy MP4 and exact-cut MP4 modes.
- Added A–B selection playback, preview volume/mute, and optional audio-free exports.
- Compacted the manual editor around the video and replaced the empty character pane with a clips-only sidebar.
- Moved Video, People, and Settings navigation into the top toolbar and removed the left rail.
- Changed single-output exports to a named MP4 save dialog and added opposite-marker double-clicks.
- Added an Avidemux-inspired Filters tab with ordered Transformation and Colors chains.
- Added Crop, Rotate, Vertical Flip, Frame Rate resampling/speed change, and Contrast controls.
- Added seekable input/output/side-by-side filter previews and filtered H.264/AAC export.
- Reworked Filters into a preview-first tab with a compact library/active-chain inspector.
- Added an interactive crop overlay with masked discard regions, draggable edges and corners, movable selection, guides, output dimensions, and synchronized pixel controls.
- Moved filters into a dedicated modeless manager window opened by the Filters tab; it shows Categories, Available filters, and Active filters first, and reveals video preview only for Configure or Preview.
- Changed filter add/edit into dedicated modal editors where OK commits and Cancel discards, matching the Avidemux manager workflow.
- Expanded Contrast with sliders, luma/chroma plane switches, MPEG2-to-PC preset, and histogram preview; added shadow/midtone/highlight Color Balance.
- Added filtered preview Play/Pause and Play A–B controls to every filter editor.
- Kept Original/Filtered switching live during preview playback and made the preview timeline seek on click or drag.
- Made Exact-cut MP4 the default export mode; source-copy remains available as an explicit quality-preserving option.
