# Edit these values as needed:
$TELEGRAM_TOKEN = "8286459403:AAG9VzJeX8UoSX9wl0JTJITETdsiMPDrhKo"
$WEBHOOK_SECRET = "myfinsightsecret"
$NGROK_PATH = "C:\Users\seema\Downloads\ngrok-v3-stable-windows-amd64\ngrok.exe"  # or just "ngrok.exe" if in PATH

# start backend
cd "$PSScriptRoot\backend"
.\venv\Scripts\Activate.ps1

# start uvicorn in new window
Start-Process powershell -ArgumentList "-NoExit","-Command","cd `"$PSScriptRoot\backend`"; .\venv\Scripts\Activate.ps1; python -m uvicorn app:app --reload --port 8000"

# start ngrok in new window
Start-Process powershell -ArgumentList "-NoExit","-Command","& `"$NGROK_PATH`" http 8000"

# wait a little for ngrok to be ready
Start-Sleep -Seconds 3

# fetch current ngrok public url from local api (optional)
try {
  $ngrokApi = Invoke-RestMethod -Uri http://127.0.0.1:4040/api/tunnels -UseBasicParsing -ErrorAction Stop
  $publicUrl = $ngrokApi.tunnels[0].public_url
} catch {
  Write-Host "Could not read ngrok API. Please copy the https URL from the ngrok window and set it below."
  $publicUrl = Read-Host "Enter ngrok https URL (e.g. https://abcd1234.ngrok-free.dev)"
}

# register webhook
$TELEGRAM_TOKEN = $TELEGRAM_TOKEN.Trim()
$WEBHOOK_SECRET = $WEBHOOK_SECRET.Trim()
Write-Host "Registering webhook to $publicUrl"
curl.exe -s "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook?url=$publicUrl/webhook/telegram&secret_token=$WEBHOOK_SECRET" | ConvertFrom-Json | Write-Host
