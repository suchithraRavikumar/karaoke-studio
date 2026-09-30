@echo off
setlocal
cd /d "%~dp0"
for %%D in ("D:\Anaconda3" "D:\anaconda3" "D:\miniconda3" "%USERPROFILE%\anaconda3" "%USERPROFILE%\Anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\Anaconda3" "%ProgramData%\miniconda3" "C:\anaconda3" "C:\Anaconda3" "C:\miniconda3") do (
  if not defined PYW if exist "%%~D\pythonw.exe" set "PYW=%%~D\pythonw.exe"
)
if not defined PYW set "PYW=pythonw"
start "" "%PYW%" "%~dp0karaoke_studio.py"
