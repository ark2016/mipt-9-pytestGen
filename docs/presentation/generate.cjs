(function generator(){
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'../..');
const PptxGenJS=require(path.join(root,'out/presentation-tools/node_modules/pptxgenjs'));
const args=process.argv.slice(2),draft=args.includes('--draft'),ei=args.indexOf('--evidence');
const evidencePath=path.resolve(root,ei>=0?args[ei+1]:'docs/presentation/evidence.json');
const readJson=p=>JSON.parse(fs.readFileSync(p,'utf8').replace(/^\uFEFF/,''));
const evidence=readJson(evidencePath),sources=[];
if(evidence.schema_version!==1)throw new Error('Unsupported evidence schema');
function getReport(key){
 let p=evidence[key]?.report_path&&path.resolve(root,evidence[key].report_path);
 if(draft&&key==='mbpp'&&!fs.existsSync(p||''))p=path.join(root,'out/mbpp-verified/mbpp-report.json');
 if(!p||!fs.existsSync(p)){if(draft)return null;throw new Error('Missing raw report: '+key);}
 const bytes=fs.readFileSync(p);
 sources.push({kind:key,path:path.relative(root,p).replace(/\\/g,'/'),sha256:crypto.createHash('sha256').update(bytes).digest('hex')});
 return readJson(p);
}
const mbpp=getReport('mbpp'),heuristic=getReport('heuristic'),library=getReport('library'),ci=evidence.ci;
if(!draft){
 for(const key of ['heuristic','library','ci'])if(!evidence[key]?.completed)throw new Error('Incomplete evidence: '+key);
 if(!evidence.source_commit||!ci.run_url||!ci.commit||!Number.isInteger(ci.passed)||!Number.isFinite(ci.coverage_percent))throw new Error('Incomplete CI/provenance');
 if(heuristic.repairs.length!==32||heuristic.oracles.length!==32)throw new Error('Expected 32 repairs and 32 oracle observations');
 if(!library.baseline||!['existing','cold'].every(mode=>library.modes.some(r=>r.mode===mode)))throw new Error('Incomplete library modes');
 if(!evidence.heuristic.evidence_url||!evidence.library.evidence_url)throw new Error('Missing experiment URLs');
 if(ci.commit!==evidence.source_commit)throw new Error('CI and experiment source commits differ');
 const commits=heuristic.source_commits||[heuristic.source_commit];
 if(!commits.every(c=>c===evidence.source_commit))throw new Error('Heuristic report does not match the evidence commit');
}
function matrix(rows,truthKey){
 const m={tp:0,fp:0,tn:0,fn:0,n:0};
 for(const r of rows.filter(r=>r.status==='classified')){
  if(typeof r[truthKey]!=='boolean'||typeof r.predicted_suspicious!=='boolean')throw new Error('Invalid classified record '+r.id);
  m[r[truthKey]?(r.predicted_suspicious?'tp':'fn'):(r.predicted_suspicious?'fp':'tn')]++;
  m.n++;
 }
 m.precision=m.tp+m.fp?m.tp/(m.tp+m.fp):null;m.recall=m.tp+m.fn?m.tp/(m.tp+m.fn):null;
 return m;
}
let repairMatrix,oracleMatrix,oracleAllMatrix;
if(heuristic){
 repairMatrix=matrix(heuristic.repairs,'actual_masking');oracleAllMatrix=matrix(heuristic.oracles,'actual_bug');oracleMatrix=matrix(heuristic.oracles.filter(r=>['ok','suspicious'].includes(r.verdict)),'actual_bug');
 for(const [k,m] of [['repair_all',repairMatrix],['oracle_all',oracleAllMatrix],['oracle_reviewed',oracleMatrix]])for(const n of ['tp','fp','tn','fn','n'])if(m[n]!==heuristic.summary[k][n])throw new Error('Confusion mismatch: '+k+'.'+n);
 const broad=matrix(heuristic.repairs.map(r=>r.status==='degenerate_repair'?{...r,status:'classified'}:r),'actual_masking');
 for(const k of ['tp','fp','tn','fn','n'])if(broad[k]!==heuristic.summary.repair_all_passing_rewrites[k])throw new Error('Broader repair matrix mismatch: '+k);
 for(const [label,flag] of [['documented',true],['undocumented',false]]){
   const subset=heuristic.repairs.filter(r=>r.documented===flag);
   for(const [key,rows] of [['repair_'+label,subset],['repair_all_passing_'+label,subset.map(r=>r.status==='degenerate_repair'?{...r,status:'classified'}:r)]]){
     const m=matrix(rows,'actual_masking');
     for(const k of ['tp','fp','tn','fn','n'])if(m[k]!==heuristic.summary[key][k])throw new Error('Stratified repair matrix mismatch: '+key+'.'+k);
   }
 }
 for(const list of [heuristic.repairs,heuristic.oracles])if(new Set(list.map(r=>r.id)).size!==list.length)throw new Error('Duplicate scenario');
}
if(mbpp){
 const s=mbpp.summary,rows=mbpp.tasks,mutants=rows.flatMap(r=>r.mutants);
 const checks={tasks:rows.length,tasks_with_passing_tests:rows.filter(r=>r.passed_reference>0).length,
 invalid_references:rows.filter(r=>r.status==='invalid_reference').length,
 generated_executed:rows.reduce((a,r)=>a+r.generated_executed,0),passed_reference:rows.reduce((a,r)=>a+r.passed_reference,0),
 tests_passing_reference_and_killing_mutant:rows.reduce((a,r)=>a+r.tests_with_kills.length,0),
 killed:mutants.filter(r=>r.status==='killed').length,
 eligible_mutants:mutants.filter(r=>['killed','survived','unverified_mutant'].includes(r.status)).length,
 unverified_mutants:mutants.filter(r=>r.status==='unverified_mutant').length};
 for(const[k,v]of Object.entries(checks))if(s[k]!==v)throw new Error('MBPP summary mismatch: '+k);
}

if(library){
 const runs=[library.baseline,...library.modes.flatMap(r=>[r.generated,r.combined])].filter(Boolean);
 const same=(a,b)=>JSON.stringify([...a].sort())===JSON.stringify([...b].sort());
 for(const r of runs){
   if(r.collected!==r.outcomes.length)throw new Error('Library collected count mismatch');
   for(const [status,count]of Object.entries(r.counts))if(r.outcomes.filter(o=>o.status===status).length!==count)throw new Error('Library outcome count mismatch');
   const c=r.coverage;if(!c)continue;
   if(c.covered_lines!==c.executed_lines.length||c.covered_branches!==c.executed_arcs.length)throw new Error('Library coverage count mismatch');
   if(c.total_lines!==c.executed_lines.length+c.missing_lines.length||c.total_branches!==c.executed_arcs.length+c.missing_arcs.length)throw new Error('Library denominator mismatch');
   if(Math.abs(c.line_percent-100*c.covered_lines/c.total_lines)>1e-8||Math.abs(c.branch_percent-100*c.covered_branches/c.total_branches)>1e-8)throw new Error('Library coverage percentage mismatch');
 }
 for(const mode of library.modes){
   const g=mode.gain_over_upstream;if(!g)continue;
   const a=library.baseline.coverage,b=mode.combined.coverage;
   if(a.total_lines!==b.total_lines||a.total_branches!==b.total_branches)throw new Error('Library coverage scopes differ');
   for(const[k,left,right]of [['lines',a.executed_lines,b.executed_lines],['arcs',a.executed_arcs,b.executed_arcs]]){
     if(!same(g['new_'+k],right.filter(v=>!left.includes(v)))||!same(g['lost_'+k],left.filter(v=>!right.includes(v))))throw new Error('Library coverage difference mismatch: '+k);
   }
   if(Math.abs(g.line_percentage_points-(b.line_percent-a.line_percent))>1e-8||Math.abs(g.branch_percentage_points-(b.branch_percent-a.branch_percent))>1e-8)throw new Error('Library gain mismatch');
 }
}

const out=draft?path.join(__dirname,'qa/draft'):__dirname;
fs.mkdirSync(out,{recursive:true});fs.mkdirSync(path.join(__dirname,'qa'),{recursive:true});
const pptx=new PptxGenJS();
pptx.layout='LAYOUT_WIDE';pptx.author='Аркадий Лебедев';pptx.subject='Защита проекта pytestgen, МФТИ, материалы на 13 ноября 2026';
pptx.title='pytestgen: гибридная генерация pytest-тестов';pptx.company='МФТИ';pptx.lang='ru-RU';
pptx.theme={headFontFace:'Arial',bodyFontFace:'Arial',lang:'ru-RU'};
const C={navy:'172B3A',teal:'087F80',mint:'CBE7DF',paper:'F7F5EF',white:'FFFFFF',muted:'536670',grid:'D5DEDC',amber:'93601B'},notes=[];
const percent=(n,d,dec=1)=>d?(100*n/d).toFixed(dec).replace('.',',')+'%':'н/д';
const pct=(v,dec=1)=>v==null?'н/д':(100*v).toFixed(dec).replace('.',',')+'%';
function text(s,t,x,y,w,h,size=22,opts={}){s.addText(t,{x,y,w,h,fontFace:'Arial',fontSize:size,color:C.navy,margin:0,isTextBox:true,vertAnchor:'mid',...opts});}
function slide(t,{dark=false}={}){
 const s=pptx.addSlide();s.background={color:dark?C.navy:C.paper};
 if(t)text(s,t,.65,.48,12.03,.72,34,{bold:true,color:dark?C.paper:C.navy});
 text(s,String(pptx._slides.length).padStart(2,'0'),12.12,7.02,.53,.23,12,{color:dark?'A5BCBF':C.muted,align:'right'});
 if(draft)text(s,'ВНУТРЕННИЙ ЧЕРНОВИК: результаты не все получены',.65,7.02,10.6,.25,11,{color:dark?'D8B781':C.amber});
 return s;
}
function addNotes(s,title,seconds,narrative,details,urls=[]){
 const n=`${title}\nВремя: около ${seconds} секунд.\n\n${narrative}\n\nОпределения и ограничения:\n${details}\n\nИсточники:\n${urls.filter(Boolean).join('\n')}`;
 s.addNotes(n);notes.push({title,seconds,text:n});
}
function box(s,label,x,y,w,h,{fill=C.white,color=C.navy,border=C.grid,size=22,bold=false}={}){
 s.addShape(pptx.ShapeType.rect,{x,y,w,h,fill:{color:fill},line:{color:border,width:1}});
 text(s,label,x+.15,y+.09,w-.3,h-.18,size,{color,bold,align:'center'});
}
function arrow(s,x1,y1,x2,y2){s.addShape(pptx.ShapeType.line,{x:Math.min(x1,x2),y:Math.min(y1,y2),w:Math.abs(x2-x1),h:Math.abs(y2-y1),line:{color:C.teal,width:1.7,beginArrowType:x2<x1?'triangle':'none',endArrowType:x2<x1?'none':'triangle'}});}
function table(s,rows,x,y,widths,rowH=.7,size=20){
 const data=rows.map((r,i)=>r.map((v,j)=>({text:String(v),options:{fill:{color:i===0?C.navy:(i%2===1?C.white:C.paper)},color:i===0?C.paper:C.navy,bold:i===0,fontSize:size,align:j===0?'left':'center',valign:'middle'}})));
 s.addTable(data,{x,y,w:widths.reduce((a,b)=>a+b,0),colW:widths,rowH,fontFace:'Arial',fontSize:size,margin:[.13,.15,.13,.15],border:{type:'solid',color:C.grid,pt:.6},color:C.navy,autoPage:false});
}
const repo=evidence.repository_url,rev=evidence.source_commit||'develop',blob=p=>`${repo}/blob/${rev}/${p}`;
const rawUrl=k=>evidence[k]?.evidence_url,sourcePath=k=>sources.find(s=>s.kind===k)?.path||'результаты ещё не получены';
{
 const s=slide(null,{dark:true});
 text(s,'pytestgen',.8,1.55,11.8,1.05,64,{bold:true,color:C.paper});
 text(s,'Гибридная генерация\npytest-тестов',.84,2.9,11.6,1.34,32,{color:C.mint});
 text(s,'Аркадий Лебедев',.84,5.7,11.6,.4,23,{color:C.paper});
 text(s,'МФТИ, материалы на 13 ноября 2026',.84,6.22,11.6,.35,19,{color:'A5BCBF'});
 addNotes(s,'1. pytestgen',15,'Я представляю pytestgen: генератор pytest-тестов, в котором символьный анализ строит основу, а небольшая локальная языковая модель помогает на сложных участках. Коротко покажу устройство и измерения, затем перейду к CLI. Вся живая демонстрация укладывается в десять минут, для её поддержки использую выбранные слайды.','Автор: Аркадий Лебедев, МФТИ. Полный самостоятельный обзор слайдов занимает около пяти минут. На десятиминутной защите используются только выбранные слайды вместе с живым CLI, пять минут к десяти не добавляются.',[repo,blob('docs/demo.md')]);
}
{
 const s=slide('Гибридный пайплайн'),xs=[.65,3.85,7.05,10.25],bw=2.43;
 const nodes=[['AST','Функции и ветки'],['Z3','Входы и границы'],['Запись','Результат вызова'],['Шаблон','pytest-тест']];
 nodes.forEach((n,i)=>{box(s,n[0],xs[i],1.8,bw,.83,{fill:i===1?C.teal:C.navy,color:C.paper,border:i===1?C.teal:C.navy,size:25,bold:true});
 text(s,n[1],xs[i],2.82,bw,.7,19,{align:'center'});if(i<3)arrow(s,xs[i]+bw+.1,2.21,xs[i+1]-.13,2.21);});
 text(s,'LLM',.65,3.98,2,.5,27,{bold:true,color:C.teal});
 text(s,'Новые входы, проверка оракула,\nсвободные тесты для сложного кода',3.85,3.91,8.83,.88,23);
 text(s,'Проверка',.65,5.08,2.8,.5,25,{bold:true});
 text(s,'Запуск pytest, repair и поиск пробелов покрытия.\nНа выходе: тесты и отчёт Markdown / JSON.',3.85,5,8.83,.95,22);
 text(s,'Без LLM остаются Z3 и граничные входы.',.65,6.38,12.03,.4,21,{color:C.teal,bold:true});
 addNotes(s,'2. Гибридный пайплайн',40,'AST выделяет функции, ветви и условия путей. Z3 ищет входы, а вокруг простых сравнений добавляются граничные значения. Программа выполняется, и наблюдаемый результат попадает в шаблон pytest. Модель предлагает дополнительные входы, проверяет оракул по описанию и пишет свободные тесты. Затем всё запускается, падающие свободные тесты можно исправить, а по покрытию запускается дополнительный цикл. При недоступной модели остаётся символьная часть.','Записанное поведение не гарантирует правильность. Функция выполняется дважды для проверки стабильности. Методы экземпляров и --strategy llm используют free-form путь. Неудачные табличные кейсы отбрасываются. Непригодный ответ модели не прерывает весь конвейер.',[blob('docs/architecture.md'),blob('src/pytestgen/application/generate.py'),blob('src/pytestgen/container.py')]);
}
{
 const s=slide('Архитектура и инженерные проверки');
 box(s,'CLI',.65,1.65,1.55,1.05,{fill:C.navy,color:C.paper,border:C.navy,bold:true});
 box(s,'Сценарии\nприложения',2.83,1.65,3.03,1.05,{fill:C.teal,color:C.paper,border:C.teal,bold:true});
 box(s,'Порты\nProtocol',6.49,1.65,2.28,1.05,{bold:true});
 box(s,'Адаптеры\nZ3 / LLM / pytest',9.4,1.65,3.28,1.05,{bold:true});
 arrow(s,2.3,2.18,2.7,2.18);arrow(s,5.96,2.18,6.36,2.18);arrow(s,9.3,2.18,8.9,2.18);
 text(s,'Адаптеры реализуют порты, домен хранит данные.',.65,3.03,12.03,.44,21,{color:C.muted});
 text(s,'Запуск кода',.65,4.02,3.4,.45,25,{bold:true});
 text(s,'Временная копия, subprocess и таймауты.\nDocker упаковывает приложение целиком.',4.16,3.96,8.52,.88,22);
 text(s,'Граница защиты',.65,5.16,3.4,.45,24,{bold:true});
 text(s,'Нет отдельного контейнера на каждый тест.\nВраждебный код требует более строгой изоляции.',4.16,5.08,8.52,.88,21);
 const ciText=ci?.completed?`CI: ${ci.passed} passed, ${ci.skipped} skipped, покрытие ${Number(ci.coverage_percent).toFixed(2).replace('.',',')}% (Python ${ci.python}).`:'Новый CI: результаты ожидаются.';
 text(s,ciText,.65,6.35,12.03,.44,20,{color:C.teal,bold:true});
 addNotes(s,'3. Архитектура и инженерные проверки',40,`Сценарии приложения работают через порты, а AST, Z3, клиент модели, раннер и рендерер отчёта подключены адаптерами. Это позволяет менять реализации независимо. Генерируемый код выполняется во временной копии в subprocess с таймаутом. Docker запускает всё приложение, но не создаёт отдельный контейнер на каждый тест. В CI проверяются стиль, типы, тесты и сборка образа. ${ci?.completed?ciText:'Актуальный результат CI будет указан после завершения запуска.'}`,
 `Под subprocess подразумевается изоляция исполнения, а не надёжная песочница для враждебного кода. POSIX дополнительно ограничивает CPU и адресное пространство, Windows этих ограничений не имеет. Доступ к сети и разрешённым файлам сохраняется.\nCI: ${JSON.stringify(ci)}. Покрытие относится к проекту pytestgen, а не к MBPP или python-slugify. Обычный CI использует mock LLM.`,[blob('docs/architecture.md'),blob('docs/adr/0004-subprocess-sandbox.md'),blob('docker-compose.yml'),ci?.run_url]);
}
{
 const s=slide('MBPP: результат на 100 задачах');
 if(mbpp){
 const m=mbpp.summary,empty=mbpp.tasks.filter(r=>r.status==='no_passing_tests').length;
 s.addChart(pptx.ChartType.bar,[{name:'Задачи',labels:['Есть прошедшие тесты','Нет прошедших тестов','Невалидный эталон'],values:[m.tasks_with_passing_tests,empty,m.invalid_references]}],{
 x:.65,y:1.62,w:5.2,h:3.72,barDir:'bar',barGrouping:'clustered',showTitle:false,showLegend:false,showValue:true,dataLabelPosition:'outEnd',dataLabelColor:C.navy,dataLabelFontFace:'Arial',dataLabelFontSize:19,dataLabelFormatCode:'0',chartColors:[C.teal],catAxisLabelFontFace:'Arial',catAxisLabelFontSize:18,catAxisLabelColor:C.navy,catAxisLineShow:false,catAxisOrientation:'maxMin',catGridLine:{style:'none'},valAxisMinVal:0,valAxisMaxVal:65,valAxisMajorUnit:20,valAxisLabelFontFace:'Arial',valAxisLabelFontSize:14,valAxisLabelColor:C.muted,valAxisLineShow:false,valGridLine:{color:C.grid,width:.6},showBorder:false,showSerName:false,showLabel:false});
 table(s,[['Метрика','Число','Доля'],['Проходят эталон',`${m.passed_reference}/${m.generated_executed}`,percent(m.passed_reference,m.generated_executed)],['Проходят эталон\nи убивают мутанта',`${m.tests_passing_reference_and_killing_mutant}/${m.generated_executed}`,percent(m.tests_passing_reference_and_killing_mutant,m.generated_executed)],['Убитые мутанты',`${m.killed}/${m.eligible_mutants}`,percent(m.killed,m.eligible_mutants)]],6.1,1.72,[3.3,1.65,1.63],.85,19);
 text(s,`Доли тестов условны: только ${m.generated_executed} сохранённых.\n${m.unverified_mutants} запуска мутантов не проверены. ${m.generated_executed-m.passed_reference} тест упал на эталоне.`,.65,5.54,12.03,.85,20);
 text(s,evidence.mbpp.model_note,.65,6.59,12.03,.33,18,{color:C.muted});
 addNotes(s,'4. MBPP',45,`На фиксированной выборке из ста задач ${m.tasks_with_passing_tests} дали хотя бы один тест, прошедший эталон. В ${empty} задачах таких тестов нет, один эталон невалиден. Из ${m.generated_executed} сохранённых тестов ${m.passed_reference} проходят эталон, а ${m.tests_passing_reference_and_killing_mutant} ещё и убивают хотя бы один мутант. Убиты ${m.killed} из ${m.eligible_mutants} допустимых мутантов. Эти проценты нельзя переносить на все задачи: они условны по сохранённым тестам. Четыре запуска мутантов остались непроверенными.`,
 `Источник: ${sourcePath('mbpp')}.\nseed=${mbpp.seed}, dataset revision=${mbpp.dataset_revision}, strategy=${mbpp.strategy}, model=${mbpp.model}, max_mutants=${mbpp.max_mutants}.\nКогорта: ${m.tasks_with_passing_tests}+${empty}+${m.invalid_references}=${m.tasks}. Доли: ${m.passed_reference}/${m.generated_executed}, ${m.tests_passing_reference_and_killing_mutant}/${m.generated_executed}, ${m.killed}/${m.eligible_mutants}. Знаменатель mutation score включает killed+survived+unverified. Подбор мутантов выполняется по эталонным тестам MBPP, их исходники не передаются генератору. Эти результаты относятся к историческому запуску 42826762843. Единственное падение сохранённого теста на эталоне не объясняется предположением о причине. Это оценка одного режима, не сравнение с другой моделью или инструментом.`,[rawUrl('mbpp'),blob('scripts/bench_mbpp.py')]);
 }else{text(s,'Ожидается исходный отчёт MBPP',.65,2.4,12,.8,28);addNotes(s,'4. MBPP',45,'Исходный отчёт ещё не подключён.','Числа не подставлены.',[rawUrl('mbpp')]);}
}
{
 const s=slide('Проверка эвристики repair');
 text(s,'8 × 2 × 2 = 32',.65,1.56,12.03,.95,49,{bold:true,color:C.teal});
 text(s,'Функции × docstring / без docstring × два типа ошибки',.65,2.73,12.03,.65,23);
 table(s,[['Сценарий','Что проверяем после repair'],['Мутант + корректный тест','Маскировку ошибки кода'],['Корректный код + ошибочный тест','Исправление ошибочного теста']],.65,3.73,[6.05,5.98],.71,22);
 text(s,'Отдельно: 32 наблюдения для oracle review.',.65,6.08,12.03,.42,22,{bold:true});
 text(s,'Арифметика, границы и исключения. Эвристику не подбираем по результатам.',.65,6.62,12.03,.35,18,{color:C.muted});
 addNotes(s,'5. Методика оценки эвристики',40,'Чтобы оценить предупреждение о подозрительном repair, взяты восемь небольших функций с заранее заданным контрактом и мутантом. Для каждой есть вариант с docstring и без него. В одном сценарии исправный тест обнаруживает ошибку кода, в другом ошибочный тест падает на корректном коде. Получается 32 сценария. После ремонта тест проверяется на мутанте и эталоне. Отдельные 32 наблюдения оценивают oracle review. Набор включает неверные границы, арифметику и исключения. Правило предупреждения не подгоняется по результатам.',
 'Production-правило требует docstring и assertion failure. До repair свидетели проверяются: корректный тест проходит эталон, ошибочный тест падает на эталоне, корректный тест падает на мутанте. Repair-классификация требует прошедшего теста с assert/pytest.raises, который исполнил целевой return. Вырожденные тесты и другие неуспехи не включаются в основную матрицу и раскрываются отдельно. Непроверенные исправления на эталоне исключаются. Дополнительная метрика repair_all_passing_rewrites включает вырожденные переписывания симметрично в обоих типах сценария. На mutant успешный repair обозначает маскировку: изменение оракула или уход от свидетельствующего входа. Это малая специально подобранная выборка, не оценка всей экосистемы Python. Исключения: safe_divide при нуле, reciprocal с отсутствующим raise.',[blob('scripts/bench_heuristic.py'),blob('scripts/repair_cases.py'),blob('src/pytestgen/application/suspicion.py')]);
}
{
 const s=slide('Ошибки и ложные предупреждения');
 function matrixTable(x,label,m,truth1,truth0){
 text(s,label,x,1.58,5.8,.48,24,{bold:true,color:C.teal});
 table(s,[['Истина','Сигнал','Нет сигнала'],[truth1,m?`${m.tp} TP`:'н/д',m?`${m.fn} FN`:'н/д'],[truth0,m?`${m.fp} FP`:'н/д',m?`${m.tn} TN`:'н/д']],x,2.22,[2.8,1.5,1.5],.76,19);
 text(s,m?`Precision ${pct(m.precision)}   Recall ${pct(m.recall)}`:'Метрики появятся после прогона',x,4.75,5.8,.45,20,{bold:true});}
 matrixTable(.65,'Repair',repairMatrix,'Маскировка','Исправление теста');
 matrixTable(6.88,'Oracle: рассмотренные случаи',oracleMatrix,'Баг в коде','Корректный код');
 const h=heuristic?.summary;
 text(s,h?`Repair: ${repairMatrix.n}/${h.repair_scenarios} классифицированы, ${h.repair_status.not_repaired||0} не исправлены.\nВсе ${h.repair_attrition_by_condition['doc/bug'].not_repaired||0} случаев doc + bug без repair; recall группы не определён.\nOracle: ${oracleMatrix.n}/${h.oracle_scenarios} рассмотрены, ${h.oracle_abstentions} без docstring получили UNREVIEWED.`:'Результаты ещё не получены. Числа матриц не предсказываются.',.65,5.53,12.03,1.25,18.5,{color:C.muted});
 addNotes(s,'6. Результат оценки эвристики',45,
 h?`Основная матрица repair включает ${repairMatrix.n} из ${h.repair_scenarios} сценариев. Сигнал означает предупреждение о подозрительном исправлении. Precision равна ${pct(repairMatrix.precision)}, recall ${pct(repairMatrix.recall)}. Рядом показаны только рассмотренные случаи oracle review: precision ${pct(oracleMatrix.precision)}, recall ${pct(oracleMatrix.recall)}. Число сценариев вне основной матрицы repair: ${h.repair_scenarios-repairMatrix.n}. Поэтому эти доли описывают только классифицированные исходы, а не все попытки ремонта. На этой выборке ложных сигналов repair: ${repairMatrix.fp}, пропущенных маскировок: ${repairMatrix.fn}. Recall для docstring-группы: ${pct(h.repair_documented.recall)}. В oracle review ${h.oracle_abstentions} случаев остались UNREVIEWED. ${(evidence.heuristic.interpretation_notes||[]).join(' ')}`:'Результат будет сформулирован по завершённому отчёту.',
 `Источник: ${sourcePath('heuristic')}.\nTP: истина положительна и сигнал есть. FN: истина положительна, сигнала нет. FP: сигнал на отрицательном случае. TN: отрицательный случай без сигнала. Repair-положительный исход: actual_masking. Oracle-положительный исход: actual_bug. Precision=TP/(TP+FP), recall=TP/(TP+FN), н/д при нулевом знаменателе. UNREVIEWED oracle считается operational no alert; число воздержаний показано отдельно.\nНа слайде показан oracle_reviewed, только ok/suspicious. Дополнительно oracle_all трактует UNREVIEWED как отсутствие сигнала. Все расширенные counts и stratified documented/undocumented перепроверяются по исходным записям. Уточнения по данным: ${h?JSON.stringify({repair_status:h.repair_status,repair_documented:h.repair_documented,repair_undocumented:h.repair_undocumented,repair_all_passing_rewrites:h.repair_all_passing_rewrites,oracle_reviewed:h.oracle_reviewed,oracle_all:h.oracle_all,oracle_abstentions:h.oracle_abstentions},null,2):'не получены'}.\nАналитические примечания: ${(evidence.heuristic.interpretation_notes||[]).join(' ')}\nМодель: ${heuristic?.model||'не получена'}. case_sha256: ${heuristic?.case_sha256||'не получен'}. До ${heuristic?.max_attempts||2} попыток repair.`,[rawUrl('heuristic'),blob('scripts/bench_heuristic.py')]);
}
{
 const s=slide('Проверка на python-slugify 8.0.4');
 text(s,'slugify/slugify.py: slugify и smart_truncate',.65,1.47,12.03,.47,22,{color:C.teal});
 function metrics(run){
 if(!run)return['н/д','н/д','н/д'];const c=run.coverage,mark=run.verified?'':'*';
 return[`${run.counts.passed}/${run.collected}${mark}`,c?`${c.covered_lines}/${c.total_lines} (${Number(c.line_percent).toFixed(1).replace('.',',')}%)${mark}`:'н/д',c?`${c.covered_branches}/${c.total_branches} (${Number(c.branch_percent).toFixed(1).replace('.',',')}%)${mark}`:'н/д'];}
 const existing=library?.modes.find(r=>r.mode==='existing'),cold=library?.modes.find(r=>r.mode==='cold');
 const rows=[['Набор','Пройдено','Строки','Ветви'],['Upstream (baseline)',...metrics(library?.baseline)],['Existing: только новые',...metrics(existing?.generated)],['Existing + upstream',...metrics(existing?.combined)],['Cold: только новые',...metrics(cold?.generated)],['Cold + upstream',...metrics(cold?.combined)]];
 table(s,rows,.65,2.1,[4.8,2,2.62,2.61],.6,18);
 const g=existing?.gain_over_upstream;
 const sameGain=g&&cold?.gain_over_upstream&&Math.abs(g.line_percentage_points-cold.gain_over_upstream.line_percentage_points)<1e-8&&Math.abs(g.branch_percentage_points-cold.gain_over_upstream.branch_percentage_points)<1e-8;
 const gain=!library?'Результаты полного прогона ещё не получены.':g?`${sameGain?'Оба режима':'Existing'}: +${g.new_lines.length} строка (+${g.line_percentage_points.toFixed(2).replace('.',',')} п.п.), +${g.new_arcs.length} ветвь (+${g.branch_percentage_points.toFixed(2).replace('.',',')} п.п.).`:`Режим existing: ${existing?.status||'н/д'}. Прирост к upstream не подтверждён.`;
 text(s,gain,.65,6.02,12.03,.48,20,{bold:true,color:C.teal});
 text(s,'Исходники upstream-тестов не входят в промпт.',.65,6.63,12.03,.35,18,{color:C.muted});
 const note=library?`Полный upstream: ${library.baseline.counts.passed}/${library.baseline.collected} пройдено. Existing: ${existing.status}, cold: ${cold.status}. ${gain}`:'Результаты будут описаны по завершённому отчёту.';
 addNotes(s,'7. Реальная библиотека',45,'Для проверки на реальном коде выбрана python-slugify версии 8.0.4. Полный upstream-набор даёт исходную точку. В existing генератор видит измеренное покрытие существующих тестов, а cold начинает без них. Затем сгенерированные тесты независимо запускаются отдельно и вместе с upstream. '+note,
 `Источник: ${sourcePath('library')}.\nПокрытие ограничено slugify/slugify.py. Пройдено означает pytest outcomes, а не число assert. Existing комбинирует полный upstream и генерацию с baseline. Cold генерируется без existing tests, затем проверяется отдельно и вместе с upstream. Процентный пункт означает разность процентов. * означает непроверенный результат прогона, а не успешный тест. Н/д означает отсутствие валидного измерения, не ноль.\n${library?JSON.stringify({revision:library.revision,archive_sha256:library.archive_sha256,application_sha256:library.application_sha256,benchmark_sha256:library.benchmark_sha256,settings:library.settings,python:library.python,platform:library.platform,dependencies:library.dependencies,baseline_note:library.baseline_note,status:library.status,mode_details:library.modes.map(r=>({mode:r.mode,status:r.status,detail:r.detail,gain:r.gain_over_upstream,usage:r.llm_usage,wall_s:r.wall_s,kept_tests:r.generation?.kept_tests,dropped:r.generation?.dropped?.length}))},null,2):'данные не получены'}\nКэш LLM отключён. Фиксированный seed не гарантирует детерминизм модели. Образцы тестов upstream используются только для исполнения, не для промптов. Одна библиотека не даёт общей оценки применимости. ${(evidence.library.interpretation_notes||[]).join(' ')}`,[rawUrl('library'),library?.release,blob('scripts/bench_library.py')]);
}
{
 const s=slide('Выводы и живая демонстрация',{dark:true});
 text(s,'Гибридный генератор работает с малой локальной LLM.',.65,1.76,12.03,.78,28,{color:C.paper,bold:true});
 text(s,'Проверяемость',.65,2.99,3,.5,23,{color:C.mint,bold:true});
 text(s,'Тесты, покрытие и причины отбраковки сохраняются.',3.85,2.93,8.83,.75,22,{color:C.paper});
 text(s,'Ограничения',.65,4.04,3,.5,23,{color:C.mint,bold:true});
 text(s,'Оракул может закрепить баг. Repair может его скрыть.\nМалые выборки ограничивают обобщение.',3.85,3.98,8.83,.91,22,{color:C.paper});
 text(s,'Демонстрация CLI',.65,5.46,12.03,.42,23,{color:C.mint,bold:true});
 text(s,'uv run pytestgen generate examples/shop/pricing.py\n    --project-root examples -o out/test_pricing_generated.py',.65,6.02,12.03,.78,20,{color:C.paper});
 addNotes(s,'8. Выводы и переход к демонстрации',30,'Получился работающий инструмент: символьная часть даёт основу, модель расширяет набор, а отчёт позволяет проверить, что сохранено и что отброшено. Главная граница состоит в оракуле: наблюдаемое поведение может содержать баг, и ремонт теста способен его скрыть. Измерения показывают результат на конкретных наборах, а не гарантию корректности. Теперь покажу CLI, сгенерированные тесты, изменение покрытия и подозрительное место на живом примере.',
 'Команду со слайда выполнить одной строкой из корня репозитория: uv run pytestgen generate examples/shop/pricing.py --project-root examples -o out/test_pricing_generated.py\nРасширенная команда с отчётами: uv run pytestgen generate examples/shop/pricing.py --project-root examples -o out/test_pricing_generated.py --report-md out/report.md --report-json out/report.json\nПолный десятиминутный сценарий: docs/demo.md. Перед выступлением сервер модели запускается и кэш прогревается по этому сценарию. Во время демонстрации явно назвать cache hits. Самостоятельный обзор всех восьми слайдов занимает примерно пять минут. В десятиминутной живой защите используются только выбранные слайды по docs/demo.md, а не дополнительный пятиминутный доклад.',[blob('docs/demo.md'),blob('examples/shop/pricing.py')]);
}
if(pptx._slides.length!==8)throw new Error('Expected eight slides');
const onlyIndex=args.indexOf('--only-slide'); const only=onlyIndex<0?null:Number(args[onlyIndex+1]); if(only){if(!draft)throw new Error('QA selection is draft only');pptx._slides=pptx._slides.filter((s,i)=>i===only-1);pptx._slides[0]._rId=2;pptx._slides[0]._slideNum=1;pptx._slides[0]._slideId=256;} const name=only?'qa-slide-'+only:(draft?'pytestgen-defense-draft':'pytestgen-defense');
(async()=>{
 await pptx.writeFile({fileName:path.join(out,name+'.pptx')});
 fs.writeFileSync(path.join(out,'speaker-notes.md'),'# Заметки к защите pytestgen\n\n'+notes.map(n=>'## '+n.title+'\n\n'+n.text).join('\n\n')+'\n','utf8');
 fs.writeFileSync(path.join(__dirname,'qa',draft?'build-draft.json':'build-final.json'),JSON.stringify({schema_version:1,draft,evidence:path.relative(root,evidencePath),created_at:new Date().toISOString(),slide_count:8,author:pptx.author,planned_duration_s:notes.reduce((a,n)=>a+n.seconds,0),sources,source_commit:evidence.source_commit,ci:ci?.run_url,checks:{mbpp_summary_recomputed:!!mbpp,heuristic_confusion_recomputed:!!heuristic,library_counts_coverage_gain_recomputed:!!library,all_required_evidence:!draft}},null,2)+'\n','utf8');
 console.log(path.join(out,name+'.pptx'));
})().catch(e=>{console.error(e);process.exitCode=1});
})();
