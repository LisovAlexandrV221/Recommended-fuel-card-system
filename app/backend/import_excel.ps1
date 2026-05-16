. (Join-Path $PSScriptRoot "data.ps1")

function Add-XlsxTsvConverter {
    if ("LocalXlsxTsvConverter" -as [type]) { return }

    $code = @"
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;

public static class LocalXlsxTsvConverter
{
    private static int ColumnIndex(string reference)
    {
        Match match = Regex.Match(reference ?? "", "^[A-Z]+");
        string letters = match.Value;
        int n = 0;
        foreach (char ch in letters)
        {
            n = n * 26 + (ch - 'A' + 1);
        }
        return n - 1;
    }

    private static string Clean(string value)
    {
        if (value == null) return "";
        return value.Replace('\t', ' ').Replace('\r', ' ').Replace('\n', ' ');
    }

    public static int Convert(string xlsxPath, string tsvPath)
    {
        int rows = 0;
        using (ZipArchive zip = ZipFile.OpenRead(xlsxPath))
        {
            ZipArchiveEntry entry = zip.GetEntry("xl/worksheets/sheet1.xml");
            if (entry == null) throw new Exception("Worksheet xl/worksheets/sheet1.xml not found");

            XmlReaderSettings settings = new XmlReaderSettings();
            settings.IgnoreWhitespace = true;

            using (XmlReader reader = XmlReader.Create(entry.Open(), settings))
            using (StreamWriter writer = new StreamWriter(tsvPath, false, new UTF8Encoding(false)))
            {
                while (reader.Read())
                {
                    if (reader.NodeType != XmlNodeType.Element || reader.LocalName != "row") continue;

                    SortedDictionary<int, string> row = new SortedDictionary<int, string>();
                    int maxCol = -1;

                    using (XmlReader rowReader = reader.ReadSubtree())
                    {
                        while (rowReader.Read())
                        {
                            if (rowReader.NodeType != XmlNodeType.Element || rowReader.LocalName != "c") continue;

                            string reference = rowReader.GetAttribute("r");
                            int col = ColumnIndex(reference);
                            if (col > maxCol) maxCol = col;
                            string value = "";

                            using (XmlReader cellReader = rowReader.ReadSubtree())
                            {
                                while (cellReader.Read())
                                {
                                    if (cellReader.NodeType == XmlNodeType.Element && (cellReader.LocalName == "v" || cellReader.LocalName == "t"))
                                    {
                                        value = cellReader.ReadElementContentAsString();
                                    }
                                }
                            }

                            row[col] = Clean(value);
                        }
                    }

                    if (maxCol >= 0)
                    {
                        for (int i = 0; i <= maxCol; i++)
                        {
                            if (i > 0) writer.Write('\t');
                            string value;
                            if (row.TryGetValue(i, out value)) writer.Write(value);
                        }
                    }
                    writer.WriteLine();
                    rows++;
                }
            }
        }
        return rows;
    }
}
"@
    Add-Type -TypeDefinition $code -ReferencedAssemblies @("System.Xml.dll", "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll")
}

function Convert-XlsxToTsv {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$OutputPath
    )

    Add-XlsxTsvConverter
    return [LocalXlsxTsvConverter]::Convert((Resolve-Path $Path), $OutputPath)
}

function Get-XlsxColumnIndex {
    param([Parameter(Mandatory=$true)][string]$Reference)

    $letters = ([regex]::Match($Reference, "^[A-Z]+")).Value
    $n = 0
    foreach ($ch in $letters.ToCharArray()) {
        $n = $n * 26 + ([int][char]$ch - [int][char]'A' + 1)
    }
    return $n - 1
}

function Read-XlsxRows {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][scriptblock]$OnRow
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $Path))
    try {
        $entry = $zip.GetEntry("xl/worksheets/sheet1.xml")
        if ($null -eq $entry) {
            throw "Worksheet xl/worksheets/sheet1.xml not found"
        }

        $settings = [System.Xml.XmlReaderSettings]::new()
        $settings.IgnoreWhitespace = $true
        $reader = [System.Xml.XmlReader]::Create($entry.Open(), $settings)
        $rowNumber = 0

        while ($reader.Read()) {
            if ($reader.NodeType -ne [System.Xml.XmlNodeType]::Element -or $reader.LocalName -ne "row") {
                continue
            }

            $row = @{}
            if (-not $reader.IsEmptyElement) {
                while ($reader.Read()) {
                    if ($reader.NodeType -eq [System.Xml.XmlNodeType]::EndElement -and $reader.LocalName -eq "row") {
                        break
                    }

                    if ($reader.NodeType -ne [System.Xml.XmlNodeType]::Element -or $reader.LocalName -ne "c") {
                        continue
                    }

                    $ref = $reader.GetAttribute("r")
                    $col = Get-XlsxColumnIndex $ref
                    $value = ""

                    if (-not $reader.IsEmptyElement) {
                        while ($reader.Read()) {
                            if ($reader.NodeType -eq [System.Xml.XmlNodeType]::EndElement -and $reader.LocalName -eq "c") {
                                break
                            }

                            if ($reader.NodeType -eq [System.Xml.XmlNodeType]::Element -and ($reader.LocalName -eq "v" -or $reader.LocalName -eq "t")) {
                                $value = $reader.ReadElementContentAsString()
                                break
                            }
                        }
                    }

                    $row[$col] = $value
                }
            }

            & $OnRow $row $rowNumber
            $rowNumber += 1
        }

        $reader.Close()
    }
    finally {
        $zip.Dispose()
    }
}

function Get-Cell {
    param($Row, [int]$Index)
    if ($Row.ContainsKey($Index)) { return [string]$Row[$Index] }
    return ""
}

function Get-Number {
    param($Value)
    if ([string]::IsNullOrWhiteSpace([string]$Value)) { return 0.0 }
    $text = ([string]$Value).Replace(",", ".")
    $result = 0.0
    if ([double]::TryParse($text, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$result)) {
        return $result
    }
    return 0.0
}

function Get-DateStringFromExcel {
    param($Value)
    $n = Get-Number $Value
    if ($n -le 0) { return $null }
    try {
        return ([DateTime]::FromOADate($n)).ToString("yyyy-MM-dd")
    }
    catch {
        return $null
    }
}

function Get-DateTimeStringFromExcel {
    param($Value)
    $n = Get-Number $Value
    if ($n -le 0) { return $null }
    try {
        return ([DateTime]::FromOADate($n)).ToString("yyyy-MM-dd HH:mm")
    }
    catch {
        return $null
    }
}

