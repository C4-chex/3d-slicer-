@echo off
setlocal
set "SLICER=D:\3dslicer\3D Slicer 5.12.4\Slicer.exe"
set "MODULE_DIR=D:\3d - 1\CT3D"
if not exist "%SLICER%" (
  echo Cannot find 3D Slicer at:
  echo %SLICER%
  pause
  exit /b 1
)
"%SLICER%" --no-splash --additional-module-path "%MODULE_DIR%" --python-code "slicer.util.selectModule('CT3D')"
