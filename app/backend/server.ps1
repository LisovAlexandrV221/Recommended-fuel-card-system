param(
    [int]$Port = 8765,
    [string]$HostName = "localhost"
)

. (Join-Path $PSScriptRoot "data.ps1")
. (Join-Path $PSScriptRoot "model.ps1")
. (Join-Path $PSScriptRoot "import_excel.ps1")

$FrontendRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "frontend"

function Get-ReasonPhrase {
    param([int]$StatusCode)
    switch ($StatusCode) {
        200 { "OK" }
        201 { "Created" }
        400 { "Bad Request" }
        403 { "Forbidden" }
        404 { "Not Found" }
        422 { "Unprocessable Entity" }
        500 { "Internal Server Error" }
        default { "OK" }
    }
}

function New-HttpResponse {
    param(
        [int]$StatusCode,
        [string]$ContentType,
        [byte[]]$Body
    )

    return [pscustomobject]@{
        StatusCode = $StatusCode
        ContentType = $ContentType
        Body = $Body
    }
}

function New-JsonResponse {
    param(
        [Parameter(Mandatory=$true)]$Data,
        [int]$StatusCode = 200
    )

    $json = $Data | ConvertTo-Json -Depth 40
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    return New-HttpResponse -StatusCode $StatusCode -ContentType "application/json; charset=utf-8" -Body $bytes
}

function New-ErrorResponse {
    param(
        [string]$Message,
        [int]$StatusCode = 400
    )

    return New-JsonResponse -StatusCode $StatusCode -Data ([pscustomobject]@{ error = $Message })
}

function Get-MimeType {
    param([string]$Path)

    switch ([System.IO.Path]::GetExtension($Path).ToLowerInvariant()) {
        ".html" { "text/html; charset=utf-8" }
        ".css"  { "text/css; charset=utf-8" }
        ".js"   { "application/javascript; charset=utf-8" }
        ".json" { "application/json; charset=utf-8" }
        ".svg"  { "image/svg+xml" }
        default { "application/octet-stream" }
    }
}

function Parse-QueryString {
    param([string]$Query)

    $result = @{}
    if ([string]::IsNullOrWhiteSpace($Query)) {
        return $result
    }

    foreach ($part in $Query.TrimStart("?").Split("&")) {
        if ([string]::IsNullOrWhiteSpace($part)) { continue }
        $kv = $part.Split("=", 2)
        $key = [System.Uri]::UnescapeDataString($kv[0])
        $value = if ($kv.Count -gt 1) { [System.Uri]::UnescapeDataString($kv[1].Replace("+", " ")) } else { "" }
        $result[$key] = $value
    }
    return $result
}

