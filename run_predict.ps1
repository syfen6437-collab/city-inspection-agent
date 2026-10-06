param(
    [string]$Source = "",
    [string]$ModelPath = "",
    [ValidateSet("test", "train")]
    [string]$Split = "test",
    [int]$Limit = 0,
    [string]$ResultDir = "result",
    [string]$CacheDir = "cache",
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundledPython = "C:\Users\MR\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$LocalPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if ([string]::IsNullOrWhiteSpace($Source)) {
    $Source = (Get-ChildItem -LiteralPath (Split-Path -Parent $ProjectRoot) -Filter "*.zip" -File | Select-Object -First 1).FullName
}
if ([string]::IsNullOrWhiteSpace($ModelPath)) {
    $ModelPath = Join-Path $ProjectRoot "models\Qwen3-8B"
}
if ([string]::IsNullOrWhiteSpace($Source) -or -not (Test-Path -LiteralPath $Source)) {
    throw "Dataset ZIP was not found. Pass -Source with the official ZIP path."
}

if (Test-Path -LiteralPath $LocalPython) {
    $Python = $LocalPython
    $PythonPrefix = @()
} elseif (Test-Path -LiteralPath $BundledPython) {
    $Python = $BundledPython
    $PythonPrefix = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
    $PythonPrefix = @("-3")
} else {
    throw "Python was not found. Install Python or create .venv."
}

Set-Location -LiteralPath $ProjectRoot
$env:CITY_AGENT_MODEL_PATH = $ModelPath
$Arguments = @(
    "scripts\predict.py",
    "--source", $Source,
    "--model-path", $ModelPath,
    "--split", $Split,
    "--result-dir", $ResultDir,
    "--cache-dir", $CacheDir
)
if ($Limit -gt 0) { $Arguments += @("--limit", $Limit) }
if ($Resume) { $Arguments += "--resume" }

Write-Host "Project: $ProjectRoot"
Write-Host "Python:  $Python"
Write-Host "Model:   $ModelPath"
Write-Host "Source:  $Source"
& $Python @PythonPrefix @Arguments
exit $LASTEXITCODE
