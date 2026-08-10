@echo off
REM Genera il report e lo apre nel browser.
REM La prima volta chiede dove si trova il database del gestionale.
cd /d "%~dp0"
python cruscotto.py
if errorlevel 1 (
  echo.
  echo Qualcosa non ha funzionato. I dettagli sono nel file cruscotto.log
  pause
)
