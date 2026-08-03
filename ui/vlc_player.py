from __future__ import annotations

from pathlib import Path
from time import monotonic

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QWidget


class VlcPlaybackController(QObject):
    """Small Qt-facing wrapper around libVLC's Linux playback engine.

    Clipivo keeps PyAV for deterministic paused-frame work. libVLC is used only
    for continuous playback, where its dav1d decoder handles AV1 reliably and
    its native video output avoids copying every frame through Python.
    """

    positionChanged = Signal(int)
    playbackStateChanged = Signal(bool)
    errorOccurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: Path | None = None
        self._surface: QWidget | None = None
        self._pending_position = 0
        self._seek_target: int | None = None
        self._seek_attempts = 0
        self._next_seek_attempt = 0.0
        self._playing = False
        self._last_position = -1
        self._reported_error = False
        self._previewing = False
        self._preview_request_at = 0.0
        self._preview_settled_at: float | None = None
        self._muted = False
        self._instance = None
        self._player = None

        # Unit tests use Qt's offscreen plugin, where no embeddable native
        # surface exists. Keep the same API without starting a media backend.
        if QApplication.platformName() == "offscreen":
            self._poll_timer = None
            return

        try:
            import vlc

            self._vlc = vlc
            self._instance = vlc.Instance(
                "--quiet",
                "--verbose=-1",
                "--no-video-title-show",
                "--no-osd",
                "--no-stats",
                "--codec=dav1d,avcodec",
                "--avcodec-hw=none",
                "--file-caching=300",
                "--vout=xcb_x11",
            )
            self._player = self._instance.media_player_new()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Clipivo's Linux player could not start. Install VLC and the python-vlc package. "
                f"[{type(exc).__name__}: {exc}]"
            ) from exc

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(25)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    def setVideoOutput(self, surface: QWidget) -> None:
        self._surface = surface
        if self._player is None:
            return
        surface.setAttribute(Qt.WA_NativeWindow, True)
        surface.setAttribute(Qt.WA_OpaquePaintEvent, True)
        surface.winId()
        self._player.set_xwindow(int(surface.winId()))
        self._player.video_set_mouse_input(False)
        self._player.video_set_key_input(False)

    def setSource(self, source: Path) -> None:
        self.stop()
        self._source = Path(source)
        self._pending_position = 0
        self._seek_target = None
        self._last_position = -1
        self._reported_error = False
        if self._player is None:
            return
        media = self._instance.media_new_path(str(self._source))
        self._player.set_media(media)
        if self._surface is not None:
            self.setVideoOutput(self._surface)

    def setPosition(self, milliseconds: int) -> None:
        self._pending_position = max(0, int(milliseconds))
        if self._player is not None and self._playing:
            self._begin_seek(self._pending_position)

    def position(self) -> int:
        if self._player is not None:
            value = self._player.get_time()
            if value >= 0:
                return int(value)
        return self._pending_position

    def play(self) -> None:
        if not self._source:
            return
        if self._player is None:
            self._set_playing(True)
            return
        if self._surface is not None:
            self.setVideoOutput(self._surface)
        if self._previewing:
            self._player.audio_set_mute(self._muted)
        self._previewing = False
        requested_position = self._pending_position
        result = self._player.play()
        if result == -1:
            self.errorOccurred.emit("VLC could not start playback for this video.")
            return
        self._set_playing(True)
        self._begin_seek(requested_position)

    def pause(self) -> None:
        was_previewing = self._previewing
        self._previewing = False
        if self._player is not None:
            current = self._player.get_time()
            if current >= 0:
                self._pending_position = int(current)
            self._player.set_pause(1)
            if was_previewing:
                self._player.audio_set_mute(self._muted)
        self._set_playing(False)

    def stop(self) -> None:
        was_previewing = self._previewing
        self._previewing = False
        if self._player is not None:
            self._player.stop()
            if was_previewing:
                self._player.audio_set_mute(self._muted)
        self._pending_position = 0
        self._seek_target = None
        self._set_playing(False)

    def isPlaying(self) -> bool:
        return self._playing

    def beginPreview(self, milliseconds: int) -> None:
        """Start native, coalesced seek preview without reporting playback."""
        if not self._source:
            return
        self._previewing = True
        self._preview_settled_at = None
        self._set_playing(False)
        if self._player is not None:
            self._player.audio_set_mute(True)
        self.previewPosition(milliseconds)

    def previewPosition(self, milliseconds: int) -> None:
        self._pending_position = max(0, int(milliseconds))
        self._preview_request_at = monotonic()
        self._preview_settled_at = None
        self._begin_seek(self._pending_position)
        if self._player is None or not self._previewing:
            return
        state = self._player.get_state()
        if state == self._vlc.State.Paused:
            # VLC 3 does not reliably repaint a paused X11 video surface after
            # set_time(). Briefly resume it for each coalesced drag request.
            self._player.set_pause(0)
        elif state not in (
            self._vlc.State.Playing,
            self._vlc.State.Opening,
            self._vlc.State.Buffering,
        ):
            self._player.play()

    def endPreview(self) -> None:
        self._previewing = False
        self._preview_settled_at = None
        self._seek_target = None
        if self._player is not None:
            current = self._player.get_time()
            if current >= 0:
                self._pending_position = int(current)
            self._player.set_pause(1)
            self._player.audio_set_mute(self._muted)
        self._set_playing(False)

    def setVolume(self, value: int) -> None:
        if self._player is not None:
            self._player.audio_set_volume(max(0, min(100, int(value))))

    def setMuted(self, muted: bool) -> None:
        self._muted = bool(muted)
        if self._player is not None:
            self._player.audio_set_mute(self._muted or self._previewing)

    def shutdown(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
        if self._player is not None:
            self._player.stop()
            self._player.release()
            self._player = None
        if self._instance is not None:
            self._instance.release()
            self._instance = None

    def _begin_seek(self, milliseconds: int) -> None:
        self._seek_target = max(0, int(milliseconds))
        self._seek_attempts = 0
        self._next_seek_attempt = 0.0

    def _poll(self) -> None:
        if self._player is None:
            return
        state = self._player.get_state()
        backend_playing = state == self._vlc.State.Playing
        logical_playing = backend_playing and not self._previewing
        if logical_playing != self._playing and state not in (self._vlc.State.Opening, self._vlc.State.Buffering):
            self._set_playing(logical_playing)
        current = self._player.get_time()
        can_seek = backend_playing or (self._previewing and state == self._vlc.State.Paused)
        if can_seek and self._seek_target is not None:
            target = self._seek_target
            tolerance_ms = 50 if self._previewing else 750
            if abs(current - target) <= tolerance_ms:
                self._seek_target = None
                if self._previewing:
                    self._preview_settled_at = monotonic()
            elif self._seek_attempts < 8 and monotonic() >= self._next_seek_attempt:
                # set_time commonly returns before a newly opened input can
                # accept a seek. Hold the requested position and retry only
                # after VLC reports Playing instead of accepting its transient
                # zero as the new Clipivo playhead.
                self._player.set_time(target)
                self._seek_attempts += 1
                self._next_seek_attempt = monotonic() + (0.05 if self._previewing else 0.12)
                return
            elif self._seek_attempts >= 8:
                self._seek_target = None
        if current >= 0 and current != self._last_position:
            self._last_position = int(current)
            self._pending_position = self._last_position
            if not self._previewing:
                self.positionChanged.emit(self._last_position)
        if (
            self._previewing
            and backend_playing
            and self._seek_target is None
            and self._preview_settled_at is not None
            and monotonic() - self._preview_settled_at >= 0.06
        ):
            self._player.set_pause(1)
            self._preview_settled_at = None
        if state == self._vlc.State.Error and not self._reported_error:
            self._reported_error = True
            self.errorOccurred.emit("VLC encountered an error while decoding this video.")

    def _set_playing(self, playing: bool) -> None:
        playing = bool(playing)
        if playing == self._playing:
            return
        self._playing = playing
        self.playbackStateChanged.emit(playing)
