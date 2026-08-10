@echo off
REM Crea Cruscotto.exe: un unico file da copiare su qualsiasi PC Windows,
REM senza bisogno di installare Python.
REM
REM Serve PyInstaller una volta sola:  pip install pyinstaller
REM
REM --add-data incorpora il foglio di stile e la logica del report, che sono
REM file separati sotto assets_web/ e vanno inclusi nell'eseguibile.
cd /d "%~dp0"

echo Controllo che i test passino prima di compilare...
python esegui_test.py
if errorlevel 1 (
  echo.
  echo I test non passano: compilazione annullata.
  pause
  exit /b 1
)

pyinstaller --onefile --noconsole ^
  --name Cruscotto ^
  --add-data "assets_web;assets_web" ^
  cruscotto.py

echo.
echo Fatto: l'eseguibile e' in dist\Cruscotto.exe
echo Copialo dove serve; config.json, report\ e cruscotto.log verranno creati
echo nella stessa cartella dell'eseguibile.
pause
