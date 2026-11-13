param([switch]$Draft)
$ErrorActionPreference = 'Stop'
$base = $PSScriptRoot
if ($Draft) {
    $folder = Join-Path $base 'qa/draft'
    $name = 'pytestgen-defense-draft'
} else {
    $folder = $base
    $name = 'pytestgen-defense'
}
$deck = Join-Path $folder ($name + '.pptx')
$previews = Join-Path $folder 'previews'
$qa = Join-Path $base 'qa'
New-Item -ItemType Directory -Force -Path $previews,$qa | Out-Null
$app = $null
$pres = $null
$issues = @()
$inventory = @()
try {
    $app = New-Object -ComObject PowerPoint.Application
    $pres = $app.Presentations.Open($deck, -1, 0, 0)
    if ($pres.Slides.Count -ne 8) { throw 'Expected exactly eight slides.' }
    $pres.SaveAs((Join-Path $folder ($name + '.pdf')), 32)
    for ($i = 1; $i -le $pres.Slides.Count; $i++) {
        $slide = $pres.Slides.Item($i)
        $slide.Export((Join-Path $previews ('slide-{0:D2}.png' -f $i)), 'PNG', 1600, 900)
        $nativeTables = 0
        $nativeCharts = 0
        foreach ($shape in $slide.Shapes) {
            if ($shape.HasTable -eq -1) { $nativeTables++ }
            if ($shape.HasChart -eq -1) { $nativeCharts++ }
            if ($shape.Left -lt -1 -or $shape.Top -lt -1 -or ($shape.Left + $shape.Width) -gt ($pres.PageSetup.SlideWidth + 1) -or ($shape.Top + $shape.Height) -gt ($pres.PageSetup.SlideHeight + 1)) {
                $issues += [pscustomobject]@{slide=$i; shape=$shape.Name; issue='shape_outside_slide'}
            }
            if ($shape.HasTextFrame -eq -1 -and $shape.TextFrame2.HasText -eq -1) {
                $tf = $shape.TextFrame2
                $tr = $tf.TextRange
                if ($tr.BoundHeight -gt ($shape.Height - $tf.MarginTop - $tf.MarginBottom + 2) -or $tr.BoundWidth -gt ($shape.Width - $tf.MarginLeft - $tf.MarginRight + 3)) {
                    $issues += [pscustomobject]@{slide=$i; shape=$shape.Name; issue='text_bounds'; text=$tr.Text; bound_height=$tr.BoundHeight; height=$shape.Height; bound_width=$tr.BoundWidth; width=$shape.Width}
                }
            }
        }
        $inventory += [pscustomobject]@{slide=$i; shapes=$slide.Shapes.Count; tables=$nativeTables; charts=$nativeCharts}
    }
    $result = [pscustomobject]@{deck=$deck; slide_count=$pres.Slides.Count; renderer='Microsoft PowerPoint COM'; issues=@($issues); native_objects=@($inventory)}
    $suffix = if ($Draft) { 'draft' } else { 'final' }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $qa ('powerpoint-' + $suffix + '.json')) -Encoding utf8
    Write-Output ($result | ConvertTo-Json -Depth 8)
} finally {
    if ($null -ne $pres) { $pres.Close(); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($pres) }
    if ($null -ne $app) { $app.Quit(); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($app) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
