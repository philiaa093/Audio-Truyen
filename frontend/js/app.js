const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const state = {job: null, variant: null, timer: null, epoch: 0, speaking: false, speechId: 0, voice: null, tts: false, rendered: null, profile: null};
const number = value => new Intl.NumberFormat('vi-VN').format(value);
const element = (tag, text, className) => {const node = document.createElement(tag); node.textContent = text; if(className) node.className = className; return node;};
let toastTimer;
let maxSource = 1000000;
const qaFields = {
  story_fidelity: 'Đã đối chiếu plot bắt buộc, nhân vật, nhân quả, setup/payoff và kết cục với nguồn.',
  pronunciation: 'Đã nghe tên riêng, số, ngày giờ, từ viết tắt và đối chiếu từ điển phát âm.',
  pacing: 'Thời lượng và khoảng nghỉ phù hợp, không có đoạn đọc quá gấp hoặc im lặng bất thường.',
  audio_quality: 'Đã nghe toàn bộ bản cuối, không mất tiếng, méo, clipping hoặc tạp âm đáng kể.',
  style_emotion: 'POV, hình ảnh, lời thoại, độ tự nhiên và cảm xúc phù hợp với truyện.',
  content_labels: 'Đã rà soát cảnh nhạy cảm và ghi nhãn nội dung cần thiết trong ghi chú.'
};
for(const [key,label] of Object.entries(qaFields)){
  const row=element('label','','check');const input=element('input','');input.type='checkbox';input.name=key;row.append(input,element('span',label));$('qa-checklist').append(row);
}
function toast(message, error=false){clearTimeout(toastTimer); $('toast').textContent=message; $('toast').className='toast'+(error?' error':''); $('toast').hidden=false; if(!error) toastTimer=setTimeout(()=>$('toast').hidden=true,6000);}
async function api(path, options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...options.headers}});const data=await response.json();if(!response.ok)throw new Error(data.error||'Không kết nối được máy chủ.');return data;}
function count(){const text=$('text').value.trim();const count=text?text.split(/\s+/u).length:0;$('source-count').textContent=`${number(count)} đơn vị cách trắng · ${number($('text').value.length)}/${number(maxSource)} ký tự`;}
function step(index){[1,2,3].forEach(i=>$('step-'+i).classList.toggle('current',i===index));}
function stopSpeech(){state.speechId++;if('speechSynthesis'in window) speechSynthesis.cancel();state.speaking=false;$('speak').textContent='Nghe thử tại máy';}
function findVoice(){if(!('speechSynthesis'in window)){state.voice=null;}else{state.voice=speechSynthesis.getVoices().find(voice=>voice.lang.toLowerCase().startsWith('vi'));}$('speak').disabled=!state.voice||!state.variant||state.variant.audio_blocked;$('voice-note').textContent=state.voice?`Giọng tại máy: ${state.voice.name}. Nghe thử, không xuất file audio.`:'Trình duyệt chưa có giọng tiếng Việt. Vẫn có thể tải kịch bản và hồ sơ.';}
if('speechSynthesis'in window) speechSynthesis.addEventListener('voiceschanged',findVoice);
function audioAvailability(){$('create-audio').disabled=!state.tts||!state.variant||state.variant.audio_blocked||!$('audio-approved').checked||state.job?.status==='running';}
function readLexicon(){
  const lexicon={};
  for(const line of $('pronunciation-lexicon').value.split(/\r?\n/u)){
    if(!line.trim())continue;
    const separator=line.indexOf('=');const key=line.slice(0,separator).trim();const value=line.slice(separator+1).trim();
    if(separator<1||!key||!value||Object.hasOwn(lexicon,key))throw new Error('Từ điển cần mỗi dòng tên = cách đọc, không trùng tên.');
    Object.defineProperty(lexicon,key,{value,enumerable:true,configurable:true});
  }
  return lexicon;
}
function renderTranscript(){
  const result=state.job.result;const variant=state.variant;const scene=$('scene-filter').value;
  $('transcript').replaceChildren();
  const byId=new Map(result.sentences.map(s=>[s.id,s]));
  for(const segment of variant.segments){
    if(scene&&segment.scene_id!==scene)continue;
    const block=element('article','','segment');const label=element('div','','segment-label');
    label.append(element('span',[segment.chapter_id,segment.scene_id,segment.speaker==='NARRATOR'?'Người dẫn':segment.speaker].filter(Boolean).join(' · ')));
    const evidence=element('button','Xem nguồn','evidence');evidence.type='button';
    evidence.addEventListener('click',()=>{const refs=segment.source_ids.map(id=>byId.get(id));$('evidence-location').textContent=refs.map(ref=>`${ref.id} · vị trí ${ref.source_span[0]}–${ref.source_span[1]} trong clean.txt`).join(' | ');$('evidence-text').textContent=refs.map(ref=>ref.text).join('\n\n');$('evidence-dialog').showModal();});
    label.append(evidence);block.append(label,element('p',segment.text));
    if(segment.tts_text&&segment.tts_text!==segment.text){const detail=element('details','','spoken-text');detail.append(element('summary','Lời đã gửi TTS'),element('p',segment.tts_text));block.append(detail);}
    $('transcript').append(block);
  }
}
function qaCanApprove(qa){return qa&&['ready_for_review','approved','rejected'].includes(qa.status)&&Object.values(qa.checks).every(check=>check.status==='passed');}
function showQA(){
  const qa=state.variant?.qa;$('qa-panel').hidden=!qa;if(!qa)return;
  const labels={blocked:'Có lỗi cần sửa',incomplete:'QA chưa đầy đủ',ready_for_review:'Kiểm tra tự động đạt, chờ nghe duyệt',approved:'Đã nghe duyệt bản cuối',rejected:'Người duyệt yêu cầu sửa'};
  const statuses={passed:'Đạt',failed:'Không đạt',incomplete:'Chưa đủ dữ liệu'};
  const checks={fidelity:'Đối chiếu truyện',duration:'Thời lượng',pronunciation:'Chuẩn hóa phát âm',acoustic:'Tín hiệu audio',loudness:'Âm lượng / true peak',asr:'ASR đối chiếu',rights:'Quyền sử dụng'};
  $('qa-status').textContent=labels[qa.status]||qa.status;$('qa-checks').replaceChildren();
  for(const [key,check] of Object.entries(qa.checks))$('qa-checks').append(element('li',`${checks[key]||key}: ${statuses[check.status]||check.status}${check.reason?' — '+check.reason:''}`));
  $('qa-json').textContent=JSON.stringify(qa,null,2);
  const human=qa.human_review||{};$('qa-reviewer').value=human.reviewer||'';$('qa-notes').value=human.notes||'';
  for(const input of $('qa-checklist').querySelectorAll('input'))input.checked=human.checklist?.[input.name]===true;
  for(const field of $('qa-review-form').querySelectorAll('input,textarea,button'))field.disabled=state.job.status==='running';
  $('qa-approve').disabled=state.job.status==='running'||!qaCanApprove(qa);
}
function showVariant(index){
  stopSpeech();const result=state.job.result;const variant=result.variants[index];state.variant=variant;state.profile=variant.profile;$('audio-approved').checked=false;
  [...$('tabs').children].forEach((tab,i)=>{tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;});
  $('variant-panel').setAttribute('aria-labelledby','profile-tab-'+index);
  $('result-units').textContent=number(variant.units);$('result-cut').textContent=Math.round((1-variant.retained_ratio)*100)+'%';
  $('result-duration').textContent=(variant.actual_duration_seconds===null?'~ ':'')+((variant.actual_duration_seconds??variant.estimated_duration_seconds)/60).toLocaleString('vi-VN',{maximumFractionDigits:1})+' phút';$('duration-label').textContent=variant.actual_duration_seconds===null?'dự báo · chưa đo audio':'đã đo từ WAV thực';
  const structure=result.structure;const selected=new Set(variant.segments.map(s=>s.scene_id));
  $('structure-note').textContent=structure?`${number(structure.chapters.length)} chương · ${number(structure.scenes.length)} cảnh/phần xử lý. Ranh giới tự nhận diện cần người đọc kiểm tra.`:'';
  $('scene-filter').replaceChildren(new Option('Tất cả cảnh',''));
  for(const scene of structure?.scenes||[])if(selected.has(scene.id)){$('scene-filter').add(new Option(`${structure.chapters.find(c=>c.id===scene.chapter_id)?.title||scene.chapter_id} · ${scene.title||scene.id}`,scene.id));}
  if(variant.segments.length>200&&$('scene-filter').options.length>1)$('scene-filter').selectedIndex=1;
  renderTranscript();showQA();
  const fidelity=variant.fidelity;const errors=['critical_errors','major_errors','unsupported_claims','broken_setup_payoff','minor_errors'].flatMap(key=>fidelity[key]||[]);
  $('review-text').textContent=result.mode==='preview'?'Bản thử trích nguyên câu theo quy tắc. Chưa kiểm định mạch truyện, nhân quả và người nói.':variant.audio_blocked?'AI kiểm định phát hiện nội dung cần sửa. Đang chặn nghe thử bản này.':'AI đã đối chiếu nguồn. Kết quả vẫn cần người đọc và nghe duyệt.';
  $('review-list').replaceChildren();for(const message of [...variant.warnings,...errors,...(variant.duration_report?[variant.duration_report.next_action]:[])])$('review-list').append(element('li',message));
  $('review').hidden=false;$('review-list').hidden=!$('review-list').children.length;findVoice();audioAvailability();
  $('audio-player').pause();$('audio-player').hidden=variant.actual_duration_seconds===null;$('audio-download').hidden=variant.actual_duration_seconds===null;
  if(variant.actual_duration_seconds!==null){const url=`/api/jobs/${state.job.id}/${variant.profile}/delivery.mp3`;$('audio-player').src=url;$('audio-download').href=url;$('audio-download').download='delivery.mp3';}else{$('audio-player').removeAttribute('src');}
}
function render(job){
  state.job=job;$('empty').hidden=true;$('progress').hidden=job.status!=='running';$('result').hidden=!job.result?.variants.length;$('submit').disabled=job.status==='running';
  $('output-state').textContent=job.status==='running'?'Đang xử lý':job.status==='failed'?'Cần kiểm tra':'Chờ duyệt';$('progress-text').textContent=job.message;
  const signature=job.id+JSON.stringify(job.result);if(job.result?.variants.length&&signature!==state.rendered){state.rendered=signature;$('tabs').replaceChildren();job.result.variants.forEach((variant,i)=>{const tab=element('button',variant.label);tab.id='profile-tab-'+i;tab.role='tab';tab.setAttribute('aria-controls','variant-panel');tab.addEventListener('click',()=>showVariant(i));tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=[...$('tabs').children];let next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(i+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();showVariant(next);});$('tabs').append(tab);});$('download').href=`/api/jobs/${job.id}/download`;const previous=job.result.variants.findIndex(v=>v.profile===state.profile);showVariant(previous<0?0:previous);}audioAvailability();
  if(state.variant)for(const field of $('qa-review-form').querySelectorAll('input,textarea,button'))field.disabled=job.status==='running'||(field.id==='qa-approve'&&!qaCanApprove(state.variant.qa));
  step(job.status==='running'?2:3);if(job.status==='failed'){toast(job.message,true);if(!job.result){$('empty').hidden=false;$('empty').querySelector('h3').textContent='Chưa tạo được bản kể.';$('empty').querySelector('p').textContent=job.message;}}
}
async function openJob(id){
  clearTimeout(state.timer);const epoch=++state.epoch;stopSpeech();$('review').hidden=true;state.variant=null;state.rendered=null;
  async function poll(){try{const job=await api('/api/jobs/'+id);if(epoch!==state.epoch)return;render(job);if(job.status==='running')state.timer=setTimeout(poll,1600);else refreshHistory();}catch(error){if(epoch===state.epoch){$('submit').disabled=false;toast(error.message,true);}}}
  sessionStorage.setItem('audio-truyen-job',id);await poll();
}
async function refreshHistory(){try{const jobs=await api('/api/jobs');for(const container of [$('history'),$('history-full')]){container.replaceChildren();if(!jobs.length)container.append(element('small','Chưa có dự án'));for(const job of (container.id==='history'?jobs.slice(0,6):jobs)){const button=element('button',job.title);button.append(element('small',new Date(job.created_at).toLocaleString('vi-VN')+' · '+({running:'Đang xử lý',completed:'Đã tạo',failed:'Gián đoạn'}[job.status]||job.status)));button.addEventListener('click',()=>{$('history-dialog').close();openJob(job.id);});container.append(button);}}}catch(error){toast(error.message,true);}}
$('editor').addEventListener('submit',async event=>{event.preventDefault();stopSpeech();$('submit').disabled=true;$('toast').hidden=true;try{const target=$('target').value.trim();const data={title:$('title').value,author:$('author').value,text:$('text').value,mode:$('mode').value,target_duration:target===''?null:Number(target),profile:$('profile').value,rights_confirmed:$('rights').checked,rights_note:$('rights-note').value,pronunciation_lexicon:readLexicon()};const job=await api('/api/jobs',{method:'POST',body:JSON.stringify(data)});await openJob(job.id);refreshHistory();}catch(error){$('submit').disabled=false;toast(error.message,true);}});
$('sample').addEventListener('click',async()=>{try{if($('text').value.trim()&&!confirm('Thay nội dung đang nhập bằng truyện mẫu?'))return;const sample=await api('/api/sample');$('title').value=sample.title;$('author').value=sample.author;$('text').value=sample.text;$('rights-note').value=sample.rights_note;$('rights').checked=false;count();$('text').focus();toast('Đã nạp truyện mẫu từ báo cáo. Xác nhận quyền sử dụng để tạo bản kể.');}catch(error){toast(error.message,true);}});
$('file').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>maxSource*4)throw new Error('Tệp vượt giới hạn dung lượng.');const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());if(text.length>maxSource)throw new Error(`Tệp vượt ${number(maxSource)} ký tự. Hãy tách thành nhiều dự án.`);if($('text').value.trim()&&!confirm('Thay nội dung đang nhập bằng tệp đã chọn?'))return;$('text').value=text;if(!$('title').value)$('title').value=file.name.replace(/\.[^.]+$/,'');$('rights').checked=false;count();}catch(error){toast(error instanceof TypeError?'Không đọc được UTF-8. Lưu lại tệp với mã hóa UTF-8.':error.message,true);}finally{event.target.value='';}});
$('text').addEventListener('input',count);$('target').addEventListener('input',()=>{$('profile-field').hidden=!$('target').value;});
$('audio-approved').addEventListener('change',audioAvailability);
$('scene-filter').addEventListener('change',renderTranscript);
$('qa-review-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!state.variant?.qa)return;
  const checklist=Object.fromEntries([...$('qa-checklist').querySelectorAll('input')].map(input=>[input.name,input.checked]));
  try{
    await api(`/api/jobs/${state.job.id}/review`,{method:'POST',body:JSON.stringify({profile:state.variant.profile,audio_sha256:state.variant.qa.audio_sha256,reviewer:$('qa-reviewer').value,notes:$('qa-notes').value,decision:event.submitter.value,checklist})});
    await openJob(state.job.id);toast('Đã lưu kết quả nghe duyệt cho bản audio này.');
  }catch(error){toast(error.message,true);}
});
$('create-audio').addEventListener('click',async()=>{if(!state.variant||!state.job)return;$('create-audio').disabled=true;try{await api(`/api/jobs/${state.job.id}/audio`,{method:'POST',body:JSON.stringify({profile:state.variant.profile,approved:$('audio-approved').checked})});await openJob(state.job.id);}catch(error){toast(error.message,true);audioAvailability();}});
$('new-project').addEventListener('click',()=>{$('text').focus();$('editor').scrollIntoView({behavior:'smooth',block:'start'});});
$('history-button').addEventListener('click',()=>{refreshHistory();$('history-dialog').showModal();});
$('close-history').addEventListener('click',()=>$('history-dialog').close());$('close-evidence').addEventListener('click',()=>$('evidence-dialog').close());
$('save-text').addEventListener('click',()=>{if(!state.variant)return;const url=URL.createObjectURL(new Blob([state.variant.summary],{type:'text/plain;charset=utf-8'}));const link=element('a','');link.href=url;link.download=`ban-ke-${state.variant.profile}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('speak').addEventListener('click',()=>{if(state.speaking){stopSpeech();return;}if(!state.variant||!state.voice||state.variant.audio_blocked)return;stopSpeech();const current=state.speechId;state.speaking=true;$('speak').textContent='Dừng nghe';const chunks=state.variant.segments.flatMap(segment=>(segment.tts_text||segment.text).match(/.{1,600}(?:\s|$)|\S{1,600}/gu)||[]);let index=0;function next(){if(current!==state.speechId)return;if(index>=chunks.length){stopSpeech();return;}const speech=new SpeechSynthesisUtterance(chunks[index++]);speech.voice=state.voice;speech.lang='vi-VN';speech.rate=1;speech.onend=next;speech.onerror=event=>{if(current!==state.speechId)return;stopSpeech();if(event.error!=='canceled')toast('Trình duyệt không phát được giọng đọc: '+event.error,true);};speechSynthesis.speak(speech);}next();});
window.addEventListener('beforeunload',stopSpeech);
async function init(){try{const health=await api('/api/health');maxSource=health.max_source;$('text').maxLength=maxSource;state.tts=health.tts_configured&&health.ffmpeg_available;$('tts-note').textContent=!health.tts_configured?'Đặt TTS_URL, TTS_MODEL và TTS_VOICE rồi khởi động lại. Xem README.md.':!health.ffmpeg_available?'Đã cấu hình TTS; cần cài FFmpeg vào PATH.':`Đã cấu hình TTS. ${health.asr_configured?'Có ASR đối chiếu.':'Chưa có ASR; QA sẽ ghi chưa đầy đủ.'} WAV/MP3 và báo cáo được lưu trong hồ sơ.`;$('connection').textContent=health.ai_configured?'Đã cấu hình AI':'Chế độ tại máy';$('ai-option').disabled=!health.ai_configured;if(health.ai_configured)$('ai-option').textContent='Biên tập bằng AI · gửi tới dịch vụ';await refreshHistory();const saved=sessionStorage.getItem('audio-truyen-job');if(saved&&/^[a-f0-9]{32}$/.test(saved))await openJob(saved);}catch(error){$('connection').textContent='Mất kết nối';toast(error.message,true);}count();findVoice();}
init();
