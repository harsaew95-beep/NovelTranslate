const $=id=>document.getElementById(id);let working=false,ready=false,imageUrl=null;
async function api(path,body){const r=await fetch('/api/browser/'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'}:{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||'Ошибка сервера');return d}
async function update(){const s=await api('status');ready=s.state==='ready';$('status').textContent=s.message||'Выберите чат и нажмите «Открыть чат».';if(ready){const r=await fetch('/api/browser/screen',{cache:'no-store'});if(!r.ok){const e=await r.json();throw Error(e.error)}const next=URL.createObjectURL(await r.blob());$('screen').src=next;$('screen').hidden=false;if(imageUrl)URL.revokeObjectURL(imageUrl);imageUrl=next}else{$('screen').hidden=true;$('screen').removeAttribute('src');if(imageUrl){URL.revokeObjectURL(imageUrl);imageUrl=null}}}
async function task(fn){if(working)return;working=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await fn();await update()}catch(e){$('status').textContent=e.message}finally{working=false;document.querySelectorAll('button').forEach(b=>b.disabled=false)}}
$('start').onclick=()=>task(()=>api('start',{provider:$('service').value}));
$('finish').onclick=()=>task(()=>api('close',{}));
$('refresh').onclick=()=>task(async()=>{});
$('screen').onclick=event=>task(async()=>{if(!ready)return;const rect=$('screen').getBoundingClientRect();const x=(event.clientX-rect.left)*$('screen').naturalWidth/rect.width;const y=(event.clientY-rect.top)*$('screen').naturalHeight/rect.height;await api('action',{action:'click',x,y})});
$('typing').onsubmit=event=>{event.preventDefault();task(async()=>{if(!ready)throw Error('Сначала откройте чат.');await api('action',{action:'text',text:$('typed').value});$('typed').value=''})};
document.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>task(()=>api('action',{action:'key',key:b.dataset.key})));
document.querySelectorAll('[data-scroll]').forEach(b=>b.onclick=()=>task(()=>api('action',{action:'scroll',delta:Number(b.dataset.scroll)})));
setInterval(()=>{if(!working&&!document.hidden)task(async()=>{})},5000);
task(async()=>{});
