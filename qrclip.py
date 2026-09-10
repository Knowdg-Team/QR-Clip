#!/usr/bin/env python3
"""QR-Clip — envoie le presse-papier du téléphone vers le PC.

Lance un petit serveur web sur le réseau local et affiche un QR code.
On scanne le QR avec le téléphone, on colle dans la page, on envoie :
le texte arrive directement dans le presse-papier du PC.

Usage :
    ./qrclip.py [--port 8765] [--no-gui] [--no-token]
"""

import argparse
import html
import http.server
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qrmini

__version__ = '1.0.0'

PORT = 8765
MAX_BODY = 1 << 20          # 1 Mio par envoi
HISTORY_MAX = 30


# --------------------------------------------------------------------------
# Réseau
# --------------------------------------------------------------------------

def lan_ip():
    """Adresse IP de l'interface qui sort vers le réseau local."""
    for target in ('8.8.8.8', '192.168.255.255'):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 1))              # UDP : aucun paquet n'est réellement émis
            return s.getsockname()[0]
        except OSError:                         # pas de route (hors ligne, réseau exotique)
            continue
        finally:
            s.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return '127.0.0.1'


# --------------------------------------------------------------------------
# Presse-papier
# --------------------------------------------------------------------------

class CommandClipboard:
    """Repli : wl-copy / xclip / xsel s'ils sont installés."""

    CANDIDATES = [
        (['wl-copy'], ['wl-copy']),
        (['xclip'], ['xclip', '-selection', 'clipboard']),
        (['xsel'], ['xsel', '--clipboard', '--input']),
    ]

    def __init__(self):
        self.cmd = None
        for probe, cmd in self.CANDIDATES:
            if shutil.which(probe[0]):
                self.cmd = cmd
                break

    @property
    def available(self):
        return self.cmd is not None

    @property
    def name(self):
        return self.cmd[0] if self.cmd else 'aucun'

    def set_text(self, text):
        subprocess.run(self.cmd, input=text.encode('utf-8'), check=True)


class GtkClipboard:
    """Presse-papier via GTK 4. Le processus doit rester en vie pour le servir."""

    def __init__(self, display):
        self.clipboard = display.get_clipboard()

    @property
    def available(self):
        return True

    name = 'GTK'

    def set_text(self, text):
        self.clipboard.set(text)


