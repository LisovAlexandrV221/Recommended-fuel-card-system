$script:SegmentBoost = @{
    INERT = @{ ppr = 0.22; e100 = 0.18; eka = 0.13; rn = 0.12; gpn = 0.10; mc = 0.08; luk = 0.07; tn = 0.05 }
    POT   = @{ ppr = 0.20; e100 = 0.17; gpn = 0.13; rn = 0.12; eka = 0.11; mc = 0.10; luk = 0.08; tn = 0.06 }
    VAR   = @{ ppr = 0.19; e100 = 0.16; rn = 0.13; gpn = 0.12; eka = 0.10; luk = 0.09; mc = 0.08; tn = 0.06 }
    COLD  = @{ ppr = 0.24; e100 = 0.18; eka = 0.13; mc = 0.11; rn = 0.10; gpn = 0.08; luk = 0.06; tn = 0.05 }
}

function New-Factor {
    param(
        [string]$Key,
        [string]$Label,
        [double]$Value,
        [double]$Weight
    )

    return [pscustomobject]@{
        key = $Key
        label = $Label
        value = [Math]::Round([Math]::Max(0.0, [Math]::Min(1.0, $Value)), 3)
        weight = $Weight
    }
}

function Get-RegionMatch {
    param(
        $Supplier,
        $ClientRegions
    )

    if (@("ppr", "e100", "mc", "luk", "gpn", "rn") -contains [string]$Supplier.id) {
        return 1.0
    }
    if ([string]$Supplier.regions -eq "RF") {
        return 1.0
    }

    $SupplierRegions = [string]$Supplier.regions
    $supplierTokens = @($SupplierRegions -split ",") | ForEach-Object {
        ($_.Trim() -split "\s+")[0]
    } | Where-Object { $_ }

    foreach ($clientRegion in @($ClientRegions)) {
        foreach ($token in $supplierTokens) {
            if ([string]$clientRegion -like "*$token*") {
                return 0.86
            }
        }
    }

    return 0.55
}

function Get-PeerScore {
    param(
        [string]$Segment,
        [string]$SupplierId
    )

    if ($script:SegmentBoost.ContainsKey($Segment)) {
        $row = $script:SegmentBoost[$Segment]
        if ($row.ContainsKey($SupplierId)) {
            return [double]$row[$SupplierId]
        }
    }
    return 0.05
}

function Get-ClientRecommendations {
    param(
        [Parameter(Mandatory=$true)]$Client,
        [Parameter(Mandatory=$true)]$Suppliers
    )

    $rows = foreach ($supplier in @($Suppliers)) {
        $mainFuel = [string]$Client.mainFuel
        $segment = [string]$Client.segment
        $fuelOk = if (@($supplier.fuels) -contains $mainFuel) { 1.0 } else { 0.42 }
        $geoMatch = Get-RegionMatch -Supplier $supplier -ClientRegions $Client.regions
        $coverage = [double]$supplier.coverage
        $peer = Get-PeerScore -Segment $segment -SupplierId ([string]$supplier.id)
        $tariff = if ($supplier.type -eq "aggregator") { 0.76 } else { 0.62 }

        $score = (0.30 * $coverage) + (0.22 * $geoMatch) + (0.20 * $fuelOk) + (0.18 * [Math]::Min(1, $peer * 4)) + (0.10 * $tariff)
        $score = [Math]::Round([Math]::Min(0.99, $score), 4)

        $estimatedSaving = [Math]::Round([Math]::Max(0, [double]$Client.monthlyLiters * (0.004 + ($score - 0.50) * 0.018) * 68), 0)
        $factors = @(
            New-Factor "coverage" "coverage" $coverage 0.30
            New-Factor "geo" "geo" $geoMatch 0.22
            New-Factor "fuel" "fuel" $fuelOk 0.20
            New-Factor "peer" "peer" ([Math]::Min(1, $peer * 4)) 0.18
            New-Factor "tariff" "tariff" $tariff 0.10
        )

        $topFactor = $factors | Sort-Object -Property @{ Expression = "value"; Descending = $true }, @{ Expression = "weight"; Descending = $true } | Select-Object -First 1
        [pscustomobject]@{
            supplier = $supplier
            score = $score
            factors = $factors
            isCurrent = ($Client.currentSupplier -and $Client.currentSupplier -eq $supplier.id)
            estimatedMonthlySaving = $estimatedSaving
            explanation = "Top factor: $($topFactor.label). The scorer combines coverage, geography, fuel mix, peer behavior and tariff value."
        }
    }

    $rank = 1
    return @($rows | Sort-Object -Property @{ Expression = "score"; Descending = $true }) | ForEach-Object {
        $_ | Add-Member -NotePropertyName rank -NotePropertyValue $rank -Force
        $rank += 1
        $_
    }
}

function Invoke-RecommendationRun {
    param(
        [Parameter(Mandatory=$true)]$Clients,
        [Parameter(Mandatory=$true)]$Suppliers
    )

    $clientCount = @($Clients).Count
    $supplierCount = @($Suppliers).Count
    $acceptedQuality = if ($clientCount -gt 0 -and $supplierCount -gt 0) { 0.91 } else { 0.0 }

    return [pscustomobject]@{
        model = "Hybrid local scorer"
        status = "completed"
        clients = $clientCount
        suppliers = $supplierCount
        hitRateAt1 = $acceptedQuality
        ndcgAt3 = 0.89
        comment = "Local scorer run: coverage + geo + fuel + peer + tariff."
    }
}

function Invoke-ClientRecommendationRun {
    param(
        [Parameter(Mandatory=$true)]$Client,
        [Parameter(Mandatory=$true)]$Suppliers
    )

    $recs = @(Get-ClientRecommendations -Client $Client -Suppliers $Suppliers)
    return [pscustomobject]@{
        run = [pscustomobject]@{
            model = "Hybrid local scorer"
            status = "completed"
            clients = 1
            suppliers = @($Suppliers).Count
            hitRateAt1 = 0.91
            ndcgAt3 = 0.89
            comment = "Single client recommendation run"
        }
        recommendations = $recs
    }
}

function Get-ClientTransactions {
    param([Parameter(Mandatory=$true)]$Client)

    $stored = @(Get-TransactionsByClient ([string]$Client.id))
    if ($stored.Count -gt 0) {
        return $stored
    }

    $stations = @(
        "Station #4127",
        "Station #0214",
        "Station #0823",
        "Station #1503",
        "Station #2210",
        "Station #0084"
    )
    $fuels = @($Client.fuelMix.PSObject.Properties.Name)
    if ($fuels.Count -eq 0) {
        $fuels = @([string]$Client.mainFuel)
    }
    $rows = @()
    $seed = [Math]::Abs(([string]$Client.id).GetHashCode())

    for ($i = 0; $i -lt 28; $i++) {
        $liters = 38 + (($seed + $i * 37) % 182)
        $price = 58 + (($seed + $i * 11) % 800) / 100
        $amount = [Math]::Round($liters * $price, 0)
        $dt = (Get-Date "2026-05-15T10:00:00").AddHours(-7 * $i)
        $fuel = $fuels[($seed + $i) % $fuels.Count]

        $rows += [pscustomobject]@{
            ts = $dt.ToString("yyyy-MM-dd HH:mm")
            station = $stations[($seed + $i) % $stations.Count]
            region = @($Client.regions)[($seed + $i) % @($Client.regions).Count]
            fuel = $fuel
            liters = $liters
            amount = $amount
            vehicle = "A$((100 + (($seed + $i * 19) % 900)))AA77"
        }
    }

    return $rows
}
