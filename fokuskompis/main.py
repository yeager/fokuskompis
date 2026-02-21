"""Fokuskompis — Focus & Task Manager for ADHD+Autism."""

import gettext
import json
import locale
import math
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from fokuskompis import __version__

# i18n
try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    pass

LOCALE_DIR = None
for d in [
    Path(__file__).parent.parent / "po",
    Path("/usr/share/locale"),
    Path("/usr/local/share/locale"),
]:
    if d.is_dir():
        LOCALE_DIR = d
        break

locale.bindtextdomain("fokuskompis", str(LOCALE_DIR) if LOCALE_DIR else None)
gettext.bindtextdomain("fokuskompis", str(LOCALE_DIR) if LOCALE_DIR else None)
gettext.textdomain("fokuskompis")
_ = gettext.gettext

APP_ID = "se.danielnylander.fokuskompis"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "fokuskompis"


def _load_settings():
    path = CONFIG_DIR / "settings.json"
    defaults = {
        "pomodoro_work": 25,
        "pomodoro_break": 5,
        "pomodoro_long_break": 15,
        "sessions_before_long": 4,
        "sound_enabled": True,
        "tasks": [],
        "parked_thoughts": [],
    }
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            defaults.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


def _save_settings(settings):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_DIR / "settings.json", "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def _speak(text):
    """TTS: try Piper first, fall back to espeak-ng."""
    def _do():
        # Try piper
        if shutil.which("piper"):
            try:
                p = subprocess.Popen(
                    ["piper", "--model", "sv_SE-nst-medium", "--output-raw"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                )
                raw, _ = p.communicate(text.encode(), timeout=10)
                if raw and shutil.which("aplay"):
                    a = subprocess.Popen(
                        ["aplay", "-r", "22050", "-f", "S16_LE", "-q"],
                        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
                    )
                    a.communicate(raw, timeout=10)
                    return
            except Exception:
                pass
        # Fallback: espeak-ng
        if shutil.which("espeak-ng"):
            try:
                subprocess.run(
                    ["espeak-ng", "-v", "sv", text],
                    timeout=10, capture_output=True
                )
            except Exception:
                pass
    threading.Thread(target=_do, daemon=True).start()


class TaskItem:
    """A single task with optional sub-steps."""
    def __init__(self, title, steps=None, done=False):
        self.title = title
        self.steps = steps or []
        self.current_step = 0
        self.done = done

    def to_dict(self):
        return {"title": self.title, "steps": self.steps, "done": self.done}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("title", ""), d.get("steps", []), d.get("done", False))


class TimerWidget(Gtk.DrawingArea):
    """Circular countdown timer."""

    def __init__(self):
        super().__init__()
        self.total_seconds = 0
        self.remaining = 0
        self.running = False
        self._tick_id = None
        self.set_content_width(200)
        self.set_content_height(200)
        self.set_draw_func(self._draw)

    def _draw(self, area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 10

        # Background circle
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.2)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()

        # Progress arc
        if self.total_seconds > 0:
            fraction = self.remaining / self.total_seconds
            cr.set_source_rgba(0.2, 0.6, 1.0, 0.8)
            cr.set_line_width(8)
            start = -math.pi / 2
            end = start + fraction * 2 * math.pi
            cr.arc(cx, cy, radius - 4, start, end)
            cr.stroke()

        # Time text
        mins = int(self.remaining) // 60
        secs = int(self.remaining) % 60
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_font_size(36)
        text = f"{mins:02d}:{secs:02d}"
        extents = cr.text_extents(text)
        cr.move_to(cx - extents.width / 2, cy + extents.height / 2)
        cr.show_text(text)

    def start(self, seconds):
        self.total_seconds = seconds
        self.remaining = seconds
        self.running = True
        if self._tick_id:
            GLib.source_remove(self._tick_id)
        self._last_tick = time.monotonic()
        self._tick_id = GLib.timeout_add(100, self._tick)

    def _tick(self):
        if not self.running:
            return False
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        self.remaining = max(0, self.remaining - dt)
        self.queue_draw()
        if self.remaining <= 0:
            self.running = False
            self.get_root().timer_finished()
            return False
        return True

    def pause(self):
        self.running = False

    def resume(self):
        if self.remaining > 0:
            self.running = True
            self._last_tick = time.monotonic()
            self._tick_id = GLib.timeout_add(100, self._tick)

    def reset(self):
        self.running = False
        self.remaining = 0
        self.total_seconds = 0
        self.queue_draw()


