@echo off
REM ABR Outreach Dashboard launcher.
REM Double-click Windows Explorer'yje arba paleisk iš terminal'o.
REM Browser atsidarys per 5-10s automatiškai (http://localhost:8501).
REM
REM Pirmas paleidimas:
REM   1. .venv\Scripts\activate
REM   2. pip install -r requirements-dashboard.txt
REM   3. python -m dashboard.importer   (CSV -> outreach.db)
REM   4. start-dashboard.bat

cd /d "%~dp0"

echo.
echo === ABR Outreach Dashboard ===
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv nerastas. Paleisk:
    echo    python -m venv .venv
    echo    .venv\Scripts\activate
    echo    pip install -r requirements.txt -r requirements-dashboard.txt
    pause
    exit /b 1
)

if not exist "dashboard\outreach.db" (
    echo Pirmas paleidimas - importuojam CSV i SQLite...
    ".venv\Scripts\python.exe" -m dashboard.importer
    if errorlevel 1 (
        echo [ERROR] Import nepavyko. Patikrink, ar yra output\*.csv failai.
        pause
        exit /b 1
    )
)

echo.
echo Paleidziam Streamlit...
echo Sustabdyti: Ctrl+C ARBA uzdaryk si langa.
echo.

".venv\Scripts\python.exe" -m streamlit run "dashboard\app.py"

pause
