# Starts a Cloudflare quick tunnel to the local Lifeline server and prints the public link only once the tunnel is really connected.
# Usage (PowerShell, from the project folder, with the Lifeline server already running):
#   powershell -ExecutionPolicy Bypass -File tools\start_tunnel.ps1
param([int]$Port = 8000)

try { Invoke-WebRequest "http://localhost:$Port/api/health" -UseBasicParsing -TimeoutSec 5 | Out-Null }
catch { Write-Host "Lifeline is not answering on port $Port. Start the server first, then run this again." -ForegroundColor Red; exit 1 }

Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
$log = Join-Path $env:TEMP "lifeline_tunnel.log"
Remove-Item $log, "$log.out" -ErrorAction SilentlyContinue
$p = Start-Process cloudflared -ArgumentList "tunnel", "--protocol", "http2", "--url", "http://localhost:$Port" `
     -RedirectStandardError $log -RedirectStandardOutput "$log.out" -NoNewWindow -PassThru

$url = $null; $ready = $false
for ($i = 0; $i -lt 60 -and -not $p.HasExited; $i++) {
    Start-Sleep -Seconds 1
    $text = try { Get-Content $log -Raw -ErrorAction Stop } catch { "" }
    if ($text -match "https://[a-zA-Z0-9-]+\.trycloudflare\.com") { $url = $Matches[0] }
    if ($text -match "Registered tunnel connection") { $ready = $true }
    if ($url -and $ready) { break }
}

if ($url -and $ready) {
    Write-Host "`nTunnel is connected. Public link:" -ForegroundColor Green
    Write-Host "  $url"
    Write-Host "  Twilio webhook:  $url/webhooks/twilio"
    Write-Host "`nKeep this window open. Press Ctrl+C to stop the tunnel."
    try { Wait-Process -Id $p.Id } finally { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
} else {
    Write-Host "The tunnel did not connect. Last log lines:" -ForegroundColor Red
    Get-Content $log -Tail 15 -ErrorAction SilentlyContinue
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}