class MainWindow(Adw.ApplicationWindow):
    """Main application window."""

    def __init__(self, app):
        super().__init__(application=app, title=_("Fokuskompis"))
        self.set_default_size(500, 700)
        self.settings = _load_settings()
        self.tasks = [TaskItem.from_dict(t) for t in self.settings.get("tasks", [])]
        self.parked = list(self.settings.get("parked_thoughts", []))
        self.pomodoro_count = 0
        self.on_break = False

        # Main layout
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        # Header bar
        header = Adw.HeaderBar()
        self.main_box.append(header)

        # Menu
        menu = Gio.Menu()
        menu.append(_("Preferences"), "app.preferences")
        menu.append(_("Keyboard Shortcuts"), "app.shortcuts")
        menu.append(_("About Fokuskompis"), "app.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(menu_btn)

        # Add task button
        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("New Task"))
        add_btn.connect("clicked", self._on_add_task)
        header.pack_start(add_btn)

        # View stack
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcherBar()
        switcher.set_stack(self.stack)
        switcher.set_reveal(True)

        # Focus page
        focus_page = self._build_focus_page()
        self.stack.add_titled(focus_page, "focus", _("Focus"))
        self.stack.get_page(focus_page).set_icon_name("focus-mode-symbolic")

        # Tasks page
        tasks_page = self._build_tasks_page()
        self.stack.add_titled(tasks_page, "tasks", _("Tasks"))
        self.stack.get_page(tasks_page).set_icon_name("view-list-symbolic")

        # Parked thoughts page
        parked_page = self._build_parked_page()
        self.stack.add_titled(parked_page, "parked", _("Parked"))
        self.stack.get_page(parked_page).set_icon_name("user-idle-symbolic")

        self.main_box.append(self.stack)
        self.main_box.append(switcher)

        self._update_focus_view()

    def _build_focus_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_top(20)
        page.set_margin_bottom(20)
        page.set_margin_start(20)
        page.set_margin_end(20)

        # Current task label
        self.focus_label = Gtk.Label(label=_("No tasks yet"))
        self.focus_label.add_css_class("title-1")
        self.focus_label.set_wrap(True)
        self.focus_label.set_justify(Gtk.Justification.CENTER)
        page.append(self.focus_label)

        # Step indicator
        self.step_label = Gtk.Label()
        self.step_label.add_css_class("dim-label")
        page.append(self.step_label)

        # Timer
        self.timer = TimerWidget()
        page.append(self.timer)

        # Timer buttons
        timer_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.start_btn = Gtk.Button(label=_("Start"))
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.add_css_class("pill")
        self.start_btn.connect("clicked", self._on_start_timer)
        timer_box.append(self.start_btn)

        self.pause_btn = Gtk.Button(label=_("Pause"))
        self.pause_btn.add_css_class("pill")
        self.pause_btn.set_sensitive(False)
        self.pause_btn.connect("clicked", self._on_pause_timer)
        timer_box.append(self.pause_btn)

        reset_btn = Gtk.Button(label=_("Reset"))
        reset_btn.add_css_class("pill")
        reset_btn.connect("clicked", self._on_reset_timer)
        timer_box.append(reset_btn)
        page.append(timer_box)

        # Task action buttons
        action_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        action_box.set_margin_top(12)

        done_btn = Gtk.Button(label=_("✓ Done"))
        done_btn.add_css_class("success")
        done_btn.add_css_class("pill")
        done_btn.connect("clicked", self._on_done)
        action_box.append(done_btn)

        skip_btn = Gtk.Button(label=_("Skip →"))
        skip_btn.add_css_class("pill")
        skip_btn.connect("clicked", self._on_skip)
        action_box.append(skip_btn)

        park_btn = Gtk.Button(label=_("💭 Park Thought"))
        park_btn.add_css_class("pill")
        park_btn.connect("clicked", self._on_park_thought)
        action_box.append(park_btn)

        page.append(action_box)

        # Reward label (hidden by default)
        self.reward_label = Gtk.Label()
        self.reward_label.set_visible(False)
        page.append(self.reward_label)

        return page

    def _build_tasks_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.task_list = Gtk.ListBox()
        self.task_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.task_list.add_css_class("boxed-list")
        self.task_list.set_margin_start(12)
        self.task_list.set_margin_end(12)
        self.task_list.set_margin_top(12)
        scroll.set_child(self.task_list)
        page.append(scroll)
        self._refresh_task_list()
        return page

    def _build_parked_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Add parked thought inline
        add_box = Gtk.Box(spacing=8)
        add_box.set_margin_start(12)
        add_box.set_margin_end(12)
        add_box.set_margin_top(12)
        self.parked_entry = Gtk.Entry(placeholder_text=_("Quick thought…"), hexpand=True)
        self.parked_entry.connect("activate", self._on_add_parked)
        add_box.append(self.parked_entry)
        add_parked_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_parked_btn.connect("clicked", self._on_add_parked)
        add_box.append(add_parked_btn)
        page.append(add_box)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.parked_list = Gtk.ListBox()
        self.parked_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.parked_list.add_css_class("boxed-list")
        self.parked_list.set_margin_start(12)
        self.parked_list.set_margin_end(12)
        self.parked_list.set_margin_top(8)
        scroll.set_child(self.parked_list)
        page.append(scroll)
        self._refresh_parked_list()
        return page

    def _current_task(self):
        for t in self.tasks:
            if not t.done:
                return t
        return None

    def _update_focus_view(self):
        task = self._current_task()
        if task:
            self.focus_label.set_text(task.title)
            if task.steps:
                step_num = min(task.current_step + 1, len(task.steps))
                self.step_label.set_text(
                    _("Step {current} of {total}: {step}").format(
                        current=step_num, total=len(task.steps),
                        step=task.steps[task.current_step] if task.current_step < len(task.steps) else ""
                    )
                )
                self.step_label.set_visible(True)
            else:
                self.step_label.set_visible(False)
        else:
            self.focus_label.set_text(_("All done! 🎉"))
            self.step_label.set_visible(False)
        self.reward_label.set_visible(False)

    def _on_add_task(self, *_):
        dialog = Adw.MessageDialog(transient_for=self, heading=_("New Task"))
        dialog.set_body(_("Enter task title and optional steps (one per line):"))

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(12)
        content.set_margin_end(12)

        title_entry = Gtk.Entry(placeholder_text=_("Task title"))
        content.append(title_entry)

        steps_label = Gtk.Label(label=_("Steps (optional, one per line):"), xalign=0)
        steps_label.add_css_class("dim-label")
        content.append(steps_label)

        steps_view = Gtk.TextView()
        steps_view.set_wrap_mode(Gtk.WrapMode.WORD)
        steps_frame = Gtk.Frame()
        steps_frame.set_child(steps_view)
        steps_frame.set_size_request(-1, 100)
        content.append(steps_frame)

        dialog.set_extra_child(content)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")

        def on_response(d, response):
            if response == "add":
                title = title_entry.get_text().strip()
                if title:
                    buf = steps_view.get_buffer()
                    steps_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
                    steps = [s.strip() for s in steps_text.split("\n") if s.strip()]
                    task = TaskItem(title, steps)
                    self.tasks.append(task)
                    self._save_tasks()
                    self._refresh_task_list()
                    self._update_focus_view()
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_done(self, *_):
        task = self._current_task()
        if not task:
            return
        if task.steps and task.current_step < len(task.steps) - 1:
            task.current_step += 1
        else:
            task.done = True
            self.reward_label.set_text(_("⭐ Great job! Task completed! ⭐"))
            self.reward_label.set_visible(True)
            if self.settings.get("sound_enabled", True):
                _speak(_("Well done!"))
        self._save_tasks()
        self._refresh_task_list()
        self._update_focus_view()

    def _on_skip(self, *_):
        task = self._current_task()
        if not task:
            return
        # Move to end
        self.tasks.remove(task)
        self.tasks.append(task)
        self._save_tasks()
        self._refresh_task_list()
        self._update_focus_view()

    def _on_park_thought(self, *_):
        dialog = Adw.MessageDialog(transient_for=self, heading=_("Park a Thought"))
        dialog.set_body(_("Write it down and get back to focus:"))
        entry = Gtk.Entry(placeholder_text=_("What's on your mind?"))
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("park", _("Park It"))
        dialog.set_response_appearance("park", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "park":
                thought = entry.get_text().strip()
                if thought:
                    self.parked.append(thought)
                    self._save_tasks()
                    self._refresh_parked_list()
            d.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_add_parked(self, *_):
        text = self.parked_entry.get_text().strip()
        if text:
            self.parked.append(text)
            self.parked_entry.set_text("")
            self._save_tasks()
            self._refresh_parked_list()

    def _on_start_timer(self, *_):
        if self.timer.remaining > 0 and not self.timer.running:
            self.timer.resume()
        else:
            mins = self.settings.get("pomodoro_work", 25)
            if self.on_break:
                if self.pomodoro_count % self.settings.get("sessions_before_long", 4) == 0:
                    mins = self.settings.get("pomodoro_long_break", 15)
                else:
                    mins = self.settings.get("pomodoro_break", 5)
            self.timer.start(mins * 60)
        self.start_btn.set_sensitive(False)
        self.pause_btn.set_sensitive(True)

    def _on_pause_timer(self, *_):
        self.timer.pause()
        self.start_btn.set_sensitive(True)
        self.pause_btn.set_sensitive(False)

    def _on_reset_timer(self, *_):
        self.timer.reset()
        self.start_btn.set_sensitive(True)
        self.pause_btn.set_sensitive(False)

    def timer_finished(self):
        self.start_btn.set_sensitive(True)
        self.pause_btn.set_sensitive(False)
        if self.on_break:
            self.on_break = False
            if self.settings.get("sound_enabled", True):
                _speak(_("Time to focus!"))
        else:
            self.pomodoro_count += 1
            self.on_break = True
            if self.settings.get("sound_enabled", True):
                _speak(_("Break time!"))

    def _refresh_task_list(self):
        while True:
            row = self.task_list.get_row_at_index(0)
            if row is None:
                break
            self.task_list.remove(row)

        for i, task in enumerate(self.tasks):
            row = Adw.ActionRow(title=task.title)
            if task.done:
                row.add_css_class("dim-label")
                row.set_subtitle(_("Completed"))
            elif task.steps:
                row.set_subtitle(_("{n} steps").format(n=len(task.steps)))

            # Delete button
            del_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", self._on_delete_task, i)
            row.add_suffix(del_btn)

            self.task_list.append(row)

    def _refresh_parked_list(self):
        while True:
            row = self.parked_list.get_row_at_index(0)
            if row is None:
                break
            self.parked_list.remove(row)

        for i, thought in enumerate(self.parked):
            row = Adw.ActionRow(title=thought)
            # Convert to task button
            to_task_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                                     tooltip_text=_("Convert to task"))
            to_task_btn.add_css_class("flat")
            to_task_btn.connect("clicked", self._on_parked_to_task, i)
            row.add_suffix(to_task_btn)
            # Delete
            del_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", self._on_delete_parked, i)
            row.add_suffix(del_btn)
            self.parked_list.append(row)

    def _on_delete_task(self, btn, index):
        if 0 <= index < len(self.tasks):
            self.tasks.pop(index)
            self._save_tasks()
            self._refresh_task_list()
            self._update_focus_view()

    def _on_delete_parked(self, btn, index):
        if 0 <= index < len(self.parked):
            self.parked.pop(index)
            self._save_tasks()
            self._refresh_parked_list()

    def _on_parked_to_task(self, btn, index):
        if 0 <= index < len(self.parked):
            thought = self.parked.pop(index)
            self.tasks.append(TaskItem(thought))
            self._save_tasks()
            self._refresh_task_list()
            self._refresh_parked_list()
            self._update_focus_view()

    def _save_tasks(self):
        self.settings["tasks"] = [t.to_dict() for t in self.tasks]
        self.settings["parked_thoughts"] = self.parked
        _save_settings(self.settings)


class FokuskompisApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect("activate", self._on_activate)

    def _on_activate(self, *_):
        win = self.props.active_window
        if not win:
            win = MainWindow(self)

        # Actions
        self._create_action("about", self._on_about)
        self._create_action("preferences", self._on_preferences)
        self._create_action("shortcuts", self._on_shortcuts)

        # Keyboard shortcuts
        self.set_accels_for_action("app.quit", ["<Control>q"])

        quit_action = Gio.SimpleAction(name="quit")
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)

        win.present()

    def _create_action(self, name, callback):
        action = Gio.SimpleAction(name=name)
        action.connect("activate", callback)
        self.add_action(action)

    def _on_about(self, *_):
        dialog = Adw.AboutDialog(
            application_name=_("Fokuskompis"),
            application_icon=APP_ID,
            version=__version__,
            developer_name="Daniel Nylander",
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/yeager/fokuskompis",
            issue_url="https://github.com/yeager/fokuskompis/issues",
            developers=["Daniel Nylander <daniel@danielnylander.se>"],
            copyright="© 2026 Daniel Nylander",
            comments=_("Focus & task manager for people with ADHD and autism"),
        )
        dialog.present(self.props.active_window)

    def _on_preferences(self, *_):
        win = self.props.active_window
        prefs = Adw.PreferencesWindow(title=_("Preferences"), transient_for=win)
        page = Adw.PreferencesPage(title=_("Timer"))

        group = Adw.PreferencesGroup(title=_("Pomodoro Timer"))

        settings = _load_settings()

        work_row = Adw.SpinRow.new_with_range(1, 60, 1)
        work_row.set_title(_("Work duration (minutes)"))
        work_row.set_value(settings.get("pomodoro_work", 25))
        group.add(work_row)

        break_row = Adw.SpinRow.new_with_range(1, 30, 1)
        break_row.set_title(_("Break duration (minutes)"))
        break_row.set_value(settings.get("pomodoro_break", 5))
        group.add(break_row)

        long_break_row = Adw.SpinRow.new_with_range(1, 60, 1)
        long_break_row.set_title(_("Long break duration (minutes)"))
        long_break_row.set_value(settings.get("pomodoro_long_break", 15))
        group.add(long_break_row)

        sound_row = Adw.SwitchRow(title=_("Sound notifications"))
        sound_row.set_active(settings.get("sound_enabled", True))
        group.add(sound_row)

        page.add(group)
        prefs.add(page)

        def on_close(*_):
            settings["pomodoro_work"] = int(work_row.get_value())
            settings["pomodoro_break"] = int(break_row.get_value())
            settings["pomodoro_long_break"] = int(long_break_row.get_value())
            settings["sound_enabled"] = sound_row.get_active()
            _save_settings(settings)
            if hasattr(win, 'settings'):
                win.settings = settings

        prefs.connect("close-request", on_close)
        prefs.present()

    def _on_shortcuts(self, *_):
        dialog = Adw.MessageDialog(
            transient_for=self.props.active_window,
            heading=_("Keyboard Shortcuts"),
            body=_(
                "Ctrl+Q — Quit\n"
                "Ctrl+N — New task\n"
                "Space — Start/pause timer"
            ),
        )
        dialog.add_response("close", _("Close"))
        dialog.present()


def main():
    app = FokuskompisApp()
    return app.run()


if __name__ == "__main__":
    main()
