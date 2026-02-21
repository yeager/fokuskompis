"""Accessibility helpers for children's apps."""
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk


_LARGE_TEXT_CSS = """
window button label,
window .pill label {
    font-size: 16pt;
    font-weight: 500;
}

window .title-1 {
    font-size: 28pt;
}

window .title-2 {
    font-size: 22pt;
}

window .title-3 {
    font-size: 18pt;
}

window .body,
window label {
    font-size: 15pt;
}

window listview row label,
window listbox row label {
    font-size: 15pt;
}

messagedialog label {
    font-size: 15pt;
}
"""


def apply_large_text():
    """Load large-text CSS provider for accessibility."""
    provider = Gtk.CssProvider()
    provider.load_from_string(_LARGE_TEXT_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 100,
    )
