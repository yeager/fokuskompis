"""Export functionality for Fokuskompis."""

import csv
import io
import json
from datetime import datetime

import gettext
_ = gettext.gettext

from fokuskompis import __version__

APP_LABEL = _("Focus Companion")
AUTHOR = "Daniel Nylander"
WEBSITE = "www.autismappar.se"

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib


def tasks_to_csv(tasks, parked_thoughts):
    """Export tasks and parked thoughts as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([_("Task"), _("Steps"), _("Done"), _("Date")])
    for t in tasks:
        steps = "; ".join(t.get("steps", []))
        writer.writerow([
            t.get("title", ""),
            steps,
            _("Yes") if t.get("done") else _("No"),
            t.get("date", ""),
        ])
    if parked_thoughts:
        writer.writerow([])
        writer.writerow([_("Parked Thoughts")])
        for thought in parked_thoughts:
            writer.writerow([thought])
    writer.writerow([])
    writer.writerow([f"{APP_LABEL} v{__version__} — {WEBSITE}"])
    return output.getvalue()


def tasks_to_json(tasks, parked_thoughts):
    """Export tasks and parked thoughts as JSON."""
    data = {
        "tasks": tasks,
        "parked_thoughts": parked_thoughts,
        "_exported_by": f"{APP_LABEL} v{__version__}",
        "_author": AUTHOR,
        "_website": WEBSITE,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def export_tasks_pdf(tasks, parked_thoughts, output_path):
    """Export tasks as PDF."""
    try:
        import cairo
    except ImportError:
        try:
            import cairocffi as cairo
        except ImportError:
            return False

    width, height = 595, 842
    surface = cairo.PDFSurface(output_path, width, height)
    ctx = cairo.Context(surface)

    ctx.set_font_size(24)
    ctx.move_to(40, 50)
    ctx.show_text(_("Focus Companion — Tasks"))

    ctx.set_font_size(12)
    ctx.move_to(40, 75)
    ctx.show_text(datetime.now().strftime("%Y-%m-%d"))

    y = 110
    for t in tasks:
        if y > height - 60:
            surface.show_page()
            y = 40

        ctx.set_font_size(16)
        if t.get("done"):
            ctx.set_source_rgb(0.18, 0.76, 0.49)
            ctx.move_to(40, y)
            ctx.show_text("✓ " + t.get("title", ""))
        else:
            ctx.set_source_rgb(0, 0, 0)
            ctx.move_to(40, y)
            ctx.show_text("○ " + t.get("title", ""))
        y += 24

        ctx.set_font_size(11)
        ctx.set_source_rgb(0.5, 0.5, 0.5)
        for step in t.get("steps", []):
            ctx.move_to(60, y)
            ctx.show_text("→ " + step)
            y += 18

        y += 8

    # Parked thoughts
    if parked_thoughts:
        y += 16
        ctx.set_font_size(18)
        ctx.set_source_rgb(0, 0, 0)
        ctx.move_to(40, y)
        ctx.show_text(_("Parked Thoughts"))
        y += 24

        ctx.set_font_size(12)
        ctx.set_source_rgb(0.4, 0.4, 0.4)
        for thought in parked_thoughts:
            if y > height - 40:
                surface.show_page()
                y = 40
            ctx.move_to(60, y)
            ctx.show_text("💭 " + thought)
            y += 20

    # Footer
    ctx.set_font_size(9)
    ctx.set_source_rgb(0.5, 0.5, 0.5)
    footer = f"{APP_LABEL} v{__version__} — {WEBSITE} — {datetime.now().strftime('%Y-%m-%d')}"
    ctx.move_to(40, height - 20)
    ctx.show_text(footer)

    surface.finish()
    return True


def show_export_dialog(window, tasks, parked_thoughts, status_callback=None):
    """Show export dialog."""
    dialog = Adw.AlertDialog.new(
        _("Export Tasks"),
        _("Choose export format:")
    )

    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("csv", _("CSV"))
    dialog.add_response("json", _("JSON"))
    dialog.add_response("pdf", _("PDF"))
    dialog.set_default_response("csv")
    dialog.set_close_response("cancel")

    dialog.connect("response", _on_export_response, window, tasks, parked_thoughts, status_callback)
    dialog.present(window)


def _on_export_response(dialog, response, window, tasks, parked_thoughts, status_callback):
    if response == "cancel":
        return
    if response == "csv":
        _save_text(window, tasks, parked_thoughts, "csv", tasks_to_csv, status_callback)
    elif response == "json":
        _save_text(window, tasks, parked_thoughts, "json", tasks_to_json, status_callback)
    elif response == "pdf":
        _save_pdf(window, tasks, parked_thoughts, status_callback)


def _save_text(window, tasks, parked_thoughts, ext, converter, status_callback):
    dialog = Gtk.FileDialog.new()
    dialog.set_title(_("Save Export"))
    dialog.set_initial_name(f"fokuskompis_{datetime.now().strftime('%Y-%m-%d')}.{ext}")
    dialog.save(window, None, _on_text_done, tasks, parked_thoughts, converter, ext, status_callback)


def _on_text_done(dialog, result, tasks, parked_thoughts, converter, ext, status_callback):
    try:
        gfile = dialog.save_finish(result)
    except GLib.Error:
        return
    try:
        with open(gfile.get_path(), "w") as f:
            f.write(converter(tasks, parked_thoughts))
        if status_callback:
            status_callback(_("Exported %s") % ext.upper())
    except Exception as e:
        if status_callback:
            status_callback(_("Export error: %s") % str(e))


def _save_pdf(window, tasks, parked_thoughts, status_callback):
    dialog = Gtk.FileDialog.new()
    dialog.set_title(_("Save PDF"))
    dialog.set_initial_name(f"fokuskompis_{datetime.now().strftime('%Y-%m-%d')}.pdf")
    dialog.save(window, None, _on_pdf_done, tasks, parked_thoughts, status_callback)


def _on_pdf_done(dialog, result, tasks, parked_thoughts, status_callback):
    try:
        gfile = dialog.save_finish(result)
    except GLib.Error:
        return
    try:
        success = export_tasks_pdf(tasks, parked_thoughts, gfile.get_path())
        if success and status_callback:
            status_callback(_("PDF exported"))
        elif not success and status_callback:
            status_callback(_("PDF export requires cairo."))
    except Exception as e:
        if status_callback:
            status_callback(_("Export error: %s") % str(e))
