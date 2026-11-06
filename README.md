<img src="assets/logo/whistleflux-mark.svg" alt="" width="96">

# Whistleflux

Dictate anywhere on Linux, macOS and Windows. Press a shortcut, talk, press it again, and
clean text is typed at your cursor.

- **Local speech-to-text** with [Cactus Whistle](https://huggingface.co/Cactus-Compute/whistle)
  (19 MB download, about 70 MB of memory). Your audio never leaves your computer. English,
  German, French, Spanish, Italian, Dutch and Polish, with punctuation and capitals.
- **Basic clean-up on your computer**: removes "um"/"uh" and stutters like "the the",
  applies "scratch that", and turns "new line" and "new paragraph" into line breaks.
- **Smart clean-up on your computer** with [S1-mini by Superwhisper](https://huggingface.co/superwhisper/s1-mini):
  applies corrections like "Tuesday, no, Wednesday", turns spoken lists into lists, and
  writes numbers, emails and dates properly (about 510 MB download, about 1.1 GB of memory
  while running). English only; other languages get the basic clean-up. If it isn't
  downloaded or fails, the basic clean-up is typed instead.
- **Fully offline**: nothing you say or type is sent anywhere.
- **Small**: two Python dependencies on macOS and Windows (`sounddevice`, `pynput`) and one
  on Linux.

## How it works

```
shortcut ─▶ record mic ─▶ shortcut ─▶ Whistle ─▶ basic clean-up ─▶ S1-mini ─▶ typed at cursor
                                      (all on your computer)
```

A background process keeps the models loaded, so after the first run a short dictation
takes about one to two seconds, and a 1.5-minute one about 8 seconds. The first `whistleflux` call starts it
automatically. While recording, a small pill at the bottom of the screen shows sound
bars that follow your voice, then a blue wave while it transcribes (turn it off with
`overlay = false`). You'll also hear a high beep when recording starts, a lower one when
it stops, and a low beep if nothing was heard or something went wrong.

## Install

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or pipx).

```sh
git clone <this repo> whistleflux && cd whistleflux
uv tool install .
whistleflux download          # fetch the speech model once (19 MB)
whistleflux download s1-mini  # and the clean-up model with llama.cpp (about 510 MB)
```

### Linux

Recording uses PortAudio, and typing uses a virtual keyboard (`/dev/uinput`), which works
on both X11 and Wayland.

1. Install PortAudio, plus `wl-clipboard` (Wayland) or `xclip` (X11) for non-English characters:
   - Fedora: `sudo dnf install portaudio wl-clipboard`
   - Debian/Ubuntu: `sudo apt install libportaudio2 wl-clipboard`
   - Arch: `sudo pacman -S portaudio wl-clipboard`
2. Allow your user to create the virtual keyboard, then **reboot**:
   ```sh
   echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
     | sudo tee /etc/udev/rules.d/99-whistleflux-uinput.rules
   sudo usermod -aG input $USER
   ```
   If `id` already lists `input`, you can skip the reboot and apply it now with
   `sudo chgrp input /dev/uinput && sudo chmod 660 /dev/uinput`.
3. Bind `whistleflux` to a shortcut in your desktop settings. Apps can't register global
   shortcuts on Wayland, so this is done by the desktop:

<details>
<summary>GNOME</summary>

```sh
name="whistleflux"
binding="<Ctrl><Alt>space"
action="$(command -v whistleflux)"

media_keys=org.gnome.settings-daemon.plugins.media-keys
custom_kbd=org.gnome.settings-daemon.plugins.media-keys.custom-keybinding
kbd_path=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/$name/
new_bindings=`gsettings get $media_keys custom-keybindings | sed -e"s>'\]>','$kbd_path']>"| sed -e"s>@as \[\]>['$kbd_path']>"`
gsettings set $media_keys custom-keybindings "$new_bindings"
gsettings set $custom_kbd:$kbd_path name "$name"
gsettings set $custom_kbd:$kbd_path binding "$binding"
gsettings set $custom_kbd:$kbd_path command "$action"
```
</details>

<details>
<summary>KDE Plasma</summary>

System Settings → Keyboard → Shortcuts → Add New → Command or Script → `whistleflux`.
</details>

<details>
<summary>i3 / sway</summary>

```
bindsym $mod+d exec whistleflux
```
</details>

<details>
<summary>Hyprland</summary>

```
bind = $mainMod, D, exec, whistleflux
```
</details>

Typing assumes a US QWERTY layout. On other layouts, set up a key that switches to US
QWERTY and set `wrap_key` in the config; Whistleflux taps it before and after typing.

### macOS

1. Start it once: `whistleflux daemon`. Leave it running, or add it as a login item.
2. When asked, allow **Microphone**, **Accessibility** (to type) and **Input Monitoring**
   (for the shortcut) for your terminal, or for Python if you start it another way.
3. Press **Ctrl+Option+Space** to start and stop. Change it with `[hotkey] toggle` in the config.

### Windows

1. Start it once: `whistleflux daemon`. To start it at login, put a shortcut to
   `pythonw -m whistleflux daemon` in `shell:startup`.
2. Press **Ctrl+Alt+Space** to start and stop. Change it with `[hotkey] toggle` in the config.

## Usage

| Command | What it does |
| --- | --- |
| `whistleflux` | Start recording, or stop and type the text |
| `whistleflux cancel` | Stop recording without typing anything |
| `whistleflux status` | Idle, recording or processing |
| `whistleflux stop` | Stop the background process (e.g. after changing the config) |
| `whistleflux transcribe file.wav` | Transcribe and format a 16-bit WAV file and print the result |
| `whistleflux config` | Create the config file and print its path |
| `whistleflux log` | Show recent activity, including raw and formatted text |

## Configuration

Run `whistleflux config` to create the file, then run `whistleflux stop` after editing so the
next shortcut press picks up the changes. Locations:

- Linux: `~/.config/whistleflux/config.toml`
- macOS: `~/Library/Application Support/whistleflux/config.toml`
- Windows: `%APPDATA%\whistleflux\config.toml`

The most useful settings:

```toml
[format]
basic_cleanup = true              # remove "um"s and stutters, apply "scratch that"
enabled = true                    # false = basic clean-up only (no S1-mini)

[s1_mini]
styling = "semi-formal"           # "casual", "semi-casual", "semi-formal" or "formal"
structure = "lists"               # "prose" makes lists less often
context = "general"               # "email" lays out a greeting, body and sign-off

[speech]
language = ""                     # "en", "de", "fr", "es", "it", "nl", "pl"; "" = detect it
keywords = ["Kubernetes", "PostgreSQL", "Whistleflux"]   # names and jargon to favor
silence_threshold_db = -50        # recordings quieter than this are skipped

[output]
sounds = true
```

## Smart clean-up (S1-mini)

[S1-mini](https://huggingface.co/superwhisper/s1-mini) by Superwhisper is a small model
trained only to tidy up dictation. `whistleflux download s1-mini` fetches it (484 MB) and
llama.cpp to run it (about 25 MB). It's English only.

It turns spoken lists into "- " lists, and counted ones ("first..., second...") into
"1. 2." lists, whether you say them in one go or one per "new line". It doesn't fix misheard words from
context ("pole request" stays as is); add names and jargon to `[speech] keywords` instead.

The background process runs llama.cpp's `llama-server` alongside itself, listening on
`127.0.0.1` only. Its own log is `s1-mini-server.log` next to `whistleflux.log`. If S1-mini
fails, or `language` is set to something other than `en`, the basic clean-up is typed.

## Troubleshooting

- **Nothing happens**: run `whistleflux log`. If the background process failed to start, the
  reason is there (for example, the `/dev/uinput` permission on Linux).
- **Wrong characters on Linux**: your layout isn't US QWERTY; see `wrap_key` above.
- **Non-English characters don't appear in a Linux terminal**: they're pasted with Ctrl+V,
  which most terminals don't use for paste. Letters, digits and punctuation are typed
  directly and work everywhere.
- **New lines** are typed as Shift+Enter so chat apps don't send your message early.

## Mobile

The Android app is in [`mobile/`](mobile/README.md): a voice keyboard that runs the same
Whistle model on the phone. Its optional smart clean-up still uses the Gemini API, since
S1-mini is too big for a phone keyboard. iOS is not started yet; iOS
keyboards can't use the microphone and have tight memory limits, so recording would have
to happen in the main app.

## Credits

Speech model: [Whistle](https://huggingface.co/Cactus-Compute/whistle) by Cactus Compute,
run with their [Needle engine](https://huggingface.co/Cactus-Compute/needle3) (both Apache 2.0).

Offline clean-up model: [S1-mini](https://huggingface.co/superwhisper/s1-mini) by Superwhisper
(Apache 2.0 with a naming term), run with [llama.cpp](https://github.com/ggml-org/llama.cpp) (MIT).
