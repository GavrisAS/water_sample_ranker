param(
    [Parameter(Mandatory=$true)][string]$SyntheticInput,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][int]$ExpectedRows
)
$ErrorActionPreference = "Stop"
if (-not $IsWindows -and $env:OS -ne "Windows_NT") { throw "Windows with Microsoft Excel required" }
if ($ExpectedRows -lt 0 -or $ExpectedRows -gt 1000) { throw "ExpectedRows must be 0..1000" }
$InputPath = (Resolve-Path $SyntheticInput).Path
$OutputPath = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path $OutputPath) { throw "Choose a new isolated output directory" }
if ($OutputPath -match '[\\/](data|templates|gdis_loader[^\\/]*)($|[\\/])') { throw "Protected output directory" }
$InputHash = (Get-FileHash $InputPath -Algorithm SHA256).Hash
New-Item -ItemType Directory $OutputPath | Out-Null
$CopyPath = Join-Path $OutputPath "native-synthetic.xlsx"
Copy-Item $InputPath $CopyPath
$Excel = $null
$Book = $null
$Result = [ordered]@{
    automatic_status = "FAIL"; manual_status = "NOT_RUN";
    windows = [Environment]::OSVersion.VersionString;
    input_sha256 = $InputHash; expected_rows = $ExpectedRows;
    scope = "MICROSOFT_EXCEL_AUTOMATION_ONLY_MANUAL_VISUAL_REQUIRED"
}
try {
    $Excel = New-Object -ComObject Excel.Application
    $Excel.Visible = $false
    $Excel.DisplayAlerts = $false
    $Excel.AskToUpdateLinks = $false
    $Excel.AutomationSecurity = 3 # ForceDisable macros; only synthetic XLSX allowed.
    $Result.excel_version = $Excel.Version
    $Result.excel_build = $Excel.Build
    $Book = $Excel.Workbooks.Open($CopyPath, 0, $false)
    $Names = @($Book.Worksheets | ForEach-Object { $_.Name })
    if (($Names -join '|') -ne 'README|Настройки|Данные|Критерии|Расчет|Рейтинг') { throw "Sheet schema mismatch" }
    $TableCount = 0
    foreach ($Sheet in $Book.Worksheets) { $TableCount += $Sheet.ListObjects.Count }
    if ($TableCount -ne 4) { throw "Table count mismatch" }
    $Rows = $Excel.WorksheetFunction.CountA($Book.Worksheets.Item('Данные').ListObjects.Item('tblData').DataBodyRange.Columns.Item(1))
    if ($Rows -ne $ExpectedRows) { throw "Data rows mismatch: $Rows" }
    $Excel.Calculation = -4105 # xlCalculationAutomatic
    $Excel.CalculateFullRebuild()
    foreach ($Check in @(@('Расчет','J4:K1003'), @('Рейтинг','A4:A1003'), @('Рейтинг','E4:E1003'))) {
        $Values = $Book.Worksheets.Item($Check[0]).Range($Check[1]).Value2
        foreach ($Value in $Values) {
            if ($null -ne $Value -and "$Value" -ne '') { throw "Unapproved numeric score/rank" }
        }
    }
    foreach ($Sheet in $Book.Worksheets) {
        foreach ($Formula in $Sheet.UsedRange.Formula) {
            if ("$Formula" -like '*#REF!*') { throw "Broken formula reference" }
        }
    }
    if ($Book.Worksheets.Item('Рейтинг').ChartObjects().Count -ne 1) { throw "Chart count mismatch" }
    $Book.Save()
    $Book.Close($false)
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Book)
    $Book = $Excel.Workbooks.Open($CopyPath, 0, $true)
    if ($Book.Worksheets.Count -ne 6) { throw "Reopen schema mismatch" }
    $Result.automatic_status = "PASS"
    $Result.output_sha256 = (Get-FileHash $CopyPath -Algorithm SHA256).Hash
    $Result.manual_checklist = "Open without recovery; visual styles/chart/filters; save/close/reopen; screenshots; see docs/WINDOWS_EXCEL_ACCEPTANCE.md"
} catch {
    $Result.error = $_.Exception.Message
    throw
} finally {
    if ($null -ne $Book) { $Book.Close($false); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Book) }
    if ($null -ne $Excel) { $Excel.Quit(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Excel) }
    $Result.input_unchanged = ((Get-FileHash $InputPath -Algorithm SHA256).Hash -eq $InputHash)
    $Result | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $OutputPath "native-result.json")
}
