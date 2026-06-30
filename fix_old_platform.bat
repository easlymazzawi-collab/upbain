@echo off
cd /d "%~dp0"
if exist platform (
  echo Xoa folder platform cu (giu research_platform)...
  rmdir /s /q platform
  echo Done.
) else (
  echo Khong co folder platform cu.
)
if exist platform.pyc del /f platform.pyc
if exist __pycache__\platform*.pyc del /f __pycache__\platform*.pyc
pause
