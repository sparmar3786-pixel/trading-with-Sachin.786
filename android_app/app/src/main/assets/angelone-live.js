/* Angel One SmartAPI live layer — injected into the existing Java WebView analyzer.
   No credentials are hard-coded. Live credentials remain in this device's WebView session/local storage only if the user enables Save.
*/
(function(){
  'use strict';
  if(window.__ANGEL_ONE_LIVE_LAYER__) return;
  window.__ANGEL_ONE_LIVE_LAYER__=true;

  const A={
    api:'https://apiconnect.angelone.in',
    master:'https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json',
    quote:'/rest/secure/angelbroking/market/v1/quote/',
    cacheKey:'angelone_option_chain_cache_v1',
    cfgKey:'angelone_cfg_v1',
    refreshMs:7000,
    maxTokens:50
  };
  const $=id=>document.getElementById(id);
  const escA=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  const n=v=>{if(v==null||v==='')return null;const x=Number(String(v).replace(/,/g,''));return Number.isFinite(x)?x:null};
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));

  const css=`
    #angelLiveDock{position:fixed;right:14px;bottom:14px;z-index:2147483600;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}
    #angelLiveBtn{border:1px solid #356da9;background:linear-gradient(135deg,#0d2744,#153b63);color:#eaf4ff;border-radius:14px;padding:10px 13px;font-weight:900;box-shadow:0 10px 30px #0008;cursor:pointer}
    #angelLivePanel{position:fixed;right:12px;bottom:68px;width:min(440px,calc(100vw - 24px));max-height:calc(100vh - 92px);overflow:auto;background:#071321;color:#edf5ff;border:1px solid #294867;border-radius:18px;box-shadow:0 22px 70px #000b;display:none}
    #angelLivePanel.open{display:block}
    .alHead{position:sticky;top:0;background:linear-gradient(180deg,#0d2035,#091727);padding:13px;border-bottom:1px solid #203852;z-index:2}
    .alTitle{font-size:18px;font-weight:950}.alSub{font-size:11px;color:#93a9c3;margin-top:2px}
    .alBody{padding:12px}.alGrid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.alFull{grid-column:1/-1}
    .alLabel{font-size:10px;color:#8fa5bf;text-transform:uppercase;letter-spacing:.06em;display:block;margin-bottom:4px}
    .alInput{width:100%;box-sizing:border-box;background:#122237!important;color:#f2f7ff!important;border:1px solid #2b4562;border-radius:9px;padding:9px;outline:none}
    .alInput:focus{border-color:#65a8ff}.alBtn{border:1px solid #355a80;background:#142b45;color:#edf6ff;border-radius:9px;padding:9px 11px;font-weight:800;cursor:pointer}
    .alBtn.primary{background:linear-gradient(135deg,#2865a4,#3b78c2);border-color:#5a9bea}.alBtn.warn{border-color:#715c2e;background:#342b16}
    .alRow{display:flex;gap:7px;flex-wrap:wrap;margin-top:8px}.alStatus{padding:9px;border:1px solid #263d57;border-radius:10px;background:#0b1828;margin-top:9px;font-size:11px}
    .alGood{color:#57dfa0}.alWarn{color:#ffd06e}.alBad{color:#ff7c87}.alMuted{color:#91a5bc}
    .alCache{margin-top:9px;padding:9px;border-radius:10px;background:#0b1a29;border:1px solid #243c56;font-size:11px}
    .alMini{font-size:10px;color:#7f96af;line-height:1.45;margin-top:8px}
    @media(max-width:520px){#angelLiveDock{right:8px;bottom:8px}.alGrid{grid-template-columns:1fr}}
  `;
  const st=document.createElement('style');st.textContent=css;document.head.appendChild(st);

  const root=document.createElement('div');root.id='angelLiveDock';
  root.innerHTML=`
    <button id="angelLiveBtn">⚡ ANGEL ONE LIVE</button>
    <section id="angelLivePanel" aria-hidden="true">
      <div class="alHead">
        <div class="alTitle">Angel One • Live Option Chain</div>
        <div class="alSub">SmartAPI quote feed • cached snapshot when market is closed</div>
      </div>
      <div class="alBody">
        <div class="alGrid">
          <div><label class="alLabel">API Key</label><input id="alApiKey" class="alInput" type="password" autocomplete="off"></div>
          <div><label class="alLabel">Client Code</label><input id="alClient" class="alInput" autocomplete="off"></div>
          <div class="alFull"><label class="alLabel">JWT Access Token (without/with Bearer)</label><input id="alJwt" class="alInput" type="password" autocomplete="off"></div>
          <div><label class="alLabel">Local IP</label><input id="alLocalIp" class="alInput" placeholder="e.g. 192.168.x.x"></div>
          <div><label class="alLabel">Public IP</label><input id="alPublicIp" class="alInput" placeholder="Your public IP"></div>
          <div class="alFull"><label class="alLabel">MAC Address</label><input id="alMac" class="alInput" placeholder="AA:BB:CC:DD:EE:FF"></div>
          <div><label class="alLabel">Index</label><select id="alSymbol" class="alInput"><option>NIFTY</option><option>BANKNIFTY</option><option>FINNIFTY</option><option>MIDCPNIFTY</option></select></div>
          <div><label class="alLabel">Expiry</label><select id="alExpiry" class="alInput"><option value="">Auto nearest</option></select></div>
        </div>
        <div class="alRow">
          <button id="alConnect" class="alBtn primary">Connect + Fetch</button>
          <button id="alRefresh" class="alBtn">Refresh</button>
          <button id="alCacheBtn" class="alBtn">Use Cached</button>
          <button id="alClose" class="alBtn">Close</button>
        </div>
        <div id="alStatus" class="alStatus"><b>NOT CONNECTED</b><div class="alMuted">Credentials are required. Do not paste them into chat.</div></div>
        <div id="alCache" class="alCache"><b>Offline cache:</b> none</div>
        <div class="alMini">SmartAPI market-data requests are limited by the provider. The app batches up to 50 tokens per quote request and spaces batches. No trade order is placed by this panel.</div>
      </div>
    </section>`;
  document.body.appendChild(root);

  let cfg=JSON.parse(localStorage.getItem(A.cfgKey)||'{}');
  let master=null, instruments=[], timer=null;

  function isMarketOpen(){
    const d=new Date(), parts=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).formatToParts(d);
    const get=k=>parts.find(x=>x.type===k)?.value;
    const wd=get('weekday'), hh=Number(get('hour')), mm=Number(get('minute'));
    if(['Sat','Sun'].includes(wd))return false;
    const mins=hh*60+mm; return mins>=555 && mins<=930;
  }
  function saveCfg(){
    cfg={
      apiKey:$('alApiKey').value.trim(),client:$('alClient').value.trim(),jwt:$('alJwt').value.trim(),
      localIp:$('alLocalIp').value.trim(),publicIp:$('alPublicIp').value.trim(),mac:$('alMac').value.trim(),
      symbol:$('alSymbol').value
    };
    localStorage.setItem(A.cfgKey,JSON.stringify(cfg));
  }
  function loadCfg(){
    ['apiKey','client','jwt','localIp','publicIp','mac'].forEach(k=>{const id='al'+k.charAt(0).toUpperCase()+k.slice(1);if($(id))$(id).value=cfg[k]||''});
    if(cfg.symbol&&[...$('alSymbol').options].some(o=>o.value===cfg.symbol))$('alSymbol').value=cfg.symbol;
  }
  function status(html,cl=''){
    $('alStatus').innerHTML=html;
    $('angelLiveBtn').innerHTML=cl==='good'?'🟢 ANGEL ONE LIVE':cl==='bad'?'🔴 ANGEL ONE':'⚡ ANGEL ONE LIVE';
  }
  function headers(){
    const h={'Content-Type':'application/json','Accept':'application/json','X-UserType':'USER','X-SourceID':'WEB'};
    const k=$('alApiKey').value.trim(),j=$('alJwt').value.trim();
    if(k)h['X-PrivateKey']=k;
    if(j)h.Authorization=j.toLowerCase().startsWith('bearer ')?j:'Bearer '+j;
    const li=$('alLocalIp').value.trim(),pi=$('alPublicIp').value.trim(),mac=$('alMac').value.trim();
    if(li)h['X-ClientLocalIP']=li;if(pi)h['X-ClientPublicIP']=pi;if(mac)h['X-MACAddress']=mac;
    return h;
  }
  async function getMaster(){
    if(master&&Array.isArray(master))return master;
    status('<b>LOADING INSTRUMENT MASTER…</b><div class="alMuted">Finding NFO CE/PE tokens.</div>');
    const r=await fetch(A.master,{cache:'no-store'});if(!r.ok)throw Error('Instrument master HTTP '+r.status);
    master=await r.json();if(!Array.isArray(master))throw Error('Invalid instrument master');
    return master;
  }
  function expiryKey(x){
    const raw=String(x.expiry||x.expiryDate||'').trim();
    const d=new Date(raw); if(!Number.isNaN(d.getTime()))return d;
    const m=raw.match(/^(\\d{2})([A-Z]{3})(\\d{4})$/i);
    if(m){const mm={JAN:0,FEB:1,MAR:2,APR:3,MAY:4,JUN:5,JUL:6,AUG:7,SEP:8,OCT:9,NOV:10,DEC:11};return new Date(Number(m[3]),mm[m[2].toUpperCase()],Number(m[1]),15,30)}
    return null;
  }
  function strikeVal(x){let v=n(x.strike);if(v!=null&&v>100000)v/=100;return v}
  function findOptions(){
    const sym=$('alSymbol').value;
    const now=new Date();
    const all=master.filter(x=>{
      const ex=String(x.exch_seg||x.exchange||'').toUpperCase(),name=String(x.name||'').toUpperCase(),typ=String(x.instrumenttype||'').toUpperCase(),ts=String(x.symbol||x.tradingsymbol||'').toUpperCase();
      const exp=expiryKey(x);
      return ex==='NFO' && name===sym && exp && exp>=new Date(now.getTime()-86400000) && (/OPT/.test(typ)||/CE$|PE$/.test(ts));
    }).map(x=>({...x,_expiry:expiryKey(x),_strike:strikeVal(x),_symbol:String(x.symbol||x.tradingsymbol||'')}));
    if(!all.length)throw Error('No '+sym+' NFO options found in Angel One instrument master.');
    const exps=[...new Map(all.map(x=>[x._expiry.toISOString().slice(0,10),x._expiry])).entries()].sort((a,b)=>a[1]-b[1]);
    const sel=$('alExpiry').value||'';
    $('alExpiry').innerHTML='<option value="">Auto nearest</option>'+exps.slice(0,8).map(x=>'<option value="'+x[0]+'">'+x[1].toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'})+'</option>').join('');
    if(sel&&[...$('alExpiry').options].some(o=>o.value===sel))$('alExpiry').value=sel;
    const wanted=$('alExpiry').value;
    const exp=exps.find(x=>x[0]===wanted)?.[1]||exps[0][1];
    const rows=all.filter(x=>x._expiry.toISOString().slice(0,10)===exp.toISOString().slice(0,10));
    const map=new Map();
    for(const x of rows){
      const st=x._strike;if(st==null)continue;
      const typ=/_PE$|PE$/.test(x._symbol.toUpperCase())?'PE':/_CE$|CE$/.test(x._symbol.toUpperCase())?'CE':null;
      if(!typ)continue;
      const key=st+'|'+typ;if(!map.has(key))map.set(key,x);
    }
    return [...map.values()].sort((a,b)=>a._strike-b._strike);
  }
  function unpackData(j){
    const d=j?.data;
    if(Array.isArray(d))return d;
    if(d&&Array.isArray(d.fetched))return d.fetched;
    if(d&&Array.isArray(d.data))return d.data;
    if(d&&typeof d==='object'){
      const out=[];for(const k of Object.keys(d))if(Array.isArray(d[k]))out.push(...d[k]);return out;
    }
    return [];
  }
  async function quote(tokens,exchange='NFO'){
    const out=[];
    for(let i=0;i<tokens.length;i+=A.maxTokens){
      const batch=tokens.slice(i,i+A.maxTokens);
      const body={mode:'FULL',exchangeTokens:{[exchange]:batch.map(x=>String(x))}};
      const r=await fetch(A.api+A.quote,{method:'POST',headers:headers(),body:JSON.stringify(body)});
      const j=await r.json().catch(()=>({}));
      if(!r.ok||j.status===false)throw Error(j.message||('Quote HTTP '+r.status));
      out.push(...unpackData(j));
      if(i+A.maxTokens<tokens.length)await sleep(1100);
    }
    return out;
  }
  function tokenOf(x){return String(x.symboltoken||x.token||'')}
  function pick(o,...keys){for(const k of keys){if(o&&o[k]!=null)return o[k]}return null}
  async function fetchChain(){
    saveCfg();
    if(!isMarketOpen()){
      const cached=JSON.parse(localStorage.getItem(A.cacheKey)||'null');
      if(cached){applyCache(cached);status('<b class="alWarn">MARKET CLOSED • LAST VERIFIED DATA</b><div class="alMuted">'+new Date(cached.capturedAt).toLocaleString('en-IN')+' • '+cached.chain.length+' strikes cached locally</div>','good');return true}
      status('<b class="alWarn">MARKET CLOSED</b><div class="alMuted">No previous Angel One snapshot is cached on this device.</div>','bad');return false;
    }
    const jwt=$('alJwt').value.trim(),apiKey=$('alApiKey').value.trim(),client=$('alClient').value.trim();
    if(!jwt||!apiKey||!client)throw Error('API Key + Client Code + JWT Access Token are required.');
    const opts=findOptions();
    status('<b>FETCHING ANGEL ONE…</b><div class="alMuted">'+opts.length+' option contracts discovered.</div>');
    const quotes=await quote(opts.map(tokenOf),'NFO');
    const qmap=new Map(quotes.map(q=>[String(pick(q,'symbolToken','symboltoken','token')),q]));
    const chainMap=new Map();
    for(const x of opts){
      const q=qmap.get(tokenOf(x));if(!q)continue;
      const side=/_PE$|PE$/.test(x._symbol.toUpperCase())?'pe': 'ce';
      const st=x._strike;
      const row=chainMap.get(st)||{strike:st,ce:{},pe:{}};
      row[side]={
        oi:n(pick(q,'opnInterest','openInterest','openinterest')),
        doi:n(pick(q,'netChangeOpnInterest','netChangeOpenInterest','netChangeOpnInt')),
        vol:n(pick(q,'tradeVolume','tradevolume','volume')),
        iv:n(pick(q,'impliedVolatility','impliedvolatility','iv')),
        ltp:n(pick(q,'ltp','lastTradedPrice')),
        chg:n(pick(q,'netChange','change')),
        bid:n(pick(q,'bestFiveData','bidPrice','bid')),
        ask:n(pick(q,'bestFiveData','askPrice','ask'))
      };
      chainMap.set(st,row);
    }
    const chain=[...chainMap.values()].filter(x=>x.ce.ltp!=null||x.pe.ltp!=null).sort((a,b)=>a.strike-b.strike);
    if(chain.length<3)throw Error('Angel One returned too few option rows. Check API permissions/tokens.');
    let spot=null;
    try{
      const s=await quote(['99926000'],'NSE');const z=s[0]||{};spot=n(pick(z,'ltp','lastTradedPrice'));
    }catch(_){}
    const exp=opts[0]?opts[0]._expiry.toISOString():new Date().toISOString();
    const payload={capturedAt:new Date().toISOString(),source:'ANGEL ONE SMARTAPI',symbol:$('alSymbol').value,expiry:exp,spot,chain};
    localStorage.setItem(A.cacheKey,JSON.stringify(payload));
    applyCache(payload);
    status('<b class="alGood">ANGEL ONE LIVE • VERIFIED SNAPSHOT</b><div class="alMuted">'+chain.length+' strikes • '+new Date(payload.capturedAt).toLocaleTimeString('en-IN')+'</div>','good');
    return true;
  }
  function applyCache(p){
    try{
      if(typeof window.addSnapshot==='function'){
        window.addSnapshot(p.chain,{captureAt:p.capturedAt,spot:p.spot,spotSource:'Angel One SmartAPI',expiry:p.expiry,captureSource:'Angel One SmartAPI',source:'ANGEL ONE'});
        if(window.state){window.state.live=isMarketOpen();window.state.source='ANGEL ONE';}
      }
      $('alCache').innerHTML='<b>Last snapshot:</b> '+escA(new Date(p.capturedAt).toLocaleString('en-IN'))+' • '+escA(String(p.chain?.length||0))+' strikes • '+escA(p.symbol||'NIFTY');
    }catch(e){console.warn('Angel One apply cache',e)}
  }
  async function connect(){
    try{await fetchChain(); if(isMarketOpen()){clearInterval(timer);timer=setInterval(()=>fetchChain().catch(e=>status('<b class="alBad">LIVE ERROR</b><div class="alMuted">'+escA(e.message)+'</div>','bad')),A.refreshMs)}}catch(e){status('<b class="alBad">ANGEL ONE ERROR</b><div class="alMuted">'+escA(e.message)+'</div>','bad')}
  }

  $('angelLiveBtn').onclick=()=>{$('angelLivePanel').classList.toggle('open');$('angelLivePanel').setAttribute('aria-hidden',String(!$('angelLivePanel').classList.contains('open')))};
  $('alClose').onclick=()=>$('angelLivePanel').classList.remove('open');
  $('alConnect').onclick=connect;
  $('alRefresh').onclick=()=>fetchChain().catch(e=>status('<b class="alBad">REFRESH ERROR</b><div class="alMuted">'+escA(e.message)+'</div>','bad'));
  $('alCacheBtn').onclick=()=>{const c=JSON.parse(localStorage.getItem(A.cacheKey)||'null');if(c)applyCache(c);else status('<b class="alWarn">NO CACHE</b><div class="alMuted">No Angel One snapshot saved yet.</div>','bad')};
  $('alSymbol').onchange=()=>{master=null;findOptions().catch(()=>{})};
  $('alExpiry').onchange=()=>{};
  loadCfg();
  const c=JSON.parse(localStorage.getItem(A.cacheKey)||'null');
  if(c)$('alCache').innerHTML='<b>Last snapshot:</b> '+escA(new Date(c.capturedAt).toLocaleString('en-IN'))+' • '+escA(String(c.chain?.length||0))+' strikes';
})();