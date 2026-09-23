/* Pixel staff, drawn as SVG on a 24 × 44 grid.
 * Visual reference: https://github.com/mrgrit/dawnofagi/blob/main/apps/pixel-office/index.html
 * Reference revision: 5da1a5c8c69301a2148a9a870599881a9898c7fa.
 * Small rectangular heads, long uniforms and trousers follow that office style.
 * Uniform = role; cap = autonomy; chest badge = model/runtime, never live status. */
(() => {
  const NS='http://www.w3.org/2000/svg';
  const SIZE=Object.freeze({width:24,height:44});
  const styles={
    'facility-engineer':     {shirt:'#b88c4b',sleeve:'#94703d',collar:'#ddbc7d',trousers:'#4e514d',hair:'#514032',skin:'#dbb58f',cut:'short',beard:true},
    'physical-security':     {shirt:'#587791',sleeve:'#435e76',collar:'#a4b9c9',trousers:'#303e51',hair:'#343339',skin:'#c79872',cut:'short'},
    'network-engineer':      {shirt:'#4b9395',sleeve:'#397478',collar:'#9bc9c0',trousers:'#304c53',hair:'#373238',skin:'#e2bd96',cut:'side'},
    'systems-engineer':      {shirt:'#7d7eaa',sleeve:'#626489',collar:'#b8b3d1',trousers:'#3e425c',hair:'#704b38',skin:'#d4a27e',cut:'short',beard:true},
    'gpu-platform-engineer': {shirt:'#798e59',sleeve:'#5c7045',collar:'#b7c88c',trousers:'#3b4a3e',hair:'#39343c',skin:'#c79972',cut:'side'},
    'service-desk':          {shirt:'#a57668',sleeve:'#835c53',collar:'#d6b49b',trousers:'#51444a',hair:'#4b3430',skin:'#e2bd96',cut:'bob'},
    'soc-analyst':           {shirt:'#607b9e',sleeve:'#4b607e',collar:'#9bb8cf',trousers:'#34445c',hair:'#303239',skin:'#d4a27e',cut:'short',glasses:true},
    'ops-lead':              {shirt:'#657a88',sleeve:'#4e626f',collar:'#bcc9c9',trousers:'#33424d',hair:'#7d8589',skin:'#dbb58f',cut:'side',beard:true},
    'compliance-auditor':    {shirt:'#708d7e',sleeve:'#567061',collar:'#b3c8b4',trousers:'#3c4d47',hair:'#8f8982',skin:'#e2bd96',cut:'bob',glasses:true},
    'skill-researcher':      {shirt:'#9b8cba',sleeve:'#796b98',collar:'#d4c9e8',trousers:'#42435f',hair:'#49414b',skin:'#e2bd96',cut:'bob',glasses:true},
    'skill-evaluator':       {shirt:'#809fa7',sleeve:'#5e7d89',collar:'#c3d7d8',trousers:'#374c5a',hair:'#695349',skin:'#d4a27e',cut:'side'},
    'agent-supervisor':      {shirt:'#5b9691',sleeve:'#417774',collar:'#a5d3c7',trousers:'#304d53',hair:'#3a3942',skin:'#dbb58f',cut:'short',glasses:true},
  };
  const caps={L0:'#626d7b',L1:'#3e9c96',L2:'#5c87be',L3:'#9772b8',approver:'#b49a63'};
  const runtimeColors={claude:'#cf9971',codex:'#72b8a4',bastion:'#77aaca',hermes:'#a18bbe'};
  const cache=new Map();
  function badgeColor(worker){
    const model=String(worker.model||'').toLowerCase();
    if(model.includes('opus'))return '#a681c7';
    if(model.includes('sonnet'))return '#77a3d4';
    if(model.includes('haiku'))return '#8cbd83';
    return runtimeColors[worker.runtime]||'#a0adb5';
  }
  function draw(worker,seated){
    const s=styles[worker.id]||styles['network-engineer'];
    const svg=document.createElementNS(NS,'svg');
    const attrs={viewBox:`0 0 ${SIZE.width} ${SIZE.height}`,width:SIZE.width,height:SIZE.height,
      'shape-rendering':'crispEdges',class:'agent-pixel-sprite','data-sprite-style':'pixel-staff'};
    Object.entries(attrs).forEach(([k,v])=>svg.setAttribute(k,v));
    const rect=(x,y,width,height,fill)=>{
      const node=document.createElementNS(NS,'rect');
      Object.entries({x,y,width,height,fill}).forEach(([k,v])=>node.setAttribute(k,v));
      svg.appendChild(node);
    };
    // Restrained silhouettes: a quarter-height head and full-length trousers.
    rect(4,42,16,2,'#14212955');
    rect(7,31,4,10,s.trousers);rect(13,31,4,10,s.trousers);
    if(seated){rect(5,32,6,3,s.trousers);rect(13,32,6,3,s.trousers)}
    rect(6,41,5,2,'#17232d');rect(13,41,5,2,'#17232d');
    rect(6,18,12,14,s.shirt);rect(6,18,12,2,s.collar);
    rect(11,20,1,12,s.sleeve);
    rect(3,19,3,11,s.sleeve);rect(18,19,3,11,s.sleeve);
    rect(3,29,3,3,s.skin);rect(18,29,3,3,s.skin);
    rect(14,22,3,3,badgeColor(worker));
    rect(10,16,4,2,s.skin);
    // Neutral one-pixel features, straight hair and occasional glasses/beard.
    rect(7,6,10,10,s.skin);
    rect(7,5,10,3,s.hair);
    if(s.cut==='bob'){
      rect(6,7,2,8,s.hair);rect(16,7,2,8,s.hair);
    }else if(s.cut==='side'){
      rect(7,8,4,1,s.hair);rect(7,9,2,1,s.hair);rect(16,8,1,3,s.hair);
    }else{
      rect(7,8,1,2,s.hair);rect(16,8,1,2,s.hair);
    }
    rect(9,11,1,1,'#20252a');rect(14,11,1,1,'#20252a');
    if(s.beard){rect(9,14,6,2,s.hair);rect(11,14,2,1,s.skin)}
    else rect(11,14,2,1,'#ab7d62');
    if(s.glasses){
      for(const x of [8,13]){
        rect(x,10,3,1,'#8596a3');rect(x,12,3,1,'#8596a3');
        rect(x,11,1,1,'#8596a3');rect(x+2,11,1,1,'#8596a3');
      }
      rect(11,11,2,1,'#8596a3');
    }
    // Compact caps encode the configured autonomy; no floating glyphs or lights.
    const cap=caps[worker.autonomy]||caps.L0;
    rect(6,2,12,3,cap);rect(5,5,14,1,'#374b60');
    return svg;
  }
  window.AGENT_SPRITE_SIZE=SIZE;
  window.createAgentSprite=(worker,{seated=false}={})=>{
    const key=JSON.stringify([worker.id,worker.runtime,worker.model,worker.autonomy,seated]);
    if(!cache.has(key))cache.set(key,draw(worker,seated));
    const sprite=cache.get(key).cloneNode(true);
    sprite.setAttribute('role','img');sprite.setAttribute('aria-label',`${worker.name} AI 에이전트`);
    return sprite;
  };
})();
