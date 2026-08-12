[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

function Get-GpuSample {
    $gpuLine = (& nvidia-smi.exe --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace([string]$gpuLine)) {
        return $null
    }
    $parts = ([string]$gpuLine).Split(',') | ForEach-Object { $_.Trim() }
    return [pscustomobject]@{
        index = [int]$parts[0]
        name = $parts[1]
        usedMemoryMiB = [int]$parts[2]
        totalMemoryMiB = [int]$parts[3]
        utilizationPercent = [int]$parts[4]
    }
}

function Get-ServerGpuMemoryMiB {
    param([Parameter(Mandatory = $true)][int]$TargetProcessId)

    $lines = & nvidia-smi.exe --query-compute-apps=pid,used_memory --format=csv,noheader,nounits 2>$null
    foreach ($line in $lines) {
        $parts = ([string]$line).Split(',') | ForEach-Object { $_.Trim() }
        if ($parts.Count -ge 2 -and $parts[0] -eq [string]$TargetProcessId -and $parts[1] -match '^\d+$') {
            return [int]$parts[1]
        }
    }
    return $null
}

function Get-LoadedCudaModules {
    param([Parameter(Mandatory = $true)][int]$TargetProcessId)

    return @(
        Get-Process -Id $TargetProcessId -Module -ErrorAction Stop |
            Where-Object { $_.ModuleName -match '^(ggml-cuda|cublas|cublasLt|nvcuda|nvcuda64|cudart|nvcudart)' } |
            ForEach-Object { [string]$_.ModuleName } |
            Sort-Object -Unique
    )
}

function Test-NvidiaComputeProcess {
    param([Parameter(Mandatory = $true)][int]$TargetProcessId)

    $lines = & nvidia-smi.exe --query-compute-apps=pid,process_name --format=csv,noheader 2>$null
    foreach ($line in $lines) {
        if ([string]$line -match "^$TargetProcessId,\s+") {
            return $true
        }
    }
    return $false
}

$state = Get-LocalModelState
if ($null -eq $state) {
    throw 'No state-managed local model is running. Use Start-Local-Model.cmd first.'
}
$inspection = Get-LocalModelProcessInspection -TargetProcessId ([int]$state.pid) -ExpectedProcessStartTicks ([string]$state.processStartTicks)
if (-not $inspection.Verified) {
    throw "Managed PID validation failed: $($inspection.Reason)."
}

$healthResponse = Invoke-WebRequest -UseBasicParsing -Uri $script:HealthUrl -Method Get -TimeoutSec 5
if ([int]$healthResponse.StatusCode -ne 200) {
    throw "Health endpoint returned HTTP $($healthResponse.StatusCode)."
}

# These requests intentionally carry no Authorization header. A successful call
# proves the launcher did not leak an inherited LLAMA_API_KEY into llama-server.
$modelsResponse = Invoke-RestMethod -Uri $script:ModelsUrl -Method Get -TimeoutSec 15
$modelIds = @($modelsResponse.data | ForEach-Object { $_.id })
if ($modelIds -notcontains $script:ModelAlias) {
    throw "The model list did not expose expected alias $($script:ModelAlias)."
}

$chatRequest = [ordered]@{
    model = $script:ModelAlias
    messages = @(
        [ordered]@{ role = 'user'; content = '请只回答“记忆就绪”，不要解释。 /no_think' }
    )
    temperature = 0
    max_tokens = 32
    stream = $false
    chat_template_kwargs = [ordered]@{ enable_thinking = $false }
}
$chatJson = $chatRequest | ConvertTo-Json -Depth 8 -Compress
$httpClient = [System.Net.Http.HttpClient]::new()
$httpClient.Timeout = [TimeSpan]::FromSeconds(120)
$httpContent = [System.Net.Http.StringContent]::new($chatJson, [System.Text.Encoding]::UTF8, 'application/json')

$gpuSamples = [System.Collections.Generic.List[object]]::new()
$initialGpu = Get-GpuSample
if ($null -ne $initialGpu) { $gpuSamples.Add($initialGpu) }
$chatTask = $httpClient.PostAsync($script:ChatUrl, $httpContent)
while (-not $chatTask.IsCompleted) {
    $sample = Get-GpuSample
    if ($null -ne $sample) { $gpuSamples.Add($sample) }
    Start-Sleep -Milliseconds 200
}
$chatResponse = $chatTask.GetAwaiter().GetResult()
$chatResponseBody = $chatResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
$httpClient.Dispose()
if (-not $chatResponse.IsSuccessStatusCode) {
    throw "Chat completion returned HTTP $([int]$chatResponse.StatusCode): $chatResponseBody"
}
$chat = $chatResponseBody | ConvertFrom-Json
$assistantContent = [string]$chat.choices[0].message.content
if ([string]::IsNullOrWhiteSpace($assistantContent)) {
    throw 'Chat completion returned an empty assistant content field.'
}

$peakGpu = $gpuSamples | Sort-Object usedMemoryMiB -Descending | Select-Object -First 1
$serverGpuMemory = Get-ServerGpuMemoryMiB -TargetProcessId ([int]$state.pid)
$loadedCudaModules = Get-LoadedCudaModules -TargetProcessId ([int]$state.pid)
$nvidiaComputeProcessObserved = Test-NvidiaComputeProcess -TargetProcessId ([int]$state.pid)
$cudaBackendConfirmed = ($loadedCudaModules -contains 'ggml-cuda.dll') -and $nvidiaComputeProcessObserved
$backendLines = @()
if (Test-Path -LiteralPath $state.stderrLog -PathType Leaf) {
    $backendLines = @(
        Get-Content -LiteralPath $state.stderrLog |
            Select-String -Pattern 'ggml_cuda_init|found [0-9]+ CUDA|CUDA0|offload|flash.attn|KV buffer|compute buffer|initializing, n_slots|model loaded|listening on' |
            Select-Object -First 30 |
            ForEach-Object { $_.Line.Trim() }
    )
}

$verification = [ordered]@{
    verifiedAt = (Get-Date).ToUniversalTime().ToString('o')
    pid = [int]$state.pid
    endpoint = "http://$($script:BindAddress):$($script:ServerPort)/v1"
    unauthenticatedRequests = $true
    healthStatusCode = [int]$healthResponse.StatusCode
    healthBody = [string]$healthResponse.Content
    modelIds = $modelIds
    chatStatusCode = [int]$chatResponse.StatusCode
    chatContent = $assistantContent
    finishReason = [string]$chat.choices[0].finish_reason
    promptTokens = [int]$chat.usage.prompt_tokens
    completionTokens = [int]$chat.usage.completion_tokens
    requestedServerArguments = @($script:ServerArguments)
    peakTotalGpuMemoryMiBDuringChat = if ($null -ne $peakGpu) { $peakGpu.usedMemoryMiB } else { $null }
    totalGpuMemoryMiB = if ($null -ne $peakGpu) { $peakGpu.totalMemoryMiB } else { $null }
    serverProcessGpuMemoryMiB = $serverGpuMemory
    serverProcessGpuMemoryNote = if ($null -eq $serverGpuMemory) { 'nvidia-smi reports per-process VRAM as N/A under this Windows WDDM session; total board memory is sampled above.' } else { $null }
    gpuName = if ($null -ne $peakGpu) { $peakGpu.name } else { $null }
    cudaBackendConfirmed = $cudaBackendConfirmed
    loadedCudaModules = $loadedCudaModules
    nvidiaComputeProcessObserved = $nvidiaComputeProcessObserved
    backendLogEvidence = $backendLines
    stderrLog = [string]$state.stderrLog
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$evidencePath = Join-Path $script:StateRoot "verification-$stamp.json"
$verification | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $evidencePath -Encoding utf8
$verification.evidencePath = $evidencePath
$verification | ConvertTo-Json -Depth 8
