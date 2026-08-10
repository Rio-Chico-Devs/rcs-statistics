@echo off
REM Sceglie un database diverso da quello configurato e genera il report.
cd /d "%~dp0"
python cruscotto.py --cambia-db
if errorlevel 1 (
  echo.
  echo Qualcosa non ha funzionato. I dettagli sono nel file cruscotto.log
  pause
)
