$script:DataRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "data"

function Get-DataFile {
    param([Parameter(Mandatory=$true)][string]$Name)
    return Join-Path $script:DataRoot $Name
}

function Read-JsonFile {
    param([Parameter(Mandatory=$true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }

    $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($text)) {
        return @()
    }

    $data = $text | ConvertFrom-Json
    if ($null -eq $data) {
        return @()
    }
    return @($data)
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)]$Data
    )

    $json = @($Data) | ConvertTo-Json -Depth 30
    Set-Content -LiteralPath $Path -Value $json -Encoding UTF8
}

function Get-Clients {
    return Read-JsonFile (Get-DataFile "clients.json")
}

function Get-ClientById {
    param([Parameter(Mandatory=$true)][string]$ClientId)
    return @(Get-Clients) | Where-Object { $_.id -eq $ClientId } | Select-Object -First 1
}

function Get-Suppliers {
    return Read-JsonFile (Get-DataFile "suppliers.json")
}

function Get-Decisions {
    return Read-JsonFile (Get-DataFile "decisions.json")
}

function Get-ModelRuns {
    return Read-JsonFile (Get-DataFile "model_runs.json")
}

function Get-DataSources {
    return Read-JsonFile (Get-DataFile "data_sources.json")
}

function Get-Users {
    return Read-JsonFile (Get-DataFile "users.json")
}

function Get-ModelCatalog {
    return Read-JsonFile (Get-DataFile "model_catalog.json")
}

function Get-Transactions {
    return Read-JsonFile (Get-DataFile "transactions.json")
}

function Get-ClientTransactionFile {
    param([Parameter(Mandatory=$true)][string]$ClientId)

    $safe = $ClientId -replace '[\\/:*?"<>|]', "_"
    return Join-Path (Join-Path $script:DataRoot "client_transactions") "$safe.json"
}

function Get-TransactionsByClient {
    param([Parameter(Mandatory=$true)][string]$ClientId)

    $path = Get-ClientTransactionFile $ClientId
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        return Read-JsonFile $path
    }

    return @(Get-Transactions) | Where-Object { $_.client -eq $ClientId } | Sort-Object ts -Descending
}

function Get-ImportState {
    $path = Get-DataFile "import_state.json"
    $state = @(Read-JsonFile $path)
    if ($state.Count -gt 0) { return $state[0] }
    return $null
}

function Update-DataSource {
    param([Parameter(Mandatory=$true)][string]$SourceId)

    $sources = @(Get-DataSources)
    $updated = $false
    $now = (Get-Date).ToString("yyyy-MM-dd HH:mm")
    $result = foreach ($source in $sources) {
        if ($source.id -eq $SourceId -or $SourceId -eq "all") {
            $source.last = $now
            $source.status = "ok"
            $source.delta = "+0"
            $updated = $true
        }
        $source
    }

    if (-not $updated) {
        throw "Data source not found"
    }

    Write-JsonFile (Get-DataFile "data_sources.json") $result
    return @($result)
}

function Save-Users {
    param([Parameter(Mandatory=$true)]$Users)
    Write-JsonFile (Get-DataFile "users.json") @($Users)
    return @(Get-Users)
}

function Add-User {
    param([Parameter(Mandatory=$true)]$Payload)

    $users = @(Get-Users)
    $max = 0
    foreach ($user in $users) {
        $digits = ([string]$user.id) -replace "\D", ""
        if ($digits) {
            $max = [Math]::Max($max, [int]$digits)
        }
    }

    $newUser = [ordered]@{
        id = "u$($max + 1)"
        name = [string]$Payload.name
        login = [string]$Payload.login
        role = if ($Payload.role) { [string]$Payload.role } else { "manager" }
        status = if ($Payload.status) { [string]$Payload.status } else { "active" }
        last = "-"
    }

    $all = @($users) + @([pscustomobject]$newUser)
    Write-JsonFile (Get-DataFile "users.json") $all
    return [pscustomobject]$newUser
}

function Toggle-UserStatus {
    param([Parameter(Mandatory=$true)][string]$UserId)

    $users = @(Get-Users)
    $found = $false
    $result = foreach ($user in $users) {
        if ($user.id -eq $UserId) {
            $user.status = if ($user.status -eq "active") { "blocked" } else { "active" }
            $found = $true
        }
        $user
    }

    if (-not $found) {
        throw "User not found"
    }

    Write-JsonFile (Get-DataFile "users.json") $result
    return @($result | Where-Object { $_.id -eq $UserId } | Select-Object -First 1)
}

function Add-Decision {
    param([Parameter(Mandatory=$true)]$Payload)

    $decisions = @(Get-Decisions)
    $maxId = 0
    if ($decisions.Count -gt 0) {
        $maxId = [int](($decisions | Measure-Object -Property id -Maximum).Maximum)
    }

    $decision = [ordered]@{
        id = $maxId + 1
        ts = (Get-Date).ToString("yyyy-MM-dd HH:mm")
        client = [string]$Payload.client
        manager = if ($Payload.manager) { [string]$Payload.manager } else { "Local manager" }
        rec = [string]$Payload.rec
        action = [string]$Payload.action
        picked = if ($Payload.picked) { [string]$Payload.picked } else { $null }
        note = if ($Payload.note) { [string]$Payload.note } else { "" }
    }

    $all = @($decision) + @($decisions)
    Write-JsonFile (Get-DataFile "decisions.json") $all
    return [pscustomobject]$decision
}

function Add-ModelRun {
    param([Parameter(Mandatory=$true)]$Run)

    $runs = @(Get-ModelRuns)
    $maxId = 0
    if ($runs.Count -gt 0) {
        $maxId = [int](($runs | Measure-Object -Property id -Maximum).Maximum)
    }

    $newRun = [ordered]@{
        id = $maxId + 1
        ts = (Get-Date).ToString("yyyy-MM-dd HH:mm")
        model = [string]$Run.model
        status = [string]$Run.status
        clients = [int]$Run.clients
        suppliers = [int]$Run.suppliers
        hitRateAt1 = [double]$Run.hitRateAt1
        ndcgAt3 = [double]$Run.ndcgAt3
        comment = [string]$Run.comment
    }

    $all = @($newRun) + @($runs)
    Write-JsonFile (Get-DataFile "model_runs.json") $all
    return [pscustomobject]$newRun
}
