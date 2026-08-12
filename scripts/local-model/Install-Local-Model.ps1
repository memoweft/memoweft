[CmdletBinding()]
param(
    [string]$RuntimeSource = $env:MEMOWEFT_LLAMA_CPP_RUNTIME_SOURCE,
    [string]$ModelSource = $env:MEMOWEFT_LOCAL_MODEL_SOURCE
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

if ([string]::IsNullOrWhiteSpace($RuntimeSource)) {
    throw 'Supply -RuntimeSource or MEMOWEFT_LLAMA_CPP_RUNTIME_SOURCE with a CUDA llama.cpp runtime directory.'
}
if ([string]::IsNullOrWhiteSpace($ModelSource)) {
    throw 'Supply -ModelSource or MEMOWEFT_LOCAL_MODEL_SOURCE with the Qwen GGUF file.'
}

if (-not (Test-Path -LiteralPath $RuntimeSource -PathType Container)) {
    throw "CUDA llama.cpp runtime source does not exist: $RuntimeSource"
}
if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource 'llama-server.exe') -PathType Leaf)) {
    throw "Runtime source has no llama-server.exe: $RuntimeSource"
}
if (-not (Test-Path -LiteralPath (Join-Path $RuntimeSource 'ggml-cuda.dll') -PathType Leaf)) {
    throw "Runtime source has no ggml-cuda.dll and is not the expected CUDA build: $RuntimeSource"
}
if (-not (Test-Path -LiteralPath $ModelSource -PathType Leaf)) {
    throw "Model source does not exist: $ModelSource"
}

Initialize-LocalModelDirectories

Get-ChildItem -LiteralPath $RuntimeSource -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $script:RuntimeRoot -Recurse -Force
}

$sourceModel = Get-Item -LiteralPath $ModelSource
$modelInstallMethod = $null
$hardLinkFailure = $null
if (Test-Path -LiteralPath $script:ModelPath -PathType Leaf) {
    $destinationModel = Get-Item -LiteralPath $script:ModelPath
    if ($destinationModel.Length -ne $sourceModel.Length) {
        throw "Existing local model has the wrong size. Refusing to overwrite it automatically: $($script:ModelPath)"
    }
    $sourceFileId = (& fsutil.exe file queryfileid $sourceModel.FullName 2>$null | Out-String).Trim()
    $destinationFileId = (& fsutil.exe file queryfileid $destinationModel.FullName 2>$null | Out-String).Trim()
    if ($sourceFileId -eq $destinationFileId -and -not [string]::IsNullOrWhiteSpace($sourceFileId)) {
        $modelInstallMethod = 'existing-hardlink'
    }
    else {
        $modelInstallMethod = 'existing-file'
    }
}
else {
    try {
        New-Item -ItemType HardLink -Path $script:ModelPath -Target $sourceModel.FullName -ErrorAction Stop | Out-Null
        $modelInstallMethod = 'hardlink'
    }
    catch {
        $hardLinkFailure = $_.Exception.GetType().FullName
        Copy-Item -LiteralPath $sourceModel.FullName -Destination $script:ModelPath
        $modelInstallMethod = 'copy-fallback'
    }
}

$sourceServer = Join-Path $RuntimeSource 'llama-server.exe'
$runtimeSourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceServer).Hash
$runtimeDestinationHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $script:ServerExecutable).Hash
if ($runtimeSourceHash -ne $runtimeDestinationHash) {
    throw 'Copied llama-server.exe hash does not match its source.'
}

$runtimeVersion = (& $script:ServerExecutable --version 2>&1 | Out-String).Trim()
$installedModel = Get-Item -LiteralPath $script:ModelPath
$installation = [ordered]@{
    installedAt = (Get-Date).ToUniversalTime().ToString('o')
    runtimeSource = (Resolve-Path -LiteralPath $RuntimeSource).Path
    runtimeDestination = $script:RuntimeRoot
    runtimeCopyMethod = 'Copy-Item'
    runtimeServerSha256 = $runtimeDestinationHash
    runtimeVersion = $runtimeVersion
    cudaLibraryPresent = (Test-Path -LiteralPath (Join-Path $script:RuntimeRoot 'ggml-cuda.dll') -PathType Leaf)
    modelSource = $sourceModel.FullName
    modelDestination = $script:ModelPath
    modelInstallMethod = $modelInstallMethod
    hardLinkFailureType = $hardLinkFailure
    modelBytes = $installedModel.Length
}
$installationPath = Join-Path $script:StateRoot 'installation.json'
$installation | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $installationPath -Encoding utf8

Write-Host "CUDA runtime installed: $($script:RuntimeRoot)"
Write-Host "Model installed: $($script:ModelPath)"
Write-Host "Model install method: $modelInstallMethod"
Write-Host "Installation record: $installationPath"