function Get-SupplierFromCardType {
    param([string]$CardType)
    if ([string]::IsNullOrWhiteSpace($CardType)) { return "unknown" }
    return ($CardType -split "\.")[0]
}

function Add-MapNumber {
    param($Map, [string]$Key, [double]$Value)
    if ([string]::IsNullOrWhiteSpace($Key)) { $Key = "unknown" }
    if (-not $Map.ContainsKey($Key)) { $Map[$Key] = 0.0 }
    $Map[$Key] = [double]$Map[$Key] + $Value
}

function Add-SetValue {
    param($Map, [string]$Key)
    if ([string]::IsNullOrWhiteSpace($Key)) { return }
    $Map[$Key] = $true
}

function Convert-MapToShareObject {
    param($Map)
    $sum = 0.0
    foreach ($value in $Map.Values) { $sum += [double]$value }
    $obj = [ordered]@{}
    if ($sum -le 0) { return [pscustomobject]$obj }
    foreach ($entry in ($Map.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 8)) {
        $obj[$entry.Key] = [Math]::Round(([double]$entry.Value) / $sum, 3)
    }
    return [pscustomobject]$obj
}

function Get-TopKeys {
    param($Map, [int]$Take = 5)
    return @($Map.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First $Take | ForEach-Object { $_.Key })
}

function New-ClientAgg {
    return [ordered]@{
        txCount = 0
        totalVolume = 0.0
        totalSpend = 0.0
        totalMarketSpend = 0.0
        supplierVolume = @{}
        fuelVolume = @{}
        regionVolume = @{}
        monthVolume = @{}
        monthSpend = @{}
        samples = New-Object "System.Collections.Generic.List[object]"
        priceRatioSum = 0.0
    }
}

function Get-CsvValues {
    param($Row)
    return @($Row.PSObject.Properties | ForEach-Object { [string]$_.Value })
}

