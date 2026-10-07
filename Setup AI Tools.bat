@echo off
setlocal
title Karaoke Studio - AI tools setup (one time)
cd /d "%~dp0"
set "LOG=%~dp0setup_ai.log"
set "MARK=%~dp0models\_checks"
if not exist "%~dp0models" mkdir "%~dp0models"
if exist "%MARK%" rmdir /s /q "%MARK%"
mkdir "%MARK%"
set "TORCH_HOME=%~dp0models\torch"
set "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1"
echo AI setup started %DATE% %TIME% > "%LOG%"
echo.
echo  ==== Karaoke Studio - AI tools ====
echo  Installs two AI helpers that run on your PC:
echo    * Vocal remover (Demucs)  - takes the singing out of the song
echo    * Auto-sync (stable-ts)   - lines up your lyrics with the song
echo  Downloads about 2-3 GB once. Keep this window open until it says "All done".
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

echo.
echo  [1/4] Installing the vocal remover (Demucs + PyTorch)...
echo ---- pip install demucs ---- >> "%LOG%"
"%PY%" -m pip install --upgrade demucs >> "%LOG%" 2>&1
"%PY%" -c "import os,demucs.pretrained,demucs.apply; open(os.path.join(r'%MARK%','demucs'),'w').write('ok'); print('demucs OK'); os._exit(0)" >> "%LOG%" 2>&1
if exist "%MARK%\demucs" (echo     OK) else (echo     *** Vocal remover install FAILED - see setup_ai.log)

echo.
echo  [2/4] Downloading the vocal remover model (about 80 MB)...
echo ---- demucs model ---- >> "%LOG%"
"%PY%" -c "import os; from demucs.pretrained import get_model; get_model('htdemucs'); open(os.path.join(r'%MARK%','demucs_model'),'w').write('ok'); print('htdemucs ready'); os._exit(0)" >> "%LOG%" 2>&1
if exist "%MARK%\demucs_model" (echo     OK) else (echo     *** Model download FAILED - see setup_ai.log)

echo.
echo  [3/4] Installing Auto-sync (stable-ts)...
echo ---- pip install stable-ts ---- >> "%LOG%"
"%PY%" -m pip install --upgrade stable-ts >> "%LOG%" 2>&1
"%PY%" -c "import os,stable_whisper; open(os.path.join(r'%MARK%','stable'),'w').write('ok'); print('stable-ts OK'); os._exit(0)" >> "%LOG%" 2>&1
if exist "%MARK%\stable" (echo     OK) else (echo     *** Auto-sync install FAILED - see setup_ai.log)

echo.
echo  [4/4] Downloading the Auto-sync 'medium' model (about 1.5 GB)...
echo ---- whisper model ---- >> "%LOG%"
"%PY%" -c "import os,stable_whisper; stable_whisper.load_model('medium', device='cpu', download_root=r'%~dp0models'); open(os.path.join(r'%MARK%','whisper_model'),'w').write('ok'); print('medium ready'); os._exit(0)" >> "%LOG%" 2>&1
if exist "%MARK%\whisper_model" (echo     OK) else (echo     *** Model download FAILED - see setup_ai.log)

echo Setup finished %DATE% %TIME% >> "%LOG%"
dir /b "%MARK%" >> "%LOG%"
echo.
echo  All done! Close Karaoke Studio if it is open and open it again.
echo  (If any step says FAILED, tell Claude - it can read setup_ai.log.)
echo.
pause
