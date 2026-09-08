#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, Gdk

import json
import os
import subprocess
import time

STATE_PATH = os.path.expanduser("~/.local/share/zerotier-switch/state.json")
POLL_INTERVAL_MS = 3000

CSS = """
@define-color zt_orange #F18A24;
@define-color zt_navy #1B1E27;
@define-color zt_amber #E8A33D;
@define-color zt_off #8A8D93;

.zt-icon-circle {
    border-radius: 999px;
    padding: 8px;
    min-width: 24px;
    min-height: 24px;
}
.zt-icon-ok { background-color: alpha(@success_color, 0.18); color: @success_color; }
.zt-icon-pending { background-color: alpha(@zt_amber, 0.20); color: @zt_amber; }
.zt-icon-off { background-color: alpha(@error_color, 0.14); color: @error_color; }

row.zt-row {
    padding-top: 4px;
    padding-bottom: 4px;
}

.zt-status-label {
    font-weight: 600;
}
.zt-status-ok { color: @success_color; }
.zt-status-pending { color: @zt_amber; }
.zt-status-off { color: @error_color; }

.zt-nwid {
    font-family: monospace;
    font-size: 0.85em;
}

switch:checked {
    background-color: @zt_orange;
}

headerbar.zt-headerbar, window headerbar.zt-headerbar, .background headerbar.zt-headerbar {
    background-color: @zt_navy;
    background-image: none;
    color: white;
    box-shadow: none;
}
.zt-headerbar button.flat image,
.zt-headerbar windowtitle title {
    color: white;
}
.zt-headerbar windowtitle subtitle {
    color: alpha(white, 0.7);
}
.zt-headerbar button.flat:hover {
    background-color: alpha(white, 0.12);
}

.zt-content {
    background-color: alpha(@zt_orange, 0.65);
}
"""

ZOOM_CSS_TEMPLATE = """
.zt-row {{ font-size: {base}px; min-height: {row_h}px; }}
.zt-row .title {{ font-size: {title}px; }}
.zt-row .subtitle {{ font-size: {sub}px; }}
.zt-status-label {{ font-size: {base}px; }}
.zt-headerbar windowtitle title {{ font-size: {title}px; }}
.zt-headerbar windowtitle subtitle {{ font-size: {sub}px; }}
.zt-icon-circle {{ padding: {icon_pad}px; }}
"""

ZOOM_LEVELS = [0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6]


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


def fmt_duration(seconds):
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def get_networks():
    """Return list of dicts from zerotier-cli, or None if it failed."""
    try:
        out = subprocess.run(
            ["zerotier-cli", "listnetworks", "-j"],
            capture_output=True, text=True, timeout=4, check=True,
        )
        return json.loads(out.stdout)
    except Exception:
        return None


def join_network(nwid):
    subprocess.run(
        ["zerotier-cli", "join", nwid],
        capture_output=True, text=True, timeout=10, check=True,
    )


def leave_network(nwid):
    subprocess.run(
        ["zerotier-cli", "leave", nwid],
        capture_output=True, text=True, timeout=10, check=True,
    )


def get_admin_up(dev):
    try:
        with open(f"/sys/class/net/{dev}/flags") as f:
            flags = int(f.read().strip(), 16)
        return bool(flags & 0x1)  # IFF_UP
    except (FileNotFoundError, ValueError):
        return False


