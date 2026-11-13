param([switch]$Draft)
$ErrorActionPreference='Stop'
$base=$PSScriptRoot
$folder=if($Draft){Join-Path $base 'qa/draft'}else{$base}
$name=if($Draft){'pytestgen-defense-draft'}else{'pytestgen-defense'}
$deck=Join-Path $folder ($name+'.pptx')
$copy=Join-Path $base 'qa/native-edit-check.pptx'
$issues=@()
$app=$null
$pres=$null
try {
 $app=New-Object -ComObject PowerPoint.Application
 $pres=$app.Presentations.Open($deck,-1,0,0)
 foreach($slide in $pres.Slides){
  foreach($shape in $slide.Shapes){
   if($shape.HasTable -eq -1){
    for($row=1;$row -le $shape.Table.Rows.Count;$row++){
     for($col=1;$col -le $shape.Table.Columns.Count;$col++){
      $cell=$shape.Table.Cell($row,$col).Shape
      $tf=$cell.TextFrame2
      if($tf.TextRange.BoundHeight -gt ($cell.Height-$tf.MarginTop-$tf.MarginBottom+2) -or $tf.TextRange.BoundWidth -gt ($cell.Width-$tf.MarginLeft-$tf.MarginRight+2)){
       $issues += [pscustomobject]@{slide=$slide.SlideIndex;row=$row;column=$col;issue='cell_text_bounds';text=$tf.TextRange.Text}
      }
     }
    }
   }
  }
 }
 $chartShape=@($pres.Slides.Item(4).Shapes | Where-Object {$_.HasChart -eq -1})[0]
 $tableShape=@($pres.Slides.Item(4).Shapes | Where-Object {$_.HasTable -eq -1})[0]
 $originalValues=@($chartShape.Chart.SeriesCollection(1).Values)
 $chartShape.Chart.HasTitle=$true
 $chartShape.Chart.ChartTitle.Text='QA native chart edit'
 $chartShape.Width=$chartShape.Width-6
 $tableShape.Table.Cell(1,1).Shape.TextFrame.TextRange.Text='QA native table edit'
 $pres.SaveAs($copy,24)
 $pres.Close()
 $pres=$null
 $pres=$app.Presentations.Open($copy,-1,0,0)
 $chartAgain=@($pres.Slides.Item(4).Shapes | Where-Object {$_.HasChart -eq -1})[0]
 $tableAgain=@($pres.Slides.Item(4).Shapes | Where-Object {$_.HasTable -eq -1})[0]
 $chartOK=$chartAgain.Chart.ChartTitle.Text -eq 'QA native chart edit'
 $tableOK=$tableAgain.Table.Cell(1,1).Shape.TextFrame.TextRange.Text -eq 'QA native table edit'
 if(-not $chartOK -or -not $tableOK){throw 'Native edit did not survive save/reopen'}
 $result=[pscustomobject]@{source=$deck;copy=$copy;cell_bounds_issues=@($issues);chart_series_values=$originalValues;chart_edit_save_reopen=$chartOK;table_edit_save_reopen=$tableOK}
 $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $base 'qa/native-edit-check.json') -Encoding utf8
 Write-Output ($result | ConvertTo-Json -Depth 6)
} finally {
 if($null -ne $pres){$pres.Close();[void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($pres)}
 if($null -ne $app){$app.Quit();[void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)}
 [GC]::Collect()
 [GC]::WaitForPendingFinalizers()
}
