param(
    [string]$FfmpegPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    python -m venv (Join-Path $projectRoot ".venv")
}
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install --upgrade -r (Join-Path $projectRoot "requirements-desktop.txt")

if ([string]::IsNullOrWhiteSpace($FfmpegPath)) {
    $FfmpegPath = (Get-Command ffmpeg -ErrorAction Stop).Source
}
if (-not (Test-Path $FfmpegPath)) {
    throw "找不到 FFmpeg: $FfmpegPath"
}
$ffprobePath = Join-Path (Split-Path $FfmpegPath) "ffprobe.exe"
if (-not (Test-Path $ffprobePath)) {
    throw "FFmpeg 目录中未找到 ffprobe.exe: $ffprobePath"
}

Remove-Item (Join-Path $projectRoot "build") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $projectRoot "dist") -Recurse -Force -ErrorAction SilentlyContinue
Push-Location $projectRoot
try {
    & $venvPython -m PyInstaller --noconfirm --clean --windowed --name CCTVNewsWeekly --add-binary "$FfmpegPath;bin" --add-binary "$ffprobePath;bin" desktop_app.py
} finally {
    Pop-Location
}

$portableDirectory = Join-Path $projectRoot "dist\CCTVNewsWeekly"
if (-not (Test-Path (Join-Path $portableDirectory "_internal\bin\ffmpeg.exe"))) {
    throw "构建完成但未找到内置 FFmpeg。"
}
if (-not (Test-Path (Join-Path $portableDirectory "_internal\bin\ffprobe.exe"))) {
    throw "构建完成但未找到内置 ffprobe。"
}
Copy-Item (Join-Path $projectRoot "THIRD_PARTY_NOTICES.md") (Join-Path $portableDirectory "THIRD_PARTY_NOTICES.md") -Force
$archive = Join-Path $projectRoot "dist\CCTVNewsWeekly-windows.zip"
Compress-Archive -Path $portableDirectory -DestinationPath $archive -Force
Write-Host "便携包已生成: $archive"