function Add-FuelDataImporter {
    if ("LocalFuelDataImporter" -as [type]) { return }

    $code = @"
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;

public sealed class LocalFuelImportStats
{
    public int Clients;
    public int Transactions;
    public int TransactionSamples;
    public int Suppliers;
}

public static class LocalFuelDataImporter
{
    private sealed class ClientRow
    {
        public string Id = "";
        public string DateEnd = null;
        public string Office = "";
        public string Activity = "";
        public string Status = "";
        public string ClientType = "";
        public double Monthly = 0.0;
    }

    private sealed class TxSample
    {
        public string Client = "";
        public string Ts = null;
        public string Station = "";
        public string Region = "";
        public string Fuel = "";
        public double Liters = 0.0;
        public double Amount = 0.0;
    }

    private sealed class ClientAgg
    {
        public int TxCount = 0;
        public double TotalVolume = 0.0;
        public double TotalSpend = 0.0;
        public double TotalMarketSpend = 0.0;
        public double PriceRatioSum = 0.0;
        public Dictionary<string, double> SupplierVolume = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, double> FuelVolume = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, double> RegionVolume = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, double> MonthVolume = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
        public Dictionary<string, double> MonthSpend = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
        public List<TxSample> Samples = new List<TxSample>();
    }

    private sealed class SupplierAgg
    {
        public double Volume = 0.0;
        public int Tx = 0;
        public double PriceRatioSum = 0.0;
        public HashSet<string> Clients = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        public HashSet<string> Stations = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        public HashSet<string> Regions = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        public HashSet<string> Fuels = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    }

    public static LocalFuelImportStats Import(string clientsTsv, string txTsv, string clientsJson, string suppliersJson, string transactionsJson)
    {
        List<ClientRow> clients = ReadClients(clientsTsv);
        Dictionary<string, ClientAgg> aggs = new Dictionary<string, ClientAgg>();
        Dictionary<string, SupplierAgg> suppliers = new Dictionary<string, SupplierAgg>();
        int txRows = ReadTransactions(txTsv, aggs, suppliers);
        List<TxSample> samples = WriteClients(clientsJson, clients, aggs);
        int supplierCount = WriteSuppliers(suppliersJson, suppliers);
        WriteTransactions(transactionsJson, samples);

        return new LocalFuelImportStats {
            Clients = clients.Count,
            Transactions = txRows,
            TransactionSamples = samples.Count,
            Suppliers = supplierCount
        };
    }

    private static List<ClientRow> ReadClients(string path)
    {
        List<ClientRow> clients = new List<ClientRow>();
        using (StreamReader reader = new StreamReader(path, Encoding.UTF8))
        {
            reader.ReadLine();
            while (!reader.EndOfStream)
            {
                string line = reader.ReadLine();
                if (String.IsNullOrWhiteSpace(line)) continue;
                string[] v = line.Split('\t');
                if (v.Length < 8 || String.IsNullOrWhiteSpace(v[0])) continue;
                double shipments = Num(v[7]);
                clients.Add(new ClientRow {
                    Id = v[0],
                    DateEnd = ExcelDate(v[2], "yyyy-MM-dd"),
                    Status = ValueAt(v, 3),
                    ClientType = ValueAt(v, 4),
                    Office = ValueAt(v, 5),
                    Activity = ValueAt(v, 6),
                    Monthly = Math.Round(shipments / 6.0, 0)
                });
            }
        }
        return clients;
    }

    private static int ReadTransactions(string path, Dictionary<string, ClientAgg> aggs, Dictionary<string, SupplierAgg> suppliers)
    {
        int txRows = 0;
        using (StreamReader reader = new StreamReader(path, Encoding.UTF8))
        {
            reader.ReadLine();
            while (!reader.EndOfStream)
            {
                string line = reader.ReadLine();
                if (String.IsNullOrWhiteSpace(line)) continue;
                string[] v = line.Split('\t');
                if (v.Length < 15) continue;
                string clientId = v[1];
                if (String.IsNullOrWhiteSpace(clientId)) continue;

                double volume = Num(v[5]);
                if (volume <= 0.0) continue;
                double marketPrice = Num(v[6]);
                double clientPrice = Num(v[7]);
                string supplier = SupplierFromCard(ValueAt(v, 2));
                string station = ValueAt(v, 10);
                string region = ValueAt(v, 11);
                string fuel = ValueAt(v, 14);
                string ts = ExcelDate(ValueAt(v, 0), "yyyy-MM-dd HH:mm");
                string month = ts == null ? "unknown" : ts.Substring(0, 7);
                double spend = volume * clientPrice;
                double marketSpend = volume * marketPrice;

                ClientAgg agg;
                if (!aggs.TryGetValue(clientId, out agg))
                {
                    agg = new ClientAgg();
                    aggs[clientId] = agg;
                }

                agg.TxCount += 1;
                agg.TotalVolume += volume;
                agg.TotalSpend += spend;
                agg.TotalMarketSpend += marketSpend;
                if (marketPrice > 0.0) agg.PriceRatioSum += clientPrice / marketPrice;
                AddMap(agg.SupplierVolume, supplier, volume);
                AddMap(agg.FuelVolume, fuel, volume);
                AddMap(agg.RegionVolume, region, volume);
                AddMap(agg.MonthVolume, month, volume);
                AddMap(agg.MonthSpend, month, spend);

                if (agg.Samples.Count < 28)
                {
                    agg.Samples.Add(new TxSample {
                        Client = clientId,
                        Ts = ts,
                        Station = station,
                        Region = region,
                        Fuel = fuel,
                        Liters = Math.Round(volume, 2),
                        Amount = Math.Round(spend, 0)
                    });
                }

                SupplierAgg sagg;
                if (!suppliers.TryGetValue(supplier, out sagg))
                {
                    sagg = new SupplierAgg();
                    suppliers[supplier] = sagg;
                }
                sagg.Volume += volume;
                sagg.Tx += 1;
                if (marketPrice > 0.0) sagg.PriceRatioSum += clientPrice / marketPrice;
                AddSet(sagg.Clients, clientId);
                AddSet(sagg.Stations, station);
                AddSet(sagg.Regions, region);
                AddSet(sagg.Fuels, fuel);
                txRows += 1;
            }
        }
        return txRows;
    }

    private static List<TxSample> WriteClients(string path, List<ClientRow> clients, Dictionary<string, ClientAgg> aggs)
    {
        List<TxSample> samples = new List<TxSample>();
        using (StreamWriter writer = new StreamWriter(path, false, new UTF8Encoding(false)))
        {
            writer.WriteLine("[");
            for (int i = 0; i < clients.Count; i++)
            {
                ClientRow c = clients[i];
                ClientAgg agg = null;
                aggs.TryGetValue(c.Id, out agg);

                double monthlyLiters = c.Monthly;
                double monthlySpend = 0.0;
                int fleet = Math.Max(1, Math.Min(300, (int)Math.Round(monthlyLiters / 2500.0, 0)));
                string currentSupplier = null;
                string mainFuel = "";
                List<string> regions = new List<string>();
                List<string> signals = new List<string>();
                string segment = "COLD";
                double priority = 0.0;

                if (agg != null && agg.TxCount > 0)
                {
                    monthlyLiters = Math.Round(agg.TotalVolume / 6.0, 0);
                    monthlySpend = Math.Round(agg.TotalSpend / 6.0, 0);
                    fleet = Math.Max(1, Math.Min(300, (int)Math.Round(monthlyLiters / 2500.0, 0)));
                    currentSupplier = TopKey(agg.SupplierVolume);
                    mainFuel = TopKey(agg.FuelVolume) ?? "";
                    regions = TopKeys(agg.RegionVolume, 5);

                    double mainShare = 0.0;
                    if (agg.TotalVolume > 0.0 && currentSupplier != null) mainShare = agg.SupplierVolume[currentSupplier] / agg.TotalVolume;
                    if (agg.TxCount < 10) segment = "COLD";
                    else if (mainShare >= 0.75) segment = "INERT";
                    else if (agg.SupplierVolume.Count >= 3) segment = "VAR";
                    else segment = "POT";

                    if (agg.TxCount < 10) AddUnique(signals, "new");
                    if (agg.TxCount > 0 && (agg.PriceRatioSum / agg.TxCount) > 1.01) AddUnique(signals, "suboptim");
                    if (c.DateEnd != null) AddUnique(signals, "contract");
                    List<string> months = agg.MonthVolume.Keys.OrderBy(x => x).ToList();
                    if (months.Count >= 2)
                    {
                        double first = agg.MonthVolume[months[0]];
                        double last = agg.MonthVolume[months[months.Count - 1]];
                        if (first > 0.0 && last < first * 0.90) AddUnique(signals, "churn");
                        else if (first > 0.0 && last > first * 1.10) AddUnique(signals, "growth");
                    }

                    priority = 0.25 + Math.Min(0.45, Math.Log10(Math.Max(1.0, monthlyLiters)) / 12.0);
                    priority += Math.Min(0.25, signals.Count * 0.06);
                    priority = Math.Round(Math.Min(0.99, priority), 2);
                    samples.AddRange(agg.Samples);
                }
                else
                {
                    AddUnique(signals, "new");
                    priority = Math.Round(Math.Min(0.65, 0.25 + Math.Log10(Math.Max(1.0, monthlyLiters)) / 14.0), 2);
                }

                if (i > 0) writer.WriteLine(",");
                writer.WriteLine("  {");
                writer.WriteLine("    \"id\": " + Json(c.Id) + ",");
                writer.WriteLine("    \"name\": " + Json(c.Id) + ",");
                writer.WriteLine("    \"inn\": \"\",");
                writer.WriteLine("    \"industry\": " + Json(c.Activity) + ",");
                writer.WriteLine("    \"segment\": " + Json(segment) + ",");
                writer.WriteLine("    \"fleet\": " + fleet.ToString(CultureInfo.InvariantCulture) + ",");
                writer.WriteLine("    \"mainFuel\": " + Json(mainFuel) + ",");
                writer.Write("    \"regions\": ");
                WriteStringArray(writer, regions);
                writer.WriteLine(",");
                writer.WriteLine("    \"monthlyLiters\": " + NumText(monthlyLiters) + ",");
                writer.WriteLine("    \"monthlySpend\": " + NumText(monthlySpend) + ",");
                writer.WriteLine("    \"currentSupplier\": " + Json(currentSupplier) + ",");
                writer.WriteLine("    \"contractEnd\": " + Json(c.DateEnd) + ",");
                writer.WriteLine("    \"manager\": " + Json(c.Office) + ",");
                writer.WriteLine("    \"office\": " + Json(c.Office) + ",");
                writer.WriteLine("    \"status\": " + Json(c.Status) + ",");
                writer.WriteLine("    \"clientType\": " + Json(c.ClientType) + ",");
                writer.Write("    \"signals\": ");
                WriteStringArray(writer, signals);
                writer.WriteLine(",");
                writer.WriteLine("    \"priority\": " + NumText(priority) + ",");
                writer.Write("    \"history\": ");
                WriteHistory(writer, agg, monthlyLiters);
                writer.WriteLine(",");
                writer.Write("    \"fuelMix\": ");
                WriteShareObject(writer, agg == null ? null : agg.FuelVolume);
                writer.WriteLine(",");
                writer.Write("    \"geoSplit\": ");
                WriteShareObject(writer, agg == null ? null : agg.RegionVolume);
                writer.WriteLine();
                writer.Write("  }");
            }
            writer.WriteLine();
            writer.WriteLine("]");
        }
        return samples;
    }

    private static int WriteSuppliers(string path, Dictionary<string, SupplierAgg> suppliers)
    {
        int maxStations = 1;
        foreach (SupplierAgg s in suppliers.Values) maxStations = Math.Max(maxStations, s.Stations.Count);
        List<KeyValuePair<string, SupplierAgg>> rows = suppliers.OrderByDescending(x => x.Value.Volume).ToList();

        using (StreamWriter writer = new StreamWriter(path, false, new UTF8Encoding(false)))
        {
            writer.WriteLine("[");
            for (int i = 0; i < rows.Count; i++)
            {
                string id = rows[i].Key;
                SupplierAgg s = rows[i].Value;
                double coverage = Math.Round(s.Stations.Count / (double)maxStations, 3);
                string regionText = s.Regions.Count >= 20 ? "RF" : String.Join(", ", s.Regions.Where(x => !String.IsNullOrWhiteSpace(x)).OrderBy(x => x).Take(4).ToArray());
                if (i > 0) writer.WriteLine(",");
                writer.WriteLine("  {");
                writer.WriteLine("    \"id\": " + Json(id) + ",");
                writer.WriteLine("    \"name\": " + Json(id) + ",");
                writer.WriteLine("    \"full\": " + Json(id) + ",");
                writer.WriteLine("    \"type\": " + Json(coverage >= 0.35 ? "aggregator" : "vink") + ",");
                writer.WriteLine("    \"coverage\": " + NumText(coverage) + ",");
                writer.WriteLine("    \"regions\": " + Json(regionText) + ",");
                writer.Write("    \"fuels\": ");
                WriteStringArray(writer, s.Fuels.Where(x => !String.IsNullOrWhiteSpace(x)).OrderBy(x => x).ToList());
                writer.WriteLine(",");
                writer.WriteLine("    \"notes\": \"Imported from df_tx.xlsx\"");
                writer.Write("  }");
            }
            writer.WriteLine();
            writer.WriteLine("]");
        }
        return rows.Count;
    }

    private static void WriteTransactions(string path, List<TxSample> samples)
    {
        WriteTransactionArray(path, samples);

        string dir = Path.Combine(Path.GetDirectoryName(path), "client_transactions");
        Directory.CreateDirectory(dir);
        foreach (IGrouping<string, TxSample> group in samples.GroupBy(x => x.Client))
        {
            string clientPath = Path.Combine(dir, SafeFileName(group.Key) + ".json");
            WriteTransactionArray(clientPath, group.ToList());
        }
    }

    private static void WriteTransactionArray(string path, IList<TxSample> samples)
    {
        using (StreamWriter writer = new StreamWriter(path, false, new UTF8Encoding(false)))
        {
            writer.WriteLine("[");
            for (int i = 0; i < samples.Count; i++)
            {
                TxSample tx = samples[i];
                if (i > 0) writer.WriteLine(",");
                writer.WriteLine("  {");
                writer.WriteLine("    \"client\": " + Json(tx.Client) + ",");
                writer.WriteLine("    \"ts\": " + Json(tx.Ts) + ",");
                writer.WriteLine("    \"station\": " + Json(tx.Station) + ",");
                writer.WriteLine("    \"region\": " + Json(tx.Region) + ",");
                writer.WriteLine("    \"fuel\": " + Json(tx.Fuel) + ",");
                writer.WriteLine("    \"liters\": " + NumText(tx.Liters) + ",");
                writer.WriteLine("    \"amount\": " + NumText(tx.Amount) + ",");
                writer.WriteLine("    \"vehicle\": \"\"");
                writer.Write("  }");
            }
            writer.WriteLine();
            writer.WriteLine("]");
        }
    }

    private static string SafeFileName(string value)
    {
        if (String.IsNullOrWhiteSpace(value)) return "unknown";
        HashSet<char> invalid = new HashSet<char>(Path.GetInvalidFileNameChars());
        StringBuilder sb = new StringBuilder();
        foreach (char ch in value)
        {
            sb.Append(invalid.Contains(ch) ? '_' : ch);
        }
        return sb.ToString();
    }

    private static void WriteHistory(StreamWriter writer, ClientAgg agg, double monthlyLiters)
    {
        if (agg == null || agg.MonthVolume.Count == 0)
        {
            writer.Write("[{\"m\":\"2025-06\",\"l\":" + NumText(Math.Round(monthlyLiters, 0)) + ",\"s\":0}]");
            return;
        }
        writer.Write("[");
        List<string> months = agg.MonthVolume.Keys.OrderBy(x => x).ToList();
        for (int i = 0; i < months.Count; i++)
        {
            string m = months[i];
            if (i > 0) writer.Write(",");
            writer.Write("{\"m\":" + Json(m) + ",\"l\":" + NumText(Math.Round(agg.MonthVolume[m], 0)) + ",\"s\":" + NumText(Math.Round(agg.MonthSpend[m], 0)) + "}");
        }
        writer.Write("]");
    }

    private static void WriteStringArray(StreamWriter writer, IEnumerable<string> values)
    {
        writer.Write("[");
        bool first = true;
        foreach (string value in values)
        {
            if (String.IsNullOrWhiteSpace(value)) continue;
            if (!first) writer.Write(",");
            writer.Write(Json(value));
            first = false;
        }
        writer.Write("]");
    }

    private static void WriteShareObject(StreamWriter writer, Dictionary<string, double> map)
    {
        if (map == null || map.Count == 0)
        {
            writer.Write("{}");
            return;
        }
        double sum = map.Values.Sum();
        if (sum <= 0.0)
        {
            writer.Write("{}");
            return;
        }
        writer.Write("{");
        bool first = true;
        foreach (KeyValuePair<string, double> entry in map.OrderByDescending(x => x.Value).Take(8))
        {
            if (String.IsNullOrWhiteSpace(entry.Key)) continue;
            if (!first) writer.Write(",");
            writer.Write(Json(entry.Key));
            writer.Write(":");
            writer.Write(NumText(Math.Round(entry.Value / sum, 3)));
            first = false;
        }
        writer.Write("}");
    }

    private static string Json(string value)
    {
        if (value == null) return "null";
        StringBuilder sb = new StringBuilder();
        sb.Append('\"');
        foreach (char ch in value)
        {
            switch (ch)
            {
                case '\\': sb.Append("\\\\"); break;
                case '\"': sb.Append("\\\""); break;
                case '\b': sb.Append("\\b"); break;
                case '\f': sb.Append("\\f"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                default:
                    if (ch < 32) sb.Append("\\u" + ((int)ch).ToString("x4"));
                    else sb.Append(ch);
                    break;
            }
        }
        sb.Append('\"');
        return sb.ToString();
    }

    private static string NumText(double value)
    {
        return value.ToString("0.###", CultureInfo.InvariantCulture);
    }

    private static double Num(string value)
    {
        if (String.IsNullOrWhiteSpace(value)) return 0.0;
        double result;
        if (Double.TryParse(value.Replace(",", "."), NumberStyles.Float, CultureInfo.InvariantCulture, out result)) return result;
        return 0.0;
    }

    private static string ExcelDate(string value, string format)
    {
        double n = Num(value);
        if (n <= 0.0) return null;
        try { return DateTime.FromOADate(n).ToString(format); }
        catch { return null; }
    }

    private static string ValueAt(string[] values, int index)
    {
        if (index >= 0 && index < values.Length) return values[index] ?? "";
        return "";
    }

    private static string SupplierFromCard(string cardType)
    {
        if (String.IsNullOrWhiteSpace(cardType)) return "unknown";
        int index = cardType.IndexOf('.');
        if (index <= 0) return cardType;
        return cardType.Substring(0, index);
    }

    private static void AddMap(Dictionary<string, double> map, string key, double value)
    {
        if (String.IsNullOrWhiteSpace(key)) key = "unknown";
        double current;
        map.TryGetValue(key, out current);
        map[key] = current + value;
    }

    private static void AddSet(HashSet<string> set, string value)
    {
        if (!String.IsNullOrWhiteSpace(value)) set.Add(value);
    }

    private static void AddUnique(List<string> values, string value)
    {
        if (!values.Contains(value)) values.Add(value);
    }

    private static string TopKey(Dictionary<string, double> map)
    {
        if (map == null || map.Count == 0) return null;
        return map.OrderByDescending(x => x.Value).First().Key;
    }

    private static List<string> TopKeys(Dictionary<string, double> map, int take)
    {
        if (map == null) return new List<string>();
        return map.Where(x => !String.IsNullOrWhiteSpace(x.Key)).OrderByDescending(x => x.Value).Take(take).Select(x => x.Key).ToList();
    }
}
"@
    Add-Type -TypeDefinition $code
}

function Import-ExcelData {
    param(
        [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
        [string]$ClientsPath = "",
        [string]$TransactionsPath = ""
    )

    if ([string]::IsNullOrWhiteSpace($ClientsPath)) {
        $ClientsPath = Join-Path $ProjectRoot "df_clients.xlsx"
    }
    if ([string]::IsNullOrWhiteSpace($TransactionsPath)) {
        $TransactionsPath = Join-Path $ProjectRoot "df_tx.xlsx"
    }

    if (-not (Test-Path -LiteralPath $ClientsPath)) { throw "df_clients.xlsx not found" }
    if (-not (Test-Path -LiteralPath $TransactionsPath)) { throw "df_tx.xlsx not found" }

    $tmpDir = Join-Path $script:DataRoot "tmp_import"
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    $clientsTsv = Join-Path $tmpDir "clients.tsv"
    $txTsv = Join-Path $tmpDir "transactions.tsv"
    Convert-XlsxToTsv -Path $ClientsPath -OutputPath $clientsTsv | Out-Null
    Convert-XlsxToTsv -Path $TransactionsPath -OutputPath $txTsv | Out-Null

    Add-FuelDataImporter
    $stats = [LocalFuelDataImporter]::Import(
        $clientsTsv,
        $txTsv,
        (Get-DataFile "clients.json"),
        (Get-DataFile "suppliers.json"),
        (Get-DataFile "transactions.json")
    )

    $sources = @(Get-DataSources)
    $now = (Get-Date).ToString("yyyy-MM-dd HH:mm")
    foreach ($source in $sources) {
        if ($source.id -eq "tx") {
            $source.last = $now
            $source.status = "ok"
            $source.rows = [int]$stats.Transactions
            $source.delta = "+0"
        }
        elseif ($source.id -eq "dbms") {
            $source.last = $now
            $source.status = "ok"
            $source.rows = [int]$stats.Clients
            $source.delta = "+0"
        }
        elseif ($source.id -eq "supp") {
            $source.last = $now
            $source.status = "ok"
            $source.rows = [int]$stats.Suppliers
            $source.delta = "+0"
        }
    }
    Write-JsonFile (Get-DataFile "data_sources.json") $sources

    $state = [ordered]@{
        ts = $now
        clients = [int]$stats.Clients
        transactions = [int]$stats.Transactions
        transactionSamples = [int]$stats.TransactionSamples
        suppliers = [int]$stats.Suppliers
        clientsFile = [System.IO.Path]::GetFileName($ClientsPath)
        transactionsFile = [System.IO.Path]::GetFileName($TransactionsPath)
    }
    Write-JsonFile (Get-DataFile "import_state.json") @([pscustomobject]$state)
    return [pscustomobject]$state

    $clients = @{}
    $clientOrder = New-Object "System.Collections.Generic.List[string]"
    $clientRows = 0

    $clientReader = [System.IO.StreamReader]::new($clientsTsv, [System.Text.Encoding]::UTF8)
    try {
        $clientReader.ReadLine() | Out-Null
        while (-not $clientReader.EndOfStream) {
            $line = $clientReader.ReadLine()
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $v = $line.Split([char]"`t")
            if ($v.Count -lt 8) { continue }
            $id = $v[0]
            if ([string]::IsNullOrWhiteSpace($id)) { continue }

            $shipments = Get-Number $v[7]
            $monthly = [Math]::Round($shipments / 6.0, 0)
            $dateEnd = Get-DateStringFromExcel $v[2]
            $office = if ($v.Count -gt 5) { $v[5] } else { "" }
            $activity = if ($v.Count -gt 6) { $v[6] } else { "" }

            $clients[$id] = [ordered]@{
                id = $id
                name = $id
                inn = ""
                industry = $activity
                segment = "COLD"
                fleet = [Math]::Max(1, [Math]::Min(300, [Math]::Round($monthly / 2500.0, 0)))
                mainFuel = ""
                regions = @()
                monthlyLiters = $monthly
                monthlySpend = 0
                currentSupplier = $null
                contractEnd = $dateEnd
                manager = $office
                office = $office
                status = if ($v.Count -gt 3) { $v[3] } else { "" }
                clientType = if ($v.Count -gt 4) { $v[4] } else { "" }
                signals = @()
                priority = 0.0
                history = @()
                fuelMix = [pscustomobject]@{}
                geoSplit = [pscustomobject]@{}
            }
            $clientOrder.Add($id)
            $clientRows += 1
        }
    }
    finally {
        $clientReader.Dispose()
    }

    $aggs = @{}
    $supplierAgg = @{}
    $txRows = 0

    $txReader = [System.IO.StreamReader]::new($txTsv, [System.Text.Encoding]::UTF8)
    try {
        $txReader.ReadLine() | Out-Null
        while (-not $txReader.EndOfStream) {
            $line = $txReader.ReadLine()
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $v = $line.Split([char]"`t")
            if ($v.Count -lt 15) { continue }
            $clientId = $v[1]
            if ([string]::IsNullOrWhiteSpace($clientId)) { continue }
            if (-not $aggs.ContainsKey($clientId)) { $aggs[$clientId] = New-ClientAgg }
            $agg = $aggs[$clientId]

            $supplier = Get-SupplierFromCardType $v[2]
            $volume = Get-Number $v[5]
            if ($volume -le 0) { continue }
            $marketPrice = Get-Number $v[6]
            $clientPrice = Get-Number $v[7]
            $station = $v[10]
            $region = $v[11]
            $fuel = $v[14]
            $ts = Get-DateTimeStringFromExcel $v[0]
            $spend = $volume * $clientPrice
            $marketSpend = $volume * $marketPrice
            $month = if ($ts) { $ts.Substring(0, 7) } else { "unknown" }

            $agg.txCount += 1
            $agg.totalVolume += $volume
            $agg.totalSpend += $spend
            $agg.totalMarketSpend += $marketSpend
            if ($marketPrice -gt 0) { $agg.priceRatioSum += ($clientPrice / $marketPrice) }
            Add-MapNumber $agg.supplierVolume $supplier $volume
            Add-MapNumber $agg.fuelVolume $fuel $volume
            Add-MapNumber $agg.regionVolume $region $volume
            Add-MapNumber $agg.monthVolume $month $volume
            Add-MapNumber $agg.monthSpend $month $spend
            if ($agg.samples.Count -lt 28) {
                $agg.samples.Add([pscustomobject]@{
                    client = $clientId
                    ts = $ts
                    station = $station
                    region = $region
                    fuel = $fuel
                    liters = [Math]::Round($volume, 2)
                    amount = [Math]::Round($spend, 0)
                    vehicle = ""
                })
            }

            if (-not $supplierAgg.ContainsKey($supplier)) {
                $supplierAgg[$supplier] = [ordered]@{
                    volume = 0.0
                    clients = @{}
                    stations = @{}
                    regions = @{}
                    fuels = @{}
                    own = 0
                    tx = 0
                    priceRatioSum = 0.0
                }
            }
            $sagg = $supplierAgg[$supplier]
            $sagg.volume += $volume
            $sagg.tx += 1
            if ($marketPrice -gt 0) { $sagg.priceRatioSum += ($clientPrice / $marketPrice) }
            Add-SetValue $sagg.clients $clientId
            Add-SetValue $sagg.stations $station
            Add-SetValue $sagg.regions $region
            Add-SetValue $sagg.fuels $fuel
            $txRows += 1
        }
    }
    finally {
        $txReader.Dispose()
    }

    $maxSupplierStations = 1
    foreach ($s in $supplierAgg.Values) {
        $maxSupplierStations = [Math]::Max($maxSupplierStations, $s.stations.Count)
    }

    $resultClients = New-Object "System.Collections.Generic.List[object]"
    $transactions = New-Object "System.Collections.Generic.List[object]"

    foreach ($id in $clientOrder) {
        $client = $clients[$id]
        $agg = if ($aggs.ContainsKey($id)) { $aggs[$id] } else { $null }

        if ($null -ne $agg -and $agg.txCount -gt 0) {
            $client.monthlyLiters = [Math]::Round($agg.totalVolume / 6.0, 0)
            $client.monthlySpend = [Math]::Round($agg.totalSpend / 6.0, 0)
            $client.fleet = [Math]::Max(1, [Math]::Min(300, [Math]::Round($client.monthlyLiters / 2500.0, 0)))
            $topSupplier = $agg.supplierVolume.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 1
            $topFuel = $agg.fuelVolume.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 1
            $client.currentSupplier = if ($topSupplier) { $topSupplier.Key } else { $null }
            $client.mainFuel = if ($topFuel) { $topFuel.Key } else { "" }
            $client.regions = @(Get-TopKeys $agg.regionVolume 5)
            $client.fuelMix = Convert-MapToShareObject $agg.fuelVolume
            $client.geoSplit = Convert-MapToShareObject $agg.regionVolume
            $history = New-Object "System.Collections.Generic.List[object]"
            foreach ($m in ($agg.monthVolume.Keys | Sort-Object)) {
                $history.Add([pscustomobject]@{
                    m = $m
                    l = [Math]::Round([double]$agg.monthVolume[$m], 0)
                    s = [Math]::Round([double]$agg.monthSpend[$m], 0)
                })
            }
            $client.history = @($history)

            $mainShare = if ($agg.totalVolume -gt 0 -and $topSupplier) { [double]$topSupplier.Value / $agg.totalVolume } else { 0 }
            if ($agg.txCount -lt 10) { $client.segment = "COLD" }
            elseif ($mainShare -ge 0.75) { $client.segment = "INERT" }
            elseif ($agg.supplierVolume.Count -ge 3) { $client.segment = "VAR" }
            else { $client.segment = "POT" }

            $signals = New-Object "System.Collections.Generic.List[string]"
            if ($agg.txCount -lt 10) { $signals.Add("new") }
            if ($agg.txCount -gt 0 -and (($agg.priceRatioSum / $agg.txCount) -gt 1.01)) { $signals.Add("suboptim") }
            if ($client.contractEnd) { $signals.Add("contract") }
            if ($history.Count -ge 2) {
                $first = [double]$history[0].l
                $last = [double]$history[$history.Count - 1].l
                if ($first -gt 0 -and $last -lt $first * 0.90) { $signals.Add("churn") }
                elseif ($first -gt 0 -and $last -gt $first * 1.10) { $signals.Add("growth") }
            }
            $client.signals = @($signals | Select-Object -Unique)
            $priority = 0.25 + [Math]::Min(0.45, [Math]::Log10([Math]::Max(1, $client.monthlyLiters)) / 12.0)
            $priority += [Math]::Min(0.25, $client.signals.Count * 0.06)
            $client.priority = [Math]::Round([Math]::Min(0.99, $priority), 2)

            foreach ($sample in $agg.samples) {
                $transactions.Add($sample)
            }
        }
        else {
            $client.history = @([pscustomobject]@{ m = "2025-06"; l = [Math]::Round([double]$client.monthlyLiters, 0); s = 0 })
            $client.signals = @("new")
            $client.priority = [Math]::Round([Math]::Min(0.65, 0.25 + [Math]::Log10([Math]::Max(1, $client.monthlyLiters)) / 14.0), 2)
        }

        $resultClients.Add([pscustomobject]$client)
    }

    $suppliers = New-Object "System.Collections.Generic.List[object]"
    foreach ($entry in ($supplierAgg.GetEnumerator() | Sort-Object { $_.Value.volume } -Descending)) {
        $id = [string]$entry.Key
        $s = $entry.Value
        $coverage = [Math]::Round($s.stations.Count / [double]$maxSupplierStations, 3)
        $regionText = if ($s.regions.Count -ge 20) { "RF" } else { (@($s.regions.Keys | Select-Object -First 4) -join ", ") }
        $suppliers.Add([pscustomobject]@{
            id = $id
            name = $id
            full = $id
            type = if ($coverage -ge 0.35) { "aggregator" } else { "vink" }
            coverage = $coverage
            regions = $regionText
            fuels = @($s.fuels.Keys)
            notes = "Imported from df_tx.xlsx"
        })
    }

    Write-JsonFile (Get-DataFile "clients.json") $resultClients
    Write-JsonFile (Get-DataFile "suppliers.json") $suppliers
    Write-JsonFile (Get-DataFile "transactions.json") $transactions

    $sources = @(Get-DataSources)
    $now = (Get-Date).ToString("yyyy-MM-dd HH:mm")
    foreach ($source in $sources) {
        if ($source.id -eq "tx") {
            $source.last = $now
            $source.status = "ok"
            $source.rows = $txRows
            $source.delta = "+0"
        }
        elseif ($source.id -eq "dbms") {
            $source.last = $now
            $source.status = "ok"
            $source.rows = $clientRows
            $source.delta = "+0"
        }
        elseif ($source.id -eq "supp") {
            $source.last = $now
            $source.status = "ok"
            $source.rows = $suppliers.Count
            $source.delta = "+0"
        }
    }
    Write-JsonFile (Get-DataFile "data_sources.json") $sources

    $state = [ordered]@{
        ts = $now
        clients = $resultClients.Count
        transactions = $txRows
        transactionSamples = $transactions.Count
        suppliers = $suppliers.Count
        clientsFile = [System.IO.Path]::GetFileName($ClientsPath)
        transactionsFile = [System.IO.Path]::GetFileName($TransactionsPath)
    }
    Write-JsonFile (Get-DataFile "import_state.json") @([pscustomobject]$state)
    return [pscustomobject]$state

    $clients = @{}
    $clientOrder = New-Object "System.Collections.Generic.List[string]"
    $clientRows = 0

    Read-XlsxRows -Path $ClientsPath -OnRow {
        param($row, $rowNumber)
        if ($rowNumber -eq 0) { return }

        $id = Get-Cell $row 0
        if ([string]::IsNullOrWhiteSpace($id)) { return }

        $shipments = Get-Number (Get-Cell $row 7)
        $monthly = [Math]::Round($shipments / 6.0, 0)
        $dateEnd = Get-DateStringFromExcel (Get-Cell $row 2)
        $office = Get-Cell $row 5
        $activity = Get-Cell $row 6

        $clients[$id] = [ordered]@{
            id = $id
            name = $id
            inn = ""
            industry = $activity
            segment = "COLD"
            fleet = [Math]::Max(1, [Math]::Min(300, [Math]::Round($monthly / 2500.0, 0)))
            mainFuel = ""
            regions = @()
            monthlyLiters = $monthly
            monthlySpend = 0
            currentSupplier = $null
            contractEnd = $dateEnd
            manager = $office
            office = $office
            status = Get-Cell $row 3
            clientType = Get-Cell $row 4
            signals = @()
            priority = 0.0
            history = @()
            fuelMix = [pscustomobject]@{}
            geoSplit = [pscustomobject]@{}
        }
        $clientOrder.Add($id)
        $clientRows += 1
    }

    $aggs = @{}
    $supplierAgg = @{}
    $txRows = 0

    Read-XlsxRows -Path $TransactionsPath -OnRow {
        param($row, $rowNumber)
        if ($rowNumber -eq 0) { return }

        $clientId = Get-Cell $row 1
        if ([string]::IsNullOrWhiteSpace($clientId)) { return }

        if (-not $aggs.ContainsKey($clientId)) { $aggs[$clientId] = New-ClientAgg }
        $agg = $aggs[$clientId]

        $supplier = Get-SupplierFromCardType (Get-Cell $row 2)
        $volume = Get-Number (Get-Cell $row 5)
        $marketPrice = Get-Number (Get-Cell $row 6)
        $clientPrice = Get-Number (Get-Cell $row 7)
        $station = Get-Cell $row 10
        $region = Get-Cell $row 11
        $stationType = Get-Cell $row 12
        $fuel = Get-Cell $row 14
        $ts = Get-DateTimeStringFromExcel (Get-Cell $row 0)
        if ($volume -le 0) { return }

        $spend = $volume * $clientPrice
        $marketSpend = $volume * $marketPrice
        $month = if ($ts) { $ts.Substring(0, 7) } else { "unknown" }

        $agg.txCount += 1
        $agg.totalVolume += $volume
        $agg.totalSpend += $spend
        $agg.totalMarketSpend += $marketSpend
        if ($marketPrice -gt 0) { $agg.priceRatioSum += ($clientPrice / $marketPrice) }
        Add-MapNumber $agg.supplierVolume $supplier $volume
        Add-MapNumber $agg.fuelVolume $fuel $volume
        Add-MapNumber $agg.regionVolume $region $volume
        Add-MapNumber $agg.monthVolume $month $volume
        Add-MapNumber $agg.monthSpend $month $spend

        $agg.samples.Add([pscustomobject]@{
            client = $clientId
            ts = $ts
            station = $station
            region = $region
            fuel = $fuel
            liters = [Math]::Round($volume, 2)
            amount = [Math]::Round($spend, 0)
            vehicle = ""
        })

        if (-not $supplierAgg.ContainsKey($supplier)) {
            $supplierAgg[$supplier] = [ordered]@{
                volume = 0.0
                clients = @{}
                stations = @{}
                regions = @{}
                fuels = @{}
                own = 0
                tx = 0
                priceRatioSum = 0.0
            }
        }
        $sagg = $supplierAgg[$supplier]
        $sagg.volume += $volume
        $sagg.tx += 1
        if ($marketPrice -gt 0) { $sagg.priceRatioSum += ($clientPrice / $marketPrice) }
        Add-SetValue $sagg.clients $clientId
        Add-SetValue $sagg.stations $station
        Add-SetValue $sagg.regions $region
        Add-SetValue $sagg.fuels $fuel
        if ($stationType.ToLowerInvariant().Contains("own")) {
            $sagg.own += 1
        }

        $txRows += 1
    }

    $maxSupplierStations = 1
    foreach ($s in $supplierAgg.Values) {
        $maxSupplierStations = [Math]::Max($maxSupplierStations, $s.stations.Count)
    }

    $resultClients = New-Object "System.Collections.Generic.List[object]"
    $transactions = New-Object "System.Collections.Generic.List[object]"

    foreach ($id in $clientOrder) {
        $client = $clients[$id]
        $agg = if ($aggs.ContainsKey($id)) { $aggs[$id] } else { $null }

        if ($null -ne $agg -and $agg.txCount -gt 0) {
            $client.monthlyLiters = [Math]::Round($agg.totalVolume / 6.0, 0)
            $client.monthlySpend = [Math]::Round($agg.totalSpend / 6.0, 0)
            $client.fleet = [Math]::Max(1, [Math]::Min(300, [Math]::Round($client.monthlyLiters / 2500.0, 0)))
            $topSupplier = $agg.supplierVolume.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 1
            $topFuel = $agg.fuelVolume.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 1
            $client.currentSupplier = if ($topSupplier) { $topSupplier.Key } else { $null }
            $client.mainFuel = if ($topFuel) { $topFuel.Key } else { "" }
            $client.regions = @(Get-TopKeys $agg.regionVolume 5)
            $client.fuelMix = Convert-MapToShareObject $agg.fuelVolume
            $client.geoSplit = Convert-MapToShareObject $agg.regionVolume

            $history = New-Object "System.Collections.Generic.List[object]"
            foreach ($m in ($agg.monthVolume.Keys | Sort-Object)) {
                $history.Add([pscustomobject]@{
                    m = $m
                    l = [Math]::Round([double]$agg.monthVolume[$m], 0)
                    s = [Math]::Round([double]$agg.monthSpend[$m], 0)
                })
            }
            $client.history = @($history)

            $mainShare = if ($agg.totalVolume -gt 0 -and $topSupplier) { [double]$topSupplier.Value / $agg.totalVolume } else { 0 }
            if ($agg.txCount -lt 10) { $client.segment = "COLD" }
            elseif ($mainShare -ge 0.75) { $client.segment = "INERT" }
            elseif ($agg.supplierVolume.Count -ge 3) { $client.segment = "VAR" }
            else { $client.segment = "POT" }

            $signals = New-Object "System.Collections.Generic.List[string]"
            if ($agg.txCount -lt 10) { $signals.Add("new") }
            if ($agg.txCount -gt 0 -and (($agg.priceRatioSum / $agg.txCount) -gt 1.01)) { $signals.Add("suboptim") }
            if ($client.contractEnd) { $signals.Add("contract") }
            if ($history.Count -ge 2) {
                $first = [double]$history[0].l
                $last = [double]$history[$history.Count - 1].l
                if ($first -gt 0 -and $last -lt $first * 0.90) { $signals.Add("churn") }
                elseif ($first -gt 0 -and $last -gt $first * 1.10) { $signals.Add("growth") }
            }
            $client.signals = @($signals | Select-Object -Unique)

            $priority = 0.25 + [Math]::Min(0.45, [Math]::Log10([Math]::Max(1, $client.monthlyLiters)) / 12.0)
            $priority += [Math]::Min(0.25, $client.signals.Count * 0.06)
            $client.priority = [Math]::Round([Math]::Min(0.99, $priority), 2)

            foreach ($sample in ($agg.samples | Sort-Object ts -Descending | Select-Object -First 28)) {
                $transactions.Add($sample)
            }
        }
        else {
            $client.history = @([pscustomobject]@{ m = "2025-06"; l = [Math]::Round([double]$client.monthlyLiters, 0); s = 0 })
            $client.signals = @("new")
            $client.priority = [Math]::Round([Math]::Min(0.65, 0.25 + [Math]::Log10([Math]::Max(1, $client.monthlyLiters)) / 14.0), 2)
        }

        $resultClients.Add([pscustomobject]$client)
    }

    $suppliers = New-Object "System.Collections.Generic.List[object]"
    foreach ($entry in ($supplierAgg.GetEnumerator() | Sort-Object { $_.Value.volume } -Descending)) {
        $id = [string]$entry.Key
        $s = $entry.Value
        $coverage = [Math]::Round($s.stations.Count / [double]$maxSupplierStations, 3)
        $regionText = if ($s.regions.Count -ge 20) { "RF" } else { (@($s.regions.Keys | Select-Object -First 4) -join ", ") }
        $suppliers.Add([pscustomobject]@{
            id = $id
            name = $id
            full = $id
            type = if ($coverage -ge 0.35) { "aggregator" } else { "vink" }
            coverage = $coverage
            regions = $regionText
            fuels = @($s.fuels.Keys)
            notes = "Imported from df_tx.xlsx"
        })
    }

    Write-JsonFile (Get-DataFile "clients.json") $resultClients
    Write-JsonFile (Get-DataFile "suppliers.json") $suppliers
    Write-JsonFile (Get-DataFile "transactions.json") $transactions

    $sources = @(Get-DataSources)
    $now = (Get-Date).ToString("yyyy-MM-dd HH:mm")
    foreach ($source in $sources) {
        if ($source.id -eq "tx") {
            $source.last = $now
            $source.status = "ok"
            $source.rows = $txRows
            $source.delta = "+0"
        }
        elseif ($source.id -eq "dbms") {
            $source.last = $now
            $source.status = "ok"
            $source.rows = $clientRows
            $source.delta = "+0"
        }
        elseif ($source.id -eq "supp") {
            $source.last = $now
            $source.status = "ok"
            $source.rows = $suppliers.Count
            $source.delta = "+0"
        }
    }
    Write-JsonFile (Get-DataFile "data_sources.json") $sources

    $state = [ordered]@{
        ts = $now
        clients = $resultClients.Count
        transactions = $txRows
        transactionSamples = $transactions.Count
        suppliers = $suppliers.Count
        clientsFile = [System.IO.Path]::GetFileName($ClientsPath)
        transactionsFile = [System.IO.Path]::GetFileName($TransactionsPath)
    }
    Write-JsonFile (Get-DataFile "import_state.json") @([pscustomobject]$state)

    return [pscustomobject]$state
}
