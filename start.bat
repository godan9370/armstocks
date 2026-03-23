@echo off
:: @RM!2T0CKS — start script (Windows)
cd /d "%~dp0"
pip install -r requirements.txt -q
echo.
echo   Starting @RM!2T0CKS...
echo.
python app.py
pause
