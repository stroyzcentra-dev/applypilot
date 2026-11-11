#!/usr/bin/env bash
set -euo pipefail

# Docker restarts this container in place (after a reboot, say), so /tmp and the
# profile volume survive. Locks left by the previous run would stop the display
# and Chromium from starting; nothing else is running yet, so they're all stale.
rm -f "/tmp/.X${DISPLAY#:}-lock" "/tmp/.X11-unix/X${DISPLAY#:}"
rm -f /home/agent/profile/SingletonLock /home/agent/profile/SingletonSocket /home/agent/profile/SingletonCookie

Xvfb "$DISPLAY" -screen 0 "$SCREEN" -nolisten tcp &
until xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 || [ -e /tmp/.X11-unix/X99 ]; do sleep 0.2; done

fluxbox >/dev/null 2>&1 &

# Viewer. Only reachable from the host's localhost (see docker-compose.yml).
x11vnc -display "$DISPLAY" -forever -shared -nopw -quiet -rfbport 5900 >/dev/null 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &

# Chromium binds DevTools to 127.0.0.1 inside the container; socat republishes it.
socat TCP-LISTEN:9223,fork,reuseaddr TCP:127.0.0.1:9222 &

# Keep Chromium running; restart it if the user (or a crash) closes it.
while true; do
  chromium \
    --no-first-run --no-default-browser-check --disable-dev-shm-usage \
    --user-data-dir=/home/agent/profile \
    --remote-debugging-port=9222 --remote-allow-origins='*' \
    --window-position=0,0 --window-size=1440,900 --start-maximized \
    about:blank >/dev/null 2>&1 || true
  sleep 1
done
