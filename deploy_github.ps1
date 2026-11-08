# MONO/RUN — one-shot GitHub Pages deployment.
#
# Usage:
#   .\deploy_github.ps1 -Username <your-github-username>
#   (you will be prompted for a Personal Access Token with `repo` scope)
#
# What it does:
#   1. Creates the GitHub repository `mono-run` (private:false).
#   2. Pushes this project to it.
#   3. Enables GitHub Pages from the `master` branch root.
#   4. Prints your permanent URL: https://<username>.github.io/mono-run/
param(
    [Parameter(Mandatory = $true)]
    [string]$Username
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$token = Read-Host -AsSecureString "GitHub Personal Access Token (repo scope)"
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($token))
$headers = @{
    "Authorization" = "token $plain"
    "User-Agent"    = "mono-run-deploy"
    "Accept"        = "application/vnd.github+json"
}

# 1. Create the repository (ignore failure if it already exists).
Write-Host "`n[1/4] Creating repository $Username/mono-run ..."
$body = '{"name":"mono-run","description":"MONO/RUN - mono 3-lane endless parkour","private":false}'
try {
    Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Method Post `
        -Headers $headers -ContentType "application/json" -Body $body | Out-Null
    Write-Host "      repository created."
} catch {
    $msg = $_.ErrorDetails.Message
    if ($msg -match "already exists") {
        Write-Host "      repository already exists, continuing."
    } else {
        throw "Failed to create repository: $msg"
    }
}

# 2. Push the project.
Write-Host "[2/4] Pushing project ..."
$remote = "https://${plain}@github.com/$Username/mono-run.git"
if (git remote | Select-String -Quiet "^origin$") {
    git remote set-url origin $remote
} else {
    git remote add origin $remote
}
git push -u origin master 2>&1 | ForEach-Object { "$_" } |
    Where-Object { $_ -notmatch $plain } | Write-Host
# Scrub the token from the stored remote URL.
git remote set-url origin "https://github.com/$Username/mono-run.git"
Write-Host "      pushed (token scrubbed from remote config)."

# 3. Enable GitHub Pages from master root.
Write-Host "[3/4] Enabling GitHub Pages ..."
$pagesBody = '{"source":{"branch":"master","path":"/"}}'
try {
    Invoke-RestMethod -Uri "https://api.github.com/repos/$Username/mono-run/pages" `
        -Method Post -Headers $headers -ContentType "application/json" -Body $pagesBody | Out-Null
    Write-Host "      Pages enabled."
} catch {
    $msg = $_.ErrorDetails.Message
    if ($msg -match "already exists") {
        Write-Host "      Pages already enabled."
    } else {
        Write-Host "      Pages setup returned: $msg"
        Write-Host "      Enable manually: repo Settings -> Pages -> branch master / root."
    }
}

# 4. Done.
Write-Host "[4/4] Deployment finished."
Write-Host ""
Write-Host "Permanent URL (first build may take 1-2 minutes):" -ForegroundColor Green
Write-Host "  https://$Username.github.io/mono-run/" -ForegroundColor Green
Write-Host ""
Write-Host "Note: the leaderboard API needs this repo's local server (python serve.py)."
Write-Host "On GitHub Pages the game runs fully; personal bests stay in the browser."
