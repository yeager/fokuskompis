"""Accessibility module for children's apps — large text for readability."""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk

_LARGE_TEXT_CSS = b"""
window label, window button label, window .title, window .subtitle {
    font-size: 20px;
}
window headerbar .title {
    font-size: 22px;
}
window .large-text {
    font-size: 24px;
}
window button {
    min-height: 44px;
    min-width: 44px;
}
"""

def apply_large_text():
    """Apply large text CSS globally for accessibility."""
    provider = Gtk.CssProvider()
    provider.load_from_data(_LARGE_TEXT_CSS)
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 100
        )
