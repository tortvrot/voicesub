#!/bin/bash
set -e
LOGFILE="/tmp/voicesub_install.log"
echo "=== VoiceSub postinstall $(date) ===" >> "$LOGFILE"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver" ]; then
    echo "Устанавливаю BlackHole..." >> "$LOGFILE"
    installer -pkg "$SCRIPT_DIR/BlackHole2ch.pkg" -target / >> "$LOGFILE" 2>&1 || \
        echo "Не удалось установить BlackHole автоматически" >> "$LOGFILE"
else
    echo "BlackHole уже установлен, пропускаю" >> "$LOGFILE"
fi

sleep 2

osascript "$SCRIPT_DIR/create_multioutput.applescript" >> "$LOGFILE" 2>&1 || \
    echo "Автонастройка не удалась - нужно сделать вручную" >> "$LOGFILE"

echo "Готово." >> "$LOGFILE"
exit 0
