# LiteDiff.exe készítése (egyetlen fájl) a dist mappába.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
& $py -m pip install -q pyinstaller
& $py -m PyInstaller --noconfirm --onefile --windowed --name LiteDiff `
    --icon (Join-Path $root 'litediff\web\litediff.ico') `
    --add-data "$(Join-Path $root 'litediff\web');litediff\web" `
    --exclude-module PIL --exclude-module pytest --exclude-module _pytest --exclude-module tkinter `
    --distpath (Join-Path $root 'dist') --workpath (Join-Path $root 'build\work') --specpath (Join-Path $root 'build') `
    (Join-Path $root 'LiteDiff.pyw')
Get-Item (Join-Path $root 'dist\LiteDiff.exe') | ForEach-Object { '{0} – {1:N1} MB' -f $_.FullName, ($_.Length / 1MB) }
