# Hutch Unthrottle

A small Windows app with **Connect** / **Disconnect** buttons that tries to
get around ISP-side DPI (Deep Packet Inspection) throttling on specific
sites — **without changing your public IP address**.

It's a GUI wrapper around the open-source engine
[ByeDPI](https://github.com/hufrea/byedpi) (MIT-style license, 3k+ stars),
which fragments/reorders the first TLS packet of a connection so that
SNI-based traffic shaping on the ISP side can't easily tell which site
you're visiting.

## How it works

1. `ciadpi.exe` (the ByeDPI engine) starts a local SOCKS5 proxy on
   `127.0.0.1:1080`.
2. Windows' system proxy setting is pointed at that local proxy.
3. Apps that use the system proxy (most browsers) send their traffic
   through `ciadpi`, which mangles the outgoing packet shape before it
   hits your router/ISP.
4. Disconnect reverts the system proxy and stops the engine — everything
   back to normal.

**Your IP never changes.** This is not a VPN and does not hide your
traffic from your ISP — it just makes the *first* handshake harder for
simple DPI filters to classify.

## Honest limitations — please read

- **Not guaranteed to work, and not "100%".** Throttling systems get
  updated; a technique that works today may stop working tomorrow. If
  it stops working, tweak the arguments in `app/dpi_bypass_gui.py`
  (`BYEDPI_ARGS`) — see the [ByeDPI README](https://github.com/hufrea/byedpi)
  for all available flags (`--fake`, `--tlsrec`, `--auto`, etc.) and try
  a different combination.
- Only affects apps that respect the Windows system proxy setting. Some
  apps/games with their own network stack will ignore it.
- Antivirus/Windows Defender may flag `ciadpi.exe` as suspicious — this
  is a common false positive for DPI-evasion tools in general (same
  family as GoodbyeDPI, used publicly in many countries against
  ISP/state throttling). The source is fully open — you can read it
  before trusting the binary.
- Circumventing your ISP's traffic-shaping may be against their
  Terms of Service even where it isn't illegal — check Hutch's ToS if
  that matters to you.

## Building it yourself

You don't need a Windows machine or a compiler. Push this repo to
GitHub and the included workflow (`.github/workflows/build.yml`) will:

1. Check out ByeDPI's own source from `hufrea/byedpi` and cross-compile
   `ciadpi.exe` from scratch using MinGW (nothing pre-built is trusted
   blindly).
2. Build the GUI (`app/dpi_bypass_gui.py`) into a Windows `.exe` with
   PyInstaller.
3. Zip both together and upload as a build artifact (and as a GitHub
   Release asset if you push a tag like `v1.0`).

### Steps

```bash
git init
git add .
git commit -m "Hutch Unthrottle v1"
git branch -M main
git remote add origin https://github.com/<your-username>/hutch-unthrottle.git
git push -u origin main
```

Then either:
- Go to the **Actions** tab on GitHub → the workflow runs automatically
  on push → download `HutchUnthrottle-windows-x64` from the run's
  artifacts, or
- Tag a release to also get it attached to a GitHub Release:
  ```bash
  git tag v1.0
  git push origin v1.0
  ```

Unzip on your Windows PC, run `HutchUnthrottle.exe` (keep `ciadpi.exe`
in the same folder), click **Connect**.

## Tuning for Hutch specifically

If the default arguments in `BYEDPI_ARGS` don't help with a particular
site, common things worth trying (edit `app/dpi_bypass_gui.py` and
re-run the workflow):

```
--disorder 1 --fake -1          # packet-fake based evasion
--fake -1 --ttl 6                # lower TTL so the fake packet dies before reaching Hutch's core, but still passes their DPI
--hosts blocked_sites.txt --disorder 3   # only apply bypass to specific domains
```

Full flag reference: https://github.com/hufrea/byedpi/blob/main/README.md
