<#
.SYNOPSIS
    Registers the Teams Presence Scraper as a Windows Scheduled Task that runs at startup.

.DESCRIPTION
    This script creates a scheduled task named 'TeamsPresenceScraper' that starts
    automatically when Windows boots. The scraper uses a headless browser, so it can
    run without an interactive desktop session after the initial --setup has been done.

    If you run this script without admin rights, it will automatically restart itself
    with a UAC prompt — you don't need to manually open PowerShell as Administrator.

.PARAMETER Users
    List of user names to track (exact names as shown in the Teams sidebar).
    If omitted, tracks only your own presence.

.PARAMETER PythonPath
    Full path to python.exe. If omitted, tries 'python' or 'python3' from PATH.

.PARAMETER ScraperDir
    Directory containing teams_scraper.py. Defaults to ..\scraper

.EXAMPLE
    .\setup-scraper-task.ps1

.EXAMPLE
    .\setup-scraper-task.ps1 -Users "Diego Zepeda","Another Colleague"
#>
param(
    [string[]]$Users = @(),

    [string]$PythonPath = "",

    [string]$ScraperDir = ""
)

# --- Self-elevation: restart as Administrator if not already ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
    Write-Host "Requesting Administrator privileges..."
    $argsForAdmin = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    foreach ($key in $PSBoundParameters.Keys) {
        $value = $PSBoundParameters[$key]
        if ($value -is [array]) {
            foreach ($item in $value) {
                $argsForAdmin += "-$key `"$item`""
            }
        } else {
            $argsForAdmin += "-$key `"$value`""
        }
    }
    Start-Process powershell -ArgumentList $argsForAdmin -Verb RunAs -Wait
    exit
}

# --- Main logic ---
try {
    $ErrorActionPreference = "Stop"

    # Resolve scraper directory
    if (-not $ScraperDir) {
        $ScraperDir = (Resolve-Path (Join-Path $PSScriptRoot "..\scraper")).Path
    }
    $scraperScript = Join-Path $ScraperDir "teams_scraper.py"

    if (-not (Test-Path $scraperScript)) {
        throw "Could not find teams_scraper.py in '$ScraperDir'. Please specify -ScraperDir."
    }

    # Resolve Python path
    if (-not $PythonPath) {
        $PythonPath = (Get-Command "python" -ErrorAction SilentlyContinue).Source
        if (-not $PythonPath) {
            $PythonPath = (Get-Command "python3" -ErrorAction SilentlyContinue).Source
        }
    }

    if (-not $PythonPath -or -not (Test-Path $PythonPath)) {
        throw "Could not find python.exe. Please install Python or specify -PythonPath."
    }

    Write-Host "Using Python: $PythonPath"
    Write-Host "Using scraper: $scraperScript"

    # Build argument list
    $argList = @("`"$scraperScript`"")

    if ($Users.Count -gt 0) {
        $argList += "--users"
        foreach ($user in $Users) {
            $argList += "`"$user`""
        }
    }

    $actionArgs = $argList -join " "

    # Task details
    $taskName = "TeamsPresenceScraper"
    $description = "Run Teams Presence Scraper at startup. No Azure AD app needed."

    # Remove existing task if present
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Removing existing scheduled task '$taskName'..."
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    # Create action, trigger, principal, settings
    $action = New-ScheduledTaskAction -Execute $PythonPath -Argument $actionArgs -WorkingDirectory $ScraperDir
    $trigger = New-ScheduledTaskTrigger -AtStartup

    # Principal: current user, run whether logged on or not, with highest privileges
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    # Prompt for password securely
    $credential = Get-Credential -UserName $currentUser -Message "Enter your Windows password for the scheduled task (required to run whether logged on or not):"
    if (-not $credential -or -not $credential.Password) {
        throw "Password is required to create a task that runs whether the user is logged on or not. If your account has no password, Windows does not allow this. Either set a Windows password or use 'Run only when user is logged on' instead."
    }

    $password = $credential.GetNetworkCredential().Password

    # Register the task
    # Note: use -User (string) + -Password + -RunLevel, NOT -Principal (object).
    # Those are mutually exclusive parameter sets.
    Write-Host "Creating scheduled task '$taskName'..."
    Register-ScheduledTask `
        -TaskName $taskName `
        -Description $description `
        -Action $action `
        -Trigger $trigger `
        -User $currentUser `
        -Password $password `
        -RunLevel Highest `
        -Settings $settings `
        -Force | Out-Null

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "SUCCESS! Task '$taskName' created." -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "It will run at every system startup under: $currentUser"
    Write-Host ""
    Write-Host "IMPORTANT: Make sure you have already run --setup so the session state exists:"
    Write-Host "  cd scraper"
    Write-Host "  python teams_scraper.py --setup"
    Write-Host ""
    Write-Host "Manage the task with:"
    Write-Host "  Start:  Start-ScheduledTask -TaskName '$taskName'"
    Write-Host "  Stop:   Stop-ScheduledTask -TaskName '$taskName'"
    Write-Host "  View:   Get-ScheduledTask -TaskName '$taskName'"

} catch {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "FAILED TO CREATE TASK" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Common causes:"
    Write-Host "  - Wrong Windows password"
    Write-Host "  - Windows account has no password (blank passwords are not allowed for 'run whether logged on or not')"
    Write-Host "  - You cancelled the password dialog"
    Write-Host ""
}

Write-Host ""
Read-Host "Press ENTER to close this window"
