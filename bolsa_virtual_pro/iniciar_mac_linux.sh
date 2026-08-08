#!/bin/bash
# Lanzador de Bolsa Virtual Pro para Mac / Linux.
# Doble clic (Mac: puede pedir permiso la primera vez) o ejecutar:
#   chmod +x iniciar_mac_linux.sh && ./iniciar_mac_linux.sh

cd "$(dirname "$0")"

echo "============================================"
echo "  Bolsa Virtual Pro - Iniciando servidor..."
echo "============================================"
echo ""

# Detecta el comando de Python disponible
if command -v python3 &> /dev/null; then
    PY=python3
    PIP=pip3
elif command -v python &> /dev/null; then
    PY=python
    PIP=pip
else
    echo "No se encontró Python en este equipo."
    echo "Descárgalo desde https://www.python.org/downloads/"
    read -p "Presiona Enter para salir..."
    exit 1
fi

echo "Instalando dependencias necesarias..."
$PIP install -r requirements.txt --quiet --break-system-packages 2>/dev/null || $PIP install -r requirements.txt --quiet

echo ""
echo "Abriendo el navegador en unos segundos..."
( sleep 2
  if command -v open &> /dev/null; then open http://127.0.0.1:5000/;      # macOS
  elif command -v xdg-open &> /dev/null; then xdg-open http://127.0.0.1:5000/; # Linux
  fi
) &

echo ""
echo "Servidor corriendo en http://127.0.0.1:5000/"
echo "Panel de administración: http://127.0.0.1:5000/admin  (usuario: admin, clave: 123)"
echo ""
echo "NO CIERRES esta ventana mientras uses la aplicación."
echo "Para detenerla, presiona Ctrl+C."
echo ""

$PY app.py
