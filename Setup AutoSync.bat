@echo off
setlocal
title Karaoke Studio - Auto-sync setup (one time)
cd /d "%~dp0"
set "LOG=%~dp0setup_autosync.log"
echo Auto-sync setup started %DATE% %TIME% > "%LOG%"
echo.
echo  ==== Auto-sync setup ====
echo  This installs a speech-recognition model that listens to the song and
echo  lines up your lyrics automatically. It downloads about 2-3 GB, once.
echo  Please keep this window open until it says "All done".
echo.
for %%D in ("D:\Anaconda3" "D:\anaconda3" "D:\miniconda3" "%USERPROFILE%\anaconda3" "%USERPROFILE%\Anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\Anaconda3" "C:\anaconda3" "C:\Anaconda3") do (
  if not defined PY if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
)
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
if not defined PY (
  echo  Could not find Python / Anaconda. Run Setup.bat first.
  echo NO PYTHON FOUND >> "%LOG%"
  pause
  exit /b 1
)
echo  Using Python: %PY%
echo Python: %PY% >> "%LOG%"
"%PY%" --version >> "%LOG%" 2>&1
echo.
echo  [1/3] Installing the aligner (stable-ts + PyTorch, about 250 MB)...
echo ---- pip install stable-ts ---- >> "%LOG%"
"%PY%" -m pip install --upgrade stable-ts 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOG%' -Append"
"%PY%" -c "import stable_whisper, torch; print('stable-ts OK, torch', torch.__version__)" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo  *** Installing the aligner FAILED. ***
  echo  Check your internet connection and run this file again.
  echo  (Details were saved to setup_autosync.log - Claude can read it.)
  echo STABLE-TS IMPORT FAILED >> "%LOG%"
  pause
  exit /b 1
)
echo  Aligner installed.
echo.
echo  [2/3] Installing the voice/music separator (demucs, optional)...
echo ---- pip install demucs ---- >> "%LOG%"
"%PY%" -m pip install --upgrade demucs 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOG%' -Append"
"%PY%" -c "import demucs; print('demucs OK')" >> "%LOG%" 2>&1
if errorlevel 1 echo  demucs could not be installed - Auto-sync still works, just without voice separation.
echo.
echo  [3/3] Downloading the recommended 'medium' model (about 1.5 GB)...
echo ---- model download ---- >> "%LOG%"
"%PY%" -c "import stable_whisper; stable_whisper.load_model('medium', device='cpu', download_root=r'%~dp0models'); print('Model ready.')" 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOG%' -Append"
echo Setup finished %DATE% %TIME% >> "%LOG%"
echo.
echo  All done! Close Karaoke Studio if it is open, open it again,
echo  add the song and lyrics, and click  Auto-sync.
echo.
pause
