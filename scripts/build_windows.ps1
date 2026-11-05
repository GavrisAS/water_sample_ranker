param(
    [string]$BuildVersion = "0.1.1",
    [string]$SourceSHA = "",
    [switch]$PublicRelease
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
function Check-Exit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "${Step} failed: $LASTEXITCODE" }
}
if ($BuildVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "BuildVersion requires MAJOR.MINOR.PATCH"
}
if (-not $SourceSHA) { $SourceSHA = git rev-parse HEAD; Check-Exit "source" }
uv sync --locked
Check-Exit "sync"
if ($PublicRelease) {
    uv run python -m scripts.run_checks --public
} else {
    uv run python -m scripts.run_checks
}
Check-Exit "checks"
uv run python -m scripts.build_frozen --version $BuildVersion --source-sha $SourceSHA
Check-Exit "build"
$Pointer = Get-Content artifacts/private/frozen-current.json | ConvertFrom-Json
$Receipt = Get-Content $Pointer.receipt | ConvertFrom-Json
uv run python -m scripts.frozen_cli_smoke --binary $Receipt.binary --version $BuildVersion --source-sha $SourceSHA
Check-Exit "frozen CLI"
uv run python -m scripts.verify_candidate --binary $Receipt.binary --version $BuildVersion --source-sha $SourceSHA
Check-Exit "candidate privacy"
$Bundle = Split-Path -Parent $Receipt.binary
if ($env:GITHUB_OUTPUT) { "bundle=$Bundle" >> $env:GITHUB_OUTPUT }
Write-Output "Fresh Windows onedir verified; manual Windows UX/native Excel NOT_RUN"
