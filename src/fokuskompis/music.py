"""Background music player using GStreamer for GTK4 apps."""

import os
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)

# Music files bundled with the app (installed to /usr/share/<app>/music/)
# Falls back to ~/.config/<app>/music/ for user-added tracks
BUNDLED_TRACKS = {
    "satie_gymnopedie1": {
        "file": "satie_gymnopedie1.mp3",
        "title": "Gymnopédie No. 1",
        "composer": "Erik Satie",
    },
    "debussy_clair_de_lune": {
        "file": "debussy_clair_de_lune.mp3",
        "title": "Clair de Lune",
        "composer": "Claude Debussy",
    },
    "bach_air": {
        "file": "bach_air.mp3",
        "title": "Air on the G String",
        "composer": "J.S. Bach",
    },
    "beethoven_moonlight": {
        "file": "beethoven_moonlight.mp3",
        "title": "Moonlight Sonata",
        "composer": "Ludwig van Beethoven",
    },
}


class MusicPlayer:
    """Simple looping background music player."""

    def __init__(self, app_name: str):
        self.app_name = app_name
        self._pipeline = None
        self._volume = 0.3  # Default low volume for background music
        self._playing = False
        self._current_track = None
        self._music_dirs = [
            os.path.join("/usr/share", app_name, "music"),
            os.path.join(
                os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                app_name, "music",
            ),
            os.path.join(
                os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                app_name, "music",
            ),
        ]

    def _find_track(self, filename: str) -> str | None:
        """Find a music file in known directories."""
        for d in self._music_dirs:
            path = os.path.join(d, filename)
            if os.path.isfile(path):
                return path
        return None

    def get_available_tracks(self) -> list[dict]:
        """Return list of available tracks with metadata."""
        tracks = []
        for track_id, info in BUNDLED_TRACKS.items():
            path = self._find_track(info["file"])
            if path:
                tracks.append({
                    "id": track_id,
                    "path": path,
                    "title": info["title"],
                    "composer": info["composer"],
                })
        # Also scan for user-added mp3/ogg files
        for d in self._music_dirs:
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if f.endswith((".mp3", ".ogg", ".opus", ".wav")):
                    already = any(t["path"] == os.path.join(d, f) for t in tracks)
                    if not already:
                        name = os.path.splitext(f)[0].replace("_", " ").title()
                        tracks.append({
                            "id": f,
                            "path": os.path.join(d, f),
                            "title": name,
                            "composer": "",
                        })
        return tracks

    def play_next(self):
        """Play next track in list."""
        tracks = self.get_available_tracks()
        if not tracks:
            return False
        if self._current_track:
            paths = [t["path"] for t in tracks]
            try:
                idx = paths.index(self._current_track)
                next_idx = (idx + 1) % len(paths)
            except ValueError:
                next_idx = 0
        else:
            next_idx = 0
        return self.play(tracks[next_idx]["path"])

    def get_current_track_info(self) -> dict | None:
        """Get info about currently playing track."""
        if not self._current_track:
            return None
        for t in self.get_available_tracks():
            if t["path"] == self._current_track:
                return t
        return None

    def play(self, track_path: str | None = None):
        """Start playing. If no path given, play first available track."""
        if not track_path:
            tracks = self.get_available_tracks()
            if not tracks:
                return False
            track_path = tracks[0]["path"]

        self.stop()

        uri = Gst.filename_to_uri(track_path)
        self._pipeline = Gst.ElementFactory.make("playbin", "musicplayer")
        if not self._pipeline:
            return False

        self._pipeline.set_property("uri", uri)
        self._pipeline.set_property("volume", self._volume)

        # Loop on end-of-stream
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)

        self._pipeline.set_state(Gst.State.PLAYING)
        self._playing = True
        self._current_track = track_path
        return True

    def stop(self):
        """Stop playback."""
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._playing = False
        self._current_track = None

    def pause(self):
        """Pause playback."""
        if self._pipeline and self._playing:
            self._pipeline.set_state(Gst.State.PAUSED)
            self._playing = False

    def resume(self):
        """Resume playback."""
        if self._pipeline and not self._playing:
            self._pipeline.set_state(Gst.State.PLAYING)
            self._playing = True

    def toggle(self):
        """Toggle play/pause."""
        if self._playing:
            self.pause()
        elif self._pipeline:
            self.resume()
        else:
            self.play()

    def set_volume(self, volume: float):
        """Set volume (0.0 - 1.0)."""
        self._volume = max(0.0, min(1.0, volume))
        if self._pipeline:
            self._pipeline.set_property("volume", self._volume)

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def is_playing(self) -> bool:
        return self._playing

    def _on_eos(self, bus, msg):
        """Loop: restart from beginning on end-of-stream."""
        if self._current_track:
            self._pipeline.seek_simple(
                Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0
            )

    def _on_error(self, bus, msg):
        """Handle playback errors gracefully."""
        err, debug = msg.parse_error()
        print(f"Music playback error: {err.message}")
        self.stop()
