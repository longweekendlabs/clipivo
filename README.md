# Clipivo

**Find the moments that matter.**

[![GitHub Release](https://img.shields.io/github/v/release/longweekendlabs/clipivo?style=flat-square)](https://github.com/longweekendlabs/clipivo/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![Windows, Linux, macOS](https://img.shields.io/badge/Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=flat-square)](#download)

Clipivo cuts every appearance of one person out of your videos, automatically. Give it a few photos of the person, press Start, and it scans the whole video, finds every time they are on screen and saves those moments as one stitched video or as separate clips. An hour-long broadcast becomes the few minutes you actually wanted. It also works as a plain, precise manual cutter. Everything runs on your own machine.

### [Download for Windows, Linux and macOS](https://github.com/longweekendlabs/clipivo/releases/latest)

## Pick who, pick how, press Start

The right-hand pane has three steps: **Who**, **Output** and **Start**. Choose a saved person, or add a few screenshots of someone new, or grab the frame on screen. Choose one stitched video or separate clips, where to save, and how to name the files. Then press Start.

Clipivo scans, saves and checks the result in the background while the window stays free. Progress shows in the pane, every appearance is marked on the timeline, and the clips appear in a list where you can untick, trim and save again. When it is done, Open folder takes you to the file.

## Saved people and one-time people

Save people you look for often in People: add photos one by one, a whole folder, or the current video frame. Five to fifteen varied photos work best. Clipivo rejects images with no face, warns about photos that do not look like the others, and lets you test a still photo against a person before scanning a video. Saved people's photos are stored encrypted on your computer.

Saved photos are encrypted, and Export photos or Export all gives you plain images back, one folder per person.

For someone you only need once, add their screenshots straight in the Who step. One-time photos stay in memory and are gone when Clipivo closes.

## Finds them across the room, not just in close-up

Clipivo checks a frame every two seconds across the complete video with a high-resolution face detector, so a presenter seen from across the studio counts as well as a close-up. It then looks frame by frame around every entrance and exit so clips start and end where the person does. Matching is by face, so a different outfit in another video does not matter.

## Original quality, untouched

By default Clipivo copies the original video stream without re-encoding it, so every frame in the result is identical to the source and saving takes seconds. Each clip starts at the keyframe just before the person appears, usually a second or a few early, and clips that then overlap are merged so nothing repeats.

When you need cuts on the exact frame, choose Exact frames. It re-encodes at high quality with HEVC (H.265), on an NVIDIA card with NVENC or on the CPU. Either way, everything Start saves is checked for the expected length and decoded through to its last frames before it is called done.

## Smaller files when you want them

A Quality slider runs from 50% to 100%. At 100% the original is copied untouched; lower values re-encode on the exact frames into smaller files for videos you only want to keep a glimpse of. Re-encoded video is HEVC (H.265), and it never spends more on the part of the picture it keeps than the source did.

Size keeps the original or limits output to 1440p, 1080p or 720p by the shorter side, so a 4K video in 16:9, 4:3 or portrait keeps its shape and nothing is ever enlarged.

## Just the person, at the size they were

Frame can follow the person instead of keeping the whole picture. In every camera shot Clipivo finds their own part of it: the window or split-screen panel they sit in, with the other guests, the news footage beside them and the background around it left out. In a full-screen studio shot it keeps the picture down to the headline banner and leaves other people out.

Dynamic keeps every shot, whatever its shape. Landscape only, portrait only and square only keep just the shots where the person is seen in that shape. Each shot keeps the person at the size they had in the source, centred on one canvas, so a stitched video never changes size and nothing is blown up.

Following someone through a broadcast is mostly throwing pixels away, so the result is usually far smaller than the source: a 13-minute panel show of 215 MB came out as an 80 MB video of the one guest. Broadcasts published at 60 fps from a 50 fps signal repeat a frame in every six; Clipivo drops the repeats, so the result plays smoothly.

## Batch: many videos, many people, unattended

Drop videos or whole folders into Batch. Every video has its own Who column, and Clipivo looks only for the people listed there, so one queue can hold three videos for one person and five for another. Select any number of videos and tick a person to add them to all of them at once. Start will not run while a video has nobody chosen.

Save results next to each video, into a "Clipivo done" folder beside it, or into a folder you choose. Then choose what happens to each original once its results are saved and checked: leave it, move it into a "Clipivo source" folder beside it, move it to a folder you choose, or move it to the Trash. Adding the same folder again later skips both, so nothing is processed twice. Nothing is moved for a video that failed, was cancelled, or where someone was not found. The list shows each video's length and cover, and while it runs, how long is left for the current video and the whole queue. Scans run at low priority so the rest of your computer stays responsive. The queue pauses, cancels safely and survives a restart.

## Names you choose

When a video carries a cover picture, as YouTube downloads do, every result keeps the same thumbnail and the Batch list shows it.

Name results after the video with the date, exactly like the video, or with your own text plus the video's ID, with or without the date. The ID is read from downloader names such as `Title-gvj96ngMgwY` or `Title [id]`, and a live example shows the name before you start. Separate clips are numbered.

## An Avidemux-style filter chain

An ordered chain you build yourself: Crop, Rotate, Vertical flip and Frame rate under Transformation, Contrast and Color balance under Colors.

Crop gives you a shaded on-picture selection with draggable edges and corners, guides, and numeric margins that stay in sync. Contrast covers 50 to 150% contrast, -127 to 127 brightness and a live histogram. Color Balance gives shadows, midtones and highlights their own hue, chroma, luma and saturation. Preview the chain as input, filtered output, or both compared, and play the filtered preview from where it sits or only the A to B selection.

![Clipivo's crop filter editor with numeric margins, guides and draggable overlay handles](docs/screenshots/filters.png)

## Or just cut it by hand

Open or drop an MP4, MKV, WebM, MOV, AVI or M4V and it appears at once. Playback runs through libVLC, so anything VLC can decode plays here. Seek by timeline, frame or keyframe. Press `A` at the start, `B` at the end, `Ctrl+Enter` to add the range, and save. `Ctrl+P` saves the current frame as a PNG and `Ctrl+W` closes the video.

## Download

Windows, Linux x86_64 and macOS Apple Silicon builds are on the [releases page](https://github.com/longweekendlabs/clipivo/releases/latest): a per-user Windows installer that never asks for administrator rights, a portable zip, rpm, deb and a tarball for Linux, and a dmg for macOS 14 or newer.

The installers stay small. On first launch Clipivo looks for the face models on your computer, for example a copy another app already downloaded, and downloads them only if none is found (191 MB); on Windows and macOS it does the same for ffmpeg. Settings shows what it found and where. Playback uses VLC: the Linux packages install it, and on Windows and macOS Clipivo points you to it if it is missing. Face matching uses your graphics card through DirectML on Windows and Core ML on macOS; on Linux with an NVIDIA card, Settings offers NVIDIA's CUDA libraries as an optional download.

## Feedback

[Open an issue](https://github.com/longweekendlabs/clipivo/issues) for a bug or a request.

## License

MIT, see [LICENSE](LICENSE). No licence key, no trial, no subscription.

The face models Clipivo downloads are InsightFace's buffalo_l pack, published by InsightFace for non-commercial research use, and stay under InsightFace's terms.

© 2026 Long Weekend Labs

---

Made with ♥ by **[Long Weekend Labs](https://github.com/longweekendlabs)**
