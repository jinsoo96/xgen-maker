# XGEN MAKER 원라이너 — 사용:  .\maker.ps1 "쿼리"   (기본 observe)
#                        .\maker.ps1 "쿼리" -Mode act   (push+MR까지)
#                        .\maker.ps1 "쿼리" -Plan       (분석·MR초안만, 레포 미접촉)
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Query,
    [ValidateSet("observe", "act")][string]$Mode = "observe",
    [switch]$Plan
)
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $PSScriptRoot
if ($Plan) {
    python -m xgen_maker run $Query --config maker.config.json
} else {
    python -m xgen_maker run $Query --config maker.observe.config.json --mode $Mode
}
