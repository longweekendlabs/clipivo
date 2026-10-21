# Clipivo

**Find the moments that matter.**

Clipivo cuts every appearance of one person out of your videos, automatically. Give it a few photos of the person, press Start, and it scans the whole video, finds every time they are on screen and saves those moments as one stitched video or as separate clips. An hour-long broadcast becomes the few minutes you actually wanted. It also works as a plain, precise manual cutter. Everything runs on your own machine.

## Pick who, pick how, press Start

The right-hand pane has three steps: **Who**, **Output** and **Start**. Choose a saved person, or add a few screenshots of someone new, or grab the frame on screen. Choose one stitched video or separate clips, where to save, and how to name the files. Then press Start.

Clipivo scans, saves and checks the result in the background while the window stays free. Progress shows in the pane, every appearance is marked on the timeline, and the clips appear in a list where you can untick, trim and save again. When it is done, Open folder takes you to the file.

## Saved people and one-time people

Save people you look for often in People: add photos one by one, a whole folder, or the current video frame. Five to fifteen varied photos work best. Clipivo rejects images with no face, warns about photos that do not look like the others, and lets you test a still photo against a person before scanning a video. Saved people's photos are stored encrypted on your computer.

For someone you only need once, add their screenshots straight in the Who step. One-time photos stay in memory and are gone when Clipivo closes.

## Finds them across the room, not just in close-up

Clipivo checks a frame every two seconds across the complete video with a high-resolution face detector, so a presenter seen from across the studio counts as well as a close-up. It then looks frame by frame around every entrance and exit so clips start and end where the person does. Matching is by face, so a different outfit in another video does not matter.

## Original quality, untouched

By default Clipivo copies the original video stream without re-encoding it, so every frame in the result is identical to the source and saving takes seconds. Each clip starts at the keyframe just before the person appears, usually a second or a few early, and clips that then overlap are merged so nothing repeats.

When you need cuts on the exact frame, choose Exact frames. It re-encodes at high quality with H.264, on an NVIDIA card with NVENC or on the CPU. Either way, everything Start saves is checked for the expected length and decoded through to its last frames before it is called done.

## Batch: many videos, many people, unattended

Drop videos or whole folders into Batch. Every video has its own Who column, and Clipivo looks only for the people listed there, so one queue can hold three videos for one person and five for another. Start will not run while a video has nobody chosen.

Choose what happens to each original once its results are saved and checked: leave it, move it into a "Clipivo done" folder beside it, move it to a folder you choose, or move it to the Trash. Nothing is moved for a video that failed, was cancelled, or where someone was not found. The queue pauses, cancels safely and survives a restart.

## Names you choose

Name results after the video with the date, exactly like the video, or with your own text plus the video's ID, with or without the date. The ID is read from downloader names such as `Title-gvj96ngMgwY` or `Title [id]`, and a live example shows the name before you start. Separate clips are numbered.

## An Avidemux-style filter chain

An ordered chain you build yourself: Crop, Rotate, Vertical flip and Frame rate under Transformation, Contrast and Color balance under Colors.

Crop gives you a shaded on-picture selection with draggable edges and corners, guides, and numeric margins that stay in sync. Contrast covers 50 to 150% contrast, -127 to 127 brightness and a live histogram. Color Balance gives shadows, midtones and highlights their own hue, chroma, luma and saturation. Preview the chain as input, filtered output, or both compared, and play the filtered preview from where it sits or only the A to B selection.

![Clipivo's crop filter editor with numeric margins, guides and draggable overlay handles](docs/screenshots/filters.png)

## Or just cut it by hand

Open or drop an MP4, MKV, WebM, MOV, AVI or M4V and it appears at once. Playback runs through libVLC, so anything VLC can decode plays here. Seek by timeline, frame or keyframe. Press `A` at the start, `B` at the end, `Ctrl+Enter` to add the range, and save. `Ctrl+P` saves the current frame as a PNG and `Ctrl+W` closes the video.

## Status

Fedora Linux. No public release yet. Private build for personal use. Not for distribution.

## Feedback

[Open an issue](https://github.com/longweekendlabs/clipivo/issues) for a bug or a request.

## License

All rights reserved. See [LICENSE](LICENSE).

© 2026 Long Weekend Labs

---

Made with ♥ by **[Long Weekend Labs](https://github.com/longweekendlabs)**