class NetworkRow(Adw.ActionRow):
    def __init__(self, app, nwinfo):
        super().__init__()
        self.app = app
        self.nwid = nwinfo["id"]
        self.dev = nwinfo["portDeviceName"] or nwinfo.get("dev", "")

        self.add_css_class("zt-row")
        self.set_title(GLib.markup_escape_text(nwinfo["name"] or self.nwid))
        self.set_subtitle(f'<span font_family="monospace">{GLib.markup_escape_text(self.nwid)}</span>')
        self.set_subtitle_lines(1)

        zoom = ZOOM_LEVELS[app.zoom_index]

        self.icon_wrap = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.icon_wrap.add_css_class("zt-icon-circle")
        self.icon = Gtk.Image.new_from_icon_name("network-wired-symbolic")
        self.icon.set_pixel_size(round(18 * zoom))
        self.icon_wrap.append(self.icon)
        self.add_prefix(self.icon_wrap)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.END)

        self.status_label = Gtk.Label(halign=Gtk.Align.END)
        self.status_label.add_css_class("caption")
        self.status_label.add_css_class("zt-status-label")
        self.ip_label = Gtk.Label(halign=Gtk.Align.END)
        self.ip_label.add_css_class("dim-label")
        self.ip_label.add_css_class("caption")

        box.append(self.status_label)
        box.append(self.ip_label)
        self.add_suffix(box)

        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.set_size_request(round(48 * zoom), round(26 * zoom))
        self.switch.connect("state-set", self.on_switch_flipped)
        self.add_suffix(self.switch)
        self.set_activatable_widget(self.switch)

        leave_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        leave_btn.add_css_class("flat")
        leave_btn.add_css_class("circular")
        leave_btn.set_tooltip_text("Odebrat síť")
        leave_btn.connect("clicked", self.on_leave_clicked)
        self.add_suffix(leave_btn)

        self.update(nwinfo)

    def update(self, nwinfo):
        up = get_admin_up(self.dev) if self.dev else False
        zt_connected = nwinfo.get("status") == "OK"

        self.switch.set_state(up)
        self.switch.set_active(up)

        state = self.app.state.setdefault(self.nwid, {})
        was_up = state.get("was_up", False)

        if up and not was_up:
            state["since"] = time.time()
        if not up:
            state.pop("since", None)
        state["was_up"] = up
        self.app.state[self.nwid] = state

        ips = ", ".join(nwinfo.get("assignedAddresses", [])) or "žádné IP"
        self.ip_label.set_text(ips)

        for cls in ("zt-icon-ok", "zt-icon-pending", "zt-icon-off"):
            self.icon_wrap.remove_css_class(cls)
        for cls in ("zt-status-ok", "zt-status-pending", "zt-status-off"):
            self.status_label.remove_css_class(cls)

        if up and zt_connected:
            since = state.get("since")
            dur = fmt_duration(time.time() - since) if since else "?"
            self.status_label.set_text(f"Připojeno · {dur}")
            self.status_label.add_css_class("zt-status-ok")
            self.icon_wrap.add_css_class("zt-icon-ok")
            self.icon.set_from_icon_name("network-wired-symbolic")
        elif up:
            self.status_label.set_text("Připojuji se…")
            self.status_label.add_css_class("zt-status-pending")
            self.icon_wrap.add_css_class("zt-icon-pending")
            self.icon.set_from_icon_name("network-wired-symbolic")
        else:
            self.status_label.set_text("Odpojeno")
            self.status_label.add_css_class("zt-status-off")
            self.icon_wrap.add_css_class("zt-icon-off")
            self.icon.set_from_icon_name("network-offline-symbolic")

    def on_switch_flipped(self, switch, state):
        desired = "up" if state else "down"
        current_admin_up = get_admin_up(self.dev)
        if (desired == "up") == current_admin_up:
            return False  # no-op, already in that state

        def run_toggle():
            try:
                subprocess.run(
                    ["pkexec", "/usr/local/bin/zerotier-toggle-helper", self.dev, desired],
                    check=True, capture_output=True, text=True, timeout=30,
                )
            except Exception as e:
                GLib.idle_add(self.app.show_toast, f"Přepnutí selhalo: {e}")
            GLib.idle_add(self.app.refresh)

        self.app.run_async(run_toggle)
        return True  # we'll set real state on next refresh

    def on_leave_clicked(self, button):
        name = self.get_title() or self.nwid
        dialog = Adw.AlertDialog(
            heading="Odebrat síť?",
            body=f"Opustíš síť „{name}“ ({self.nwid}). Pro opětovné připojení "
                 f"bude třeba síť znovu autorizovat na my.zerotier.com.",
        )
        dialog.add_response("cancel", "Zrušit")
        dialog.add_response("leave", "Odebrat")
        dialog.set_response_appearance("leave", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_leave_response)
        dialog.present(self.get_root())

    def _on_leave_response(self, dialog, response):
        if response != "leave":
            return

        def run_leave():
            try:
                leave_network(self.nwid)
            except Exception as e:
                GLib.idle_add(self.app.show_toast, f"Odebrání selhalo: {e}")
            GLib.idle_add(self.app.refresh)

        self.app.run_async(run_leave)


class ZTSwitchWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_title("ZeroTier Switch")
        self.set_default_size(520, 460)
        self.set_size_request(340, 260)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("zt-headerbar")

        self.title_widget = Adw.WindowTitle(title="ZeroTier Switch", subtitle="")
        header.set_title_widget(self.title_widget)

        toolbar_view.add_top_bar(header)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.add_css_class("circular")
        refresh_btn.set_tooltip_text("Obnovit")
        refresh_btn.connect("clicked", lambda b: self.app.refresh())
        header.pack_end(refresh_btn)

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.add_css_class("circular")
        add_btn.set_tooltip_text("Přidat síť")
        add_btn.connect("clicked", lambda b: self.show_add_dialog())
        header.pack_start(add_btn)

        self.toast_overlay = Adw.ToastOverlay(vexpand=True, hexpand=True)

        self.clamp = Adw.Clamp(maximum_size=900, tightening_threshold=700, valign=Gtk.Align.START)
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.scroller.add_css_class("zt-content")
        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_margin_top(20)
        outer.set_margin_bottom(20)
        outer.set_margin_start(16)
        outer.set_margin_end(16)

        self.empty_status = Adw.StatusPage(
            title="Žádné sítě",
            description="Přidej síť tlačítkem + vlevo nahoře.",
            icon_name="network-offline-symbolic",
        )
        self.empty_status.set_visible(False)

        outer.append(self.listbox)
        self.clamp.set_child(outer)
        self.scroller.set_child(self.clamp)

        toolbar_view.set_content(self.toast_overlay)
        self.toast_overlay.set_child(self.scroller)

        self.set_content(toolbar_view)

        scroll_ctrl = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_ctrl.connect("scroll", self._on_scroll)
        self.add_controller(scroll_ctrl)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        if dy < 0:
            self.app.zoom_in()
        elif dy > 0:
            self.app.zoom_out()
        return True

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.app.zoom_in()
            return True
        if keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.app.zoom_out()
            return True
        if keyval == Gdk.KEY_0 or keyval == Gdk.KEY_KP_0:
            self.app.zoom_reset()
            return True
        return False

    def show_add_dialog(self):
        dialog = Adw.AlertDialog(heading="Přidat síť")

        entry = Adw.EntryRow(title="Network ID")
        entry.add_css_class("zt-nwid")

        group = Adw.PreferencesGroup()
        group.add(entry)
        dialog.set_extra_child(group)

        dialog.add_response("cancel", "Zrušit")
        dialog.add_response("join", "Přidat")
        dialog.set_response_appearance("join", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("join", False)

        def is_valid(text):
            text = text.strip().lower()
            return len(text) == 16 and all(c in "0123456789abcdef" for c in text)

        entry.connect("changed", lambda e: dialog.set_response_enabled("join", is_valid(e.get_text())))

        def do_join(nwid):
            def run_join():
                try:
                    join_network(nwid)
                except Exception as e:
                    GLib.idle_add(self.app.show_toast, f"Přidání selhalo: {e}")
                GLib.idle_add(self.app.refresh)

            self.app.run_async(run_join)

        entry.connect("entry-activated", lambda e: (
            dialog.close(), do_join(e.get_text().strip().lower())
        ) if is_valid(e.get_text()) else None)

        def on_response(d, response):
            if response != "join":
                return
            do_join(entry.get_text().strip().lower())

        dialog.connect("response", on_response)
        dialog.present(self)


class ZTSwitchApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.zdenek.ZerotierSwitch")
        self.state = load_state()
        self.rows = {}
        self.win = None
        self.zoom_index = ZOOM_LEVELS.index(1.0)
        self.zoom_provider = Gtk.CssProvider()

    def do_activate(self):
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        Gtk.StyleContext.add_provider_for_display(
            display, self.zoom_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )
        self._apply_zoom()

        self.win = ZTSwitchWindow(self)
        self.win.present()
        self.refresh()
        GLib.timeout_add(POLL_INTERVAL_MS, self._tick)

    def _apply_zoom(self):
        zoom = ZOOM_LEVELS[self.zoom_index]
        css = ZOOM_CSS_TEMPLATE.format(
            base=round(13 * zoom), title=round(15 * zoom), sub=round(12 * zoom),
            row_h=round(44 * zoom), icon_pad=round(8 * zoom),
        )
        self.zoom_provider.load_from_string(css)
        for row in self.rows.values():
            row.icon.set_pixel_size(round(18 * zoom))
            row.switch.set_size_request(round(48 * zoom), round(26 * zoom))
        if self.win:
            base_w, base_h = 520, 460
            self.win.set_default_size(round(base_w * max(zoom, 1.0)), round(base_h * max(zoom, 1.0)))
            self.win.clamp.set_maximum_size(round(900 * zoom))
            self.win.clamp.set_tightening_threshold(round(700 * zoom))

    def zoom_in(self):
        if self.zoom_index < len(ZOOM_LEVELS) - 1:
            self.zoom_index += 1
            self._apply_zoom()

    def zoom_out(self):
        if self.zoom_index > 0:
            self.zoom_index -= 1
            self._apply_zoom()

    def zoom_reset(self):
        self.zoom_index = ZOOM_LEVELS.index(1.0)
        self._apply_zoom()

    def _tick(self):
        self.refresh()
        return True

    def run_async(self, fn):
        import threading
        threading.Thread(target=fn, daemon=True).start()

    def show_toast(self, text):
        self.win.toast_overlay.add_toast(Adw.Toast(title=text, timeout=4))
        return False

    def refresh(self):
        networks = get_networks()
        listbox = self.win.listbox

        if networks is None:
            self.win.empty_status.set_description(
                "Nepodařilo se spustit zerotier-cli. Běží služba zerotier-one?"
            )
            self._show_empty(True)
            return

        if not networks:
            self._show_empty(True)
            return

        self._show_empty(False)

        seen = set()
        for nw in networks:
            nwid = nw["id"]
            seen.add(nwid)
            if nwid in self.rows:
                self.rows[nwid].update(nw)
            else:
                row = NetworkRow(self, nw)
                self.rows[nwid] = row
                listbox.append(row)

        for nwid in list(self.rows):
            if nwid not in seen:
                listbox.remove(self.rows[nwid])
                del self.rows[nwid]

        connected = sum(1 for nw in networks if get_admin_up(nw.get("portDeviceName") or ""))
        total = len(networks)
        self.win.title_widget.set_subtitle(f"{connected}/{total} připojeno")

        save_state(self.state)
        return False

    def _show_empty(self, empty):
        self.win.listbox.set_visible(not empty)
        if empty:
            if self.win.empty_status.get_parent() is None:
                self.win.toast_overlay.set_child(self.win.empty_status)
        else:
            if self.win.toast_overlay.get_child() is not self.win.scroller:
                self.win.toast_overlay.set_child(self.win.scroller)


if __name__ == "__main__":
    app = ZTSwitchApp()
    app.run()
