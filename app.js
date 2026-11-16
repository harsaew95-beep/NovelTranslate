const $=id=>document.getElementById(id);let chapters=[],current=null,showOriginal=false,busy=false,jobs=[];let font=Number(localStorage.getItem('font'))||20;
if(localStorage.getItem('dark')==='1')document.body.classList.add('dark');
async function api(path,body){const r=await fetch('/api/'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const d=await r.json();if(!r.ok)throw Error(d.error||'Ошибка сервера');return d}
function status(s){$('status').textContent=s}
async function task(fn){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await fn()}catch(e){status(e.message)}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);if(current)render()}}
async function refresh(){chapters=await api('chapters');$('library').replaceChildren();if(!chapters.length){const p=document.createElement('p');p.className='muted';p.textContent='Пока пусто. Здесь появятся ваши главы.';$('library').append(p)}for(const c of chapters){const b=document.createElement('button');b.className='book';b.textContent=c.title;const s=document.createElement('small');s.textContent=c.translation?'● Переведено на русский':'○ Оригинал сохранён';b.append(s);b.onclick=()=>open(c);$('library').append(b)}}
function render(){ $('text').textContent=showOriginal||!current.translation?current.original:current.translation;$('text').style.fontSize=font+'px';$('toggle').textContent=showOriginal?'Показать перевод':'Показать оригинал';$('toggle').disabled=!current.translation;renderJob()}
function open(c){current=c;showOriginal=false;$('editor').hidden=true;$('reader').hidden=false;$('chapter-title').textContent=c.title;render();status('');window.scrollTo(0,0)}
function editor(){current=null;$('editor').hidden=false;$('reader').hidden=true;status('')}
$('new').onclick=$('back').onclick=editor;
$('original').oninput=()=>$('count').textContent=$('original').value.length.toLocaleString('ru')+' символов';
$('import').onclick=()=>task(async()=>{status('Загружаем главу…');const c=await api('import',{url:$('url').value});$('title').value=c.title;$('original').value=c.original;$('original').oninput();status('Глава загружена. Проверьте текст и сохраните её.');});
$('save').onclick=()=>task(async()=>{const c=await api('save',{title:$('title').value,original:$('original').value,url:$('url').value});await refresh();open(chapters.find(x=>x.id===c.id));$('title').value=$('original').value=$('url').value='';$('original').oninput()});
$('translate').onclick=()=>task(async()=>{if(current.translation&&!confirm('Перевести заново через чат? Это использует лимиты выбранного сервиса.'))return;await api('translate',{id:current.id,provider:$('provider').value});await syncJobs();status('Перевод запущен. На Render Free лучше держать страницу открытой до завершения. После перевода скачайте TXT на телефон.');});
$('toggle').onclick=()=>{showOriginal=!showOriginal;render()};
$('delete').onclick=()=>task(async()=>{if(!confirm('Удалить эту главу и её перевод?'))return;await api('delete',{id:current.id});await refresh();editor()});
$('download').onclick=()=>{const blob=new Blob([current.title+'\n\n'+(showOriginal||!current.translation?current.original:current.translation)],{type:'text/plain;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='chapter.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
$('theme').onclick=()=>{document.body.classList.toggle('dark');localStorage.setItem('dark',document.body.classList.contains('dark')?'1':'0')};
function resize(n){font=Math.min(32,Math.max(14,font+n));localStorage.setItem('font',font);render()};$('smaller').onclick=()=>resize(-2);$('larger').onclick=()=>resize(2);
function renderJob(){
 if(!current)return;
 const job=jobs.find(j=>j.chapter_id===current.id);
 const active=job&&['queued','running'].includes(job.state);
 $('translate').disabled=busy||!!active;
 $('delete').disabled=busy||!!active;
 $('job-status').textContent=!job?'':job.state==='queued'?'В очереди на перевод':job.state==='running'?`Перевод через ${job.provider}: готово частей ${job.done} из ${job.total||'…'}`:job.state==='error'?job.error:'Перевод сохранён.';
}
async function syncJobs(){
 jobs=await api('jobs');
 if(current){
  const job=jobs.find(j=>j.chapter_id===current.id);
  if(job&&job.state==='completed'){
   const latest=await api('chapters');const c=latest.find(c=>c.id===current.id);
   if(c&&c.translation!==current.translation){current=c;render();await refresh()}
  }
  renderJob();
 }
}
(async()=>{try{await refresh();await syncJobs();$('connection-note').textContent='Перевод через обычный чат, без API. Сначала выполните вход через ссылку в меню. На Render Free данные и вход могут исчезнуть после остановки сервера. Скачивайте готовый перевод в TXT.';}catch(e){status(e.message)}})();
setInterval(()=>{if(!busy&&!document.hidden)syncJobs().catch(()=>{if(current)$('job-status').textContent='Нет связи с сервером. После восстановления проверим перевод.'})},4000);
if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
