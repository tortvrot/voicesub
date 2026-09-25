tell application "Audio MIDI Setup"
    activate
end tell

delay 1

tell application "System Events"
    tell process "Audio MIDI Setup"
        try
            click button 1 of window 1
            delay 0.5
            click menu item "Create Multi-Output Device" of menu 1 of button 1 of window 1
            delay 0.5
        on error errMsg
            log "Не удалось выполнить UI-автоматизацию: " & errMsg
        end try
    end tell
end tell

delay 1

tell application "Audio MIDI Setup"
    quit
end tell
