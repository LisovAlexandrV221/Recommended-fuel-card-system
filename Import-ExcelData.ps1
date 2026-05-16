param(
    [string]$ClientsPath = "",
    [string]$TransactionsPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$importer = Join-Path $root "app\backend\import_excel.ps1"
. $importer

Import-ExcelData -ProjectRoot $root -ClientsPath $ClientsPath -TransactionsPath $TransactionsPath
