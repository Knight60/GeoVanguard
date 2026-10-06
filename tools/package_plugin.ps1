<#
.SYNOPSIS
  Build dist\geovanguard_qgis-<version>.zip for "Install from ZIP" / plugins.qgis.org.

.DESCRIPTION
  The ZIP holds geovanguard_qgis\ (the QGIS plugin) with the standalone engine
  copied into geovanguard_qgis\geovanguard\ (vendored, imported relatively), so
  the plugin needs nothing besides QGIS.  The development junction
  geovanguard_qgis\geovanguard is not followed; the engine is copied from
  <repo>\geovanguard instead.
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$name = 'geovanguard_qgis'
$src = Join-Path $root $name
$engine = Join-Path $root 'geovanguard'
$version = (Select-String -Path (Join-Path $src 'metadata.txt') -Pattern '^version=(.+)$').Matches[0].Groups[1].Value.Trim()
$dist = Join-Path $root 'dist'
$stage = Join-Path $dist 'stage'
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage -Confirm:$false }
$out = Join-Path $stage $name
New-Item -ItemType Directory -Force $out | Out-Null

function Copy-Tree($from, $to) {
    # plain files only, no __pycache__ / .pyc, never through junctions
    Get-ChildItem $from -Force | ForEach-Object {
        if ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) { return }
        if ($_.Name -eq '__pycache__' -or $_.Extension -eq '.pyc') { return }
        $target = Join-Path $to $_.Name
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Force $target | Out-Null
            Copy-Tree $_.FullName $target
        } else {
            Copy-Item $_.FullName $target
        }
    }
}

Copy-Tree $src $out
New-Item -ItemType Directory -Force (Join-Path $out 'geovanguard') | Out-Null
Copy-Tree $engine (Join-Path $out 'geovanguard')
$license = Join-Path $root 'LICENSE'
if (Test-Path $license) { Copy-Item $license (Join-Path $out 'LICENSE') }

$zip = Join-Path $dist "$name-$version.zip"
if (Test-Path $zip) { Remove-Item $zip -Confirm:$false }
Compress-Archive -Path $out -DestinationPath $zip
Remove-Item -Recurse -Force $stage -Confirm:$false
Write-Host "Built $zip"
