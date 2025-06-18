@echo off
taskkill /F /IM python.exe
timeout /t 2 /nobreak
start /B python bot.py
exit 