@echo off
title Bolsa Virtual Pro
cd /d "%~dp0"

echo ============================================
echo   Bolsa Virtual Pro - Iniciando servidor...
echo ============================================
echo.

REM Verifica que Python este instalado
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo No se encontro Python en este equipo.
    echo Descargalo desde https://www.python.org/downloads/
    echo IMPORTANTE: marca la casilla "Add Python to PATH" al instalar.
    pause
    exit /b
)

echo Instalando dependencias necesarias...
pip install -r requirements.txt >nul 2>nul

echo.
echo Abriendo el navegador en unos segundos...
start /min cmd /c "timeout /t 2 >nul & start http://127.0.0.1:5000/"

echo.
echo Servidor corriendo en http://127.0.0.1:5000/
echo Panel de administracion: http://127.0.0.1:5000/admin  (usuario: admin, clave: 123)
echo.
echo NO CIERRES esta ventana mientras uses la aplicacion.
echo Para detenerla, cierra esta ventana o presiona Ctrl+C.
echo.

python app.py

pause
