#!/bin/sh
# Start herdr-telegram at login, and restart it if it stops.
#   scripts/autostart.sh          install (or update) and start it now
#   scripts/autostart.sh remove   stop it and stop starting it at login
# Logs go to ~/Library/Logs/herdr-telegram.log
set -eu

LABEL=dev.bryanfrds.herdr-telegram
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/herdr-telegram.log"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = remove ]; then
    rm -f "$PLIST"
    echo "Removed. herdr-telegram won't start at login."
    exit 0
fi

REPO=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=$(command -v python3) || { echo "python3 not found" >&2; exit 1; }
HERDR=$(command -v herdr) || { echo "herdr not found on PATH" >&2; exit 1; }
[ -f "$HOME/.config/herdr-telegram/config" ] || {
    echo "Set up ~/.config/herdr-telegram/config first (see the README)." >&2; exit 1; }

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
# launchd starts jobs with a bare PATH, so pass on the one that finds herdr.
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array><string>$PYTHON</string><string>-u</string><string>-m</string><string>herdr_tg</string></array>
    <key>WorkingDirectory</key><string>$REPO</string>
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>$(dirname "$HERDR"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <!-- A refused token exits straight away; don't restart it more than once a minute. -->
    <key>ThrottleInterval</key><integer>60</integer>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
plutil -lint -s "$PLIST"
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed. herdr-telegram is running and will start at login."
echo "Logs: $LOG"
