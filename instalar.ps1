# Instalador de Accesos Directos. NO requiere Python ni permisos de administrador.
# Instala la app compilada (.exe) desde la carpeta donde esta este script
# (la carpeta de actualizaciones de la empresa) en:
#   %LOCALAPPDATA%\AccesosDirectos\app
# y crea accesos directos en el Escritorio y en el menu Inicio.
# (Solo ASCII a proposito: Windows PowerShell 5.1 lee mal los acentos sin BOM.)

$ErrorActionPreference = "Stop"

try {
    $share = $PSScriptRoot
    $manifestPath = Join-Path $share "latest.json"
    if (-not (Test-Path $manifestPath)) {
        throw "No se encuentra latest.json en $share. Ejecuta este instalador desde la carpeta de actualizaciones."
    }

    $manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $manifest.exe -or -not $manifest.exe.file) {
        throw "La version publicada no incluye la app compilada (quien publica debe usar publicar.py --build)."
    }

    $zipSource = Join-Path $share $manifest.exe.file
    if (-not (Test-Path $zipSource)) { throw "No se encuentra el paquete $($manifest.exe.file)." }

    Write-Host "Instalando Accesos Directos $($manifest.version) ..."

    # Copia local + verificacion de integridad antes de tocar nada.
    $tmpZip = Join-Path $env:TEMP "AccesosDirectos-instalacion.zip"
    Copy-Item $zipSource $tmpZip -Force
    $hash = (Get-FileHash $tmpZip -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $manifest.exe.sha256.ToLower()) {
        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
        throw "El paquete no coincide con su suma SHA-256 (archivo danado o modificado). No se ha instalado nada."
    }

    $appDir = Join-Path $env:LOCALAPPDATA "AccesosDirectos\app"
    $exe = Join-Path $appDir "main.exe"

    # Si ya esta en marcha, se cierra para poder reemplazar los archivos.
    Get-Process -Name "main" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exe } | Stop-Process -Force
    Start-Sleep -Seconds 1

    if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force }
    New-Item -ItemType Directory -Path $appDir -Force | Out-Null

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($tmpZip, $appDir)
    Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path $exe)) { throw "El paquete no contiene main.exe." }

    # Accesos directos (Escritorio y menu Inicio).
    $shell = New-Object -ComObject WScript.Shell
    $folders = @(
        [Environment]::GetFolderPath("Desktop"),
        (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs")
    )
    foreach ($folder in $folders) {
        $lnk = $shell.CreateShortcut((Join-Path $folder "Accesos Directos.lnk"))
        $lnk.TargetPath = $exe
        $lnk.WorkingDirectory = $appDir
        $lnk.IconLocation = "$exe,0"
        $lnk.Save()
    }

    Write-Host "Listo. Abriendo la aplicacion..."
    Start-Process $exe
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Read-Host "Pulsa Intro para cerrar"
    exit 1
}