function Read-HttpRequest {
    param([Parameter(Mandatory=$true)]$TcpClient)

    $stream = $TcpClient.GetStream()
    $buffer = New-Object byte[] 8192
    $data = New-Object "System.Collections.Generic.List[byte]"
    $headerEnd = -1

    while ($headerEnd -lt 0) {
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -le 0) { return $null }
        for ($i = 0; $i -lt $read; $i++) {
            $data.Add($buffer[$i])
        }
        $text = [System.Text.Encoding]::ASCII.GetString($data.ToArray())
        $headerEnd = $text.IndexOf("`r`n`r`n")
        if ($data.Count -gt 1048576) {
            throw "Request header too large"
        }
    }

    $allBytes = $data.ToArray()
    $headerBytesLength = $headerEnd + 4
    $headerText = [System.Text.Encoding]::ASCII.GetString($allBytes, 0, $headerEnd)
    $lines = $headerText -split "`r`n"
    $requestLine = $lines[0].Split(" ")
    if ($requestLine.Count -lt 2) {
        throw "Invalid request line"
    }

    $headers = @{}
    for ($i = 1; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        $idx = $line.IndexOf(":")
        if ($idx -gt 0) {
            $headers[$line.Substring(0, $idx).Trim().ToLowerInvariant()] = $line.Substring($idx + 1).Trim()
        }
    }

    $contentLength = 0
    if ($headers.ContainsKey("content-length")) {
        $contentLength = [int]$headers["content-length"]
    }

    $bodyBytes = New-Object "System.Collections.Generic.List[byte]"
    for ($i = $headerBytesLength; $i -lt $allBytes.Length; $i++) {
        $bodyBytes.Add($allBytes[$i])
    }

    while ($bodyBytes.Count -lt $contentLength) {
        $read = $stream.Read($buffer, 0, [Math]::Min($buffer.Length, $contentLength - $bodyBytes.Count))
        if ($read -le 0) { break }
        for ($i = 0; $i -lt $read; $i++) {
            $bodyBytes.Add($buffer[$i])
        }
    }

    $target = $requestLine[1]
    $path = $target
    $query = ""
    $qIndex = $target.IndexOf("?")
    if ($qIndex -ge 0) {
        $path = $target.Substring(0, $qIndex)
        $query = $target.Substring($qIndex + 1)
    }

    $body = ""
    if ($contentLength -gt 0) {
        $body = [System.Text.Encoding]::UTF8.GetString($bodyBytes.ToArray(), 0, [Math]::Min($contentLength, $bodyBytes.Count))
    }

    return [pscustomobject]@{
        Method = $requestLine[0].ToUpperInvariant()
        Path = [System.Uri]::UnescapeDataString($path)
        Query = Parse-QueryString $query
        Body = $body
    }
}

function Send-HttpResponse {
    param(
        [Parameter(Mandatory=$true)]$TcpClient,
        [Parameter(Mandatory=$true)]$Response
    )

    $stream = $TcpClient.GetStream()
    $reason = Get-ReasonPhrase $Response.StatusCode
    $headers = @(
        "HTTP/1.1 $($Response.StatusCode) $reason",
        "Content-Type: $($Response.ContentType)",
        "Content-Length: $($Response.Body.Length)",
        "Access-Control-Allow-Origin: *",
        "Access-Control-Allow-Methods: GET, POST, OPTIONS",
        "Access-Control-Allow-Headers: Content-Type",
        "Connection: close",
        "",
        ""
    ) -join "`r`n"

    $headerBytes = [System.Text.Encoding]::ASCII.GetBytes($headers)
    $stream.Write($headerBytes, 0, $headerBytes.Length)
    if ($Response.Body.Length -gt 0) {
        $stream.Write($Response.Body, 0, $Response.Body.Length)
    }
    $stream.Flush()
}

function New-StaticResponse {
    param([Parameter(Mandatory=$true)]$Path)

    $relative = if ($Path -eq "/") { "index.html" } else { $Path.TrimStart("/") }
    $candidate = Join-Path $FrontendRoot $relative
    $fullPath = [System.IO.Path]::GetFullPath($candidate)
    $rootPath = [System.IO.Path]::GetFullPath($FrontendRoot)

    if (-not $fullPath.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return New-ErrorResponse -StatusCode 403 -Message "Forbidden"
    }

    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        return New-ErrorResponse -StatusCode 404 -Message "Not found"
    }

    $bytes = [System.IO.File]::ReadAllBytes($fullPath)
    return New-HttpResponse -StatusCode 200 -ContentType (Get-MimeType $fullPath) -Body $bytes
}

