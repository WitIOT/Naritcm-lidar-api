const elStatus = document.getElementById('status');
const raw = document.getElementById('raw');
const t_in = document.getElementById('t_indoor');
const h_in = document.getElementById('h_indoor');
const t_out = document.getElementById('t_outdoor');
const h_out = document.getElementById('h_outdoor');
const dp_in = document.getElementById('dp_indoor');
const dp_out = document.getElementById('dp_outdoor');

const chTemp = new Chart(document.getElementById('temp').getContext('2d'),{
  type:'line',
  data:{datasets:[
    {label:'Temp Indoor (°C)', data:[], borderColor:'blue', borderWidth:2, tension:0.2},
    {label:'Temp Outdoor (°C)', data:[], borderColor:'red', borderWidth:2, tension:0.2}
  ]},
  options:{
    parsing:false, animation:false, interaction:{mode:'nearest',intersect:false},
    scales:{ x:{type:'time', time:{unit:'second'}, title:{display:true,text:'Time'}},
             y:{title:{display:true,text:'°C'}} }
  }
});

const chHumi = new Chart(document.getElementById('humi').getContext('2d'),{
  type:'line',
  data:{datasets:[
    {label:'Humi Indoor (%RH)', data:[], borderColor:'blue', borderWidth:2, tension:0.2},
    {label:'Humi Outdoor (%RH)', data:[], borderColor:'red', borderWidth:2, tension:0.2}
  ]},
  options:{
    parsing:false, animation:false, interaction:{mode:'nearest',intersect:false},
    scales:{ x:{type:'time', time:{unit:'second'}, title:{display:true,text:'Time'}},
             y:{title:{display:true,text:'%RH'}} }
  }
});


const chDew = new Chart(document.getElementById('dew').getContext('2d'),{
  type:'line',
  data:{datasets:[
    {label:'Dew Point Indoor (°C)', data:[], borderColor:'blue', borderWidth:2, tension:0.2},
    {label:'Dew Point Outdoor (°C)', data:[], borderColor:'red', borderWidth:2, tension:0.2}
  ]},
  options:{
    parsing:false, animation:false, interaction:{mode:'nearest',intersect:false},
    scales:{ x:{type:'time', time:{unit:'second'}, title:{display:true,text:'Time'}},
             y:{title:{display:true,text:'°C'}} }
  }
});


const ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/ws');
ws.onopen  = ()=> elStatus.textContent = 'connected';
ws.onclose = ()=> elStatus.textContent = 'disconnected';
ws.onmessage = (e)=>{
  const j = JSON.parse(e.data);
  const ts = j.ts;

  // raw text
  const r1 = j.indoor && Array.isArray(j.indoor.raw) ? j.indoor.raw.join(',') : '-';
  const r2 = j.outdoor && Array.isArray(j.outdoor.raw) ? j.outdoor.raw.join(',') : '-';
  raw.textContent = `raw: indoor=${r1} | outdoor=${r2}`;

  // KPI update
  if (j.indoor && typeof j.indoor.temp === 'number') t_in.textContent = j.indoor.temp.toFixed(1);
  if (j.indoor && typeof j.indoor.humi === 'number') h_in.textContent = j.indoor.humi.toFixed(1);
  if (j.outdoor && typeof j.outdoor.temp === 'number') t_out.textContent = j.outdoor.temp.toFixed(1);
  if (j.outdoor && typeof j.outdoor.humi === 'number') h_out.textContent = j.outdoor.humi.toFixed(1);
  if (j.indoor && typeof j.indoor.dewpoint === 'number') dp_in.textContent = j.indoor.dewpoint.toFixed(1);
  if (j.outdoor && typeof j.outdoor.dewpoint === 'number') dp_out.textContent = j.outdoor.dewpoint.toFixed(1);

  // Chart update
  if (j.indoor && typeof j.indoor.temp === 'number')
    chTemp.data.datasets[0].data.push({x:ts,y:j.indoor.temp});
  if (j.outdoor && typeof j.outdoor.temp === 'number')
    chTemp.data.datasets[1].data.push({x:ts,y:j.outdoor.temp});

  if (j.indoor && typeof j.indoor.humi === 'number')
    chHumi.data.datasets[0].data.push({x:ts,y:j.indoor.humi});
  if (j.outdoor && typeof j.outdoor.humi === 'number')
    chHumi.data.datasets[1].data.push({x:ts,y:j.outdoor.humi});

  if (j.indoor && typeof j.indoor.dewpoint === 'number')
    chDew.data.datasets[0].data.push({x:ts,y:j.indoor.dewpoint});
  if (j.outdoor && typeof j.outdoor.dewpoint === 'number')
    chDew.data.datasets[1].data.push({x:ts,y:j.outdoor.dewpoint});

  const MAX=600;
  for (const ds of chTemp.data.datasets){ while (ds.data.length>MAX) ds.data.shift(); }
  for (const ds of chHumi.data.datasets){ while (ds.data.length>MAX) ds.data.shift(); }
  for (const ds of chDew.data.datasets){ while (ds.data.length>MAX) ds.data.shift(); }

  chTemp.update('none'); chHumi.update('none'); chDew.update('none');
};
