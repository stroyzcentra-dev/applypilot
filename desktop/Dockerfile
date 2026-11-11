# The agent's computer: a small Linux desktop running Chromium.
#   - Xvfb gives it a screen, fluxbox a window manager.
#   - x11vnc + noVNC let you watch (and take over) from a browser tab.
#   - Chromium exposes the DevTools protocol so the agent can drive it with Playwright.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    SCREEN=1440x900x24

RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium chromium-sandbox xvfb fluxbox x11vnc novnc websockify socat \
        fonts-liberation fonts-noto-color-emoji ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash agent

# The profile dir is owned by agent so the mounted volume inherits that owner.
# The fluxbox overlay gives a plain background instead of a "can't set wallpaper" popup.
RUN mkdir -p /home/agent/profile /home/agent/.fluxbox \
    && echo 'background: none' > /home/agent/.fluxbox/overlay \
    && chown -R agent:agent /home/agent

COPY start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

USER agent
WORKDIR /home/agent
# 6080: noVNC (watch / take over).  9223: Chromium DevTools for the agent.
EXPOSE 6080 9223
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/start.sh"]