function Handle-Api {
    param([Parameter(Mandatory=$true)]$Request)

    $method = $Request.Method
    $path = $Request.Path.TrimEnd("/")
    if ($path -eq "") { $path = "/" }

    if ($method -eq "OPTIONS") {
        return New-JsonResponse -Data ([pscustomobject]@{ ok = $true })
    }

    try {
        if ($method -eq "GET" -and $path -eq "/api/health") {
            return New-JsonResponse -Data ([pscustomobject]@{
                status = "ok"
                service = "recommended-fuel-card-system"
                time = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
            })
        }

        if ($method -eq "GET" -and $path -eq "/api/meta") {
            $clients = @(Get-Clients)
            $suppliers = @(Get-Suppliers)
            $decisions = @(Get-Decisions)
            $runs = @(Get-ModelRuns)
            $sources = @(Get-DataSources)
            $users = @(Get-Users)
            $importState = Get-ImportState
            return New-JsonResponse -Data ([pscustomobject]@{
                clients = $clients.Count
                suppliers = $suppliers.Count
                decisions = $decisions.Count
                sources = $sources.Count
                users = $users.Count
                importState = $importState
                lastRun = if ($runs.Count -gt 0) { $runs[0] } else { $null }
            })
        }

        if ($method -eq "GET" -and $path -eq "/api/suppliers") {
            return New-JsonResponse -Data @(Get-Suppliers)
        }

        if ($method -eq "GET" -and $path -eq "/api/clients") {
            $clients = @(Get-Clients)
            $search = $Request.Query["search"]
            $priority = $Request.Query["priority"]

            if (-not [string]::IsNullOrWhiteSpace($search)) {
                $q = $search.ToLowerInvariant()
                $clients = @($clients | Where-Object {
                    ([string]$_.name).ToLowerInvariant().Contains($q) -or
                    ([string]$_.id).ToLowerInvariant().Contains($q) -or
                    ([string]$_.inn).Contains($q)
                })
            }

            if ($priority -eq "true") {
                $clients = @($clients | Where-Object { [double]$_.priority -ge 0.60 })
            }

            $clients = @($clients | Sort-Object -Property @{ Expression = "priority"; Descending = $true })
            return New-JsonResponse -Data $clients
        }

        if ($method -eq "GET" -and $path -match "^/api/clients/([^/]+)$") {
            $clientId = [System.Uri]::UnescapeDataString($Matches[1])
            $client = Get-ClientById $clientId
            if ($null -eq $client) {
                return New-ErrorResponse -StatusCode 404 -Message "Client not found"
            }
            return New-JsonResponse -Data $client
        }

        if ($method -eq "GET" -and $path -match "^/api/clients/([^/]+)/recommendations$") {
            $clientId = [System.Uri]::UnescapeDataString($Matches[1])
            $client = Get-ClientById $clientId
            if ($null -eq $client) {
                return New-ErrorResponse -StatusCode 404 -Message "Client not found"
            }
            $recs = Get-ClientRecommendations -Client $client -Suppliers @(Get-Suppliers)
            return New-JsonResponse -Data $recs
        }

        if ($method -eq "POST" -and $path -match "^/api/clients/([^/]+)/recommendations/run$") {
            $clientId = [System.Uri]::UnescapeDataString($Matches[1])
            $client = Get-ClientById $clientId
            if ($null -eq $client) {
                return New-ErrorResponse -StatusCode 404 -Message "Client not found"
            }
            $result = Invoke-ClientRecommendationRun -Client $client -Suppliers @(Get-Suppliers)
            $saved = Add-ModelRun $result.run
            return New-JsonResponse -StatusCode 201 -Data ([pscustomobject]@{
                run = $saved
                recommendations = $result.recommendations
            })
        }

        if ($method -eq "GET" -and $path -match "^/api/clients/([^/]+)/transactions$") {
            $clientId = [System.Uri]::UnescapeDataString($Matches[1])
            $client = Get-ClientById $clientId
            if ($null -eq $client) {
                return New-ErrorResponse -StatusCode 404 -Message "Client not found"
            }
            return New-JsonResponse -Data @(Get-ClientTransactions $client)
        }

        if ($method -eq "GET" -and $path -eq "/api/decisions") {
            return New-JsonResponse -Data @(Get-Decisions)
        }

        if ($method -eq "POST" -and $path -eq "/api/decisions") {
            $payload = if ([string]::IsNullOrWhiteSpace($Request.Body)) { [pscustomobject]@{} } else { $Request.Body | ConvertFrom-Json }
            if (-not $payload.client -or -not $payload.rec -or -not $payload.action) {
                return New-ErrorResponse -StatusCode 422 -Message "client, rec and action are required"
            }
            $decision = Add-Decision $payload
            return New-JsonResponse -StatusCode 201 -Data $decision
        }

        if ($method -eq "GET" -and $path -eq "/api/model-runs") {
            return New-JsonResponse -Data @(Get-ModelRuns)
        }

        if ($method -eq "GET" -and $path -eq "/api/model-catalog") {
            return New-JsonResponse -Data @(Get-ModelCatalog)
        }

        if ($method -eq "POST" -and $path -eq "/api/recalculate") {
            $run = Invoke-RecommendationRun -Clients @(Get-Clients) -Suppliers @(Get-Suppliers)
            $saved = Add-ModelRun $run
            return New-JsonResponse -StatusCode 201 -Data $saved
        }

        if ($method -eq "GET" -and $path -eq "/api/import-state") {
            return New-JsonResponse -Data (Get-ImportState)
        }

        if ($method -eq "POST" -and $path -eq "/api/import-excel") {
            $result = Import-ExcelData
            return New-JsonResponse -StatusCode 201 -Data $result
        }

        if ($method -eq "GET" -and $path -eq "/api/data-sources") {
            return New-JsonResponse -Data @(Get-DataSources)
        }

        if ($method -eq "POST" -and $path -match "^/api/data-sources/([^/]+)/reload$") {
            $sourceId = [System.Uri]::UnescapeDataString($Matches[1])
            $sources = Update-DataSource $sourceId
            return New-JsonResponse -Data $sources
        }

        if ($method -eq "GET" -and $path -eq "/api/users") {
            return New-JsonResponse -Data @(Get-Users)
        }

        if ($method -eq "POST" -and $path -eq "/api/users") {
            $payload = if ([string]::IsNullOrWhiteSpace($Request.Body)) { [pscustomobject]@{} } else { $Request.Body | ConvertFrom-Json }
            if (-not $payload.name -or -not $payload.login) {
                return New-ErrorResponse -StatusCode 422 -Message "name and login are required"
            }
            return New-JsonResponse -StatusCode 201 -Data (Add-User $payload)
        }

        if ($method -eq "POST" -and $path -match "^/api/users/([^/]+)/toggle$") {
            $userId = [System.Uri]::UnescapeDataString($Matches[1])
            return New-JsonResponse -Data (Toggle-UserStatus $userId)
        }

        return New-ErrorResponse -StatusCode 404 -Message "API route not found"
    }
    catch {
        return New-ErrorResponse -StatusCode 500 -Message $_.Exception.Message
    }
}

function Handle-Request {
    param([Parameter(Mandatory=$true)]$Request)

    if ($Request.Path -like "/api/*") {
        return Handle-Api $Request
    }
    return New-StaticResponse $Request.Path
}

$ip = if ($HostName -eq "0.0.0.0") { [System.Net.IPAddress]::Any } else { [System.Net.IPAddress]::Loopback }
$listener = [System.Net.Sockets.TcpListener]::new($ip, $Port)

try {
    $listener.Start()
    Write-Host "Recommended fuel card system is running at http://localhost:$Port/"
    Write-Host "Press Ctrl+C to stop."

    while ($true) {
        $tcpClient = $listener.AcceptTcpClient()
        try {
            $request = Read-HttpRequest $tcpClient
            if ($null -ne $request) {
                $response = Handle-Request $request
                Send-HttpResponse -TcpClient $tcpClient -Response $response
            }
        }
        catch {
            $response = New-ErrorResponse -StatusCode 500 -Message $_.Exception.Message
            Send-HttpResponse -TcpClient $tcpClient -Response $response
        }
        finally {
            $tcpClient.Close()
        }
    }
}
finally {
    $listener.Stop()
}
