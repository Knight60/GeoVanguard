<#
.SYNOPSIS
  Link (or copy) the GeoVanguard QGIS plugin into the QGIS 3 and/or QGIS 4 profile.

.DESCRIPTION
  Default: directory junctions so edits in VS Code are picked up after reloading
  the plugin (Plugin Reloader) or restarting QGIS:
      <repo>\geovanguard_qgis\geovanguard        -> <repo>\geovanguard   (bundled engine)
      <profile>\python\plugins\geovanguard_qgis  -> <repo>\geovanguard_qgis
  -Copy installs a plain copy (engine included), like the released ZIP.
  The old development link "TopologyTools" (before the rename) is removed.
  Then enable "GeoVanguard" in QGIS: Plugins > Manage and Install Plugins.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\install_plugin.ps1
  powershell -ExecutionPolicy Bypass -File tools\install_plugin.ps1 -Qgis 4 -Copy
  powershell -ExecutionPolicy Bypass -File tools\install_plugin.ps1 -Uninstall
#>
param(
    [ValidateSet('3', '4', 'both')] [string] $Qgis = 'both',
    [string] $Profile = 'default',
    [switch] $Copy,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root 'geovanguard_qgis'
$engine = Join-Path $root 'geovanguard'
$name = 'geovanguard_qgis'
$versions = if ($Qgis -eq 'both') { @('3', '4') } else { @($Qgis) }

function Remove-PluginDir($path) {
    if (Test-Path $path) {
        $item = Get-Item $path -Force
        if ($item.LinkType) { $item.Delete() } else { Remove-Item -Recurse -Force $path -Confirm:$false }
        Write-Host "  removed $path"
    }
}

# development junction: plugin\geovanguard -> engine
$inner = Join-Path $src 'geovanguard'
if (-not (Test-Path $inner)) {
    New-Item -ItemType Junction -Path $inner -Target $engine | Out-Null
    Write-Host "junction $inner -> $engine"
}

foreach ($v in $versions) {
    $plugins = Join-Path $env:APPDATA "QGIS\QGIS$v\profiles\$Profile\python\plugins"
    if (-not (Test-Path (Split-Path -Parent (Split-Path -Parent $plugins)))) {
        Write-Host "QGIS$v profile '$Profile' not found - skipped"
        continue
    }
    Write-Host "QGIS$v :"
    New-Item -ItemType Directory -Force $plugins | Out-Null
    Remove-PluginDir (Join-Path $plugins 'TopologyTools')
    $dst = Join-Path $plugins $name
    Remove-PluginDir $dst
    if ($Uninstall) { continue }
    if ($Copy) {
        New-Item -ItemType Directory -Force $dst | Out-Null
        Get-ChildItem $src -Force | Where-Object { $_.Name -ne 'geovanguard' -and $_.Name -ne '__pycache__' } |
            ForEach-Object { Copy-Item -Recurse $_.FullName (Join-Path $dst $_.Name) }
        Copy-Item -Recurse $engine (Join-Path $dst 'geovanguard')
        Get-ChildItem $dst -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -Confirm:$false
        Write-Host "  copied to $dst"
    } else {
        New-Item -ItemType Junction -Path $dst -Target $src | Out-Null
        Write-Host "  junction $dst -> $src"
    }
}
