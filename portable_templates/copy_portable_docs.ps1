param(
    [Parameter(Mandatory = $true)]
    [string]$BundleRoot
)

$templateRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$readmeDest = "README_{0}{1}{2}.md" -f ([char]0x4FBF), ([char]0x643A), ([char]0x7248)
$textDest = "{0}{1}{2}{3}{4}.txt" -f ([char]0x4F7F), ([char]0x7528), ([char]0x524D), ([char]0x5FC5), ([char]0x8BFB)

Copy-Item -LiteralPath (Join-Path $templateRoot 'README_portable_zh.md') `
    -Destination (Join-Path $BundleRoot $readmeDest) `
    -Force

Copy-Item -LiteralPath (Join-Path $templateRoot 'USE_BEFORE.txt') `
    -Destination (Join-Path $BundleRoot $textDest) `
    -Force
