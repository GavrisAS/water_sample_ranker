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
    if ($TableCount -ne 5) { throw "Table count mismatch" }
    $Rows = $Excel.WorksheetFunction.CountA($Book.Worksheets.Item('Данные').ListObjects.Item('tblData').DataBodyRange.Columns.Item(1))
    if ($Rows -ne $ExpectedRows) { throw "Data rows mismatch: $Rows" }
    $Excel.Calculation = -4105 # xlCalculationAutomatic
    $Excel.CalculateFullRebuild()
    if ($ExpectedRows -gt 0) {
        $Score = $Book.Worksheets.Item('Расчет').Range('J4').Value2
        $Rank = $Book.Worksheets.Item('Расчет').Range('K4').Value2
        if ($null -eq $Score -or $Rank -lt 1) { throw "Preliminary score/rank missing" }
        $Settings = $Book.Worksheets.Item('Настройки')
        $Operators = $Settings.ListObjects.Item('tblOperator').DataBodyRange
        $Field = $Settings.Range('B8').Value2
        $Well = $Book.Worksheets.Item('Данные').Range('A7').Value2
        $OperatorRow = $null
        for ($r=1; $r -le $Operators.Rows.Count; $r++) {
            if ($Operators.Cells.Item($r,2).Value2 -eq $Field -and $Operators.Cells.Item($r,3).Value2 -eq $Well) {
                $OperatorRow = $r; break
            }
        }
        if ($null -eq $OperatorRow) { throw "Manual identity missing" }
        $Operators.Cells.Item($OperatorRow,6).Value2 = 'Нет'
        $Excel.CalculateFullRebuild()
        $BaseScore = $Book.Worksheets.Item('Расчет').Range('J4').Value2
        $Settings.Range('D28').Value2 = 0.23
        $Operators.Cells.Item($OperatorRow,6).Value2 = 'Да'
        $Excel.CalculateFullRebuild()
        $EditedScore = $Book.Worksheets.Item('Расчет').Range('J4').Value2
        if ([Math]::Abs($EditedScore - $BaseScore - 0.23) -gt 0.000000001) {
            throw "Manual GTM/weight recalculation mismatch"
        }
        $Result.manual_gtm_weight_formula_check = "PASS"
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
