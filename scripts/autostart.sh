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

if [ "${1:-}" = remove ]; then
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed. herdr-telegram won't start at login."
    exit 0
fi

REPO=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=$(command -v python3) || { echo "python3 not found" >&2; exit 1; }
HERDR=$(command -v herdr) || { echo "herdr not found on PATH" >&2; exit 1; }
[ -f "$HOME/.config/herdr-telegram/config" ] || {
    echo "Set up ~/.config/herdr-telegram/config first (see the README)." >&2; exit 1; }

# Paths go into XML, so escape the characters that would break it.
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
REPO=$(xml "$REPO"); PYTHON=$(xml "$PYTHON"); LOG_XML=$(xml "$LOG")
# launchd starts jobs with a bare PATH, so pass on the one that finds herdr.
BIN=$(xml "$(dirname "$HERDR")")

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
NEW="$PLIST.new"
cat > "$NEW" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array><string>$PYTHON</string><string>-u</string><string>-m</string><string>herdr_tg</string></array>
    <key>WorkingDirectory</key><string>$REPO</string>
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>$BIN:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <!-- A refused token exits straight away; retry at most once a minute. -->
    <key>ThrottleInterval</key><integer>60</integer>
    <key>StandardOutPath</key><string>$LOG_XML</string>
    <key>StandardErrorPath</key><string>$LOG_XML</string>
</dict>
</plist>
PLIST
plutil -lint -s "$NEW" || { rm -f "$NEW"; echo "Couldn't write a valid launchd file." >&2; exit 1; }

# Everything checks out: only now stop the running copy and swap the file in.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
mv "$NEW" "$PLIST"
# launchd can refuse for a moment while the old copy finishes unloading.
tries=0
until launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; do
    tries=$((tries + 1))
    [ "$tries" -lt 5 ] || { echo "launchctl couldn't start it; try again in a few seconds." >&2; exit 1; }
    sleep 1
done
echo "Installed. herdr-telegram is running and will start at login."
echo "Logs: $LOG"
