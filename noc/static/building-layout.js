/* 층 분해도 배치: SVG의 실제 윤곽 사이 간격을 검사하고 화면 배율을 최대화한다.
 * 사각 바운딩 박스의 빈 모서리 때문에 별도 구역이 바깥으로 밀려나지 않게 한다. */
function buildingHull(points) {
  const p=points.slice().sort((a,b)=>a[0]-b[0]||a[1]-b[1]);
  const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
  const half=rows=>{const out=[];for(const pt of rows){while(out.length>1&&cross(out.at(-2),out.at(-1),pt)<=0)out.pop();out.push(pt)}return out};
  const lo=half(p),hi=half(p.slice().reverse());lo.pop();hi.pop();return lo.concat(hi);
}
function buildingOutline(room) {
  const points=[],inverse=room.getScreenCTM().inverse();
  for(const node of room.querySelectorAll('polygon,polyline,rect,line,circle,ellipse,path,text')){
    if(node.getAttribute('fill')==='transparent')continue;
    const m=inverse.multiply(node.getScreenCTM()),add=(x,y)=>points.push([m.a*x+m.c*y+m.e,m.b*x+m.d*y+m.f]);
    if(node.points){for(const p of node.points)add(p.x,p.y)}
    else if(node.localName==='line') {add(node.x1.baseVal.value,node.y1.baseVal.value);add(node.x2.baseVal.value,node.y2.baseVal.value)}
    else {const b=node.getBBox();add(b.x,b.y);add(b.x+b.width,b.y);add(b.x+b.width,b.y+b.height);add(b.x,b.y+b.height)}
  }
  return buildingHull(points);
}
const buildingMove=(shape,x,y)=>shape.map(p=>[p[0]+x,p[1]+y]);
function buildingBounds(shapes) {
  let x=Infinity,y=Infinity,right=-Infinity,bottom=-Infinity;
  for(const shape of shapes)for(const p of shape){x=Math.min(x,p[0]);y=Math.min(y,p[1]);right=Math.max(right,p[0]);bottom=Math.max(bottom,p[1])}
  return {x,y,width:right-x,height:bottom-y};
}
const BUILDING_SHAPE_BOUNDS=new WeakMap(),BUILDING_LAYOUT_CACHE=new Map();
function buildingSeparated(a,b,gap=14) {
  const bounds=p=>{if(!BUILDING_SHAPE_BOUNDS.has(p))BUILDING_SHAPE_BOUNDS.set(p,buildingBounds([p]));return BUILDING_SHAPE_BOUNDS.get(p)};
  const ba=bounds(a),bb=bounds(b);
  if(ba.x+ba.width+gap<=bb.x||bb.x+bb.width+gap<=ba.x||ba.y+ba.height+gap<=bb.y||bb.y+bb.height+gap<=ba.y)return true;
  for(const polygon of [a,b])for(let i=0;i<polygon.length;i++){
    const p=polygon[i],q=polygon[(i+1)%polygon.length],dx=q[1]-p[1],dy=p[0]-q[0],len=Math.hypot(dx,dy);
    if(!len)continue;
    let amin=Infinity,amax=-Infinity,bmin=Infinity,bmax=-Infinity;
    for(const v of a){const p=(v[0]*dx+v[1]*dy)/len;amin=Math.min(amin,p);amax=Math.max(amax,p)}
    for(const v of b){const p=(v[0]*dx+v[1]*dy)/len;bmin=Math.min(bmin,p);bmax=Math.max(bmax,p)}
    if(amax+gap<=bmin||bmax+gap<=amin)return true;
  }
  return false;
}
function arrangeBuilding(outlines,mainIds,frame) {
  const key=JSON.stringify([frame.width,frame.height,[...outlines].map(([id,hull])=>[id,hull.map(p=>p.map(v=>Math.round(v*10)))])]);
  if(BUILDING_LAYOUT_CACHE.has(key))return BUILDING_LAYOUT_CACHE.get(key);
  let best=null;
  const score=shapes=>{const b=buildingBounds(shapes);return {bounds:b,scale:Math.min(frame.width/b.width,frame.height/b.height)}};
  // 층을 화면 순서대로 분해해 배치한다. 길게 늘어선 대각선 대신
  // 위층→아래층 순서를 유지하는 2~4열을 비교해 화면에 가장 크게 맞춘다.
  const ids=[...mainIds].reverse().concat(['SITE','XOC']);
  for(const columns of [2,3,4])for(const offset of [0,.3,-.3])for(let dx=380;dx<=600;dx+=20){
    const positions=dy=>new Map(ids.map((id,i)=>{
      const row=Math.floor(i/columns),column=i%columns;
      return [id,[column*dx+(row%2)*dx*offset,row*dy+column*22]];
    }));
    const shapes=map=>ids.map(id=>buildingMove(outlines.get(id),...map.get(id)));
    const clear=rows=>rows.every((a,i)=>rows.slice(i+1).every(b=>buildingSeparated(a,b,12)));
    let low=100,high=450;
    if(!clear(shapes(positions(high))))continue;
    for(let n=0;n<9;n++){const mid=(low+high)/2;if(clear(shapes(positions(mid))))high=mid;else low=mid}
    const map=positions(high+2),polygons=shapes(map),candidate=score(polygons);
    const merit=candidate.scale-1e-10*candidate.bounds.width*candidate.bounds.height;
    if(!best||merit>best.merit)best={positions:map,shapes:polygons,dx,dy:high+2,columns,...candidate,merit};
  }
  // 향후 방·장비가 커져 탐색 범위를 벗어나도 겹침 없는 배치를 유지한다.
  if(!best){
    const boxes=ids.map(id=>buildingBounds([outlines.get(id)]));
    const dx=Math.max(...boxes.map(b=>b.width))+14,dy=Math.max(...boxes.map(b=>b.height))+14;
    const positions=new Map(ids.map((id,i)=>[id,[(i%2)*dx-boxes[i].x,Math.floor(i/2)*dy-boxes[i].y]]));
    const shapes=ids.map(id=>buildingMove(outlines.get(id),...positions.get(id)));
    best={positions,shapes,dx,dy,columns:2,...score(shapes)};
  }
  if(BUILDING_LAYOUT_CACHE.size>=12)BUILDING_LAYOUT_CACHE.delete(BUILDING_LAYOUT_CACHE.keys().next().value);
  BUILDING_LAYOUT_CACHE.set(key,best);return best;
}