# --------------------------------------------------------------------------
# Serveur web
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#111418">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>QR-Clip</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f6f7f9; --card: #ffffff; --fg: #14181d; --muted: #667085;
    --line: #dfe3e8; --accent: #2f6fed; --accent-fg: #ffffff; --ok: #17864a;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1216; --card: #171c22; --fg: #e8ecf1; --muted: #93a0b0;
      --line: #262d36; --accent: #4f8bff; --accent-fg: #0b0f14; --ok: #46d08a;
    }
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; padding: 16px calc(16px + env(safe-area-inset-right)) 24px
            calc(16px + env(safe-area-inset-left));
    background: var(--bg); color: var(--fg);
    font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  header { display: flex; align-items: baseline; gap: 8px; margin: 4px 2px 14px; }
  h1 { font-size: 20px; margin: 0; letter-spacing: -0.01em; }
  header span { color: var(--muted); font-size: 13px; }
  .card {
    background: var(--card); border: 1px solid var(--line);
    border-radius: 14px; padding: 12px; margin-bottom: 12px;
  }
  textarea {
    width: 100%; min-height: 34vh; resize: vertical; border: 0; outline: 0;
    background: transparent; color: inherit; font: inherit; padding: 4px;
  }
  .row { display: flex; gap: 10px; align-items: center; }
  button {
    font: 600 16px/1 inherit; border-radius: 12px; border: 1px solid var(--line);
    padding: 15px 18px; background: var(--card); color: var(--fg);
  }
  button.primary { flex: 1; background: var(--accent); color: var(--accent-fg); border-color: transparent; }
  button:active { transform: scale(0.985); }
  button:disabled { opacity: .5; }
  #status { min-height: 22px; margin: 12px 2px 0; font-size: 14px; color: var(--muted); }
  #status.ok { color: var(--ok); font-weight: 600; }
  #status.err { color: #d64545; font-weight: 600; }
  .hint { color: var(--muted); font-size: 13px; margin: 2px 2px 14px; }
  ul { list-style: none; margin: 0; padding: 0; }
  li { border-top: 1px solid var(--line); padding: 9px 2px; font-size: 13px; color: var(--muted);
       white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  li:first-child { border-top: 0; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .05em;
       color: var(--muted); margin: 0 2px 8px; font-weight: 600; }
</style>
</head>
<body>
<header><h1>QR-Clip</h1><span>vers __HOST__</span></header>
<p class="hint">Collez ici (appui long &rarr; Coller), puis Envoyer.</p>

<div class="card"><textarea id="txt" autofocus placeholder="Texte à envoyer sur le PC&#10;&#10;Astuce : partagez vers ce navigateur, ou collez directement."></textarea></div>

<div class="row">
  <button class="primary" id="send">Envoyer sur le PC</button>
  <button id="clear" title="Vider">Vider</button>
</div>
<p id="status">&nbsp;</p>

<div class="card" id="histcard" hidden>
  <h2>Envoyés</h2>
  <ul id="hist"></ul>
</div>

<script>
const TOKEN = "__TOKEN__";
const txt = document.getElementById('txt');
const status = document.getElementById('status');
const hist = document.getElementById('hist');
const histcard = document.getElementById('histcard');
const sendBtn = document.getElementById('send');

function say(msg, cls) {
  status.textContent = msg;
  status.className = cls || '';
}

function remember(text) {
  const li = document.createElement('li');
  const t = new Date().toLocaleTimeString('fr-FR', {hour: '2-digit', minute: '2-digit'});
  li.textContent = t + '  ·  ' + text.replace(/\\s+/g, ' ').slice(0, 80);
  hist.prepend(li);
  histcard.hidden = false;
  while (hist.children.length > 10) hist.lastChild.remove();
}

async function send() {
  const text = txt.value;
  if (!text) { say('Rien à envoyer.', 'err'); txt.focus(); return; }
  sendBtn.disabled = true;
  say('Envoi…');
  try {
    const r = await fetch('clip', {
      method: 'POST',
      headers: {'Content-Type': 'text/plain; charset=utf-8', 'X-QRClip-Token': TOKEN},
      body: text
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    say('Copié sur le PC · ' + data.chars + ' caractères', 'ok');
    remember(text);
    txt.value = '';
    if (navigator.vibrate) navigator.vibrate(18);
  } catch (e) {
    say('Échec : ' + e.message + ' (PC éteint ou hors réseau ?)', 'err');
  } finally {
    sendBtn.disabled = false;
    txt.focus();
  }
}

sendBtn.addEventListener('click', send);
document.getElementById('clear').addEventListener('click', () => {
  txt.value = ''; say('\\u00a0'); txt.focus();
});
txt.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') send();
});

// Si le navigateur l'autorise (contexte sécurisé), pré-remplir depuis le presse-papier.
window.addEventListener('load', async () => {
  txt.focus();
  if (navigator.clipboard && navigator.clipboard.readText) {
    try {
      const t = await navigator.clipboard.readText();
      if (t && !txt.value) { txt.value = t; say('Presse-papier pré-rempli, vérifiez puis envoyez.'); }
    } catch (e) { /* refuse hors HTTPS : collage manuel */ }
  }
});
</script>
</body>
</html>
"""

DENIED = ("<!doctype html><meta charset=utf-8><title>QR-Clip</title>"
          "<body style='font:16px system-ui;padding:2rem'>"
          "<h1>Lien invalide</h1><p>Rescannez le QR code affiché sur le PC.</p>")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'QR-Clip'
    protocol_version = 'HTTP/1.1'

    # injectés par make_server
    token = ''
    on_text = None
    host_label = ''

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype='text/html; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _token_ok(self, given):
        return secrets.compare_digest(given or '', self.token) if self.token else True

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ('/', '/index.html'):
            key = urllib.parse.parse_qs(parsed.query).get('k', [''])[0]
            if not self._token_ok(key):
                return self._send(403, DENIED)
            page = (PAGE.replace('__TOKEN__', html.escape(self.token, quote=True))
                        .replace('__HOST__', html.escape(self.host_label)))
            return self._send(200, page)
        if parsed.path == '/favicon.ico':
            self.send_response(204)
            self.send_header('Content-Length', '0')
            return self.end_headers()
        self._send(404, 'introuvable', 'text/plain; charset=utf-8')

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path.rstrip('/') not in ('/clip', ''):
            return self._send(404, 'introuvable', 'text/plain; charset=utf-8')
        if not self._token_ok(self.headers.get('X-QRClip-Token')):
            return self._send(403, 'jeton invalide', 'text/plain; charset=utf-8')
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self._send(413, 'contenu trop volumineux', 'text/plain; charset=utf-8')
        text = self.rfile.read(length).decode('utf-8', 'replace')
        if not text:
            return self._send(400, 'contenu vide', 'text/plain; charset=utf-8')
        self.on_text(text, self.client_address[0])
        self._send(200, json.dumps({'ok': True, 'chars': len(text)}),
                   'application/json; charset=utf-8')


def make_server(port, token, on_text, host_label):
    cls = type('BoundHandler', (Handler,),
               {'token': token, 'on_text': staticmethod(on_text), 'host_label': host_label})
    httpd = http.server.ThreadingHTTPServer(('0.0.0.0', port), cls)
    httpd.daemon_threads = True
    return httpd


# --------------------------------------------------------------------------
# Interface GTK
# --------------------------------------------------------------------------

def run_gui(state, matrix, url, clipboard_holder):
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, Gdk, GLib, Pango

    png = qrmini.to_png(matrix, scale=7, quiet=3)

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title='QR-Clip')
            self.set_default_size(400, 660)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.set_margin_top(18); box.set_margin_bottom(18)
            box.set_margin_start(18); box.set_margin_end(18)
            self.set_child(box)

            title = Gtk.Label(label='Scannez avec le téléphone')
            title.add_css_class('title-3')
            box.append(title)

            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            pic = Gtk.Picture.new_for_paintable(texture)
            pic.set_size_request(300, 300)
            pic.set_can_shrink(True)
            box.append(pic)

            link = Gtk.Label(label=url)
            link.set_selectable(True)
            link.set_can_focus(False)              # sinon l'URL s'ouvre déjà sélectionnée
            link.set_wrap(True)
            link.add_css_class('dim-label')
            box.append(link)

            copy_url = Gtk.Button(label="Copier l'adresse")
            copy_url.connect('clicked', lambda *_: state['clipboard'].set_text(url))
            copy_url.set_halign(Gtk.Align.CENTER)
            box.append(copy_url)

            self.status = Gtk.Label(label='En attente d’un envoi…')
            self.status.add_css_class('dim-label')
            box.append(self.status)

            self.listbox = Gtk.ListBox()
            self.listbox.add_css_class('boxed-list')
            self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            empty = Gtk.Label(label='Les textes reçus apparaîtront ici.')
            empty.add_css_class('dim-label')
            empty.set_margin_top(24)
            self.listbox.set_placeholder(empty)
            scroll = Gtk.ScrolledWindow()
            scroll.set_child(self.listbox)
            scroll.set_vexpand(True)
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            box.append(scroll)

        def on_received(self, text, when, ok):
            preview = ('(masqué)' if state.get('discret')
                       else ' '.join(text.split())[:70] or '(vide)')
            self.status.set_label(
                ('Copié à %s · %d caractères' % (when, len(text))) if ok
                else 'Reçu à %s mais copie impossible' % when)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_margin_top(6); row.set_margin_bottom(6)
            row.set_margin_start(8); row.set_margin_end(8)
            label = Gtk.Label(label='%s  %s' % (when, preview))
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_xalign(0)
            label.set_hexpand(True)
            row.append(label)
            again = Gtk.Button(label='Recopier')
            again.add_css_class('flat')
            again.connect('clicked', lambda *_: state['clipboard'].set_text(text))
            row.append(again)
            self.listbox.prepend(row)

    class App(Gtk.Application):
        def do_activate(self):
            state['clipboard'] = clipboard_holder(Gdk.Display.get_default())
            win = Window(self)
            state['window'] = win
            win.present()
            print('[qr-clip] presse-papier : %s' % state['clipboard'].name)

    GLib.set_prgname('qrclip')
    state['idle_add'] = GLib.idle_add
    App(application_id=None).run([])
    state['idle_add'] = None                             # boucle GTK terminée


# --------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description='Presse-papier téléphone → PC via QR code.')
    ap.add_argument('--version', action='version', version='QR-Clip ' + __version__)
    ap.add_argument('--port', type=int, default=PORT, help='port d’écoute (défaut %d)' % PORT)
    ap.add_argument('--no-gui', action='store_true',
                    help='pas de fenêtre : QR code dans le terminal uniquement')
    ap.add_argument('--no-token', action='store_true',
                    help='désactive le jeton de sécurité dans l’URL')
    ap.add_argument('--backend', choices=('auto', 'x11', 'wayland'), default='auto',
                    help='backend GTK (défaut auto : XWayland si disponible)')
    ap.add_argument('--discret', action='store_true',
                    help='n’affiche jamais le contenu reçu (utile pour les mots de passe)')
    ap.add_argument('--host', default=None,
                    help='adresse à annoncer dans le QR (défaut : IP locale détectée)')
    args = ap.parse_args()

    ip = args.host or lan_ip()
    token = '' if args.no_token else secrets.token_urlsafe(9)
    url = 'http://%s:%d/%s' % (ip, args.port, ('?k=' + token) if token else '')
    matrix = qrmini.encode(url, 'M')

    state = {'clipboard': None, 'window': None, 'idle_add': None,
             'discret': args.discret}

    def copy_now(text):
        """Copie le texte. GDK n'est pas thread-safe : si une boucle GTK tourne,
        la copie doit s'exécuter dans le thread principal, pas dans celui du serveur."""
        cb = state['clipboard']
        if cb is None:
            return False, 'aucun presse-papier disponible'
        idle = state['idle_add']
        if idle is None:
            try:
                cb.set_text(text)
                return True, ''
            except Exception as exc:
                return False, str(exc)
        done, result = threading.Event(), {}
        def run():
            try:
                cb.set_text(text)
                result['ok'] = True
            except Exception as exc:
                result['ok'], result['err'] = False, str(exc)
            done.set()
            return False                                 # ne pas replanifier
        idle(run)
        if not done.wait(3):
            return False, 'délai dépassé'
        return result.get('ok', False), result.get('err', '')

    def deliver(text, sender):
        when = time.strftime('%H:%M:%S')
        ok, err = copy_now(text)
        if not ok:
            print('[qr-clip] copie impossible : %s' % err, file=sys.stderr)
        preview = '(masqué)' if args.discret else ' '.join(text.split())[:70]
        print('[%s] %s → %d caractères%s : %s'
              % (when, sender, len(text), '' if ok else ' (NON COPIE)', preview))
        if not ok and not args.discret:
            print(text)
        win = state['window']
        if win is not None and state['idle_add'] is not None:
            state['idle_add'](win.on_received, text, when, ok)

    try:
        # deliver() est appelé depuis les threads du serveur
        httpd = make_server(args.port, token, deliver, '%s:%d' % (ip, args.port))
    except OSError as exc:
        sys.exit('[qr-clip] impossible d’écouter sur le port %d : %s' % (args.port, exc))

    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print(qrmini.to_ansi(matrix))
    print('  QR-Clip — scannez le QR code, ou ouvrez :')
    print('  %s\n' % url)
    print('  Le téléphone doit être sur le même réseau Wi-Fi que le PC.')
    print('  Ctrl+C pour arrêter.\n')

    # Sous Wayland, seul le client qui a le focus clavier peut prendre la sélection :
    # un serveur en arrière-plan n'y arrive jamais. On force donc XWayland, où la
    # propriété du presse-papier ne dépend pas du focus. La session exporte souvent
    # GDK_BACKEND=wayland : il faut vraiment l'écraser, pas seulement le compléter.
    if args.backend != 'auto':
        os.environ['GDK_BACKEND'] = args.backend
    elif os.environ.get('DISPLAY'):
        os.environ['GDK_BACKEND'] = 'x11'
    elif os.environ.get('WAYLAND_DISPLAY'):
        print('[qr-clip] XWayland indisponible : sous Wayland pur, la copie ne '
              'fonctionne que si la fenêtre QR-Clip a le focus.', file=sys.stderr)

    use_gui = not args.no_gui and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
    if use_gui:
        try:
            run_gui(state, matrix, url, GtkClipboard)
            return
        except Exception as exc:
            print('[qr-clip] interface graphique indisponible (%s), mode terminal.' % exc,
                  file=sys.stderr)

    fallback = CommandClipboard()
    if fallback.available:
        state['clipboard'] = fallback
        print('[qr-clip] presse-papier : %s' % fallback.name)
    else:
        try:
            import gi
            gi.require_version('Gtk', '4.0')
            from gi.repository import Gtk, Gdk, GLib
            Gtk.init()
            state['clipboard'] = GtkClipboard(Gdk.Display.get_default())
            state['idle_add'] = GLib.idle_add
            print('[qr-clip] presse-papier : GTK (sans fenêtre)')
            GLib.MainLoop().run()
            return
        except Exception:
            print('[qr-clip] aucun presse-papier disponible : le texte recu sera '
                  'affiché ici.\n  (installez wl-clipboard ou xclip)', file=sys.stderr)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n[qr-clip] arrêt.')
