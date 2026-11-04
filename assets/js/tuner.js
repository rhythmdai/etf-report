var $id = id => document.getElementById(id);
var maxHoldings = 6, discStep = 5, confType = 'ma_trend', rebalanceFreq = 'daily', f1ActiveDays = 1;
var navChart = null, ddChart = null, distChart = null;
var currentMetricsPage = 1;
var etfHoldingsMap = {}; // code → [top10]
var stockMetadataMap = {}; // stockCode → {biz_short, industry, ...}
function switchKlineView(view) {
 $id('kline-tab-chart').classList.toggle('active', view === 'chart');
 $id('kline-tab-holdings').classList.toggle('active', view === 'holdings');
 $id('kline-replay').style.display = view === 'chart' ? '' : 'none';
 $id('kline-holdings').style.display = view === 'holdings' ? '' : 'none';
 if (view === 'chart' && klineReplayChart) klineReplayChart.resize();
 if (view === 'holdings' && currentKlineCode) renderHoldingsTable(currentKlineCode);
}
function renderHoldingsTable(code) {
 var top10 = etfHoldingsMap[code] || [];
 var html = '';
 if (!top10.length) {
  html = '<div style="text-align:center;color:var(--text-muted);padding:60px 0;">暂无成分股数据</div>';
 } else {
  var maxW = top10[0].weight_pct || 1;
  html = '<div style="display:flex;flex-direction:column;gap:6px;">';
  for (var i = 0; i < top10.length; i++) {
   var h = top10[i], w = h.weight_pct || 0, barPct = Math.round(w / maxW * 100);
   var barColor = i < 3 ? TC.accent : i < 6 ? TC.accentLight : TC.textMuted;
   var sm = stockMetadataMap[h.code] || {};
   var bizShort = sm.biz_short || '';
   html += '<div style="display:flex;align-items:center;gap:10px;padding:6px 8px;background:rgba(255,255,255,0.015);border-radius:4px;">' +
    '<span style="width:20px;text-align:center;font-size:12px;font-weight:700;color:' + (i < 3 ? TC.warning : TC.textDim) + ';">' + (i+1) + '</span>' +
    '<span style="width:90px;font-size:13px;color:var(--text-body);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (h.name||'') + '">' + (h.name||'') + '</span>' +
    '<span style="font-size:11px;color:var(--accent-light);font-weight:600;width:55px;">' + (h.code||'') + '</span>' +
    '<span style="font-size:10px;color:var(--text-muted);width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + (bizShort || h.name) + '">' + (bizShort || '-') + '</span>' +
    '<div style="flex:1;height:5px;background:var(--bg-input);border-radius:3px;overflow:hidden;">' +
     '<div style="height:100%;width:' + barPct + '%;background:' + barColor + ';border-radius:3px;transition:width 0.3s;"></div>' +
    '</div>' +
    '<span style="font-size:12px;color:var(--text-secondary);font-weight:600;width:42px;text-align:right;">' + w.toFixed(1) + '%</span>' +
   '</div>';
  }
  html += '</div>';
 }
 $id('kline-holdings-body').innerHTML = html;
}
var lastResult = null;
var lastBacktestData = null; // last full backtest response for redraws
var navDates = []; // current nav-chart x-axis dates (post-resample)
var tunerSignalHistory = []; // 存储调仓历史，用于快照展示
var tunerSnapshotIdx = -1; // 当前快照索引
var currentSelectedDate = null; // 用户当前选中的日期 (红线位置, 可能不在调仓日)

/* ====== Left view switching ====== */
/* (removed - presets now inline) */

/* ====== Right view switching ====== */
function toggleGuide(id) {
 var el = $id(id);
 el.classList.toggle('open');
 if (id === 'guide-f1' && el.classList.contains('open') && !f1Chart) {
  setTimeout(renderF1GuideChart, 50);
 }
 if (id === 'guide-f1-zoh' && el.classList.contains('open') && !f1ZohChart) {
  setTimeout(renderF1ZOHChart, 50);
 }
 if (id === 'guide-f3' && el.classList.contains('open') && !f3Chart) {
  setTimeout(renderF3GuideChart, 50);
 }
  if (id === 'guide-f7' && el.classList.contains('open') && !f7Chart) {
  setTimeout(renderF7GuideChart, 50);
 }
}


/* ====== F1 d₁ → F1 sigmoid 映射可视化 ====== */
var f1Chart = null;

function renderF1GuideChart() {
 if (f1Chart) { f1Chart.dispose(); f1Chart = null; }
 var dom = $id('f1-curve');
 if (!dom) return;
 f1Chart = echarts.init(dom);
 var sensitivities = [
  { s: 4, color: TC.pink, label: 's=4 (敏感)' },
  { s: 8, color: TC.accentLight, label: 's=8 (参考)', annotate: true },
  { s: 12, color: '#a78bfa', label: 's=12 (迟钝)' },
 ];
 var devPoints = [];
 for (var d = -20; d <= 20.001; d += 0.5) devPoints.push(+d.toFixed(1));
 var annotData = [];
 var series = sensitivities.map(function(cfg) {
  var data = devPoints.map(function(d) {
   return +((1 / (1 + Math.exp(-d / cfg.s))) * 100).toFixed(2);
  });
  var idx0 = Math.round((0 + 20) / 0.5);   // d₁=0
  var idxP = Math.round((cfg.s + 20) / 0.5); // d₁=+σ
  var idxN = Math.round((-cfg.s + 20) / 0.5); // d₁=−σ
  if (cfg.annotate) {
   annotData = [
    { value: [idx0, data[idx0]], name: '中性 d₁=0 F₁=0.50', symbolSize: 14, itemStyle: { color: TC.highlight } },
    { value: [idxP, data[idxP]], name: 'd₁=+σ₁ F₁≈0.73', symbolSize: 14, itemStyle: { color: TC.positive } },
    { value: [idxN, data[idxN]], name: 'd₁=−σ₁ F₁≈0.27', symbolSize: 14, itemStyle: { color: TC.negative } },
   ];
  }
  return {
   type: 'line', name: cfg.label, data: data, showSymbol: false,
   lineStyle: { color: cfg.color, width: 2 },
   itemStyle: { color: cfg.color },
  };
 });
 if (annotData.length > 0) {
  series.push({
   type: 'scatter', name: '', data: annotData, z: 10,
   label: { show: true, color: '#fff', fontSize: 11, fontWeight: 'bold', formatter: '{b}', position: 'top', distance: 8 },
  });
 }
 f1Chart.setOption({
  backgroundColor: 'transparent',
  title: { show: false },
  tooltip: {
   trigger: 'axis',
   axisPointer: { type: 'cross' },
   backgroundColor: 'rgba(10,25,47,0.95)',
   borderColor: 'rgba(59,130,246,0.2)',
   textStyle: { color: TC.textBody, fontSize: 11 },
   formatter: function(params) {
    var s = 'd₁ = ' + params[0].axisValue + '%';
    params.forEach(function(p) { s += '<br/><span style="color:' + p.color + '">' + p.seriesName + '</span> = <b>' + (p.value / 100).toFixed(3) + '</b>'; });
    return s;
   },
  },
  legend: { data: sensitivities.map(function(c) { return c.label; }), top: 20, right: 6, textStyle: { color: '#9ca3af', fontSize: 9 }, itemWidth: 14, itemHeight: 8 },
  grid: { left: 40, right: 12, top: 50, bottom: 36 },
  xAxis: {
   type: 'category', data: devPoints.map(function(d) { return '' + d; }),
   axisLabel: { color: TC.textMuted, fontSize: 9, interval: Math.floor(devPoints.length / 8) },
   axisLine: { lineStyle: { color: TC.border } },
   splitLine: { show: false },
   name: 'd₁ (%)', nameTextStyle: { color: TC.textMuted, fontSize: 9 }, nameLocation: 'middle', nameGap: 18,
  },
  yAxis: {
   type: 'value', min: 0, max: 100,
   axisLabel: { color: TC.textMuted, fontSize: 9, formatter: function(v) { return (v / 100).toFixed(1); } },
   splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
   name: 'F1', nameTextStyle: { color: TC.textMuted, fontSize: 9 },
  },
  series: series,
 });
 return f1Chart;
}

/* ====== F1 阶梯波可视化 ====== */
var f1ZohChart = null;

function renderF1ZOHChart() {
 if (f1ZohChart) { f1ZohChart.dispose(); f1ZohChart = null; }
 var dom = $id('f1-zoh-curve');
 if (!dom) return;
 if (dom.offsetWidth === 0 || dom.offsetHeight === 0) return;
 f1ZohChart = echarts.init(dom);

 var DAYS=['一','二','三','四','五'];
 var N=8;

 // ==== REAL DATA: 515220 coal ETF, April 1-22 2026, 30-min K-line ====
 // X-axis: tick 0=Apr1(Wed),1=Apr2(Thu),2=Apr3(Fri),3=Apr7(Tue),4=Apr8(Wed),
 //  5=Apr9(Thu),6=Apr10(Fri),7=Apr13(Mon),8=Apr14(Tue),9=Apr15(Wed),
 //  10=Apr16(Thu),11=Apr17(Fri),12=Apr20(Mon),13=Apr21(Tue),14=Apr22(Wed)

 // Base split into 4 independent series — avoids ECharts step+null rendering bug
 // Segments meet at exact tick boundaries — jump at shared point
 var baseSegs=[
  [[0,0.94],[2,0.94]],
  [[2,-3.55],[6,-3.55]],
  [[6,-2.01],[11,-2.01]],
  [[11,-2.02],[13,-2.02]]
 ];

 // Blue hold = Mon-Thu only, Friday left for intraday
 var friHoldSegs=[
  [[0,0.94],[1,0.94]],
  [[2,-3.55],[5,-3.55]],
  [[6,-2.01],[10,-2.01]],
  [[11,-2.02],[13,-2.02]]
 ];
var friIntraSegs=[
 [[1.125,-2.17],[1.25,-2.36],[1.375,-2.15],[1.5,-3.3],[1.625,-3.35],[1.75,-3.6],[1.875,-3.6],[2,-3.55]],
 [[5.125,-2.16],[5.25,-2.16],[5.375,-1.99],[5.5,-2.11],[5.625,-1.89],[5.75,-2.09],[5.875,-2.09],[6,-2.01]],
 [[10.125,-1.85],[10.25,-1.9],[10.375,-1.88],[10.5,-1.72],[10.625,-1.8],[10.75,-1.8],[10.875,-1.75],[11,-2.02]]
];

 // Tue+Fri mode (ad=9): Mon hold, Tue checkpoint, Wed-Thu freeze, Fri checkpoint
 var tfFreezeSegs=[
  [[0,-2.17],[1,-2.17]],     // W14 Wed-Thu freeze at Tue checkpoint
  [[3,-2.21],[5,-2.21]],     // W15 Wed-Thu freeze
  [[8,-1.80],[10,-1.80]],     // W16 Wed-Thu freeze
  // W17 freeze removed (no green line data past 13)
 ];
 var tfTueIntraSegs=[
  [[2.125,-1.88],[2.25,-1.66],[2.375,-2.16],[2.5,-1.91],[2.625,-1.93],[2.75,-2.1],[2.875,-2.18],[3.0,-2.21]],
  [[7.125,-1.78],[7.25,-1.85],[7.375,-1.75],[7.5,-1.79],[7.625,-1.81],[7.75,-1.81],[7.875,-1.81],[8.0,-1.80]],
  [[12.125,-1.68],[12.25,-1.72],[12.375,-1.65],[12.5,-1.65],[12.625,-1.72],[12.75,-1.65],[12.875,-1.62],[13.0,-1.62]]
 ];

 // Tue+Fri Monday hold segments: connect Fri close to Mon, before Tue intraday
 var tfMonHoldSegs=[
  [[6,-2.01],[7,-2.01]],     // W16 Mon hold
  [[11,-2.02],[12,-2.02]]     // W17 Mon hold
 ];

 var dailyData=[
  [-0.875,-0.18],[-0.75,-0.1],[-0.625,-0.21],[-0.5,-0.23],[-0.375,-0.29],[-0.25,-0.1],[-0.125,0.03],[0.0,0.05],
  [0.125,0.08],[0.25,0.21],[0.375,0.23],[0.5,0.21],[0.625,0.2],[0.75,0.12],[0.875,0.03],
  [1.0,-1.88],[1.125,-2.17],[1.25,-2.36],[1.375,-2.15],[1.5,-3.3],[1.625,-3.35],[1.75,-3.6],[1.875,-3.6],
  [2.125,-1.88],[2.25,-1.66],[2.375,-2.16],[2.5,-1.91],[2.625,-1.93],[2.75,-2.1],[2.875,-2.18],[3.0,-2.21],
  [3.125,-1.95],[3.25,-2.02],[3.375,-2.15],[3.5,-2.3],[3.625,-2.28],[3.75,-2.23],[3.875,-2.03],[4.0,-2.26],
  [4.125,-1.81],[4.25,-1.75],[4.375,-1.72],[4.5,-1.78],[4.625,-1.8],[4.75,-1.85],[4.875,-1.82],[5.0,-1.96],
  [5.125,-2.16],[5.25,-2.16],[5.375,-1.99],[5.5,-2.11],[5.625,-1.89],[5.75,-2.09],[5.875,-2.09],
  [6.125,-1.81],[6.25,-1.73],[6.375,-1.7],[6.5,-1.82],[6.625,-1.79],[6.75,-1.9],[6.875,-1.83],[7.0,-1.55],
  [7.125,-1.78],[7.25,-1.85],[7.375,-1.75],[7.5,-1.79],[7.625,-1.81],[7.75,-1.81],[7.875,-1.81],[8.0,-1.8],
  [8.125,-1.78],[8.25,-1.62],[8.375,-1.62],[8.5,-1.71],[8.625,-1.69],[8.75,-1.66],[8.875,-1.7],[9.0,-1.65],
  [9.125,-1.81],[9.25,-1.77],[9.375,-1.68],[9.5,-1.66],[9.625,-1.58],[9.75,-1.69],[9.875,-1.63],[10.0,-1.75],
  [10.125,-1.85],[10.25,-1.9],[10.375,-1.88],[10.5,-1.72],[10.625,-1.8],[10.75,-1.8],[10.875,-1.75],
  [11.125,-1.62],[11.25,-1.62],[11.375,-1.72],[11.5,-1.82],[11.625,-1.72],[11.75,-1.68],[11.875,-1.69],[12.0,-1.86],
  [12.125,-1.68],[12.25,-1.72],[12.375,-1.65],[12.5,-1.65],[12.625,-1.72],[12.75,-1.65],[12.875,-1.62],[13.0,-1.62]
 ];

 // Filter daily to visible range
 dailyData = dailyData.filter(function(p){return p[0]>=0 && p[0]<=13;});

 var option={
  backgroundColor:'transparent',
  legend:{data:['Base (ad=0)','Friday (ad=1)','Tue+Fri (ad=9)','Daily (ad=31)'],top:6,right:6,textStyle:{color:'#9ca3af',fontSize:9},itemWidth:16,itemHeight:3},
  tooltip:{trigger:'axis',backgroundColor:'rgba(10,25,47,0.95)',borderColor:'rgba(59,130,246,0.2)',textStyle:{color:TC.textBody,fontSize:11},axisPointer:{type:'cross'},
   formatter:function(ps){if(!ps||ps.length===0)return'';var rx=ps[0].axisValue,t=Math.round(rx);if(Math.abs(rx-t)>0.45)return'';if(t<0||t>13)return'';var dows=[2,3,4,1,2,3,4,0,1,2,3,4,0,1,2];var dow=dows[t]||0;var dates=['4/1','4/2','4/3','4/7','4/8','4/9','4/10','4/13','4/14','4/15','4/16','4/17','4/20','4/21'];var dates=['4/1','4/2','4/3','4/7','4/8','4/9','4/10','4/13','4/14','4/15','4/16','4/17','4/20','4/21'];var ds=t<15?dates[t]:'';var s=ds+' 周'+DAYS[dow]+' 收盘';ps.forEach(function(p){if(p.seriesName&&p.value[1]!=null){if(Math.abs(p.value[0]-t)<0.55)s+='<br/><span style=\"color:'+p.color+'\">'+p.seriesName+'</span> = <b>'+p.value[1].toFixed(2)+'%</b>';}});return s;}},
  grid:{left:48,right:16,top:36,bottom:32},
  xAxis:{type:'value',min:0,max:13,interval:1,axisLine:{lineStyle:{color:TC.border}},axisTick:{show:true,lineStyle:{color:'#374151'}},axisLabel:{color:TC.textMuted,fontSize:9,formatter:function(v){var t=Math.round(v);if(t<0||t>13)return'';var dates=['4/1','4/2','4/3','4/7','4/8','4/9','4/10','4/13','4/14','4/15','4/16','4/17','4/20','4/21'];return dates[t]||''||'';}},splitLine:{show:false}},
  yAxis:{type:'value',axisTick:{inside:true},axisLabel:{color:TC.textMuted,fontSize:9,formatter:function(v){return v.toFixed(1)+'%';}},splitLine:{lineStyle:{color:'rgba(255,255,255,0.05)'}},name:'F1',nameTextStyle:{color:TC.textMuted,fontSize:9}},
  series:[
   {name:'Base (ad=0)',type:'line',data:baseSegs[0],step:'start',lineStyle:{color:TC.textMuted,width:2.5},symbol:'none',z:1},
   {name:'Base (ad=0)',type:'line',data:baseSegs[1],step:'start',lineStyle:{color:TC.textMuted,width:2.5},symbol:'none',z:1,legendHoverLink:false},
   {name:'Base (ad=0)',type:'line',data:baseSegs[2],step:'start',lineStyle:{color:TC.textMuted,width:2.5},symbol:'none',z:1,legendHoverLink:false},
   {name:'Base (ad=0)',type:'line',data:baseSegs[3],step:'start',lineStyle:{color:TC.textMuted,width:2.5},symbol:'none',z:1,legendHoverLink:false},
   {name:'Friday (ad=1)',type:'line',data:friHoldSegs[0],step:'start',lineStyle:{color:TC.accentLight,width:2},symbol:'none',z:2},
   {name:'Friday (ad=1)',type:'line',data:friHoldSegs[1],step:'start',lineStyle:{color:TC.accentLight,width:2},symbol:'none',z:2,legendHoverLink:false},
   {name:'Friday (ad=1)',type:'line',data:friHoldSegs[2],step:'start',lineStyle:{color:TC.accentLight,width:2},symbol:'none',z:2,legendHoverLink:false},
   {name:'Friday (ad=1)',type:'line',data:friHoldSegs[3],step:'start',lineStyle:{color:TC.accentLight,width:2},symbol:'none',z:2,legendHoverLink:false},
   {name:'Friday (ad=1)',type:'line',data:friIntraSegs[0],step:false,lineStyle:{color:TC.accentLight,width:2.5},symbol:'none',z:4,legendHoverLink:false},
   {name:'Friday (ad=1)',type:'line',data:friIntraSegs[1],step:false,lineStyle:{color:TC.accentLight,width:2.5},symbol:'none',z:4,legendHoverLink:false},
   {name:'Friday (ad=1)',type:'line',data:friIntraSegs[2],step:false,lineStyle:{color:TC.accentLight,width:2.5},symbol:'none',z:4,legendHoverLink:false},
   {name:'Tue+Fri (ad=9)',type:'line',data:tfFreezeSegs[0],step:'start',lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:3},
   {name:'Tue+Fri (ad=9)',type:'line',data:tfFreezeSegs[1],step:'start',lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:3,legendHoverLink:false},
   {name:'Tue+Fri (ad=9)',type:'line',data:tfFreezeSegs[2],step:'start',lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:3,legendHoverLink:false},
   // tfFreezeSegs[3] removed (trim to x=13)
   {name:'Tue+Fri (ad=9)',type:'line',data:tfTueIntraSegs[0],step:false,lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:5,legendHoverLink:false},
   {name:'Tue+Fri (ad=9)',type:'line',data:tfTueIntraSegs[1],step:false,lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:5,legendHoverLink:false},
   {name:'Tue+Fri (ad=9)',type:'line',data:tfTueIntraSegs[2],step:false,lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:5,legendHoverLink:false},
   {name:'Tue+Fri (ad=9)',type:'line',data:friIntraSegs[0],step:false,lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:5,legendHoverLink:false},
   {name:'Tue+Fri (ad=9)',type:'line',data:friIntraSegs[1],step:false,lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:5,legendHoverLink:false},
   {name:'Tue+Fri (ad=9)',type:'line',data:friIntraSegs[2],step:false,lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:5,legendHoverLink:false},
   {name:'Tue+Fri (ad=9)',type:'line',data:tfMonHoldSegs[0],step:'start',lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:3,legendHoverLink:false},
   {name:'Tue+Fri (ad=9)',type:'line',data:tfMonHoldSegs[1],step:'start',lineStyle:{color:'#a78bfa',width:2},symbol:'none',z:3,legendHoverLink:false},
   {name:'Daily (ad=31)',type:'line',data:dailyData,step:false,lineStyle:{color:TC.greenLight,width:1.2},symbol:'none',silent:true,z:3},
   {name:'检查点',type:'scatter',data:[[2,-3.55],[3,-2.21],[6,-2.01],[8,-1.80],[11,-2.02]],symbolSize:10,itemStyle:{color:TC.warning,borderColor:'#1f2937',borderWidth:2},label:{show:true,position:'top',color:TC.warning,fontSize:9,formatter:function(p){return['W14F','W15T','W15F','W16T','W16F','W17T'][p.dataIndex];},distance:8},z:10},
   {type:'line',name:'检查点',markLine:{silent:true,symbol:'none',lineStyle:{color:'rgba(255,255,255,0.06)'},data:[{yAxis:0}]},data:[],z:0},
  ],
 };
 f1ZohChart.setOption(option);
}

/* ====== F3 r → F3 (log+sigmoid) 映射可视化 ====== */
var f3Chart = null;

function renderF3GuideChart() {
 if (f3Chart) { f3Chart.dispose(); f3Chart = null; }
 var dom = $id('f3-curve');
 if (!dom) return;
 f3Chart = echarts.init(dom);
 var sensitivities = [
  { s: 0.5, color: TC.pink, label: 's=0.5 (敏感)' },
  { s: 1.0, color: TC.positive, label: 's=1.0 (参考)', annotate: true },
  { s: 2.0, color: '#a78bfa', label: 's=2.0 (迟钝)' },
 ];
 var ratioPoints = [];
 for (var r = 0.1; r <= 10.001; r += 0.1) ratioPoints.push(+r.toFixed(1));
 var annotData3 = [];
 var series = sensitivities.map(function(cfg) {
  var data = ratioPoints.map(function(r) {
   var logR = r >= 1 ? Math.log(r) : -Math.log(1 / r);
   return +((1 / (1 + Math.exp(-logR / cfg.s))) * 100).toFixed(2);
  });
  if (cfg.annotate) {
   var idx05 = Math.round((0.5 - 0.1) / 0.1);
   var idx1 = Math.round((1.0 - 0.1) / 0.1);
   var idx2 = Math.round((2.0 - 0.1) / 0.1);
   var idx5 = Math.round((5.0 - 0.1) / 0.1);
   var idx10 = Math.round((10.0 - 0.1) / 0.1);
   annotData3 = [
    { value: [idx05, data[idx05]], name: '量缩 r=0.5 F₃=0.34', symbolSize: 14, itemStyle: { color: TC.negative } },
    { value: [idx1, data[idx1]], name: '中性 r=1 F₃=0.50', symbolSize: 14, itemStyle: { color: TC.highlight } },
    { value: [idx2, data[idx2]], name: '温和放量 r=2 F₃=0.66', symbolSize: 14, itemStyle: { color: TC.positive } },
    { value: [idx5, data[idx5]], name: '爆量 r=5 F₃=0.83', symbolSize: 14, itemStyle: { color: TC.warning } },
    { value: [idx10, data[idx10]], name: '极端爆量 r=10 F₃=0.91', symbolSize: 14, itemStyle: { color: TC.pink } },
   ];
  }
  return {
   type: 'line', name: cfg.label, data: data, showSymbol: false,
   lineStyle: { color: cfg.color, width: 2 },
   itemStyle: { color: cfg.color },
  };
 });
 if (annotData3.length > 0) {
  series.push({
   type: 'scatter', name: '', data: annotData3, z: 10,
   label: { show: true, color: '#fff', fontSize: 11, fontWeight: 'bold', formatter: '{b}', position: 'top', distance: 8 },
  });
 }
 f3Chart.setOption({
  backgroundColor: 'transparent',
  title: { show: false },
  tooltip: {
   trigger: 'axis',
   axisPointer: { type: 'cross' },
   backgroundColor: 'rgba(10,25,47,0.95)',
   borderColor: 'rgba(16,185,129,0.2)',
   textStyle: { color: TC.textBody, fontSize: 11 },
   formatter: function(params) {
    var s = 'r = ' + params[0].axisValue;
    params.forEach(function(p) { s += '<br/><span style="color:' + p.color + '">' + p.seriesName + '</span> = <b>' + (p.value / 100).toFixed(3) + '</b>'; });
    return s;
   },
  },
  legend: { data: sensitivities.map(function(c) { return c.label; }), top: 20, right: 6, textStyle: { color: '#9ca3af', fontSize: 9 }, itemWidth: 14, itemHeight: 8 },
  grid: { left: 40, right: 12, top: 50, bottom: 36 },
  xAxis: {
   type: 'category', data: ratioPoints.map(function(r) { return '' + r; }),
   axisLabel: { color: TC.textMuted, fontSize: 9, interval: Math.floor(ratioPoints.length / 8) },
   axisLine: { lineStyle: { color: TC.border } },
   splitLine: { show: false },
   name: 'r', nameTextStyle: { color: TC.textMuted, fontSize: 9 }, nameLocation: 'middle', nameGap: 18,
  },
  yAxis: {
   type: 'value', min: 0, max: 100,
   axisLabel: { color: TC.textMuted, fontSize: 9, formatter: function(v) { return (v / 100).toFixed(1); } },
   splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
   name: 'F3', nameTextStyle: { color: TC.textMuted, fontSize: 9 },
  },
  series: series,
 });
 return f3Chart;
}

/* ====== F7 Z-score → F7 分段映射可视化 ====== */
var f7Chart = null;

function f7MapScore(z, t, k) {
 var absZ = Math.abs(z);
 if (absZ <= k) {
  var ratio = absZ / k;
  var powered = Math.sign(z) * Math.pow(ratio, t);
  return 0.5 + 0.5 * (-powered);
 } else {
  var slope = -t / (2.0 * k);
  if (z > 0) {
   return slope * (z - k);
  } else {
   return 1.0 + slope * (z + k);
  }
 }
}

function renderF7GuideChart() {
 if (f7Chart) { f7Chart.dispose(); f7Chart = null; }
 var dom = $id('f7-curve');
 if (!dom) return;
 f7Chart = echarts.init(dom);
 var combos = [
  { t: 3, k: 3.0, color: TC.pink, label: 't=3, k=3 (温和)' },
  { t: 7, k: 3.0, color: TC.positive, label: 't=7, k=3 (参考)', annotate: true },
  { t: 11, k: 3.0, color: TC.accentLight, label: 't=11, k=3 (激进)' },
  { t: 7, k: 2.0, color: TC.warning, label: 't=7, k=2 (早切换)' },
 ];
 var zMin = -8, zMax = 8, zStep = 0.2;
 var annotData7 = [];
 var series = combos.map(function(cfg) {
  var data = [];
  for (var z = zMin; z <= zMax + 0.0001; z += zStep) {
   data.push([+z.toFixed(1), +f7MapScore(z, cfg.t, cfg.k).toFixed(4)]);
  }
  if (cfg.annotate) {
   annotData7 = [
    { value: [0, +f7MapScore(0, cfg.t, cfg.k).toFixed(4)], name: 'Z=0 F₇=0.50', symbolSize: 14, itemStyle: { color: TC.highlight } },
    { value: [cfg.k, +f7MapScore(cfg.k, cfg.t, cfg.k).toFixed(4)], name: 'Z=+k 切线切换 F₇→0', symbolSize: 14, itemStyle: { color: TC.negative } },
    { value: [-cfg.k, +f7MapScore(-cfg.k, cfg.t, cfg.k).toFixed(4)], name: 'Z=−k 切线切换 F₇→1', symbolSize: 14, itemStyle: { color: TC.accentLight } },
   ];
  }
  return {
   type: 'line', name: cfg.label, data: data, showSymbol: false,
   lineStyle: { color: cfg.color, width: 2 },
   itemStyle: { color: cfg.color },
  };
 });
 if (annotData7.length > 0) {
  series.push({
   type: 'scatter', name: '', data: annotData7, z: 10,
   label: { show: true, color: '#fff', fontSize: 11, fontWeight: 'bold', formatter: '{b}', position: 'top', distance: 8 },
  });
 }
 f7Chart.setOption({
  backgroundColor: 'transparent',
  title: { show: false },
  tooltip: {
   trigger: 'axis',
   axisPointer: { type: 'cross' },
   backgroundColor: 'rgba(10,25,47,0.95)',
   borderColor: 'rgba(16,185,129,0.2)',
   textStyle: { color: TC.textBody, fontSize: 11 },
   formatter: function(params) {
    var z = params[0].data[0];
    var s = 'Z = ' + z.toFixed(1);
    params.forEach(function(p) { s += '<br/><span style="color:' + p.color + '">' + p.seriesName + '</span> = <b>' + p.data[1].toFixed(3) + '</b>'; });
    return s;
   },
  },
  legend: { data: combos.map(function(c) { return c.label; }), top: 20, right: 6, textStyle: { color: '#9ca3af', fontSize: 9 }, itemWidth: 14, itemHeight: 8 },
  grid: { left: 44, right: 16, top: 50, bottom: 36 },
  xAxis: {
   type: 'value', min: zMin, max: zMax,
   axisLabel: { color: TC.textMuted, fontSize: 9 },
   axisLine: { show: true, lineStyle: { color: TC.textDim }, onZero: true },
   splitLine: { lineStyle: { color: 'rgba(255,255,255,0.03)' } },
   name: 'Z-score', nameTextStyle: { color: TC.textMuted, fontSize: 9 }, nameLocation: 'middle', nameGap: 18,
  },
  yAxis: {
   type: 'value', min: -2, max: 3,
   axisLabel: { color: TC.textMuted, fontSize: 9 },
   axisLine: { show: true, lineStyle: { color: TC.textDim }, onZero: true },
   splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
   name: 'F7', nameTextStyle: { color: TC.textMuted, fontSize: 9 },
  },
  series: series,
 });
 return f7Chart;
}

function switchRightView(view) {
 document.querySelectorAll('.right-view').forEach(el => el.classList.remove('active'));
 $id('right-view-' + view).classList.add('active');
 var labels = { guide: '参数原理', results: '回测结果', heatmap: '涨跌热力', datamgmt: '数据管理' };
 document.querySelectorAll('#right-view-switch button').forEach(b => b.classList.toggle('active', b.textContent === (labels[view] || '')));
 if (view === 'results' && navChart) {
  setTimeout(() => { navChart.resize(); (ddChart && ddChart.resize(), distChart && distChart.resize()); }, 50);
 }
 if (view === 'heatmap') {
  initHeatmap();
 }
 if (view === 'datamgmt') {
  initDataMgmt();
 }
}




/* ====== Parameter helpers ====== */
function setMH(n) {
 maxHoldings = n;
 $id('v-mh').textContent = n + ' 支';
 document.querySelectorAll('#mh-group button').forEach(b => b.classList.toggle('active', parseInt(b.textContent) === n));
}
function setDisc(n) {
 discStep = n;
 $id('v-disc').textContent = n + '%';
 document.querySelectorAll('#disc-group button').forEach(b => b.classList.toggle('active', parseInt(b.textContent) === n));
}
function setFreq(f) {
 rebalanceFreq = f;
 var label = f === 'daily' ? '日调仓' : '周调仓';
 $id('v-freq').textContent = label;
 document.querySelectorAll('#freq-group button').forEach(b => {
  var bv = b.getAttribute('data-val');
  b.classList.toggle('active', bv === f);
 });
 renderParamSchemaTable();
}
function setF1Normalize(v) {}  // removed (REQ-375 rollback)
function setF1ActiveDays(v) {
 v = parseInt(v) || 0; f1ActiveDays = v;
 updateF1DayButtons();
}
function toggleF1Day(bit) {
 f1ActiveDays ^= bit; // toggle the bit
 updateF1DayButtons();
}
function updateF1DayButtons() {
 var v = f1ActiveDays;
 var dayNames = ['一','二','三','四','五'];
 var dayBits = [16, 8, 4, 2, 1];
 var selected = [];
 for (var i = 0; i < 5; i++) {
  var btn = $id('f1ad-' + ['mon','tue','wed','thu','fri'][i]);
  if (btn) btn.classList.toggle('active', (v & dayBits[i]) !== 0);
  if (v & dayBits[i]) selected.push(dayNames[i]);
 }
 // Display label
 if (v === 31) $id('v-f1ad').textContent = '全选';
 else if (v === 0) $id('v-f1ad').textContent = '全不选';
 else $id('v-f1ad').textContent = selected.join('、');
}
function setExecutionTiming(t) { /* deprecated — always same_close */ }
function setConfType(t) {
 confType = t;
 var labels = {'ma_trend':'MA趋势','regime':'市场状态','dd_trigger':'回撤触发','momentum_crash':'动量崩溃','always_full':'始终满仓'};
 var el = $id('v-conf-type'); if (el) el.textContent = labels[t] || t;
 renderParamSchemaTable();
}

var maDirConfirm = true;
function setMaDirConfirm(v) {
 maDirConfirm = v;
 // ma-dir panel removed — direction confirm always on
 renderParamSchemaTable();
}

// ── Voting committee ──────────────────────────────────────────────
var BENCHMARK_CHOICES = [
 {code:'510050', name:'上证50', role:'超大盘价值'},
 {code:'510300', name:'沪深300', role:'大盘基准'},
 {code:'510500', name:'中证500', role:'中盘成长'},
 {code:'159915', name:'创业板指',role:'高Beta成长'},
];
var selectedBenchmarks = ['510300']; // default: HS300 ETF

function initBenchmarkPanel() {
 var container = $id('benchmark-btns');
 container.innerHTML = '';
 BENCHMARK_CHOICES.forEach(function(b) {
  var btn = document.createElement('button');
  btn.id = 'bm-btn-' + b.code;
  btn.textContent = b.name;
  btn.title = b.code + ' · ' + b.role;
  btn.className = 'bm-toggle';
  btn.onclick = function() { toggleBenchmark(b.code); };
  container.appendChild(btn);
 });
 updateBenchmarkPanel();
}

function toggleBenchmark(code) {
 var idx = selectedBenchmarks.indexOf(code);
 if (idx >= 0) {
  if (selectedBenchmarks.length > 1) {
   selectedBenchmarks.splice(idx, 1);
  }
  // If only 1 selected, don't deselect (must have at least 1)
 } else {
  selectedBenchmarks.push(code);
 }
 updateBenchmarkPanel();
}

function updateBenchmarkPanel() {
 var n = selectedBenchmarks.length;

 // Update toggle buttons
 BENCHMARK_CHOICES.forEach(function(b) {
  var btn = $id('bm-btn-' + b.code);
  if (!btn) return;
  var active = selectedBenchmarks.indexOf(b.code) >= 0;
  if (active) { btn.classList.add('active'); }
  else { btn.classList.remove('active'); }
 });

 // Update label
 if (n === 1) {
  var solo = BENCHMARK_CHOICES.find(function(b) { return b.code === selectedBenchmarks[0]; });
  var name = solo ? solo.name : selectedBenchmarks[0];
  $id('v-benchmarks').textContent = '单指数 · ' + name;
 } else {
  $id('v-benchmarks').textContent = n + '票投票 · ' +
   (n % 2 === 1 ? '多数决' : '平票维持');
 }
}

// Update vote status display with live backtest results
function updateBenchmarkVoteStatus(details) {
 var el = $id('benchmark-vote-status');
 if (!el) return; // locked — benchmark panel replaced
 if (!details || !details.per_index) {
  el.innerHTML = '等待回测';
  return;
 }
 var lines = [];
 var abstainCodes = [];
 for (var code in details.per_index) {
  var v = details.per_index[code];
  var name = '';
  BENCHMARK_CHOICES.forEach(function(b) { if (b.code === code) name = b.name; });
  if (v.vote === 'abstain') {
   abstainCodes.push(name);
   continue;
  }
  var icon = v.vote === 'bull' ? '🐂' : '🐻';
  var dir = v.rising ? '↑' : (v.above ? '→' : '↓');
  lines.push('<span style="margin-right:10px;">' + icon + ' ' + name + ' ' + dir + '</span>');
 }
 // Append abstained indices at the end
 if (abstainCodes.length > 0) {
  lines.push('<span style="margin-right:10px;color:var(--text-dim);">— ' + abstainCodes.join('/') + '</span>');
 }
 el.innerHTML = lines.join('') +
  ' <span style="color:var(--accent-light);font-weight:600;">bull ' +
  details.bull_votes + '/' + details.total_voting + '</span>' +
  (abstainCodes.length > 0 ? ' <span style="color:var(--warning);">弃权 ' + abstainCodes.length + '</span>' : '');
}

// Called at page init
initBenchmarkPanel();
// ── End voting committee ──────────────────────────────────────────

function getWeightTotal() {
 return parseInt($id('w1').value) + parseInt($id('w3').value) + parseInt($id('w7').value);
}

function validateRunInputs() {
 var reasons = [];
 var bull = parseFloat($id('ma_bull_pos').value);
 var bear = parseFloat($id('ma_bear_pos').value);
 var total = getWeightTotal();
 if (total !== 100) reasons.push('因子权重合计必须为100%');
 if (UNIVERSE_OPTIONS.length && UNIVERSE_SELECTED.size < 6) reasons.push('标的池至少选择6支ETF');
 if (bull <= bear) reasons.push('Bull仓位必须大于Bear仓位');

 var invalid = reasons.length > 0;
 var btn = $id('btn-run');
 if (btn) {
  btn.disabled = invalid;
  btn.style.opacity = invalid ? '0.4' : '1';
  btn.style.cursor = invalid ? 'not-allowed' : 'pointer';
  btn.title = reasons.join('；');
 }
 var bearLabel = $id('v-ma-bear');
 if (bearLabel) {
  bearLabel.style.color = bull <= bear ? TC.negative : '';
  bearLabel.title = bull <= bear ? 'Bear仓位不能 >= Bull仓位' : '';
 }
 renderParamSchemaTable();
 return !invalid;
}

function validateBullBear() {
 return validateRunInputs();
}

function syncWeights() {
 $id('v-w1').textContent = $id('w1').value + '%';
 var _vw3 = $id('v-w3'); if (_vw3) _vw3.textContent = $id('w3').value + '%';
 $id('v-w7').textContent = $id('w7').value + '%';
 var total = getWeightTotal();
 var te = $id('v-total');
 te.textContent = total + '%';
 te.style.color = total === 100 ? TC.positive : TC.negative;
 validateRunInputs();
}

function preventTrackJump(e) {
 // Only block clicks on the track, not drags on the thumb
 if (e.target.type === 'range') return true;
 e.preventDefault();
 return false;
}

function nudgeDate(which, days) {
 var slider = $id(which + '_date_slider');
 var val = parseInt(slider.value) + days;
 var lo = parseInt(slider.min), hi = parseInt(slider.max);
 slider.value = Math.max(lo, Math.min(hi, val));
 // Ensure start <= end
 var sVal = parseInt($id('start_date_slider').value);
 var eVal = parseInt($id('end_date_slider').value);
 if (sVal > eVal) {
  if (which === 'start') $id('end_date_slider').value = sVal;
  else $id('start_date_slider').value = eVal;
 }
 onPeriodSliderChange(null);
}



/* ====== Period sliders ====== */
var PERIOD_SPAN = 1; // years; 1, 3, 6 or 0 (none = from 2020-01-01)

function periodMaxDays() {
 if (PERIOD_SPAN === 0) {
  // "None" state: full range from 2020-01-01 to today
  var origin = new Date('2020-01-01');
  origin.setHours(0, 0, 0, 0);
  var now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((now - origin) / 86400000);
 }
 return PERIOD_SPAN * 365;
}

function setPeriodSpan(years) {
 // Clicking the already-active button → toggle to "none" (0)
 if (years === PERIOD_SPAN && years !== 0) {
  years = 0;
 }
 // Save current dates BEFORE changing PERIOD_SPAN (so getDateFromSlider uses old MAX)
 var oldStart = getDateFromSlider(parseInt($id('start_date_slider').value));
 var oldEnd = getDateFromSlider(parseInt($id('end_date_slider').value));
 PERIOD_SPAN = years;
 var MAX = periodMaxDays();
 // Update button states (none active when years === 0)
 $id('span-btn-1').classList.toggle('active', years === 1);
 $id('span-btn-3').classList.toggle('active', years === 3);
 $id('span-btn-6').classList.toggle('active', years === 6);
 // Update slider max
 $id('start_date_slider').max = MAX;
 $id('end_date_slider').max = MAX;
 if (years === 0) {
  // "None" state: start at 2020-01-01, end at today
  $id('start_date_slider').value = 0;
  $id('end_date_slider').value = MAX;
 } else {
  // Clamp preserved dates to new range
  var now = new Date(); now.setHours(0,0,0,0);
  var minDate = new Date(now); minDate.setDate(minDate.getDate() - MAX);
  var startD = new Date(oldStart); var endD = new Date(oldEnd);
  if (startD < minDate) startD = new Date(minDate);
  if (endD < minDate) endD = new Date(minDate);
  $id('start_date_slider').value = getSliderFromDate(startD.toISOString().slice(0,10));
  $id('end_date_slider').value = getSliderFromDate(endD.toISOString().slice(0,10));
 }
 onPeriodSliderChange(null);
}

// Convert slider value (0..MAX, days from "SPAN years ago") to YYYY-MM-DD
function getDateFromSlider(val) {
 var n = parseInt(val);
 var MAX = periodMaxDays();
 if (isNaN(n)) n = MAX;
 var origin = new Date();
 origin.setHours(0, 0, 0, 0);
 origin.setDate(origin.getDate() - (MAX - n));
 var y = origin.getFullYear();
 var m = String(origin.getMonth() + 1).padStart(2, '0');
 var d = String(origin.getDate()).padStart(2, '0');
 return y + '-' + m + '-' + d;
}

function onPeriodSliderChange(evt) {
 var startSlider = $id('start_date_slider');
 var endSlider = $id('end_date_slider');
 var s = parseInt(startSlider.value);
 var e = parseInt(endSlider.value);
 var MAX = periodMaxDays();
 // Enforce start ≤ end - 30 (at least 30 days)
 if (s > e - 30) {
  var fromEnd = evt && evt.target === endSlider;
  if (fromEnd) {
   s = Math.max(0, e - 30);
   startSlider.value = s;
  } else {
   e = Math.min(MAX, s + 30);
   endSlider.value = e;
  }
 }
 // z-index: bring last-touched handle to front
 if (evt && evt.target === startSlider) { startSlider.style.zIndex = 3; endSlider.style.zIndex = 2; }
 else if (evt && evt.target === endSlider) { endSlider.style.zIndex = 3; startSlider.style.zIndex = 2; }

 // Fill bar
 var fill = $id('period-fill');
 if (fill) {
  var leftPct = s / MAX * 100;
  var rightPct = e / MAX * 100;
  fill.style.left = leftPct + '%';
  fill.style.width = (rightPct - leftPct) + '%';
 }

 $id('v-start-date').textContent = getDateFromSlider(s);
 $id('v-end-date').textContent = getDateFromSlider(e);
 var spanDays = e - s;
 var spanLabel;
 if (spanDays >= 365) spanLabel = (spanDays / 365).toFixed(1) + ' 年';
 else if (spanDays >= 30) spanLabel = Math.round(spanDays / 30) + ' 个月';
 else spanLabel = spanDays + ' 天';
 $id('v-period-span').textContent = spanLabel;
}

function getParams() {
 return {
  // Active parameters (from UI controls)
  w1: parseInt($id('w1').value),
  w3: parseInt($id('w3').value),
  w7: parseInt($id('w7').value),
  conf_type: (LOCKED_PARAMS_CACHE.conf_type || {}).value || confType,
  ma_trend_period: parseInt($id('ma_trend_period').value),
  ma_bull_pos: parseFloat($id('ma_bull_pos').value),
  ma_bear_pos: parseFloat($id('ma_bear_pos').value),
  ma_direction_confirm: maDirConfirm,
  benchmarks: (LOCKED_PARAMS_CACHE.benchmarks || {}).value || selectedBenchmarks,
  max_holdings: parseInt($id('max_holdings').value),
  signal_steps: (LOCKED_PARAMS_CACHE.signal_steps || {}).value || 40,
  top_boost: parseInt($id('top_boost').value),
  concentration: parseFloat($id('concentration').value),
  c_sensitivity: parseFloat($id('c_sensitivity').value),
  f1_ema_period: parseInt($id('f1_ema_period').value),
  f3_vol_window: parseInt($id('f3_vol_window').value),
  f1_sensitivity: parseFloat($id('f1_sensitivity').value),
  f3_sensitivity: parseFloat($id('f3_sensitivity').value),
  rebalance_freq: (LOCKED_PARAMS_CACHE.rebalance_freq || {}).value || rebalanceFreq,
  f1_active_days: f1ActiveDays,
  band: parseFloat($id('band').value), // 0-20，单位%
  band_sensitivity: parseInt($id('band_sensitivity').value), // 0-100
  f7_up_power: parseFloat($id('f7_up_power').value),
  f7_up_span: parseFloat($id('f7_up_span').value),
  f7_down_power: parseFloat($id('f7_down_power').value),
  f7_down_span: parseFloat($id('f7_down_span').value),
  f7_window: parseInt($id('f7_window').value),
  f7_lookback: (LOCKED_PARAMS_CACHE.f7_lookback || {}).value || 250,
  start_date: getDateFromSlider($id('start_date_slider').value),
  end_date: getDateFromSlider($id('end_date_slider').value),
  universe: getUniverseParam(),
  debug: !!$id('chk-debug').checked,
 };
}

function setSlider(id, val) {
 var el = $id(id);
 if (!el) return;
 var s = el.getAttribute('step');
 if (s && s !== 'any') { el.setAttribute('data-step', s); }
 el.step = 'any'; // allow any value (default step=1 would lock to extremes)
 el.value = val;
}

// Global: snap slider to data-step on user interaction (not on programmatic set)
document.addEventListener('input', function(e) {
 var el = e.target;
 if (el.type !== 'range') return;
 var ds = el.getAttribute('data-step');
 if (!ds) return;
 var step = parseFloat(ds);
 if (isNaN(step) || step <= 0) return;
 var v = parseFloat(el.value);
 var snapped = Math.round(v / step) * step;
 var lo = parseFloat(el.min), hi = parseFloat(el.max);
 if (!isNaN(lo)) snapped = Math.max(lo, snapped);
 if (!isNaN(hi)) snapped = Math.min(hi, snapped);
 if (snapped !== v) {
  el.value = snapped;
  el.dispatchEvent(new Event('input', { bubbles: false }));
 }
}, true);

function setParams(p) { console.log('setParams called with keys:', Object.keys(p||{}).slice(0,5));
 // Active parameters — set UI controls
 if (p.w1 != null) { setSlider('w1', p.w1); }
 if (p.w3 != null) { setSlider('w3', p.w3); }
 if (p.w7 != null) { setSlider('w7', p.w7); }
 syncWeights();
 if (p.ma_trend_period != null) { setSlider('ma_trend_period', p.ma_trend_period); $id('v-ma-period').textContent = p.ma_trend_period + ' 周'; }
 if (p.ma_bull_pos != null) { setSlider('ma_bull_pos', p.ma_bull_pos); $id('v-ma-bull').textContent = Math.round(p.ma_bull_pos * 100) + '%'; }
 if (p.ma_bear_pos != null) { setSlider('ma_bear_pos', p.ma_bear_pos); $id('v-ma-bear').textContent = Math.round(p.ma_bear_pos * 100) + '%'; }
 if (p.ma_direction_confirm != null) { setMaDirConfirm(!!p.ma_direction_confirm); }
 var lb = (LOCKED_PARAMS_CACHE.benchmarks || {}).value;
 if (lb != null) { selectedBenchmarks = Array.isArray(lb) ? lb.slice() : [lb]; }
 else if (p.benchmarks != null && Array.isArray(p.benchmarks) && p.benchmarks.length > 0) {
  selectedBenchmarks = p.benchmarks.slice();
 }
 if (p.f1_ema_period != null) { setSlider('f1_ema_period', p.f1_ema_period); $id('v-f1-ema').textContent = p.f1_ema_period + ' 周'; }
 if (p.f3_vol_window != null) { setSlider('f3_vol_window', p.f3_vol_window); var _vv = $id('v-vol'); if (_vv) _vv.textContent = p.f3_vol_window + ' 日'; }
 if (p.f1_active_days != null) { setF1ActiveDays(p.f1_active_days); }
 if (p.f1_sensitivity != null) { setSlider('f1_sensitivity', p.f1_sensitivity); $id('v-f1s').textContent = parseFloat(p.f1_sensitivity).toFixed(1); }
 if (p.f3_sensitivity != null) { setSlider('f3_sensitivity', p.f3_sensitivity); var _vf = $id('v-f3s'); if (_vf) _vf.textContent = parseFloat(p.f3_sensitivity).toFixed(1); }
 if (p.band != null) { setSlider('band', p.band); $id('v-band').textContent = (p.band * 100).toFixed(1) + '%'; }
 if (p.band_sensitivity != null) { setSlider('band_sensitivity', p.band_sensitivity); $id('v-band-sensitivity').textContent = p.band_sensitivity; }
 if (p.f7_up_power != null) { setSlider('f7_up_power', p.f7_up_power); $id('v-f7-up-power').textContent = p.f7_up_power; }
 if (p.f7_up_span != null) { setSlider('f7_up_span', p.f7_up_span); $id('v-f7-up-span').textContent = parseFloat(p.f7_up_span).toFixed(2); }
  if (p.f7_down_power != null) { setSlider('f7_down_power', p.f7_down_power); $id('v-f7-down-power').textContent = p.f7_down_power; }
  if (p.f7_down_span != null) { setSlider('f7_down_span', p.f7_down_span); $id('v-f7-down-span').textContent = parseFloat(p.f7_down_span).toFixed(2); }
 if (p.f7_window != null) { setSlider('f7_window', p.f7_window); $id('v-f7-window').textContent = p.f7_window + ' 日'; }
 // locked params always use locked value, not preset value
 var lf7 = (LOCKED_PARAMS_CACHE.f7_lookback || {}).value; if (lf7 != null) { setSlider('f7_lookback', lf7); $id('v-f7-lookback').textContent = lf7 + ' 日'; }
 var lfreq = (LOCKED_PARAMS_CACHE.rebalance_freq || {}).value; if (lfreq) { setFreq(lfreq); }
 var ln = (LOCKED_PARAMS_CACHE.signal_steps || {}).value; if (ln != null) { setSlider('signal_steps', ln); $id('v-steps').textContent = ln; }
 var lct = (LOCKED_PARAMS_CACHE.conf_type || {}).value; if (lct) setConfType(lct);
 if (p.top_boost != null) { setSlider('top_boost', p.top_boost); $id('v-boost').textContent = p.top_boost; }
 if (p.concentration != null) { setSlider('concentration', p.concentration); $id('v-conc').textContent = p.concentration.toFixed(2); }
 if (p.c_sensitivity != null) { setSlider('c_sensitivity', p.c_sensitivity); $id('v-csens').textContent = p.c_sensitivity.toFixed(1); }
 if (p.max_holdings != null) { setSlider('max_holdings', p.max_holdings); $id('v-maxh').textContent = p.max_holdings + ' 支'; }
 if (p.universe != null) setUniverseFromParam(p.universe);
 validateRunInputs();
}

/* ====== Confidence function chart ====== */
function confVal(type, score, dz, fz) {
 if (score < dz) return 0;
 if (score >= fz) return 1;
 var t = (score - dz) / (fz - dz);
 if (type === 'quadratic') return t * t;
 return t; // linear fallback
}

function drawConfChart() {
 // ma_trend mode: no confidence curve to draw (binary signal)
 // Chart element kept for potential future visualization
}

/* ====== Strategy presets ====== */
var PRESETS = {};
var CURRENT_PRESET = null; // currently selected preset key (or 'cst-1')
var snapshotSort = { col: 'z', dir: 'desc' }; // default sort by dispersion descending
var PARAM_SCHEMA = null;

/* ====== Universe selector ====== */
var UNIVERSE_OPTIONS = []; // [{code, name, sector}]
var UNIVERSE_SELECTED = new Set(); // set of selected ETF codes

function escapeHtml(s) {
 return String(s == null ? '' : s)
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;');
}

function updateParamSchemaBadge() {
 var el = $id('param-schema-badge');
 if (!el) return;
 if (!PARAM_SCHEMA || !PARAM_SCHEMA.groups) {
  el.textContent = 'schema: unavailable';
  el.style.color = TC.warning;
  return;
 }
 var count = 0;
 PARAM_SCHEMA.groups.forEach(function(g) { count += (g.params || []).length; });
 el.textContent = 'schema v' + PARAM_SCHEMA.version + ' · ' + count + ' params';
 el.style.color = TC.accentLight;
}

function schemaEngineValue(param, uiValue) {
 if (uiValue == null || uiValue === '') return '';
 if (param.unit === 'ui_percent_to_ratio') return (parseFloat(uiValue) / 100).toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
 if (param.unit === 'ui_x10_to_raw') return (parseFloat(uiValue) / 10).toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
 if (param.unit === 'ui_percent' && /^w\d/.test(param.key)) return (parseFloat(uiValue) / 100).toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
 if (param.key === 'universe') return uiValue ? uiValue : '(all)';
 return uiValue;
}

function focusParamControl(key) {
 var target = null;
 if (key === 'universe') {
  target = $id('universe-head');
 } else if (key === 'conf_type') {
  target = $id('tuner-sec-confidence');
 } else if (key === 'max_holdings') {
  target = $id('param-mh') || $id('tuner-sec-position');
 } else {
  target = $id(key) || $id('param-' + key);
 }
 if (!target) return;
 target.scrollIntoView({ behavior: 'smooth', block: 'center' });
 target.classList.add('param-focus-flash');
 setTimeout(function() { target.classList.remove('param-focus-flash'); }, 1200);
}

function renderParamSchemaTable() {
 var el = $id('param-schema-table');
 if (!el) return;
 if (!PARAM_SCHEMA || !PARAM_SCHEMA.groups) {
  el.innerHTML = '<p style="color:var(--warning);">参数契约不可用；Tuner 主流程仍可继续使用。</p>';
  return;
 }
 var current = {};
 try { current = getParams(); } catch(e) { current = {}; }
 var html = '';
 PARAM_SCHEMA.groups.forEach(function(group) {
  html += '<div class="schema-group">';
  html += '<div class="schema-group-title">' + escapeHtml(group.label || group.key) + '</div>';
  html += '<table class="schema-table"><thead><tr><th style="width:15%;">UI key</th><th style="width:18%;">名称</th><th style="width:15%;">单位</th><th style="width:14%;">当前UI</th><th style="width:14%;">后端值</th><th>后端路径</th></tr></thead><tbody>';
  (group.params || []).forEach(function(p) {
   var uiValue = current[p.key];
   var engineValue = schemaEngineValue(p, uiValue);
   html += '<tr data-param-key="' + escapeHtml(p.key) + '">' +
    '<td><code>' + escapeHtml(p.key) + '</code></td>' +
    '<td>' + escapeHtml(p.label) + '</td>' +
    '<td><span class="schema-unit">' + escapeHtml(p.unit) + '</span></td>' +
    '<td><span class="schema-ui-val">' + escapeHtml(uiValue == null ? '' : uiValue) + '</span></td>' +
    '<td><span class="schema-engine-val">' + escapeHtml(engineValue) + '</span></td>' +
    '<td><code>' + escapeHtml(p.engine_path) + '</code></td>' +
    '</tr>';
  });
  html += '</tbody></table></div>';
 });
 el.innerHTML = html;
}

var LOCKED_PARAMS_CACHE = {};

function cacheLockedParams() {
 // Populate locked values + reasons from schema — single source of truth
 LOCKED_PARAMS_CACHE = {};
 if (!PARAM_SCHEMA || !PARAM_SCHEMA.groups) return;
 PARAM_SCHEMA.groups.forEach(function(g) {
  g.params.forEach(function(p) {
   if (p.locked) LOCKED_PARAMS_CACHE[p.key] = {value: p.locked_value, reason: p.lock_reason || ''};
  });
 });
 applyLockedStyles();
}

function applyLockedStyles() {
 // Two lock modes based on schema unit: enum=hide btn-row, slider=disable input
 Object.keys(LOCKED_PARAMS_CACHE).forEach(function(key) {
  var info = LOCKED_PARAMS_CACHE[key];
  var container = document.getElementById('param-' + key.replace(/_/g, '-'));
  if (!container) return;

  var lockIcon = document.createElement('span');
  lockIcon.className = 'lock-icon';
  lockIcon.setAttribute('data-tip', info.reason);
  lockIcon.textContent = '🔒';

  // Determine lock mode from schema
  var isEnum = false;
  if (PARAM_SCHEMA && PARAM_SCHEMA.groups) {
   PARAM_SCHEMA.groups.forEach(function(g) {
    g.params.forEach(function(p) { if (p.key === key && p.unit === 'enum') isEnum = true; });
   });
  }

  container.classList.add('locked');

  if (isEnum) {
   // Enum lock: keep DOM, hide btn-row + status line
   var btnRow = container.querySelector('.btn-row');
   if (btnRow) btnRow.style.display = 'none';
   var statusEl = container.querySelector('#benchmark-vote-status');
   if (statusEl) statusEl.style.display = 'none';
  } else {
   // Slider lock: disable input
   var input = container.querySelector('input[type=range]');
   if (input) input.disabled = true;
  }

  // Attach lock icon after param name
  var nameEl = container.querySelector('.slider-label .name');
  if (nameEl) {
   var wrapper = document.createElement('span');
   nameEl.parentNode.insertBefore(wrapper, nameEl);
   wrapper.appendChild(nameEl);
   wrapper.appendChild(lockIcon);
  }
 });
}

async function loadParamSchema() {
 try {
  var resp = await fetch('/api/param_schema');
  PARAM_SCHEMA = await resp.json();
 } catch(e) {
  console.warn('Failed to load param schema:', e);
  PARAM_SCHEMA = null;
 }
 updateParamSchemaBadge();
 cacheLockedParams();
 renderParamSchemaTable();
}

async function loadPresets() {
 try {
  var resp = await fetch('/api/presets');
  PRESETS = await resp.json();
  // Extract universe options (top-level key in response)
  if (PRESETS._universe_options) {
   UNIVERSE_OPTIONS = PRESETS._universe_options;
   delete PRESETS._universe_options;
  }
  initUniverseSelector();
  renderPresetCards();
 } catch(e) { console.warn('Failed to load presets:', e); }
}

/* ── Centralized color table (sync with :root CSS variables) ── */
var TC = {
 accent:    '#3b82f6',
 accentLight: '#60a5fa',
 positive:   '#10b981',
 negative:   '#ef4444',
 warning:   '#f59e0b',
 highlight:  '#fbbf24',
 textHeading: '#f0f0f0',
 textBody:   '#e0e0e0',
 textSecondary:'#94a3b8',
 textMuted:  '#6b7280',
 textDim:   '#4b5563',
 bgHover:   '#1e293b',
 bgActive:   '#1e3050',
 bgInput:   '#1e2d3d',
 bgPanel:   '#1a2332',
 bgBody:    '#0f1419',
 border:    '#2a3a4a',
 borderLight: '#1e293b',
 borderMuted: '#334155',
 chartNav:   '#3b82f6',
 chartBench:  '#f59e0b',
 chartEqWt:  '#8b5cf6',
 chartMdd:   '#ef4444',
 cyan:     '#06b6d4',
 purple:    '#a855f7',
 pink:     '#f472b6',
 orange:    '#f97316',
 gold:     '#d97706',
 greenLight:  '#22c55e',
 greenDark:  '#16a34a',
 redDark:   '#dc2626',
 blueDark:   '#2563eb',
 blueDarker:  '#1d4ed8',
 schoolGambler: '#f97316',
 schoolZen:   '#14b8a6',
 schoolActuary: '#6366f1',
};

var FRONTIER = null;       // loaded from /api/frontier (metrics for preset cards)

var SCHOOLS = [
 {id:'mh_ar', name:'赌徒', target:'max AR', constraint:'gam-0~2 (MH=2~4)', presets:['gam-0','gam-1','gam-2'], def:'gam-0'},
 {id:'mh_sortino', name:'禅修者', target:'max Sortino', constraint:'zen-0~4 (MH=2~6) · zen-nl 无杠杆', presets:['zen-nl','zen-0','zen-1','zen-2','zen-3','zen-4'], def:'zen-nl'},
 {id:'mh_calmar', name:'精算师', target:'max Calmar', constraint:'act-0~4 (MH=2~6)', presets:['act-0','act-1','act-2','act-3','act-4'], def:'gam-0'},
 {id:'custom', name:'自定义', target:'自定义', constraint:'—', presets:['cst-1'], def:'cst-1'},
];

var SELECTED = null;       // {type:'frontier'|'preset', schoolId?, idx?, name?}

// ── Clear all selection state across entire page ──
function clearSelection() {
 SELECTED = null;
 document.querySelectorAll('.preset-card').forEach(function(c) { c.classList.remove('active'); });
}

/* ── Frontier loader ── */
/* ── Apply a frontier point ── */
function applyFrontierPoint(schoolId, idx) {
 var pts = FRONTIER[schoolId].points;
 if (!pts || idx < 0 || idx >= pts.length) return;
 var pt = pts[idx];
 SELECTED = { type: 'frontier', schoolId: schoolId, idx: idx };
 document.querySelectorAll('.preset-card').forEach(function(c) { c.classList.remove('active'); });
 if (pt.preset) {
  // New: preset-based point — delegate to applyPreset
  applyPreset(pt.preset);
  SELECTED = { type: 'frontier', schoolId: schoolId, idx: idx }; // restore after applyPreset overwrites
 } else if (pt.params) {
  // Legacy: inline params
  setParams(pt.params);
  CURRENT_PRESET = 'frontier';
 }
 // Re-render all charts: only the selected school shows gold highlight
 Object.keys(FRONTIER_CHARTS).forEach(function(sid) {
  renderFrontierChart(sid, sid === schoolId ? idx : -1);
 });
}

/* ── Render frontier scatter chart (click-to-select, minimal) ── */
function renderPresetCards() {
 var container = $id('preset-cards');
 if (!container) return;
 container.innerHTML = '';

 // Fetch frontier data for metrics display
 var fdata = null;
 try { fdata = FRONTIER; } catch(e) {}

 SCHOOLS.forEach(function(school) {
  var group = document.createElement('div');
  group.className = 'school-group';
  var sid = school.id;

  // Foldable header
  var hdrRow = document.createElement('div');
  hdrRow.className = 'school-row';
  var hdr = document.createElement('div');
  hdr.className = 'school-header';
  var sub = school.target + (school.constraint && school.constraint !== '—' ? ', ' + school.constraint : '');
  hdrRow.style.cssText = 'cursor:pointer; display:flex; align-items:center; gap:6px; padding:2px 4px; border-radius:3px; transition:background 0.15s;';
  hdrRow.title = '点击展开/折叠预设列表';
  hdr.style.flex = '1'; hdr.style.width = 'auto';
  hdr.innerHTML = school.name + ' <span style="font-weight:400;color:#64748b;font-size:10px">max ' + sub + '</span>';
  var arrow = document.createElement('span');
  arrow.style.cssText = 'font-size:10px; color:#64748b; transition:transform 0.2s ease; flex-shrink:0;';
  arrow.textContent = '◀';
  hdrRow.appendChild(hdr); hdrRow.appendChild(arrow);
  hdrRow.addEventListener('mouseenter', function() { hdrRow.style.background = TC.bgHover; });
  hdrRow.addEventListener('mouseleave', function() { hdrRow.style.background = 'transparent'; });
  group.appendChild(hdrRow);

  // Foldable card area — small cards with metrics below
  var wrap = document.createElement('div');
  wrap.id = 'preset-wrap-' + sid;
  wrap.style.cssText = 'max-height:0; overflow:hidden; transition:max-height 0.25s ease;';

  var cardRow = document.createElement('div');
  cardRow.style.cssText = 'display:flex; flex-wrap:wrap; gap:3px; margin-top:3px;';

  // Get school metrics from FRONTIER
  var sf = FRONTIER && FRONTIER[sid];
  var pts = (sf && sf.points) ? sf.points : [];
  // Metric label per school
  var metricLabel = sid === 'mh_sortino' ? 'Sortino' : sid === 'mh_calmar' ? 'Calmar' : 'AR';

  school.presets.forEach(function(key) {
   var p = PRESETS[key];
      if (!p) return;

   var label = ((p.label || key).replace(/（推荐）/, ''));
   var short = label.match(/\d+$/);
   label = short ? short[0] : label;

   // Find metrics
   var ar = '—', mdd = '—', sortino = '—', calmar = '—';
   for (var pi = 0; pi < pts.length; pi++) {
    if (pts[pi].preset === key) {
     ar = (pts[pi].ar_6y != null ? pts[pi].ar_6y.toFixed(0) + '%' : '—');
     mdd = (pts[pi].mdd != null ? pts[pi].mdd.toFixed(0) + '%' : '—');
     sortino = (pts[pi].sortino != null ? pts[pi].sortino.toFixed(2) : '—');
     calmar = (pts[pi].calmar != null ? pts[pi].calmar.toFixed(1) : '—');
     break;
    }
   }
   var metricVal = sid === 'mh_sortino' ? sortino : sid === 'mh_calmar' ? calmar : ar;

   var wrapDiv = document.createElement('div');
   wrapDiv.style.cssText = 'display:flex;flex-direction:column;align-items:center;gap:1px;';

   var card = document.createElement('div');
   card.className = 'preset-card';
   card.id = 'preset-' + key;
   card.style.cssText = 'cursor:pointer;width:26px;height:26px;display:flex;align-items:center;justify-content:center;border-radius:3px;background:var(--bg-panel);border:1px solid var(--border);font-size:12px;font-weight:700;color:var(--text-body);';
   card.textContent = label;
   card.onclick = (function(k) { return function() { applyPreset(k); pulseBtn(); }; })(key);

   var sel = SELECTED && (SELECTED.name === key || (SELECTED.type === 'preset' && SELECTED.name === key));
   if (sel) card.style.borderColor = 'var(--accent)';
   var metric = document.createElement('div');
   metric.style.cssText = 'font-size:8px;color:var(--text-muted);text-align:center;line-height:1;';
   metric.textContent = metricVal;

   wrapDiv.appendChild(card);
   wrapDiv.appendChild(metric);
   cardRow.appendChild(wrapDiv);
  });

  wrap.appendChild(cardRow);
  group.appendChild(wrap);
  container.appendChild(group);

  // Toggle fold
  hdrRow.onclick = function() {
   var w = $id('preset-wrap-' + sid);
   var isOpen = w.style.maxHeight !== '0px' && w.style.maxHeight !== '';
   if (!isOpen) {
    w.style.maxHeight = '80px';
    arrow.textContent = '▼';
   } else {
    w.style.maxHeight = '0';
    arrow.textContent = '◀';
   }
  };
 });

 if (!window._presetsFirstLoad && PRESETS && PRESETS['gam-0']) {
  window._presetsFirstLoad = true;
 }
}

async function loadFrontier() {
 try {
  var resp = await fetch('/api/frontier');
  if (!resp.ok) throw new Error(resp.status);
  FRONTIER = await resp.json();
 } catch(e) { FRONTIER = null; }
 renderPresetCards();
}

// ── (placeholder for removed renderFrontierChart) ──
function renderFrontierChart() {} // no-op stub, kept for backward compat

// Render skeleton immediately on page load (before API responds)
renderPresetCards();

function pulseBtn() {
 var btn = $id('btn-run');
 if (!btn) return;
 btn.classList.remove('pulse');
 void btn.offsetWidth;
 btn.classList.add('pulse');
}

function applyPreset(name) {
 // Frontier mode: params applied by applyFrontierPoint()
 if (name === 'frontier') {
  CURRENT_PRESET = 'frontier';
  document.querySelectorAll('.preset-card').forEach(function(c) { c.classList.remove('active'); });
  renderParamSchemaTable();
  return;
 }
 // Clear all frontier selections when switching to a preset
 clearSelection();
 SELECTED = { type: 'preset', name: name };
 // Re-render charts so reference diamond gets yellow highlight

 CURRENT_PRESET = name;
 // Update card highlight (inline borderColor used by new card design)
 document.querySelectorAll('.preset-card').forEach(function(c) { c.style.borderColor = 'var(--border)'; });
 var card = $id('preset-' + name);
 if (card) card.style.borderColor = 'var(--accent)';
 if (PRESETS[name]) {
  setParams(PRESETS[name]);
 }
 renderParamSchemaTable();
}

/* ====== Universe selector logic ====== */
function initUniverseSelector() {
 // Default: only active ETFs selected
 UNIVERSE_SELECTED = new Set(UNIVERSE_OPTIONS.filter(function(e) { return e.active !== false; }).map(function(e) { return e.code; }));
 renderUniverseSectors();
 updateUniverseCount();
}

function renderUniverseSectors() {
 var container = $id('universe-sectors');
 if (!container || !UNIVERSE_OPTIONS.length) return;
 container.innerHTML = '';
 // Group by sector
 var sectors = {};
 UNIVERSE_OPTIONS.forEach(function(e) {
  var s = e.sector || '其他';
  if (!sectors[s]) sectors[s] = [];
  sectors[s].push(e);
 });
 Object.keys(sectors).forEach(function(sector) {
  var etfs = sectors[sector];
  var allSelected = etfs.every(function(e) { return UNIVERSE_SELECTED.has(e.code); });
  var div = document.createElement('div');
  div.className = 'u-sector';
  // Sector header with checkbox
  var head = document.createElement('div');
  head.className = 'u-sector-head';
  var cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = allSelected;
  cb.id = 'usec-' + sector;
  cb.onchange = function() { toggleSector(sector, this.checked); };
  var lbl = document.createElement('label');
  lbl.htmlFor = 'usec-' + sector;
  lbl.textContent = sector + ' (' + etfs.length + ')';
  head.appendChild(cb);
  head.appendChild(lbl);
  div.appendChild(head);
  // ETF chips
  var chips = document.createElement('div');
  chips.className = 'u-chips';
  etfs.forEach(function(e) {
   var chip = document.createElement('span');
   chip.className = 'u-chip' + (UNIVERSE_SELECTED.has(e.code) ? ' active' : '');
   chip.dataset.code = e.code;
   chip.textContent = e.name.replace(/ETF$/, '');
   chip.onclick = function() { toggleEtf(e.code); };
   chips.appendChild(chip);
  });
  div.appendChild(chips);
  container.appendChild(div);
 });
}

function toggleUniverse() {
 var head = $id('universe-head');
 var body = $id('universe-body');
 head.classList.toggle('open');
 body.classList.toggle('open');
}

function toggleEtf(code) {
 if (UNIVERSE_SELECTED.has(code)) {
  UNIVERSE_SELECTED.delete(code);
 } else {
  UNIVERSE_SELECTED.add(code);
 }
 updateUniverseUI();
}

function toggleSector(sector, checked) {
 UNIVERSE_OPTIONS.forEach(function(e) {
  if ((e.sector || '其他') === sector) {
   if (checked) UNIVERSE_SELECTED.add(e.code);
   else UNIVERSE_SELECTED.delete(e.code);
  }
 });
 updateUniverseUI();
}

function universeSelectAll() {
 UNIVERSE_OPTIONS.forEach(function(e) { UNIVERSE_SELECTED.add(e.code); });
 updateUniverseUI();
}

function universeSelectNone() {
 UNIVERSE_SELECTED.clear();
 updateUniverseUI();
}

function universeSelectInverse() {
 UNIVERSE_OPTIONS.forEach(function(e) {
  if (UNIVERSE_SELECTED.has(e.code)) UNIVERSE_SELECTED.delete(e.code);
  else UNIVERSE_SELECTED.add(e.code);
 });
 updateUniverseUI();
}

async function saveUniverse() {
 var msg = document.getElementById('universe-save-msg');
 var activeCodes = Array.from(UNIVERSE_SELECTED);
 if (activeCodes.length === 0) {
  msg.textContent = '至少勾选一支 ETF';
  msg.style.color = '#e74c3c';
  return;
 }
 try {
  var resp = await fetch('/api/universe/save', {
   method: 'POST',
   headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({active_codes: activeCodes})
  });
  var data = await resp.json();
  if (data.error) {
   msg.textContent = '保存失败: ' + data.error;
   msg.style.color = '#e74c3c';
  } else {
   // Update UNIVERSE_OPTIONS active flags to reflect saved state
   var savedSet = new Set(activeCodes);
   UNIVERSE_OPTIONS.forEach(function(e) { e.active = savedSet.has(e.code); });
   msg.textContent = '已保存：' + data.active + ' 支活跃，' + data.dormant + ' 支休眠';
   msg.style.color = '#27ae60';
   setTimeout(function() { msg.textContent = ''; }, 5000);
  }
 } catch(e) {
  msg.textContent = '请求失败: ' + e.message;
  msg.style.color = '#e74c3c';
 }
}

function updateUniverseUI() {
 // Update chips
 document.querySelectorAll('.u-chip').forEach(function(chip) {
  if (UNIVERSE_SELECTED.has(chip.dataset.code)) chip.classList.add('active');
  else chip.classList.remove('active');
 });
 // Update sector checkboxes
 var sectors = {};
 UNIVERSE_OPTIONS.forEach(function(e) {
  var s = e.sector || '其他';
  if (!sectors[s]) sectors[s] = [];
  sectors[s].push(e);
 });
 Object.keys(sectors).forEach(function(sector) {
  var cb = $id('usec-' + sector);
  if (cb) cb.checked = sectors[sector].every(function(e) { return UNIVERSE_SELECTED.has(e.code); });
 });
 updateUniverseCount();
}

function updateUniverseCount() {
 var total = UNIVERSE_OPTIONS.length;
 var selected = UNIVERSE_SELECTED.size;
 var el = $id('universe-count');
 if (el) {
  el.textContent = selected + '/' + total;
  el.classList.toggle('warn', selected < 6);
 }
 validateRunInputs();
 renderParamSchemaTable();
}

function getUniverseParam() {
 if (UNIVERSE_SELECTED.size === 0) return '__NONE__';
 return Array.from(UNIVERSE_SELECTED).sort().join(',');
}

function setUniverseFromParam(str) {
 if (str === '__NONE__') {
  UNIVERSE_SELECTED = new Set();
 } else if (!str) {
  // No param: default to active-only
  UNIVERSE_SELECTED = new Set(UNIVERSE_OPTIONS.filter(function(e) { return e.active !== false; }).map(function(e) { return e.code; }));
 } else {
  var codes = new Set(str.split(','));
  UNIVERSE_SELECTED = new Set();
  UNIVERSE_OPTIONS.forEach(function(e) { if (codes.has(e.code)) UNIVERSE_SELECTED.add(e.code); });
 }
 updateUniverseUI();
}

/* ====== Async Progress ====== */
var _progressTimer = null;

function _showProgress(label) {
 var wrap = $id('tuner-progress-wrap');
 $id('tuner-progress-bar').style.width = '0%';
 $id('tuner-progress-text').textContent = label + '… 0s';
 wrap.style.maxHeight = '40px';
 var start = Date.now();
 _progressTimer = setInterval(function() {
  var elapsed = Math.round((Date.now() - start) / 1000);
  $id('tuner-progress-text').textContent = label + '… ' + elapsed + 's';
 }, 1000);
}

function _updateProgress(pct) {
 $id('tuner-progress-bar').style.width = Math.min(pct, 99) + '%';
}

function _hideProgress() {
 if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }
 $id('tuner-progress-wrap').style.maxHeight = '0';
}

async function _pollTask(taskId, label, onDone) {
 _showProgress(label);
 var maxPct = 0;
 var lastMsg = '';
 var start = Date.now();
 while (true) {
  try {
   var resp = await fetch('/api/progress/' + taskId);
   var data = await resp.json();
   if (data.status === 'done') {
    _updateProgress(100);
    _hideProgress();
    if (onDone) onDone(data);
    return;
   }
   if (data.status === 'error') {
    _hideProgress();
    $id('status').textContent = '错误: ' + (data.message || 'unknown');
    return;
   }
   var pct = data.pct || 0;
   if (pct > maxPct) maxPct = pct;
   _updateProgress(maxPct);
   // Show phase label from backend
   var msg = data.message || '';
   if (msg && msg !== lastMsg) {
    lastMsg = msg;
    var elapsed = Math.round((Date.now() - start) / 1000);
    $id('tuner-progress-text').textContent = label + ' · ' + msg + ' … ' + elapsed + 's';
   }
  } catch(e) {}
  await new Promise(function(r) { setTimeout(r, 400); });
 }
}

/* ====== Refresh Data ====== */
async function refreshData() {
 var btn = $id('btn-refresh'), status = $id('data-status');
 if (btn.disabled) return;
 btn.disabled = true; btn.textContent = '拉取中...';
 status.textContent = '';
 _showProgress('拉取行情');
 try {
  var resp = await fetch('/api/refresh_data', { method:'POST' });
  var data = await resp.json();
  _hideProgress();
  var badge = '';
  if (data.status === 'confirmed') badge = '<span class="data-badge confirmed">confirmed</span>';
  else if (data.status === 'intraday') badge = '<span class="data-badge intraday">intraday</span>';
  else if (data.status === 'error') badge = '<span class="data-badge stale">error</span>';
  status.innerHTML = (data.message || 'done') + ' ' + badge;
 } catch(e) { _hideProgress(); status.innerHTML = '请求失败: ' + e.message + ' <span class="data-badge stale">error</span>'; }
 finally { btn.disabled = false; btn.textContent = '刷新数据'; }
}

async function refreshMetadata() {
 var btn = $id('btn-meta'), status = $id('data-status');
 if (btn.disabled) return;
 btn.disabled = true; btn.textContent = '拉取中...';
 status.textContent = '';
 _showProgress('拉取元数据');
 try {
  var resp = await fetch('/api/refresh_metadata', { method:'POST' });
  var data = await resp.json();
  _hideProgress();
  status.innerHTML = (data.message || 'done');
 } catch(e) { _hideProgress(); status.innerHTML = '元数据请求失败: ' + e.message; }
 finally { btn.disabled = false; btn.textContent = '刷新元数据'; }
}

/* ====== Backtest ====== */
async function runBacktest() {
 var btn = $id('btn-run'), status = $id('status');
 if (!validateRunInputs()) {
  status.textContent = '参数非法：' + (btn.title || '请检查参数');
  return;
 }
 if (btn.disabled) return;
 btn.disabled = true; btn.textContent = '回测中...';
 status.textContent = '';
 try {
  var resp = await fetch('/api/run?async=1', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(getParams()) });
  var init = await resp.json();
  if (init.error) { status.textContent = '错误: ' + init.error; return; }
  await _pollTask(init.task_id, '回测中', async function(taskData) {
   var rResp = await fetch('/api/result/' + init.task_id);
   var rData = await rResp.json();
   var data = rData.result;
   if (!data || data.error) { status.textContent = '错误: ' + (data ? data.error : 'unknown'); return; }
   lastResult = data;
   renderResults(data);
   switchRightView('results');
   status.textContent = '完成 ' + data.summary.startDate + ' → ' + data.summary.endDate + ' · 耗时 ' + taskData.elapsed + 's';
  });
 } catch(e) { status.textContent = '请求失败: ' + e.message; }
 finally { btn.textContent = '运行回测'; btn.disabled = false; validateRunInputs(); }
}

function flipSortinoSharpe() {
 var card = document.getElementById('metric-sortino-sharpe');
 if (card) card.classList.toggle('flipped');
}

function flipMetricsPage() {
 var page1 = $id('tuner-metrics-page1');
 var page2 = $id('tuner-metrics-page2');
 var arrowL = $id('metrics-arrow-left');
 var arrowR = $id('metrics-arrow-right');
 if (currentMetricsPage === 1) {
  page1.className = 'metrics metrics-page-hidden';
  page2.className = 'metrics metrics-page-hidden active';
  arrowL.style.visibility = 'visible';
  arrowR.style.visibility = 'hidden';
  currentMetricsPage = 2;
  if (distChart) distChart.resize();
 } else {
  page1.className = 'metrics metrics-page-active';
  page2.className = 'metrics metrics-page-hidden';
  arrowL.style.visibility = 'hidden';
  arrowR.style.visibility = 'visible';
  currentMetricsPage = 1;
  if (ddChart) ddChart.resize();
 }
}

function renderResults(data) {
 $id('results-placeholder').style.display = 'none';
 $id('results').style.display = 'block';
 $id('tuner-kline-section').style.display = 'block';
 var s = data.summary;
 $id('m-annual').textContent = (s.annualReturn>0?'+':'') + s.annualReturn + '%';
 $id('m-total').textContent = (s.totalReturn>0?'+':'') + s.totalReturn + '%';
 $id('m-dd').textContent = s.maxDrawdown + '%';
 $id('m-sortino').textContent = s.sortino.toFixed(2);
 $id('m-sharpe').textContent = s.sharpe.toFixed(2);
 $id('m-calmar').textContent = (s.calmar||0).toFixed(2);
 $id('m-wr-po').textContent = s.winRate + '% / ' + s.payoffRatio.toFixed(1) + 'x';
 $id('m-comm').textContent = (s.commissionPct || 0) + '%';
 // Financing & leverage metrics
 var es = s.exposureSummary || {};
 var interestPct = es.total_interest_accrued_pct != null ? es.total_interest_accrued_pct : (es.interest_drag_estimate || 0);
 $id('m-interest').textContent = interestPct.toFixed(1) + '%';
 var levContrib = es.leverage_contribution_pct;
 if (levContrib != null) {
  var nearZero = Math.abs(levContrib) < 0.05;
  $id('m-lev-contrib').textContent = nearZero ? '0.0%' : ((levContrib >= 0 ? '+' : '') + levContrib.toFixed(1) + '%');
  $id('m-lev-contrib').className = 'value' + (nearZero ? '' : (levContrib >= 0 ? ' green' : ' red'));
 }
 var intradayBanner = $id('intraday-estimate-banner');
 if (intradayBanner) {
  intradayBanner.style.display = s.hasIntradayEstimate ? 'block' : 'none';
  if (s.hasIntradayEstimate) {
   intradayBanner.textContent = '盘中估算：' + (s.intradayDate || '今日') + (s.intradayTime ? ' ' + s.intradayTime : '') + ' 的实时行情与预估全天成交额（' + (s.intradayCount || 0) + ' 支 ETF），适合收盘前调仓参考；正式复盘请以收盘确认数据为准。';
  }
 }

 // Factor dispersion summary card (REQ-377): σ_F7 percentiles
 var fd = data.factorDispersion;
 if (fd && fd.f7_std && fd.f7_std.length > 0) {
  var sorted = fd.f7_std.slice().sort(function(a,b){return a-b;});
  var n = sorted.length;
  var p50 = sorted[Math.floor(n*0.5)].toFixed(1);
  var p90 = sorted[Math.floor(n*0.9)].toFixed(1);
  $id('m-factor-dom').innerHTML =
   '<span style="color:#a855f7;">&#x1F313; ' + p50 + '</span>' +
   ' <span style="color:#a855f7;">&#x1F315; ' + p90 + '</span>';
 } else {
  $id('m-factor-dom').textContent = '-';
 }

 // Store signal history and name map for snapshot
 tunerSignalHistory = data.signalHistory || [];
 // Update benchmark vote status from last signal
 if (tunerSignalHistory.length > 0) {
  var lastSig = tunerSignalHistory[tunerSignalHistory.length - 1];
  if (lastSig.benchmark_votes) {
   updateBenchmarkVoteStatus(lastSig.benchmark_votes);
  }
 }
 tunerSnapshotIdx = tunerSignalHistory.length - 1; // Start with latest
 if (data.etfNameMap) window.etfNameMap = data.etfNameMap;
 currentKlineCode = null; // reset selection on new backtest
 lastBacktestData = data; // cache for freq switch redraw
 if (data.etfHoldings) etfHoldingsMap = data.etfHoldings;
 if (data.stockMetadata) stockMetadataMap = data.stockMetadata;
 // Pre-render holdings for first ETF so 十大重仓 tab has data immediately
 if (tunerSignalHistory.length > 0 && etfHoldingsMap) {
  var firstSig = tunerSignalHistory[tunerSignalHistory.length - 1];
  var topCodes = firstSig.topN || firstSig.top_n || [];
  if (topCodes.length > 0) {
   currentKlineCode = topCodes[0];
   renderHoldingsTable(currentKlineCode);
   highlightSnapRow(currentKlineCode);
   loadKline(currentKlineCode, firstSig.date || firstSig.signalDate);
  }
 }
 // Default selected date = end of backtest (or last signal's date)
 currentSelectedDate = (data.nav && data.nav.dates.length) ? data.nav.dates[data.nav.dates.length - 1]
            : (tunerSignalHistory.length ? tunerSignalHistory[tunerSnapshotIdx].date : null);

 renderNavChart(data);
 renderDdChart(data);
 renderDistChart(data);
 // Show snapshot section and render initial snapshot (latest)
 $id('tuner-snapshot-section').style.display = 'block';
 if (tunerSignalHistory.length > 0) {
  renderTunerSnapshot(tunerSnapshotIdx);
 }

 // Connect three charts for shared axisPointer + dataZoom
 setTimeout(function() {
  if (navChart && ddChart) {
   echarts.connect(klineReplayChart ? [navChart, ddChart, klineReplayChart] : [navChart, ddChart]);
  }
 }, 200);
}

/* ====== Resample daily series to weekly (Friday-end) ====== */
function isIntradayEstimateDate(dateStr) {
 var s = lastBacktestData && lastBacktestData.summary;
 return !!(s && s.hasIntradayEstimate && s.intradayDate === dateStr);
}

function hasIntradayLastPoint(data, dates) {
 var s = data && data.summary;
 return !!(s && s.hasIntradayEstimate && s.intradayDate && dates && dates.length && dates[dates.length - 1] === s.intradayDate);
}

function splitEstimatedLastSegment(values, enabled) {
 var confirmed = values.slice();
 var estimated = new Array(values.length).fill(null);
 if (enabled && values.length >= 2) {
  confirmed[values.length - 1] = null;
  estimated[values.length - 2] = values[values.length - 2];
  estimated[values.length - 1] = values[values.length - 1];
 }
 return { confirmed: confirmed, estimated: estimated };
}

function resampleWeekly(dates, values) {
 if (!dates || dates.length === 0) return { dates: [], values: [] };
 var outDates = [], outValues = [];
 var lastWeekKey = null;
 for (var i = 0; i < dates.length; i++) {
  var d = new Date(dates[i]);
  // ISO week key: year + week number (rough: floor((dayOfYear + offset) / 7))
  var year = d.getUTCFullYear();
  var jan1 = new Date(Date.UTC(year, 0, 1));
  var dayOfYear = Math.floor((d - jan1) / 86400000);
  var weekKey = year * 100 + Math.floor(dayOfYear / 7);
  if (weekKey !== lastWeekKey && lastWeekKey !== null) {
   // Push prev week's last value
   outDates.push(dates[i - 1]);
   outValues.push(values[i - 1]);
  }
  lastWeekKey = weekKey;
 }
 // Push final
 outDates.push(dates[dates.length - 1]);
 outValues.push(values[values.length - 1]);
 return { dates: outDates, values: outValues };
}

function renderNavChart(data) {
 if (!navChart) navChart = echarts.init($id('nav-chart'));

 var dates = data.nav.dates;
 var navPct = data.nav.pct;
 var hs300 = data.hs300 || [];
 var eqWeight = data.eqWeight || [];

 if (klineFreq === 'weekly') {
  var navW = resampleWeekly(dates, navPct);
  var hsW = hs300.length ? resampleWeekly(dates, hs300).values : [];
  var eqW = eqWeight.length ? resampleWeekly(dates, eqWeight).values : [];
  dates = navW.dates;
  navPct = navW.values;
  hs300 = hsW;
  eqWeight = eqW;
 }
 // ── Alpha heat band: deviation from self-trendline ──
 // Compare strategy NAV against its own constant-CAGR trend (start→end exponential curve).
 // Green = strategy ahead of its own average pace, Red = behind.
 var W = Math.max(10, Math.round(dates.length / 12));
 var alphaSignals = null;

 if (dates.length >= W && navPct.length === dates.length) {
  var T = dates.length - 1; // number of intervals (trading days - 1)
  var lastNav = navPct[navPct.length - 1];
  if (T > 0 && lastNav > 0) {
   // Constant-CAGR trend: Trend(t) = 100 * (lastNav/100)^(t/T)
   // Deviation: (navPct[t] / Trend[t] - 1) * 100 → percentage relative to trend
   var cagrRatio = lastNav / 100; // S(T) / S(0)
   var deviation = [];
   for (var ti = 0; ti < dates.length; ti++) {
    if (navPct[ti] != null && navPct[ti] > 0) {
     var trend = 100 * Math.pow(cagrRatio, ti / T);
     deviation[ti] = (navPct[ti] / trend - 1) * 100; // percentage
    } else { deviation[ti] = NaN; }
   }
   // Rolling window average of deviation
   var rollingExcess = new Array(dates.length);
   for (var ti = 0; ti < dates.length; ti++) {
    var start = Math.max(0, ti - W + 1);
    var sum = 0, cnt = 0;
    for (var j = start; j <= ti; j++) {
     if (!isNaN(deviation[j])) { sum += deviation[j]; cnt++; }
    }
    rollingExcess[ti] = (cnt > 0) ? (sum / cnt) : NaN;
   }
   // Build signal array for buildHeatBand (skip leading NaN window)
   alphaSignals = [];
   for (var ti = 0; ti < dates.length; ti++) {
    if (!isNaN(rollingExcess[ti])) {
     alphaSignals.push({ date: dates[ti], rollingExcess: rollingExcess[ti] });
    }
   }
   // ── Distribution statistics for threshold tuning ──
   (function() {
    var vals = [];
    for (var i = 0; i < rollingExcess.length; i++) {
     if (!isNaN(rollingExcess[i])) vals.push(rollingExcess[i]);
    }
    if (vals.length === 0) return;
    vals.sort(function(a,b){return a-b;});
    var n = vals.length;
    var mean = vals.reduce(function(s,v){return s+v;},0) / n;
    var variance = vals.reduce(function(s,v){return s+(v-mean)*(v-mean);},0) / n;
    var std = Math.sqrt(variance);
    var pct = function(p) {
     var idx = (n - 1) * p / 100;
     var lo = Math.floor(idx), hi = Math.ceil(idx);
     return lo === hi ? vals[lo] : vals[lo] + (vals[hi] - vals[lo]) * (idx - lo);
    };
    var red = 0, orange = 0, yellow = 0, green = 0;
    for (var i = 0; i < vals.length; i++) {
     var v = vals[i];
     if (v <= -20) red++;
     else if (v <= -7) orange++;
     else if (v <= 7) yellow++;
     else green++;
    }
    console.log('=== Alpha Heat Band | trend-deviation distribution (W=' + W + ', CAGR=' + ((Math.pow(cagrRatio, 1/(T/252))-1)*100).toFixed(1) + '%) ===');
    console.log('N:', n, ' Mean:', mean.toFixed(2), ' Median:', pct(50).toFixed(2));
    console.log('Min:', vals[0].toFixed(2), ' Max:', vals[n-1].toFixed(2), ' Std:', std.toFixed(2));
    console.log('P1:', pct(1).toFixed(2), ' P5:', pct(5).toFixed(2), ' P10:', pct(10).toFixed(2),
          ' P25:', pct(25).toFixed(2), ' P75:', pct(75).toFixed(2),
          ' P90:', pct(90).toFixed(2), ' P95:', pct(95).toFixed(2), ' P99:', pct(99).toFixed(2));
    console.log('Buckets (<=-20% red | (-20,-7]% orange | (-7,7]% yellow | >7% green):');
    console.log(' Red:', red, '(' + (red/n*100).toFixed(1) + '%)');
    console.log(' Orange:', orange, '(' + (orange/n*100).toFixed(1) + '%)');
    console.log(' Yellow:', yellow, '(' + (yellow/n*100).toFixed(1) + '%)');
    console.log(' Green:', green, '(' + (green/n*100).toFixed(1) + '%)');
   })();
  }
 }
 navDates = dates; // remember for click handler

 var curDate = currentSelectedDate;
 var intradayLastPoint = hasIntradayLastPoint(data, dates);
 var navSplit = splitEstimatedLastSegment(navPct, intradayLastPoint);

 // Build regime switch markPoints on the NAV curve
 var switchMarkPts = [];
 if (tunerSignalHistory && tunerSignalHistory.length > 1) {
  // Build date->index lookup for fast matching
  var dateLookup = {};
  for (var di = 0; di < dates.length; di++) dateLookup[dates[di]] = di;
  var prevRegime = tunerSignalHistory[0].regime;
  for (var si = 1; si < tunerSignalHistory.length; si++) {
   var cur = tunerSignalHistory[si];
   if (cur.regime && cur.regime !== prevRegime) {
    var isToBear = cur.regime === 'ma_below';
    var idx = dateLookup[cur.date];
    if (idx != null) {
     switchMarkPts.push({
      name: (isToBear ? 'Bear ' : 'Bull ') + cur.totalPosition.toFixed(0) + '%',
      coord: [cur.date, navPct[idx]],
      symbol: 'triangle',
      symbolRotate: isToBear ? 180 : 0,
      symbolSize: 12,
      itemStyle: {
       color: isToBear ? TC.negative : TC.greenLight,
       borderColor: TC.bgBody,
       borderWidth: 1,
      },
      label: { show: false },
     });
    }
    prevRegime = cur.regime;
   }
  }
 }

 var series = [{
  name:'策略净值', type:'line', data:navSplit.confirmed, showSymbol:false,
  color:TC.accent, lineStyle:{width:2, color:TC.accent},
  connectNulls:false,
  markPoint: switchMarkPts.length ? {
   data: switchMarkPts,
   animation: false,
   z: 10,
  } : undefined,
 }];
 if (intradayLastPoint) {
  series.push({
   name:'盘中估算', type:'line', data:navSplit.estimated, showSymbol:false,
   color:TC.accentLight, lineStyle:{width:2, color:TC.accentLight, type:'dashed'},
   connectNulls:false,
  });
 }
 if (hs300.length) series.push({name:'沪深300', type:'line', data:hs300, showSymbol:false, color:TC.warning, lineStyle:{width:1, color:TC.warning, type:'dashed'}});
 if (eqWeight.length) series.push({name:'等权持有', type:'line', data:eqWeight, showSymbol:false, color:TC.chartEqWt, lineStyle:{width:1, color:TC.chartEqWt, type:'dashed'}});

 // === Reusable: build a heat-band markArea from signal-level data ===
 // opts: {name, seriesName, getValue(sig), levels:[{max, color}], z}
 function buildHeatBand(signals, dates, opts) {
  var areas = [];
  var levels = opts.levels || [];
  for (var si = 0; si < signals.length - 1; si++) {
   var v = opts.getValue(signals[si]);
   var color = levels[0].color;
   for (var li = 0; li < levels.length; li++) {
    if (v <= levels[li].max) { color = levels[li].color; break; }
   }
   areas.push([
    {xAxis: signals[si].date, itemStyle:{color:color}},
    {xAxis: signals[si+1].date}
   ]);
  }
  return {
   name: opts.name, type:'line', data:[], showSymbol:false,
   lineStyle:{width:1.5, color: opts.color || '#9ca3af'},
   itemStyle:{color: opts.color || '#9ca3af'},
   markArea:{silent:true, data:areas, label:{show:false}},
   tooltip:{show:false}, z: opts.z || 0,
  };
 }

 // ── Heat bands (both always present; legend toggles mutual exclusion) ──
 if (tunerSignalHistory && tunerSignalHistory.length > 1) {
  series.unshift(buildHeatBand(tunerSignalHistory, dates, {
   name: '仓位集中度',
   color: TC.accentLight,
   z: 0,
   levels: [
    {max:0.25, color:'rgba(34,197,94,0.08)'},
    {max:0.50, color:'rgba(250,204,21,0.08)'},
    {max:0.75, color:'rgba(249,115,22,0.10)'},
    {max:1.00, color:'rgba(239,68,68,0.12)'},
   ],
   getValue: function(sig) {
    var pos = sig.positions || {};
    var maxSingle = 0;
    for (var k in pos) { if (pos[k] > maxSingle) maxSingle = pos[k]; }
    return maxSingle;
   },
  }));
 }
 if (alphaSignals && alphaSignals.length > 1) {
  series.unshift(buildHeatBand(alphaSignals, dates, {
   name: '超额区间',
   color: TC.accentLight,
   z: 0,
   levels: [
    {max: -20,   color: 'rgba(239,68,68,0.12)'},
    {max: -7,   color: 'rgba(249,115,22,0.10)'},
    {max:  7,   color: 'rgba(250,204,21,0.08)'},
    {max: Infinity, color: 'rgba(34,197,94,0.08)'},
   ],
   getValue: function(sig) { return sig.rollingExcess; },
  }));
 }

 // F7 dominance overlay (REQ-377) — step area like "持仓山脉图", forward-filled from rebalance dates
 var f7AreaData = [];
 if (data.factorDispersion && data.factorDispersion.dates && data.factorDispersion.dates.length > 0) {
  var fdD = data.factorDispersion;
  var fdIdx = 0;
  for (var di = 0; di < dates.length; di++) {
   while (fdIdx + 1 < fdD.dates.length && fdD.dates[fdIdx + 1] <= dates[di]) fdIdx++;
   f7AreaData.push(fdD.f7_std[fdIdx]);
  }
  series.push({
   name: '💜 F7驱动', type: 'line', data: f7AreaData,
   yAxisIndex: 1, step: 'end', showSymbol: false,
   lineStyle: { color: 'rgba(168,85,247,0.45)', width: 1 },
   areaStyle: { color: 'rgba(168,85,247,0.15)' },
   z: 0,
  });
 }

 navChart.setOption({
  backgroundColor:'transparent',
  tooltip:{trigger:'axis', triggerOn:'mousemove', axisPointer:{type:'cross', triggerOn:'mousemove'}, backgroundColor:'rgba(10,25,47,0.95)', borderColor:'rgba(59,130,246,0.2)', textStyle:{color:TC.textBody,fontSize:11},
   formatter: function(params) {
    if (!params || !params.length) return '';
    var html = '<div style="font-weight:600;margin-bottom:3px">' + params[0].axisValue + '</div>';
    for (var i = 0; i < params.length; i++) {
     var p = params[i], c = p.color || TC.textBody;
     html += '<div style="display:flex;justify-content:space-between;gap:18px;font-size:10px;line-height:1.6">' +
      '<span style="color:#9ca3af">' + p.seriesName + '</span>' +
      '<span style="color:' + c + ';font-weight:600">' + (p.value != null ? p.value.toFixed(2) + '%' : '-') + '</span></div>';
    }
    var idx = params[0].dataIndex;
    if (idx > 0 && navPct[idx] != null && navPct[idx-1] != null) {
     var chg = (navPct[idx] / navPct[idx-1] - 1) * 100;
     html += '<div style="margin-top:4px;padding-top:4px;border-top:1px solid rgba(255,255,255,0.08)">' +
      '<span style="font-size:10px;color:#9ca3af">日涨跌 </span>' +
      '<span style="font-size:10px;font-weight:600;color:' + (chg >= 0 ? TC.negative : TC.positive) + '">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</span></div>';
    }
    return html;
   },
  },
  legend:{data:series.filter(s=>!s.name.startsWith('_')).map(s=>s.name), textStyle:{color:'#9ca3af', fontSize:11}, top:0, selected:{'仓位集中度':false, '超额区间':false, '💜 F7驱动':true}},
  grid:{left:50, right:20, top:30, bottom:30},
  xAxis:{type:'category', data:dates, boundaryGap:false, axisLabel:{fontSize:10, color:TC.textMuted}, axisLine:{lineStyle:{color:TC.border}}},
  yAxis:[
   {type:'value', scale:true, axisLabel:{fontSize:10, color:TC.textMuted, formatter:'{value}%'}, splitLine:{lineStyle:{color:'rgba(255,255,255,0.06)'}}},
   {type:'value', scale:true, show:false},
  ],
  axisPointer: { link: [{xAxisIndex:'all'}] },
  series:series,
  dataZoom:[{type:'inside', start:0, end:100, zoomOnMouseWheel:true, groupId:'tuner-zoom'}],
 }, true);

 // Single owner for markLine — set after full redraw
 updateNavMarkLine();
 if (ddChart) echarts.connect(klineReplayChart ? [navChart, ddChart, klineReplayChart] : [navChart, ddChart]);

 // ── Heat band mutual exclusion via legend ──
 navChart.off('legendselectchanged');
 navChart.on('legendselectchanged', function(params) {
  var heatNames = ['仓位集中度', '超额区间'];
  var changed = params.name;
  if (heatNames.indexOf(changed) === -1) return; // not a heat band
  var isSelected = params.selected[changed];
  if (isSelected) {
   // User turned ON one heat band → turn OFF the other
   var other = (changed === '仓位集中度') ? '超额区间' : '仓位集中度';
   if (params.selected[other]) {
    navChart.setOption({ legend: { selected: (function() { var s = {}; s[other] = false; return s; })() } });
   }
  }
  // If user turned OFF the last visible heat band → both off (00), allowed
 });

 // Click on NAV chart → switch snapshot (does NOT trigger kline-replay redraw)
 navChart.getZr().off('click');
 navChart.getZr().on('click', function(params) {
  var pointInPixel = [params.offsetX, params.offsetY];
  if (!navChart.containPixel('grid', pointInPixel)) return;
  var dataIdx = navChart.convertFromPixel({seriesIndex:0}, pointInPixel)[0];
  if (dataIdx == null || dataIdx < 0 || dataIdx >= navDates.length) return;
  jumpToSnapshotByDate(navDates[Math.round(dataIdx)]);
 });

}



// Update only the nav-chart markLine (selected date) — no full redraw
function updateNavMarkLine() {
 if (!navChart || !currentSelectedDate) return;
 // Clear old markLine first, then set new one (ECharts merge appends arrays by default)
 navChart.setOption({ series: [{ name: '策略净值', markLine: null }] }, false);
 navChart.setOption({
  series: [{
   name: '策略净值',
   markLine: {
    silent: true, symbol: 'none',
    data: [{ xAxis: currentSelectedDate, lineStyle:{color:TC.negative,width:1.5,type:'dashed'} }],
    label: { show: false },
   },
  }],
 }, false);
}

function renderDdChart(data) {
 if (!ddChart) ddChart = echarts.init($id('dd-chart'));

 var dates = data.nav.dates;
 var ddData = data.drawdown;

 if (klineFreq === 'weekly') {
  var w = resampleWeekly(dates, ddData);
  dates = w.dates;
  ddData = w.values;
 }

 var intradayLastPoint = hasIntradayLastPoint(data, dates);
 var ddSplit = splitEstimatedLastSegment(ddData, intradayLastPoint);
 var ddSeries = [{type:'line', data:ddSplit.confirmed, showSymbol:false, areaStyle:{color:'rgba(239,68,68,0.15)'}, lineStyle:{width:1, color:TC.negative}, connectNulls:false}];
 if (intradayLastPoint) {
  ddSeries.push({type:'line', data:ddSplit.estimated, showSymbol:false, lineStyle:{width:1, color:TC.negative, type:'dashed'}, connectNulls:false});
 }

 ddChart.setOption({
  backgroundColor:'transparent',
  tooltip:{trigger:'axis', triggerOn:'mousemove', axisPointer:{type:'cross', triggerOn:'mousemove'}, backgroundColor:'rgba(10,25,47,0.95)', borderColor:'rgba(59,130,246,0.2)', textStyle:{color:TC.textBody,fontSize:11}, formatter:function(p){var item=(p||[]).find(function(x){return x.value != null;}) || p[0]; return item.axisValue + '<br/>回撤 <b style="color:var(--negative)">' + item.value.toFixed(2) + '%</b>' + (intradayLastPoint && item.dataIndex === dates.length - 1 ? '<br/><span style="color:var(--warning)">盘中估算，仅供收盘前参考</span>' : '');}},
  axisPointer: { link: [{xAxisIndex:'all'}] },
  grid:{left:50, right:20, top:8, bottom:20},
  xAxis:{type:'category', data:dates, show:false},
  yAxis:{type:'value', scale:true, axisLabel:{fontSize:10, color:TC.textMuted, formatter:'{value}%'}, splitLine:{lineStyle:{color:'rgba(255,255,255,0.06)'}}},
  series:ddSeries,
  dataZoom:[{type:'inside', start:0, end:100, zoomOnMouseWheel:true, groupId:'tuner-zoom'}],
 }, true);
}

function renderDistChart(data) {
 if (!distChart) distChart = echarts.init($id('dist-chart'));
 var navPct = data.nav.pct, dates = data.nav.dates;
 if (klineFreq === 'weekly') {
  var w = resampleWeekly(dates, navPct);
  dates = w.dates; navPct = w.values;
 }
 // Daily returns
 var rets = [];
 for (var i = 1; i < navPct.length; i++) {
  if (navPct[i-1] > 0) rets.push((navPct[i] - navPct[i-1]) / navPct[i-1] * 100);
 }
 // Bin into 7 buckets
 var bins = [
  {label: '< -5%', lo: -Infinity, hi: -5, color: TC.redDark},
  {label: '-5~-2%', lo: -5, hi: -2, color: TC.negative},
  {label: '-2~-1%', lo: -2, hi: -1, color: TC.orange},
  {label: '-1~1%', lo: -1, hi: 1, color: '#eab308'},
  {label: '1~2%', lo: 1, hi: 2, color: TC.greenLight},
  {label: '2~5%', lo: 2, hi: 5, color: TC.greenDark},
  {label: '> 5%', lo: 5, hi: Infinity, color: '#15803d'}
 ];
 var counts = [], colors = [], labels = [];
 for (var b = 0; b < bins.length; b++) {
  var cnt = 0;
  for (var r = 0; r < rets.length; r++) {
   if (rets[r] >= bins[b].lo && rets[r] < bins[b].hi) cnt++;
  }
  counts.push(cnt); colors.push(bins[b].color); labels.push(bins[b].label);
 }

 distChart.setOption({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: 'rgba(10,25,47,0.95)', borderColor: 'rgba(59,130,246,0.2)', textStyle: { color: TC.textBody, fontSize: 11 },
   formatter: function(p) { var v = p[0]; var pct = rets.length > 0 ? (v.value / rets.length * 100).toFixed(1) : '0'; return v.name + '<br/>' + v.value + ' 天 (' + pct + '%)'; }
  },
  grid: { left: 58, right: 24, top: 8, bottom: 4 },
  xAxis: { type: 'value', axisLabel: { fontSize: 9, color: TC.textMuted }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } } },
  yAxis: { type: 'category', data: labels, axisLabel: { fontSize: 9, color: TC.textMuted }, axisLine: { show: false }, axisTick: { show: false } },
  series: [{ type: 'bar', data: counts.map(function(v, i) { return { value: v, itemStyle: { color: colors[i], borderRadius: [0, 2, 2, 0] } }; }), barWidth: '70%', label: { show: true, position: 'right', fontSize: 9, color: TC.textSecondary, formatter: function(p) { return p.value > 0 ? p.value + '天' : ''; } } }]
 }, true);

 // Stats
 var n = rets.length;
 if (n > 0) {
  var sorted = rets.slice().sort(function(a, b) { return a - b; });
  var mean = rets.reduce(function(a, b) { return a + b; }, 0) / n;
  var median = n % 2 ? sorted[(n-1)/2] : (sorted[n/2-1] + sorted[n/2]) / 2;
  var variance = rets.reduce(function(s, v) { return s + (v - mean) * (v - mean); }, 0) / n;
  var std = Math.sqrt(variance);
  var skew = rets.reduce(function(s, v) { return s + Math.pow((v - mean) / std, 3); }, 0) / n;
  var kurt = rets.reduce(function(s, v) { return s + Math.pow((v - mean) / std, 4); }, 0) / n - 3;
  var bestIdx = 0, worstIdx = 0;
  for (var i = 1; i < n; i++) { if (rets[i] > rets[bestIdx]) bestIdx = i; if (rets[i] < rets[worstIdx]) worstIdx = i; }
  $id('ds-mean').textContent = (mean >= 0 ? '+' : '') + mean.toFixed(2) + '%';
  $id('ds-median').textContent = (median >= 0 ? '+' : '') + median.toFixed(2) + '%';
  $id('ds-std').textContent = std.toFixed(2) + '%';
  $id('ds-skew').textContent = skew.toFixed(2);
  $id('ds-kurt').textContent = kurt.toFixed(2);
  $id('ds-best').textContent = '+' + rets[bestIdx].toFixed(2) + '%';
  $id('ds-best-date').textContent = dates[bestIdx + 1] || '';
  $id('ds-worst').textContent = rets[worstIdx].toFixed(2) + '%';
  $id('ds-worst-date').textContent = dates[worstIdx + 1] || '';
  _distBestDate = dates[bestIdx + 1] || '';
  _distWorstDate = dates[worstIdx + 1] || '';
 }
}

var _distBestDate = '', _distWorstDate = '', _klineDates = [];
function jumpToDistDate(which) {
 var d = which === 'best' ? _distBestDate : _distWorstDate;
 if (!d) return;
 jumpToSnapshotByDate(d);
 // Zoom K-line chart to ±25 trading days around the target date
 if (klineReplayChart && _klineDates.length > 0) {
  var idx = -1;
  for (var i = 0; i < _klineDates.length; i++) { if (_klineDates[i] === d) { idx = i; break; } }
  if (idx >= 0) {
   var halfWin = 25;
   var start = Math.max(0, idx - halfWin);
   var end = Math.min(_klineDates.length - 1, idx + halfWin);
   klineReplayChart.dispatchAction({ type: 'dataZoom', startValue: _klineDates[start], endValue: _klineDates[end] });
  }
 }
}

// Set selected date (red line position), update snapshot to nearest prior signal
function jumpToSnapshotByDate(clickDate) {
 currentSelectedDate = clickDate;
 updateNavMarkLine();
 updateTradeReplayMarkLine();
 var bestIdx = 0;
 for (var i = 0; i < tunerSignalHistory.length; i++) {
  if (tunerSignalHistory[i].date <= clickDate) bestIdx = i;
 }
 renderTunerSnapshot(bestIdx);
}

async function undoSave() {
 try {
  var resp = await fetch('/api/undo_save', { method:'POST', headers:{'Content-Type':'application/json'} });
  var data = await resp.json();
  if (data.ok) { $id('status').textContent = '已撤销保存 (' + (data.restored||[]).join(', ') + ')'; loadPresets(); }
  else { $id('status').textContent = '撤销失败: ' + (data.error||''); }
 } catch(e) { $id('status').textContent = '撤销出错: ' + e.message; }
}

async function saveYAML() {
 try {
  if (!validateRunInputs()) {
   $id('status').textContent = '参数非法：' + ($id('btn-run').title || '请检查参数');
   return;
  }
  var payload = getParams();
  payload._preset = CURRENT_PRESET || 'zen-1';
  var resp = await fetch('/api/save', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
  var data = await resp.json();
  var label = (PRESETS[CURRENT_PRESET] && PRESETS[CURRENT_PRESET].label) || CURRENT_PRESET || '预设';
  if (data.ok) { $id('status').textContent = '已保存到 ' + label; loadPresets(); }
  else { $id('status').textContent = '保存失败: ' + (data.error||''); }
 } catch(e) { $id('status').textContent = '保存出错: ' + e.message; }
}

/* ====== Snapshot Column Sort (tri-state: desc → asc → default) ====== */
function onSnapSort(col) {
 if (snapshotSort.col === col) {
  if (snapshotSort.dir === 'desc') { snapshotSort.dir = 'asc'; }
  else if (snapshotSort.dir === 'asc') { snapshotSort = { col: 'score', dir: 'desc' }; }
 } else {
  snapshotSort = { col: col, dir: 'desc' };
 }
 renderTunerSnapshot(tunerSnapshotIdx);
}

function updateSortArrows() {
 var arrows = document.querySelectorAll('.snap-sort-arrow');
 for (var i = 0; i < arrows.length; i++) { arrows[i].textContent = ''; arrows[i].style.color = ''; }
 var activeId = 'sort-ar-' + snapshotSort.col;
 var activeArrow = document.getElementById(activeId);
 if (activeArrow) {
  activeArrow.textContent = snapshotSort.dir === 'desc' ? ' ▼' : ' ▲';
  activeArrow.style.color = TC.accentLight;
  activeArrow.style.fontSize = '9px';
 }
}

function getSnapSortVal(d, code, col) {
 switch (col) {
  case 'code': return code;
  case 'sector':
   for (var si = 0; si < hmETFList.length; si++) {
    if (hmETFList[si].code === code) {
     return (groupLevel === 'group1' && hmETFList[si].group1) ? hmETFList[si].group1 : (hmETFList[si].sector || '');
    }
   }
   return '';
  case 'f1': return d.f1 || 0;
  case 'f7': return (d.f7 != null ? d.f7 : 50);
  case 'score': return d.score || 0;
  case 'z': return d.z || 0;
  case 'pos': return d.position || 0;
  default: return 0;
 }
}

/* ====== Snapshot 全局翻转：加工值 ↔ 原始值 ====== */
var snapShowRaw = false;
function toggleSnapRaw() {
 snapShowRaw = !snapShowRaw;
 var el = $id('snap-flip-btn');
 if (el) {
  el.style.background = snapShowRaw ? 'var(--accent)' : 'var(--bg-input)';
  el.style.color = snapShowRaw ? '#fff' : 'var(--text-muted)';
  el.textContent = snapShowRaw ? '原始' : '加工';
 }
 var hdr = $id('snap-score-hdr'); if (hdr) hdr.textContent = snapShowRaw ? '离散度' : '综合';
 var h1 = $id('snap-f1-hdr'); if (h1) h1.textContent = snapShowRaw ? 'F1_raw' : 'F1';
 var h7 = $id('snap-f7-hdr'); if (h7) h7.textContent = snapShowRaw ? 'F7_raw' : 'F7';
 renderTunerSnapshot(tunerSnapshotIdx);
}

/* ====== Snapshot Rendering (C方案风格) ====== */
function renderTunerSnapshot(idx) {
 if (idx < 0 || idx >= tunerSignalHistory.length) return;
 tunerSnapshotIdx = idx;
 var sig = tunerSignalHistory[idx];
 var nameMap = window.etfNameMap || {};

 // Subtitle
 var sub = $id('tuner-snapshot-subtitle');
 // Update benchmark vote status for this snapshot
 if (sig.benchmark_votes) {
  updateBenchmarkVoteStatus(sig.benchmark_votes);
 }

 if (sub) {
  var dateText = sig.signalDate && sig.executionDate && sig.signalDate !== sig.executionDate
   ? sig.signalDate + ' 信号 → ' + sig.executionDate + ' 执行'
   : sig.date;
  if (isIntradayEstimateDate(sig.date)) {
   var s = lastBacktestData && lastBacktestData.summary || {};
   sub.innerHTML = dateText + ' · 第 ' + (idx + 1) + '/' + tunerSignalHistory.length + ' 次调仓 · <span style="color:var(--highlight);background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.35);border-radius:4px;padding:1px 6px;">盘中估算' + (s.intradayTime ? ' ' + s.intradayTime : '') + '</span>';
  } else {
   sub.textContent = dateText + ' · 第 ' + (idx + 1) + '/' + tunerSignalHistory.length + ' 次调仓';
  }
 }

 // Top metrics
 var holdingsCount = sig.topN ? sig.topN.length : 0;
 var totalPos = sig.totalPosition || 0;
 var targetExp = sig.targetExposure || sig.avgConfidence || 0; // REQ-373: prefer targetExposure
 var cashPct = sig.cashPct || (100 - totalPos);
 // BUG-046: round to integer to match per-position LR display
 var totalPosInt = Math.round(totalPos);
 var cashPctInt = 100 - totalPosInt; // guaranteed totalPos + cash = 100

 $id('snap-m-holdings').textContent = holdingsCount;
 $id('snap-m-total-pos').textContent = totalPosInt + '%';
 $id('snap-m-avg-conf').textContent = targetExp.toFixed(0) + '%';
 $id('snap-m-cash').textContent = cashPctInt + '%';

 // Color coding for metrics
 $id('snap-m-total-pos').style.color = totalPos > 70 ? TC.positive : totalPos > 40 ? TC.warning : TC.negative;
 $id('snap-m-avg-conf').style.color = targetExp > 60 ? TC.positive : targetExp > 30 ? TC.warning : TC.negative;
 $id('snap-m-cash').style.color = cashPct > 50 ? TC.negative : cashPct > 20 ? TC.warning : TC.textMuted;

 // Build table rows sorted by score desc
 var detail = sig.detail || {};
 var codes = Object.keys(detail);
 // Sort by snapshotSort state
 var sortCol = snapshotSort.col, sortDir = snapshotSort.dir;
 codes.sort(function(a, b) {
  var va = getSnapSortVal(detail[a], a, sortCol);
  var vb = getSnapSortVal(detail[b], b, sortCol);
  if (typeof va === 'string') return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
  return sortDir === 'asc' ? va - vb : vb - va;
 });
 updateSortArrows();

 var maxScore = 100;
 if (codes.length > 0 && detail[codes[0]]) maxScore = detail[codes[0]].score || 100;

 // BUG-046: largest-remainder rounding to ensure sum(position%) == round(totalPos)
 var _lrPositions = {}; // code -> display percentage (integer)
 var _lrTotal = Math.round(totalPos);
 var _lrFloors = {}, _lrSumFloors = 0, _lrFracs = [];
 for (var _i = 0; _i < codes.length; _i++) {
  var _c = codes[_i], _p = detail[_c] ? (detail[_c].position || 0) : 0;
  if (_p <= 0) continue;
  var _fl = Math.floor(_p);
  _lrFloors[_c] = _fl;
  _lrSumFloors += _fl;
  _lrFracs.push({c: _c, frac: _p - _fl});
 }
 _lrFracs.sort(function(a, b) { return b.frac - a.frac; });
 var _lrRemainder = Math.max(0, _lrTotal - _lrSumFloors);
 for (var _j = 0; _j < _lrFracs.length; _j++) {
  _lrPositions[_lrFracs[_j].c] = _lrFloors[_lrFracs[_j].c] + (_j < _lrRemainder ? 1 : 0);
 }

 var newCount = 0, adjUpCount = 0, adjDownCount = 0, outCount = 0, holdCount = 0;
 var html = '';

 for (var i = 0; i < codes.length; i++) {
  var code = codes[i];
  var d = detail[code];
  var name = (nameMap[code] || code).replace(/ETF$/i, '');
  // Sector color dot (group1 or sector based on toggle)
  var sectorColor = TC.textMuted, etfSector = '', etfGroup1 = '';
  for (var si = 0; si < hmETFList.length; si++) {
   if (hmETFList[si].code === code) { etfSector = hmETFList[si].sector; etfGroup1 = hmETFList[si].group1||''; break; }
  }
  if (groupLevel === 'group1' && etfGroup1) {
   sectorColor = GROUP1_COLORS[etfGroup1] || TC.textMuted;
   etfSector = etfGroup1; // use group1 as sort key
  } else {
   sectorColor = HM_SEC_COLOR[etfSector] || TC.textMuted;
  }
  if (etfSector && sectorFilter.has(etfSector)) continue;
  var sectorDot = '<span class=\"snap-sector-dot\" style=\"display:inline-block;width:12px;height:12px;border-radius:2px;background:' + sectorColor + ';\"></span>';
  // Margin eligibility
  var etfMeta = null;
  for (var mi = 0; mi < hmETFList.length; mi++) { if (hmETFList[mi].code === code) { etfMeta = hmETFList[mi]; break; } }
  var marginBadge = (etfMeta && etfMeta.marginable === false) ? ' <span title=\"不支持融资买入\" style=\"color:var(--negative);font-size:11px;font-weight:700;\">⛔</span>' : '';
  var suspendedBadge = (sig.suspendedCodes && sig.suspendedCodes.indexOf(code) >= 0) ? ' <span title="停牌，持仓冻结" style="color:var(--text-dim);font-size:11px;font-weight:700;">🚫</span>' : '';
  var score = d.score || 0;
  var pos = d.position || 0;
  var action = d.action || '';
  var isTop = sig.topN && sig.topN.indexOf(code) >= 0;
  var isSuspended = d.suspended === true; // REQ-373

  // Count actions
  if (action === 'new') newCount++;
  else if (action === 'adj_up') adjUpCount++;
  else if (action === 'adj_down') adjDownCount++;
  else if (action === 'out') outCount++;
  else if (action === 'hold') holdCount++;

  // Score bar width
  var barW = Math.max(0, Math.min(100, Math.round(score / maxScore * 100)));

  var actionBadge = '';
  // REQ-349: 替换因果链 tooltip
  var actionTT = '';
  var swaps = sig.swap_pairs || [];
  // REQ-373: frozen (suspended) holdings — position change is passive NAV drift, not active trade
  if (sig.suspendedCodes && sig.suspendedCodes.indexOf(code) >= 0) {
   actionBadge = '<span title="停牌冻结，仓位变化为被动NAV漂移" style="color:var(--warning);font-size:12px;font-weight:600;display:inline-block;line-height:1;">冻结</span>';
  } else if (action === 'new') {
   actionBadge = '<span style="color:var(--positive);font-size:12px;font-weight:600;display:inline-block;line-height:1;">NEW</span>';
   // Find which ETF(s) this code replaced
   var replaced = swaps.filter(function(sp) { return sp.in === code && sp.passed; });
   if (replaced.length) {
    actionTT = replaced.map(function(sp) {
     return sp.in_name + '(' + sp.in_score.toFixed(0) + ') 替换 ' + sp.out_name + '(' + sp.out_score.toFixed(0) + ') 分差' + sp.gap.toFixed(1) + ' > band ' + sp.band.toFixed(1);
    }).join('\n');
   }
  } else if (action === 'out') {
   actionBadge = '<span style="color:var(--negative);font-size:12px;font-weight:600;display:inline-block;line-height:1;">OUT</span>';
   // Find which ETF(s) replaced this code
   var replacedBy = swaps.filter(function(sp) { return sp.out === code && sp.passed; });
   if (replacedBy.length) {
    actionTT = replacedBy.map(function(sp) {
     return '被 ' + sp.in_name + '(' + sp.in_score.toFixed(0) + ') 替换 分差' + sp.gap.toFixed(1) + ' > band ' + sp.band.toFixed(1);
    }).join('\n');
   }
  } else if (action === 'adj_up') {
   actionBadge = '<span style="color:var(--positive);font-size:12px;font-weight:600;display:inline-block;line-height:1;">⬆ +' + (d.delta||0).toFixed(1) + '%</span>';
  } else if (action === 'adj_down') {
   actionBadge = '<span style="color:var(--negative);font-size:12px;font-weight:600;display:inline-block;line-height:1;">⬇ ' + (d.delta||0).toFixed(1) + '%</span>';
  } else if (action === 'hold') {
   actionBadge = '<span style="color:var(--text-dim);font-size:10px;display:inline-block;line-height:1;">HOLD</span>';
  }
  if (actionTT) {
   actionBadge = '<span class="swap-tip">' + actionBadge + '<span class="swap-tip-text">' + actionTT.replace(/\n/g, '<br>') + '</span></span>';
  }
  // Score color based on value

  // Row class
  var rowBg = isTop ? 'background:rgba(16,185,129,0.04);' : '';
  var hotSuffix = isTop ? ' <span style="color:var(--warning);font-size:11px;">&#x1F525;</span>' : '';

  // Factor bar — weighted F1/F7 contributions (F3 retired v3.15.0)
  var w1=0.82, w7=0.18;
  var f1v = d.f1 || 50, f7v = (d.f7 != null ? d.f7 : 50);
  // F1/F7/Score cells: snapShowRaw flips all to raw values
  var f1Disp, f1Color, f7Disp, f7Color, scoreDisp, scoreColor;
  if (isSuspended) {
   f1Disp = '—'; f1Color = TC.textDim;
   f7Disp = '—'; f7Color = TC.textDim;
   scoreDisp = '—'; scoreColor = TC.textDim;
  } else if (snapShowRaw) {
   // Raw mode: F1=EMA%, F7=Z-score, Score=Z(d.z)
   var f1r = d.f1_raw || 0;
   f1Disp = (f1r >= 0 ? '+' : '') + f1r.toFixed(1) + '%';
   f1Color = f1r >= 10 ? TC.positive : f1r <= -10 ? TC.negative : TC.textSecondary;
   var f7r = d.f7_raw || 0;
   f7Disp = (f7r >= 0 ? '+' : '') + f7r.toFixed(2);
   f7Color = f7r >= 2.0 ? TC.positive : f7r <= -2.0 ? TC.negative : TC.textSecondary;
   var z = d.z != null ? d.z : 0;
   scoreDisp = (z >= 0 ? '+' : '') + z.toFixed(2);
   scoreColor = z >= 1.5 ? TC.positive : z <= -1.5 ? TC.negative : TC.textSecondary;
  } else {
   // Processed mode: F1=±50, F7=±50, Score=0-100
   f1Disp = ((d.f1||0)-50).toFixed(0);
   f1Color = (d.f1 || 50) >= 65 ? TC.positive : (d.f1 || 50) <= 35 ? TC.negative : TC.textSecondary;
   f7Disp = (d.f7 != null ? (d.f7 - 50).toFixed(0) : 'N/A');
   f7Color = d.f7 != null ? (d.f7 <= 20 ? TC.negative : d.f7 >= 80 ? TC.positive : TC.textSecondary) : TC.textDim;
   scoreDisp = score.toFixed(1);
   scoreColor = 'var(--text-secondary)';
  }
  var c1 = f1v * w1, c7 = f7v * w7, cTot = c1 + c7 || 1;
  var p1 = Math.round(c1 / cTot * 100), p7 = 100 - p1;
  if (p1 < 0) p1 = 0; if (p7 < 0) p7 = 0;
  var f1r = d.f1_raw, f7r = d.f7_raw;
  var fBarTT = 'F1: EMA' + (f1r>0?'+':'') + (f1r||0).toFixed(1) + '% → ' + (f1v-50).toFixed(0)
   + ' | F7: Z=' + (f7r||0).toFixed(2) + ' → ' + (f7v-50).toFixed(0);
  var fBar = isSuspended ? '<span style="color:var(--text-dim);font-size:10px;">—</span>' : '<div style="display:inline-flex;gap:1px;width:56px;height:8px;border-radius:2px;overflow:hidden;" title="' + fBarTT + '">'
   + '<span style="width:' + p1 + '%;height:100%;background:var(--accent);border-radius:1px 0 0 1px;"></span>'
   + '<span style="width:' + p7 + '%;height:100%;background:#a855f7;border-radius:0 1px 1px 0;"></span></div>';

  html += '<tr data-code="' + code + '" class="snap-row" data-sector="' + etfSector + '" data-group1="' + (etfMeta ? (etfMeta.group1||'') : '') + '" style="border-bottom:1px solid var(--bg-hover);cursor:pointer;' + rowBg + '">' +
   '<td class="snap-sector-cell" style="padding:9px 2px;text-align:center;vertical-align:middle;">' + sectorDot + '</td>' +
   '<td style="padding:9px 8px;font-size:12px;white-space:nowrap;"><span style="color:var(--accent-light);font-weight:600;">' + code + '</span> <span style="color:var(--text-secondary);font-size:12px;font-weight:600;">' + name + '</span>' + marginBadge + suspendedBadge + hotSuffix + '</td>' +
   '<td style="padding:9px 4px;text-align:center;vertical-align:middle;">' + fBar + '</td>' +
   '<td style="padding:9px 6px;text-align:center;font-size:12px;color:' + f1Color + ';">' + f1Disp + '</td>' +
   '<td style="padding:9px 6px;text-align:center;font-size:12px;color:' + f7Color + ';">' + f7Disp + '</td>' +
   '<td style="padding:9px 3px;text-align:center;font-size:12px;font-weight:700;color:' + scoreColor + ';" title="' + (isSuspended ? '停牌' : (snapShowRaw ? 'z=' + (d.z!=null?(d.z>0?'+':'')+d.z.toFixed(2):'N/A') : 'F1=' + (d.f1||0).toFixed(0) + ' F7=' + (d.f7!=null?d.f7.toFixed(0):'N/A'))) + '">' + scoreDisp + '</td>' +
   '<td style="padding:9px 6px;text-align:center;font-size:12px;' +
 (pos >= 30 ? 'color:var(--highlight);font-weight:700;' : pos >= 20 ? 'color:var(--warning);font-weight:700;' : pos >= 10 ? 'color:#d97706;font-weight:600;' : pos > 0 ? 'color:#b45309;font-weight:400;' : '') +
 '">' + (pos > 0 ? (_lrPositions[code] != null ? _lrPositions[code] + '%' : '-') : '-') + '</td>' +
   '<td style="padding:9px 6px;text-align:center;vertical-align:middle;">' + actionBadge + '</td>' +
   '</tr>';
 }

 $id('tuner-snapshot-body').innerHTML = html;

 // Subtitle: action counts
 var subEl = $id('tuner-snapshot-subtitle');
 if (subEl) {
  var parts = [];
  if (newCount > 0) parts.push('<span style="color:var(--positive);">NEW ' + newCount + '</span>');
  if (adjUpCount > 0) parts.push('<span style="color:var(--positive);">⬆ ' + adjUpCount + '</span>');
  if (adjDownCount > 0) parts.push('<span style="color:var(--negative);">⬇ ' + adjDownCount + '</span>');
  if (holdCount > 0) parts.push('<span style="color:var(--text-muted);">HOLD ' + holdCount + '</span>');
  if (outCount > 0) parts.push('<span style="color:var(--negative);">OUT ' + outCount + '</span>');
  var cEffStr = '';
  if (sig.cEff != null && sig.cEff > 0) {
   var baseC = (lastBacktestData && lastBacktestData.summary) ? 0.5 : 0.5; // preset1 default
   cEffStr = ' · <span style=\"color:var(--warning);\" title=\"有效C=baseC×c_mult。共识强时放大，弱时缩小\">C=' + sig.cEff.toFixed(1) + '</span>';
  }
  subEl.innerHTML = '点击左侧 NAV 切换调仓 · 点击表格行查看 K 线 · ' + parts.join(' · ') + cEffStr + ' · &#x1F525;=持仓 · <span style=\"color:var(--negative);font-weight:700;\">⛔</span>=不支持融资 · <span style=\"color:var(--warning);font-weight:700;\">🚫</span>=停牌冻结';
 }

 // Footer: sector legend with toggle
 var footer = $id('tuner-snapshot-footer');
 if (footer) {
  // Build legend based on groupLevel
  var legendItems = [];
  if (groupLevel === 'group1') {
   legendItems = [{key:'成长进攻',color:GROUP1_COLORS['成长进攻']},{key:'周期博弈',color:GROUP1_COLORS['周期博弈']},{key:'防御避险',color:GROUP1_COLORS['防御避险']},{key:'跨市场另类',color:GROUP1_COLORS['跨市场另类']}];
  } else if (HM_SEC_ORDER.length > 0) {
   for (var si = 0; si < HM_SEC_ORDER.length; si++) {
    var sec = HM_SEC_ORDER[si];
    legendItems.push({key:sec, color:HM_SEC_COLOR[sec]||TC.textMuted});
   }
  }
  // Store all sector keys globally for footer button handlers
  window._sectorAllKeys = legendItems.map(function(s){return s.key;});
  var legHtml = '<span style="color:var(--text-muted);">扇区:</span> ' +
   '<span data-action="sectorFilterClear" style="cursor:pointer;color:var(--accent-light);font-size:9px;margin:0 2px;">全选</span> ' +
   '<span data-action="_sectorSelectNone" style="cursor:pointer;color:var(--text-muted);font-size:9px;margin:0 2px;">全不选</span> ' +
   '<span data-action="_sectorInvert" style="cursor:pointer;color:var(--warning);font-size:9px;margin:0 2px;">反选</span>' +
   '<span style="margin:0 6px;">|</span>';
  if (legendItems.length > 0) {
   for (var li = 0; li < legendItems.length; li++) {
    var item = legendItems[li];
    var off = sectorFilter.has(item.key);
    legHtml += '<span data-sector-legend="' + item.key + '" style="cursor:pointer;user-select:none;' +
     (off ? 'opacity:0.25;' : '') + '">' +
     '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + item.color +
     ';margin-right:3px;vertical-align:middle;' + (off ? 'filter:grayscale(1);' : '') + '"></span>' +
     '<span style="font-size:10px;' + (off ? 'text-decoration:line-through;' : '') + '">' + item.key + '</span></span> ';
   }
  }
  legHtml += '<span style="margin-left:auto;display:flex;gap:2px;"><button id=\"group-level-btn\" data-action=\"toggleGroupLevel\" style=\"background:var(--bg-input);border:1px solid var(--border);color:var(--text-muted);font-size:10px;padding:2px 8px;border-radius:3px;cursor:pointer;\">一级</button><button id=\"snap-flip-btn\" data-action=\"toggleSnapRaw\" style=\"background:var(--bg-input);border:1px solid var(--border);color:var(--text-muted);font-size:10px;padding:2px 8px;border-radius:3px;cursor:pointer;\">加工</button></span>';
  footer.innerHTML = legHtml;
 // Restore toggle button states (footer is rebuilt on every render)
 var gb = $id('group-level-btn'); if (gb) gb.textContent = (groupLevel === 'sector' ? '二级' : '一级');
 var fb = $id('snap-flip-btn');
 if (fb && snapShowRaw) { fb.style.background = 'var(--accent)'; fb.style.color = '#fff'; fb.textContent = '原始'; }
 var sh = $id('snap-score-hdr'); if (sh) sh.textContent = snapShowRaw ? '离散度' : '综合';
 var h1 = $id('snap-f1-hdr'); if (h1) h1.textContent = snapShowRaw ? 'F1_raw' : 'F1';
 var h7 = $id('snap-f7-hdr'); if (h7) h7.textContent = snapShowRaw ? 'F7_raw' : 'F7';
 }

 // After table re-render, restore K-line for selected code (if any).
 // Snapshot switch should NOT trigger full redraw; only update markLines.
 updateNavMarkLine(); // always update NAV markLine on snapshot change
 if (currentKlineCode && detail[currentKlineCode]) {
  highlightSnapRow(currentKlineCode);
  updateTradeReplayMarkLine();
 } else if (sig.topN && sig.topN.length > 0) {
  // First render or no current selection → load default
  var firstHolding = sig.topN[0];
  highlightSnapRow(firstHolding);
  currentKlineCode = firstHolding;
  loadKline(firstHolding, sig.date);
 } else {
  updateTradeReplayMarkLine(); // still update line if no holdings
 }
}

/* ====== ETF Detail: row click + load + render ====== */
var currentKlineCode = null;
var klineReplayChart = null, _klineJumpMarkPoint = false;
var klineFreq = 'daily'; // 'daily' | 'weekly'
var sectorFilter = new Set(); // sectors to hide

function toggleSector(sec) {
 if (sectorFilter.has(sec)) sectorFilter.delete(sec); else sectorFilter.add(sec);
 if (tunerSnapshotIdx >= 0) renderTunerSnapshot(tunerSnapshotIdx);
}
function _sectorSelectNone() {
 var keys = window._sectorAllKeys || [];
 for (var i = 0; i < keys.length; i++) { sectorFilter.add(keys[i]); }
 if (tunerSnapshotIdx >= 0) renderTunerSnapshot(tunerSnapshotIdx);
}
function _sectorInvert() {
 var keys = window._sectorAllKeys || [];
 for (var i = 0; i < keys.length; i++) {
  if (sectorFilter.has(keys[i])) sectorFilter.delete(keys[i]);
  else sectorFilter.add(keys[i]);
 }
 if (tunerSnapshotIdx >= 0) renderTunerSnapshot(tunerSnapshotIdx);
}

function switchKlineFreq(freq) {
 if (freq === klineFreq) return;
 klineFreq = freq;
 $id('kline-freq-daily').classList.toggle('active', freq === 'daily');
 $id('kline-freq-weekly').classList.toggle('active', freq === 'weekly');
 // Redraw NAV + DD with new freq
 if (lastBacktestData) {
  renderNavChart(lastBacktestData);
  renderDdChart(lastBacktestData);
 }
 // Reload kline-replay with new freq
 if (currentKlineCode) loadTradeReplay(currentKlineCode);
 // Reconnect (chart instances may be re-created)
 setTimeout(function() {
  echarts.connect(klineReplayChart ? [navChart, ddChart, klineReplayChart] : [navChart, ddChart]);
 }, 200);
}

// Update only the markLine (selected date) — no full redraw
function updateTradeReplayMarkLine() {
 if (!klineReplayChart || !currentSelectedDate) return;
 klineReplayChart.setOption({
  series: [{
   markLine: {
    silent: true, symbol: 'none',
    data: [{ xAxis: currentSelectedDate, lineStyle:{color:TC.negative,width:1.5,type:'dashed'} }],
    label: { show: false },
   },
  }],
 });
}

function highlightSnapRow(code) {
 document.querySelectorAll('.snap-row.snap-sel').forEach(function(tr) { tr.classList.remove('snap-sel'); });
 var sel = document.querySelector('.snap-row[data-code="' + code + '"]');
 if (sel) { sel.classList.add('snap-sel'); }
}

function onSnapshotRowClick(code) {
 currentKlineCode = code;
 var sig = tunerSignalHistory[tunerSnapshotIdx];
 if (!sig) return;
 highlightSnapRow(code);
 loadKline(code, sig.date);
 renderHoldingsTable(code);
}

var currentContribCode = null;

async function loadKline(code, date) {
 var nameMap = window.etfNameMap || {};
 var name = nameMap[code] || code;
 var heading = $id('tuner-kline-heading');
 if (heading) heading.innerHTML = '📈 <span style="color:var(--accent-light);">' + code + '</span> ' + name;
 await loadTradeReplay(code);
 renderContribution(code);
}

function fmtVal(v, unit, fallback) { return v != null ? v + unit : (fallback || '—'); }
function fmtDate(d) { return d ? d.slice(5) : '—'; } // MM-DD

function renderContribution(code) {
 if (currentContribCode === code) return;
 currentContribCode = code;
 var grid = $id('etf-contrib-grid');
 var extra = $id('tuner-kline-subtitle');
 if (!grid || !extra) return;
 var contribs = (lastBacktestData && lastBacktestData.etfContributions) || {};
 var c = contribs[code] || {};
 grid.style.display = 'grid';
 var has = c.selectedCount > 0;

 var items = [
  {l:'选中率', v:fmtVal(c.selectionRate,'%'), color:has&&c.selectionRate>=10?TC.positive:TC.textMuted, tip:'Top-N出现次数÷总调仓信号数。>20%为核心品种'},
  {l:'均权重', v:fmtVal(c.avgWeight,'%'), color:has&&c.avgWeight>=20?TC.highlight:has&&c.avgWeight>=10?TC.warning:TC.textMuted, tip:'入选时仓位%均值。反映策略对该ETF的配置力度'},
  {l:'均持有', v:fmtVal(c.avgHoldDays,'天'), color:has&&c.avgHoldDays>=30?TC.accentLight:'', tip:'连续在仓的自然日数均值。>15d长期持有，<8d短线轮动'},
  {l:'笔数', v:fmtVal(c.tradeCount,'笔'), color:has&&c.tradeCount>=50?TC.positive:'', tip:'FIFO平仓记录总数=交易数据层'},
  {l:'胜率', v:fmtVal(c.winRate,'%'), color:has&&c.winRate>=55?TC.positive:has&&c.winRate>=40?TC.warning:TC.textMuted, tip:'盈利平仓÷总平仓。>55%为优。需结合赔率判断'},
  {l:'赔率', v:c.payoffRatio!=null?c.payoffRatio.toFixed(2):'—', color:has&&c.payoffRatio>=2?TC.positive:has&&c.payoffRatio>=1?TC.warning:'', tip:'盈利均收益÷亏损均亏损。>2=赚一次够亏两次'},
  {l:'交易盈亏', v:c.totalPnlPct!=null?(c.totalPnlPct>=0?'+':'')+c.totalPnlPct+'%':'—', color:has&&c.totalPnlPct>=0?TC.positive:has&&c.totalPnlPct<0?TC.negative:'', tip:'所有平仓盈亏%之和（未加权仓位）。反映择时质量+品种回报率'},
  {l:'份额', v:fmtVal(c.sectorShare,'%'), color:has&&c.sectorShare>=50?TC.highlight:has&&c.sectorShare>=30?TC.warning:TC.textMuted, tip:'同扇区内的选中次数占比。>50%=扇区主导'},
  {l:'共现', v:c.topCoName||'—', color:TC.accentLight, tip:'最常同时入选Top-N的ETF。揭示组合搭配逻辑'},
 ];
 var html = '';
 for (var i = 0; i < items.length; i++) {
  var it = items[i];
  var tipHtml = it.tip ? ' <i class="tip-icon" style="display:inline-block;width:12px;height:12px;line-height:12px;font-size:9px;border-radius:50%;background:var(--border);color:var(--text-muted);text-align:center;cursor:help;font-style:normal;vertical-align:middle;">?<span class="tip-text">' + it.tip + '</span></i>' : '';
  html += '<div id="contrib-item-' + i + '" style="background:var(--bg-panel);border:1px solid var(--border);border-radius:6px;padding:8px;text-align:center;line-height:1.3;">' +
   '<div style="color:var(--text-muted);font-size:11px;">' + it.l + tipHtml + '</div>' +
   '<div style="font-size:12px;font-weight:700;margin-top:2px;color:' + (it.color || TC.textSecondary) + ';">' + it.v + '</div></div>';
 }
 grid.innerHTML = html;

 var trendLabel = {rising:'↑', stable:'→', declining:'↓'}[c.trend]||c.trend;
 var trendColor = c.trend==='rising'?TC.positive:c.trend==='declining'?TC.negative:TC.textMuted;
 var obsTag = '';
 if (c.observation) obsTag = ' · <span style="color:var(--warning);">观察期 上市' + (c.tradingDays||0) + '天</span>';
 extra.innerHTML =
  '<span>' + (c.sector||'—') + '</span> · ' +
  '<span>趋势 <b style="color:' + trendColor + ';">' + trendLabel + '</b></span>' +
  (has ? '' : ' · <span style="color:var(--negative);">从未入选</span>') + obsTag;
}

/* ====== Trade Replay: full-period price + RSI + 成交额 + 买卖标记 ====== */
async function loadTradeReplay(code) {
 if (!tunerSignalHistory.length) return;
 var startDate = tunerSignalHistory[0].date;
 var endDate = tunerSignalHistory[tunerSignalHistory.length - 1].date;
 var rsiPeriod = 14; // 🔒

 try {
  var resp = await fetch('/api/etf_prices?code=' + code + '&start=' + startDate + '&end=' + endDate + '&rsi_period=' + rsiPeriod + '&freq=' + klineFreq);
  var prices = await resp.json();
  if (prices.error) return;
  renderTradeReplay(code, prices);
 } catch (e) { /* silent */ }
}

// Build trades array for current code + freq, aggregating per-week if needed
function buildTradesForReplay(code, prices) {
 var dateToIdx = {};
 for (var i = 0; i < prices.dates.length; i++) dateToIdx[prices.dates[i]] = i;

 // Helper: find the bar (daily or weekly) that contains a given rebalance date
 function findBarIdx(rbDate) {
  if (dateToIdx[rbDate] != null) return dateToIdx[rbDate];
  // For weekly: find smallest bar.date >= rbDate, but rb is usually last trading day → take that or previous
  for (var k = 0; k < prices.dates.length; k++) {
   if (prices.dates[k] >= rbDate) return k;
  }
  return prices.dates.length - 1;
 }

 // Daily: 1 marker per signal
 if (klineFreq === 'daily') {
  var trades = [];
  var prevPos = 0; // running prev-position tracker
  for (var i = 0; i < tunerSignalHistory.length; i++) {
   var sig = tunerSignalHistory[i];
   var d = sig.detail && sig.detail[code];
   if (!d) {
    // ETF not in universe at this signal? skip but keep prev
    continue;
   }
   // Refine action into NEW / ADJ_UP / ADJ_DOWN / OUT for trades that change position
   var curPos = d.position;
   var refinedAction = '';
   if (curPos > prevPos + 0.01)   refinedAction = prevPos === 0 ? 'new' : 'adj_up';
   else if (curPos < prevPos - 0.01) refinedAction = curPos === 0 ? 'out' : 'adj_down';
   // hold (curPos === prevPos): no marker

   if (refinedAction) {
    trades.push({
     sigIdx: i,
     date: sig.date,
     barIdx: findBarIdx(sig.date),
     price: prices.close[findBarIdx(sig.date)],
     pos: curPos,
     prevPos: prevPos,
     action: refinedAction,
    });
   }
   prevPos = curPos;
  }
  return trades;
 }

 // Weekly: aggregate signals that fall into the same weekly bar by net position change
 // Group signals by their bar index, then compare end-of-week pos vs start-of-week pos
 var groups = {}; // barIdx → [signals in chronological order]
 for (var i = 0; i < tunerSignalHistory.length; i++) {
  var sig = tunerSignalHistory[i];
  var d = sig.detail && sig.detail[code];
  if (!d) continue;
  var idx = findBarIdx(sig.date);
  if (!groups[idx]) groups[idx] = [];
  groups[idx].push({ sigIdx: i, sig: sig, detail: d });
 }

 var trades = [];
 Object.keys(groups).forEach(function(barIdx) {
  var grp = groups[barIdx];
  var last = grp[grp.length - 1];
  var endPos = last.detail.position;
  var prevSigIdx = grp[0].sigIdx - 1;
  var startPos = 0;
  if (prevSigIdx >= 0) {
   var prevDetail = tunerSignalHistory[prevSigIdx].detail && tunerSignalHistory[prevSigIdx].detail[code];
   if (prevDetail) startPos = prevDetail.position;
  }
  var netAction;
  if (endPos > startPos + 0.01)   netAction = startPos === 0 ? 'new' : 'adj_up';
  else if (endPos < startPos - 0.01) netAction = endPos === 0 ? 'out' : 'adj_down';
  else                netAction = '';
  if (!netAction) return;
  trades.push({
   sigIdx: last.sigIdx,
   date: last.sig.date,
   barIdx: parseInt(barIdx),
   price: prices.close[parseInt(barIdx)],
   pos: endPos,
   prevPos: startPos,
   action: netAction,
   _aggregated: grp.length > 1,
  });
 });
 return trades;
}

function renderTradeReplay(code, prices) {
 var dom = $id('kline-replay');
 if (!dom) return;
 if (klineReplayChart) klineReplayChart.dispose();
 klineReplayChart = echarts.init(dom);
 _klineDates = prices.dates;

 var trades = buildTradesForReplay(code, prices);
 // Map: barIdx → trade (for tooltip lookup)
 var barIdxToTrade = {};
 trades.forEach(function(t) { barIdxToTrade[t.barIdx] = t; });

 // ---- Build per-bar position series for the "持仓山脉图" ----
 // For each bar in prices.dates, find the latest signal at-or-before that date,
 // then take its detail[code].position (or 0 if not held).
 var posSeries = new Array(prices.dates.length).fill(0);
 var sigPtr = 0;
 var curPos = 0;
 // Pre-sort signals by date (already sorted but be safe)
 var sortedSigs = tunerSignalHistory.slice();
 for (var i = 0; i < prices.dates.length; i++) {
  var bd = prices.dates[i];
  while (sigPtr < sortedSigs.length && sortedSigs[sigPtr].date <= bd) {
   var sd = sortedSigs[sigPtr].detail && sortedSigs[sigPtr].detail[code];
   curPos = sd ? (sd.position || 0) : 0;
   sigPtr++;
  }
  posSeries[i] = curPos;
 }

 var markPoints = trades.map(function(t) {
  // 4 action types: new (深绿上三角) / adj_up (浅绿上三角) / adj_down (浅红下三角) / out (深红下三角)
  var spec;
  if (t.action === 'new')      spec = { color:TC.positive, up:true, size:13 };
  else if (t.action === 'adj_up')  spec = { color:'#34d399', up:true, size:11 };
  else if (t.action === 'adj_down') spec = { color:'#fca5a5', up:false, size:11 };
  else if (t.action === 'out')   spec = { color:TC.negative, up:false, size:13 };
  else               spec = { color:TC.textMuted, up:true, size:9 };
  return {
   name: t.action.toUpperCase().replace('_', ' ') + ' ' + t.pos.toFixed(0) + '%',
   coord: [prices.dates[t.barIdx], t.price],
   symbol: 'triangle',
   symbolRotate: spec.up ? 0 : 180,
   symbolSize: spec.size,
   itemStyle: { color: spec.color, borderColor: TC.bgBody, borderWidth: 1 },
   label: { show: false },
   _sigIdx: t.sigIdx,
  };
 });

 // Detect intraday: split close series for dashed estimated segment on kline-replay
 var lastDate2 = prices.dates[prices.dates.length - 1];
 var isIntradayKline = isIntradayEstimateDate(lastDate2);
 var closeConfirmed = prices.close.slice();
 var closeEstimated = new Array(prices.close.length).fill(null);
 if (isIntradayKline && prices.close.length >= 2) {
  closeConfirmed[prices.close.length - 1] = null;
  closeEstimated[prices.close.length - 2] = prices.close[prices.close.length - 2];
  closeEstimated[prices.close.length - 1] = prices.close[prices.close.length - 1];
 }

 // Pad kline data to navDates so echarts.connect index-based sync (axisPointer + dataZoom)
 // matches date-for-date between nav-chart and kline-replay.
 // Always pad: even when lengths match, dates may differ slightly.
 var _lookup = Object.create(null);
 for (var _i = 0; _i < navDates.length; _i++) { _lookup[navDates[_i]] = _i; }

 function _pad(arr, def) {
  var out = new Array(navDates.length);
  for (var j = 0; j < navDates.length; j++) out[j] = def;
  for (var k = 0; k < prices.dates.length; k++) {
   var ni = _lookup[prices.dates[k]];
   if (ni != null) out[ni] = arr[k];
  }
  return out;
 }

 // Pad price / indicator series
 prices.close  = _pad(prices.close, null);
 prices.rsi   = _pad(prices.rsi, null);
 prices.volume  = _pad(prices.volume, null);
 closeConfirmed = _pad(closeConfirmed, null);
 closeEstimated = _pad(closeEstimated, null);
 posSeries    = _pad(posSeries, 0);

 // Remap trade markers
 markPoints.forEach(function(mp) {
  if (mp.coord) {
   var ni2 = _lookup[mp.coord[0]];
   if (ni2 != null) mp.coord[0] = navDates[ni2];
  }
 });
 var _newTrade = Object.create(null);
 Object.keys(barIdxToTrade).forEach(function(oldIdx) {
  var dt = prices.dates[parseInt(oldIdx)];
  var ni3 = dt ? _lookup[dt] : null;
  if (ni3 != null) _newTrade[ni3] = barIdxToTrade[oldIdx];
 });
 barIdxToTrade = _newTrade;

 prices.dates = navDates;

 var curDate = currentSelectedDate;

 // Custom tooltip formatter: dedupe date + show trade if exists
 var nameMap = window.etfNameMap || {};
 var actionLabel = { new: 'NEW (建仓)', adj_up: 'UP ⬆ (加仓)', adj_down: 'DOWN ⬇ (减仓)', out: 'OUT (清仓)' };
 function fmtTooltip(params) {
  if (!params || !params.length) return '';
  var dateStr = params[0].axisValue;
  var idx = params[0].dataIndex;
  var price = prices.close[idx];
  var rsi = prices.rsi[idx];
  var vol = prices.volume[idx];
  var pct = idx > 0 && prices.close[idx - 1] ? (price / prices.close[idx - 1] - 1) * 100 : null;
  var pos = posSeries[idx];
  var html = '<div style="font-weight:600;margin-bottom:4px;color:var(--text-body);">' + dateStr + '</div>';
  html += '<div style="color:var(--text-secondary);font-size:11px;line-height:1.6;">';
  html += '价格 <b style="color:var(--text-body)">' + (price != null ? price.toFixed(3) : '-') + '</b>';
  if (pct != null) html += ' · 涨跌幅 <b style="color:' + (pct >= 0 ? TC.negative : TC.positive) + '">' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%</b>';
  html += '<br/>当前持仓 <b style="color:' + (pos > 0 ? TC.positive : TC.textMuted) + '">' + pos.toFixed(1) + '%</b><br/>';
  if (rsi != null) html += 'RSI <b style="color:var(--warning)">' + rsi.toFixed(1) + '</b><br/>';
  if (vol != null) html += (prices.volumeLabel || '成交') + ' <b style="color:var(--text-body)">' + (vol >= 1e8 ? (vol/1e8).toFixed(2)+' 亿' : vol >= 1e4 ? (vol/1e4).toFixed(1)+' 万' : vol.toFixed(0)) + '</b>';
  if (isIntradayEstimateDate(dateStr)) { var summary = lastBacktestData && lastBacktestData.summary || {}; html += '<br/><span style="color:var(--highlight)">盘中估算' + (summary.intradayTime ? ' ' + summary.intradayTime : '') + '：价格为实时价，成交额为预估全天值</span>'; }
  html += '</div>';
  var t = barIdxToTrade[idx];
  if (t) {
   html += '<div style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.08);font-size:11px;">';
   html += (actionLabel[t.action] || t.action.toUpperCase()) + ' → 仓位 <b style="color:var(--positive)">' + t.pos.toFixed(1) + '%</b>';
   if (t._aggregated) html += ' <span style="color:var(--text-muted);">(本周聚合)</span>';
   html += '</div>';
  }
  return html;
 }

 klineReplayChart.setOption({
  backgroundColor: 'transparent',
  tooltip: {
   trigger: 'axis',
   axisPointer: { type: 'cross' },
   backgroundColor: 'rgba(10,25,47,0.95)',
   borderColor: 'rgba(59,130,246,0.2)',
   textStyle: { color: TC.textBody, fontSize: 11 },
   formatter: fmtTooltip,
  },
  axisPointer: { link: [{ xAxisIndex: 'all' }] },
  legend: {
   data: [
    { name: '价格', icon: 'rect' },
    { name: '盘中估算', icon: 'rect' },

    { name: '🏔️ 持仓', icon: 'rect' },
    { name: 'RSI'+(prices.rsiPeriod||14), icon: 'rect' },
    { name: '📊 ' + (prices.volumeLabel || '成交额'), icon: 'rect' },
   ],
   textStyle: { color: TC.textSecondary, fontSize: 10 },
   top: 0, right: 8,
  },
  grid: [
   { left: 50, right: 20, top: 28,  height: 220 },  // 价格
   { left: 50, right: 20, top: 256,  height: 50 },  // RSI
   { left: 50, right: 20, top: 312,  height: 30 },  // 成交
  ],
  xAxis: [
   { type:'category', data:prices.dates, scale:true, boundaryGap:false, axisLine:{lineStyle:{color:TC.border}}, axisLabel:{show:false}, splitLine:{show:false} },
   { type:'category', gridIndex:1, data:prices.dates, scale:true, boundaryGap:false, axisLine:{lineStyle:{color:TC.border}}, axisLabel:{show:false}, splitLine:{show:false} },
   { type:'category', gridIndex:2, data:prices.dates, scale:true, boundaryGap:false, axisLine:{lineStyle:{color:TC.border}}, axisLabel:{color:TC.textMuted,fontSize:9}, splitLine:{show:false} },
  ],
  yAxis: [
   // grid 0 价格 (左)
   { scale:true, axisLabel:{color:TC.textMuted,fontSize:9}, splitLine:{lineStyle:{color:'rgba(255,255,255,0.05)'}} },
   // grid 1 RSI
   { scale:true, gridIndex:1, axisLabel:{color:TC.textMuted,fontSize:9}, splitLine:{show:false}, max:100, min:0, splitNumber:2 },
   // grid 2 成交额 (亿元)
   { scale:true, gridIndex:2, axisLabel:{color:TC.textMuted,fontSize:9, formatter:function(v){return (v/1e8).toFixed(1)+'亿'}}, splitLine:{show:false}, splitNumber:2 },
   // grid 0 持仓 (右)
   { gridIndex:0, position:'right', min:0, max:100, splitNumber:2,
    axisLabel:{show:false},
    splitLine:{show:false},
    axisLine:{show:false},
   },
  ],
  visualMap: {
   show: false,
   seriesIndex: 1, // applies to "持仓" series
   pieces: [
    { gte: 30, color: 'rgba(16,185,129,0.18)' },   // 重仓 (深绿浅填)
    { gte: 10, lt: 30, color: 'rgba(16,185,129,0.32)' }, // 中仓 (中绿)
    { gt: 0, lt: 10, color: 'rgba(16,185,129,0.50)' },  // 轻仓 (亮绿)
    { value: 0, color: 'rgba(255,255,255,0)' },      // 空仓 (透明)
   ],
  },
  dataZoom: [
   { type:'inside', xAxisIndex:[0,1,2], start:0, end:100, groupId:'tuner-zoom' },
  ],
  series: [
   {
    name: '价格', type: 'line', data: closeConfirmed, showSymbol: false,
    color: TC.textSecondary,
    lineStyle: { color: TC.textSecondary, width: 1.2 },
    areaStyle: { color: 'rgba(148,163,184,0.08)' },
    connectNulls: false,
    markPoint: { data: markPoints, animation: false, z: 10 },
    markLine: curDate ? {
     silent: true, symbol: 'none',
     data: [{ xAxis: curDate, lineStyle:{color:TC.negative,width:1.5,type:'dashed'} }],
     label: { show: false },
    } : undefined,
   },
   {
    name: '盘中估算', type: 'line', data: closeEstimated, showSymbol: false,
    color: TC.accentLight,
    lineStyle: { color: TC.accentLight, width: 2, type: 'dashed' },
    connectNulls: false,
    markPoint: { data: markPoints, animation: false, z: 10 },
   },
   {
    name: '🏔️ 持仓', type: 'line',
    yAxisIndex: 3, // bind to right Y axis
    color: TC.positive,
    data: posSeries,
    showSymbol: false,
    step: 'end', // 90° vertical jumps at rebalance
    lineStyle: { color: 'rgba(16,185,129,0.55)', width: 1 },
    areaStyle: { color: 'rgba(16,185,129,0.18)' }, // base color (visualMap overrides)
    z: 1, // behind price line
   },
   {
    name: 'RSI'+(prices.rsiPeriod||14), type: 'line',
    xAxisIndex: 1, yAxisIndex: 1,
    color: TC.warning,
    data: prices.rsi || [], showSymbol: false,
    lineStyle: { color: TC.warning, width: 1.2 },
    markLine: { silent:true, symbol:'none', label:{show:false},
     data: [
      { yAxis:70, lineStyle:{color:'rgba(239,68,68,0.3)',type:'dashed',width:1} },
      { yAxis:30, lineStyle:{color:'rgba(16,185,129,0.3)',type:'dashed',width:1} },
     ]
    },
   },
   {
    name: '📊 ' + (prices.volumeLabel || '成交额'), type: 'bar',
    xAxisIndex: 2, yAxisIndex: 2,
    color: '#64748b', // legend swatch color
    data: prices.volume || [],
    itemStyle: {
     color: function(p) {
      // Color by current bar's net direction (close vs prev close)
      var idx = p.dataIndex;
      if (isIntradayEstimateDate(prices.dates[idx])) return 'rgba(245,158,11,0.75)';
      if (idx === 0) return 'rgba(148,163,184,0.5)';
      var prev = prices.close[idx - 1], cur = prices.close[idx];
      return cur >= prev ? 'rgba(239,68,68,0.55)' : 'rgba(16,185,129,0.55)';
     }
    },
   },
  ],
 });

 // Click marker → jump to its date; Click series/blank → jump to nearest signal
 klineReplayChart.off('click');
 klineReplayChart.on('click', function(params) {
  if (params.componentType === 'markPoint' && params.data && params.data._sigIdx != null) {
   var sig = tunerSignalHistory[params.data._sigIdx];
   if (sig) { _klineJumpMarkPoint = true; jumpToSnapshotByDate(sig.date); }
  }
 });
 // ZR-level click — handles candlestick / blank area / volume bar (not markPoint)
 klineReplayChart.getZr().off('click');
 klineReplayChart.getZr().on('click', function(params) {
  if (_klineJumpMarkPoint) { _klineJumpMarkPoint = false; return; }
  var pointInPixel = [params.offsetX, params.offsetY];
  var inGrid = klineReplayChart.containPixel({gridIndex: 0}, pointInPixel) ||
         klineReplayChart.containPixel({gridIndex: 1}, pointInPixel) ||
         klineReplayChart.containPixel({gridIndex: 2}, pointInPixel);
  if (!inGrid) return;
  var dataIdx = klineReplayChart.convertFromPixel({xAxisIndex: 0}, pointInPixel[0]);
  if (dataIdx == null || dataIdx < 0 || dataIdx >= prices.dates.length) return;
  jumpToSnapshotByDate(prices.dates[Math.round(dataIdx)]);
 });

 // Connect all three charts for shared axisPointer + dataZoom sync
 if (navChart && ddChart && klineReplayChart) {
  var navOpt = navChart.getOption(), ddOpt = ddChart.getOption(), klOpt = klineReplayChart.getOption();
  var navLen = (navOpt.xAxis && navOpt.xAxis[0] && navOpt.xAxis[0].data) ? navOpt.xAxis[0].data.length : '?';
  var ddLen = (ddOpt.xAxis && ddOpt.xAxis[0] && ddOpt.xAxis[0].data) ? ddOpt.xAxis[0].data.length : '?';
  var klLen = (klOpt.xAxis && klOpt.xAxis[0] && klOpt.xAxis[0].data) ? klOpt.xAxis[0].data.length : '?';
  echarts.connect([navChart, ddChart, klineReplayChart]);
 }

 // Push nav's current zoom state to kline via ECharts' internal group sync —
 // the same mechanism used by user drag/zoom, guaranteeing identical alignment.
 if (navChart) {
  var _navDz = (navChart.getOption().dataZoom || [{}])[0];
  if (_navDz.start != null && (_navDz.start > 0 || _navDz.end < 100)) {
   navChart.dispatchAction({ type: 'dataZoom', start: _navDz.start, end: _navDz.end });
  }
 }
}

/* ====== URL parameter support ====== */
// getParams()/setParams() are the single source of truth for parameter names and types.
// New params auto-supported: just add them to getParams() + setParams(), URL picks them up.

function getSliderFromDate(dateStr) {
 var target = new Date(dateStr);
 target.setHours(0, 0, 0, 0);
 var now = new Date();
 now.setHours(0, 0, 0, 0);
 var diff = Math.round((now - target) / 86400000);
 var MAX = periodMaxDays();
 return MAX - diff;
}

function applyUrlParams() {
 var qs = new URLSearchParams(location.search);
 if (!qs.has('w1') && !qs.has('rebalance_freq') && !qs.has('start_date') && !qs.has('universe')) return false;

 var defaults = getParams();
 var p = {};
 Object.keys(defaults).forEach(function(key) {
  if (!qs.has(key)) return;
  var raw = qs.get(key);
  if (key === 'start_date' || key === 'end_date' || key === 'rebalance_freq' || key === 'conf_type' || key === 'universe') {
   p[key] = raw;
  } else {
   p[key] = parseFloat(raw);
  }
 });

 setParams(p);

 // start_date is fixed at 2026-05-01 — ignore URL param
 if (p.end_date) {
  var ev = getSliderFromDate(p.end_date);
  if (ev < 0) { setPeriodSpan(6); ev = getSliderFromDate(p.end_date); }
  $id('end_date_slider').value = ev;
 }
 onPeriodSliderChange();
 return true;
}

/* ====== Init ====== */
async function waitForReady() {
 var overlay = document.getElementById('tuner-loading');
 for (var i = 0; i < 120; i++) {
  try {
   var resp = await fetch('/api/data_status');
   var data = await resp.json();
   if (data.ready) break;
  } catch(e) {
   // Server not listening yet — keep waiting
  }
  await new Promise(function(r) { setTimeout(r, 500); });
 }
 if (overlay) overlay.style.display = 'none';
}

function initSliderSteppers() {
 document.querySelectorAll('.slider-group input[type=range]').forEach(function(input) {
  if (input.closest('.slider-row')) return; // already wrapped

  var step = parseFloat(input.getAttribute('data-step')) || parseFloat(input.step) || 1;
  var min = parseFloat(input.min);
  var max = parseFloat(input.max);

  var row = document.createElement('div');
  row.className = 'slider-row';

  var btnL = document.createElement('button');
  btnL.className = 'btn-step';
  btnL.textContent = '◀';
  btnL.setAttribute('aria-label', 'Decrease');
  btnL.addEventListener('click', function() {
   var val = parseFloat(input.value);
   if (isNaN(val)) return;
   var newVal = val - step;
   if (!isNaN(min)) newVal = Math.max(min, newVal);
   input.value = newVal;
   input.dispatchEvent(new Event('input', {bubbles: true}));
  });

  var btnR = document.createElement('button');
  btnR.className = 'btn-step';
  btnR.textContent = '▶';
  btnR.setAttribute('aria-label', 'Increase');
  btnR.addEventListener('click', function() {
   var val = parseFloat(input.value);
   if (isNaN(val)) return;
   var newVal = val + step;
   if (!isNaN(max)) newVal = Math.min(max, newVal);
   input.value = newVal;
   input.dispatchEvent(new Event('input', {bubbles: true}));
  });

  // Wrap: insert row before input, then move input + buttons into row
  input.parentNode.insertBefore(row, input);
  row.appendChild(btnL);
  row.appendChild(input);
  row.appendChild(btnR);
 });
}

window.addEventListener('load', async function() {
 await waitForReady();

 initSliderSteppers();

 setConfType(confType); // sync panel visibility with default confType
 drawConfChart();
 validateBullBear(); // initial bull/bear validation
 // Default start date fixed at 2026-05-01 (same as Server酱 push window)
 $id('start_date_slider').value = getSliderFromDate('2026-05-01');
 onPeriodSliderChange(); // initialize date labels
 await loadParamSchema();
 var leftPanel = $id('tuner-panel-left');
 if (leftPanel) {
  leftPanel.addEventListener('input', function() { renderParamSchemaTable(); });
  leftPanel.addEventListener('change', function() { renderParamSchemaTable(); });
 }
 var fromUrl = applyUrlParams();
 // Restore cached backtest first (params may be stale — presets will overwrite)
 if (!fromUrl) {
  try {
   var cachedResp = await fetch('/api/last_result');
   var cached = await cachedResp.json();
   if (cached.cached && cached.params && cached.result) {
    setParams(cached.params);
    lastResult = cached.result;
    renderResults(cached.result);
    switchRightView('results');
   }
  } catch(e) { console.warn('Cache restore skipped:', e); }
 }
 await loadPresets();
 await loadFrontier(); // REQ-375: load metrics for preset cards
 // Safety net for non-frontier schools:
 if (CURRENT_PRESET === null || CURRENT_PRESET === undefined) {
  var defaultPreset = PRESETS['gam-0'] ? 'gam-0' : Object.keys(PRESETS)[0];
  if (defaultPreset) applyPreset(defaultPreset);
 }
});
window.addEventListener('resize', () => {
 navChart && navChart.resize();
 (ddChart && ddChart.resize(), distChart && distChart.resize());
 klineReplayChart && klineReplayChart.resize();
 hmChart && hmChart.resize();
 f1ZohChart && f1ZohChart.resize();
});
/* ====== Keyboard shortcuts + help overlay ====== */
function toggleShortcuts() {
 var ov = document.getElementById('shortcuts-overlay');
 ov.classList.toggle('show');
}
document.addEventListener('keydown', function(e) {
 if (e.key === 'Escape') {
  var ov = document.getElementById('shortcuts-overlay');
  if (ov.classList.contains('show')) { ov.classList.remove('show'); return; }
 }
 if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
 if (e.key === '?') { e.preventDefault(); toggleShortcuts(); }
 else if (e.key === '3') { e.preventDefault(); applyPreset('cst-1'); pulseBtn(); }
 else if (e.key === 'r' || e.key === 'R') { e.preventDefault(); runBacktest(); }
 else if (e.key === 's' || e.key === 'S') { e.preventDefault(); saveYAML(); }
 else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && currentSelectedDate && navDates.length) {
  e.preventDefault();
  var dates = navDates;
  var idx = dates.indexOf(currentSelectedDate);
  if (idx === -1) {
   for (var i = 0; i < dates.length; i++) { if (dates[i] >= currentSelectedDate) { idx = i; break; } }
  }
  if (e.key === 'ArrowLeft' && idx > 0) { jumpToSnapshotByDate(dates[idx - 1]); }
  else if (e.key === 'ArrowRight' && idx < dates.length - 1) { jumpToSnapshotByDate(dates[idx + 1]); }
  else { return; }
  updateNavMarkLine();
  updateTradeReplayMarkLine();
 }
 else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
  var rows = document.querySelectorAll('#tuner-snapshot-body .snap-row');
  if (!rows.length) return;
  var sel = document.querySelector('#tuner-snapshot-body .snap-row.snap-sel');
  if (!sel) return;
  var curIdx = -1;
  for (var i = 0; i < rows.length; i++) { if (rows[i] === sel) { curIdx = i; break; } }
  if (curIdx < 0) return;
  var nextIdx = e.key === 'ArrowUp' ? curIdx - 1 : curIdx + 1;
  if (nextIdx < 0 || nextIdx >= rows.length) return;
  e.preventDefault();
  rows[nextIdx].click();
  rows[nextIdx].scrollIntoView({block:'nearest'});
 }
});

/* ====== Heatmap ====== */
var HM_CELL = 40;
var HM_GRID_L = 2, HM_GRID_R = 2, HM_GRID_T = 10, HM_GRID_B = 2;
var HM_ETF_COL_W = 110; // narrowed to fit dot + 2-line text
var hmPeriod = 20, hmSortDate = null, hmSortDir = 0;
var hmETFList = []; // {code, name, sector, group1}
var hmOrder = [];
var groupLevel = 'sector'; // 'sector' or 'group1'
var GROUP1_COLORS = {'成长进攻':TC.accent,'周期博弈':TC.warning,'防御避险':'#78716c','跨市场另类':TC.purple};
function toggleGroupLevel() {
 groupLevel = groupLevel === 'sector' ? 'group1' : 'sector';
 $id('group-level-btn').textContent = groupLevel === 'sector' ? '二级' : '一级';
 if (tunerSignalHistory && tunerSignalHistory.length) renderTunerSnapshot(tunerSnapshotIdx);
}
var hmAllDates = [];
var hmChart = null;
var hmInited = false;
var hmLoading = false;

var HM_SEC_COLOR = {};
var HM_SEC_ORDER = [];

function initHeatmap() {
 if (hmLoading) return;
 // If already rendered, just resize; data is fresh
 if (hmInited) { if (hmChart) hmChart.resize(); return; }
 hmLoading = true;
 $id('hm-loading').style.display = '';

 // Fetch presets to get universe metadata, then load heatmap data
 fetch('/api/presets')
  .then(function(r) { return r.json(); })
  .then(function(data) {
   var opts = data._universe_options || [];
   hmETFList = opts.map(function(e) { return { code: e.code, name: (e.name||'').replace(/ETF$/,''), sector: e.sector, group1: e.group1||'', marginable: e.marginable !== false }; });
   // Build sector color map
   var secs = [];
   hmETFList.forEach(function(e) {
    if (secs.indexOf(e.sector) < 0) secs.push(e.sector);
   });
   HM_SEC_ORDER = secs;
   var secPalette = {
    '科技': TC.accent, '新能源': TC.greenLight, '制造': TC.cyan,
    '资源周期': TC.warning, '医药': TC.negative, '金融': '#eab308',
    '传统': '#78716c', '消费': TC.orange, '平台经济': '#6366f1',
    '海外科技': TC.purple, '另类': '#c084fc'
   };
   secs.forEach(function(s) { HM_SEC_COLOR[s] = secPalette[s] || TC.textMuted; });
   // Sector legend
   var leg = $id('hm-sec-legend');
   leg.innerHTML = secs.map(function(s) { return '<span class="hm-sec-item"><span class="hm-sec-dot" style="background:' + HM_SEC_COLOR[s] + ';"></span>' + s + '</span>'; }).join('')
    + '<span class="hm-sec-bar"><span>-50%</span><span class="hm-sec-bar-grad"></span><span>+50%</span></span>';

   return fetch('/api/heatmap_data?lookback=' + hmPeriod);
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
   hmAllDates = data.dates || [];
   var retByCode = {};
   (data.etfs || []).forEach(function(e) { retByCode[e.code] = e.returns; });
   hmETFList.forEach(function(e) { e._returns = retByCode[e.code] || []; });
   hmLoading = false;
   hmInited = true;
   $id('hm-loading').style.display = 'none';
   renderHeatmap();
  })
  .catch(function(e) { console.error('Heatmap init error:', e); hmLoading = false; });
}

function loadHeatmapData() {
 fetch('/api/heatmap_data?lookback=' + hmPeriod)
  .then(function(r) { return r.json(); })
  .then(function(data) {
   hmAllDates = data.dates || [];
   // Build return lookup: {code: [ret, ret, ...]} aligned to hmAllDates
   var retByCode = {};
   (data.etfs || []).forEach(function(e) { retByCode[e.code] = e.returns; });
   // Attach returns to hmETFList (maintain order from presets)
   hmETFList.forEach(function(e) {
    e._returns = retByCode[e.code] || [];
   });
   hmLoading = false;
   hmInited = true;
   $id('hm-loading').style.display = 'none';
   renderHeatmap();
  })
  .catch(function(e) { console.error('Heatmap data error:', e); hmLoading = false; });
}

function hmSectorOrder() {
 return hmETFList.map(function(_, i) { return i; }).sort(function(a, b) {
  var sa = HM_SEC_ORDER.indexOf(hmETFList[a].sector);
  var sb = HM_SEC_ORDER.indexOf(hmETFList[b].sector);
  if (sa !== sb) return sa - sb;
  return hmETFList[a].code.localeCompare(hmETFList[b].code);
 });
}

function renderHeatmap() {
 var p = hmPeriod, nRows = hmETFList.length, nCols = Math.min(90, hmAllDates.length);
 if (!nRows || !nCols) return;
 // Render only the most recent nCols
 var colOffset = hmAllDates.length - nCols;
 var visDates = hmAllDates.slice(colOffset);

 // Order
 if (hmSortDir === 0) {
  hmOrder = hmSectorOrder();
 } else {
  var di = hmAllDates.indexOf(hmSortDate);
  if (di < 0) { hmOrder = hmSectorOrder(); hmSortDir = 0; }
  else {
   hmOrder = hmETFList.map(function(_, i) { return i; }).sort(function(a, b) {
    var ra = hmETFList[a]._returns[di], rb = hmETFList[b]._returns[di];
    ra = (ra == null) ? 0 : ra; rb = (rb == null) ? 0 : rb;
    return hmSortDir === 1 ? rb - ra : ra - rb; // 1=desc (top gainers first), 2=asc
   });
  }
 }

 // Heatmap data: [col, row, value] — only the most recent nCols
 var hd = [];
 hmOrder.forEach(function(ei, row) {
  var rets = hmETFList[ei]._returns;
  for (var ci = 0; ci < nCols; ci++) {
   var v = rets[colOffset + ci];
   if (v != null) hd.push([ci, row, parseFloat(v.toFixed(3))]);
  }
 });

 var yLabels = hmOrder.map(function(i) { return hmETFList[i].code; });

 // ---- External ETF label column ----
 var etfCol = $id('hm-etf-labels');
 etfCol.style.paddingTop = '0px';
 // Adjust chart grid top to 0 since date header is now above viewport
 var gridTop = 0;
 var etfHtml = '';
 hmOrder.forEach(function(i) {
  var e = hmETFList[i];
  var dot = HM_SEC_COLOR[e.sector] || TC.textMuted;
  etfHtml += '<div style="height:' + HM_CELL + 'px;display:flex;align-items:center;gap:6px;padding-left:8px;padding-right:6px;">' +
   '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + dot + ';flex-shrink:0;"></span>' +
   '<div style="line-height:1.3;overflow:hidden;">' +
    '<div style="font-size:10px;color:#c0c0c0;white-space:nowrap;">' + (e.name || e.code) + '</div>' +
    '<div style="font-size:9px;color:var(--text-muted);font-family:monospace;">' + e.code + '</div>' +
   '</div></div>';
 });
 etfCol.innerHTML = etfHtml;

 // ---- Chart ----
 var chartW = HM_GRID_L + nCols * HM_CELL + HM_GRID_R;
 var chartH = gridTop + nRows * HM_CELL + HM_GRID_B;

 var dom = $id('hm-chart');
 if (hmChart) hmChart.dispose();
 hmChart = echarts.init(dom);
 dom.style.width = chartW + 'px';
 dom.style.height = chartH + 'px';

 hmChart.setOption({
  grid: { left: HM_GRID_L, right: HM_GRID_R, top: gridTop, bottom: HM_GRID_B },
  xAxis: {
   type: 'category', data: visDates,
   axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }
  },
  yAxis: {
   type: 'category', data: yLabels, inverse: true,
   axisLabel: { show: false },
   axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }
  },
  visualMap: {
   min: -0.50, max: 0.50, show: false,
   inRange: { color: ['#8b1515','#ae1c1c','#d32222','#e83a3a','#f56565',TC.bgHover,'#0f7a34','#109b3d','#1cc04d','#3cdb78'] }
  },
  tooltip: {
   backgroundColor: TC.bgHover, borderColor: '#3b4a5a',
   textStyle: { color: TC.textBody, fontSize: 12 },
   formatter: function(p) {
    var etf = hmETFList[hmOrder[p.value[1]]];
    var r = (p.value[2] * 100).toFixed(1);
    return '<b>' + etf.code + ' ' + (etf.name||'') + '</b> <span style="color:' + (HM_SEC_COLOR[etf.sector]||'#999') + '">' + (etf.sector||'') + '</span><br/>' +
     '日期: ' + (visDates[p.value[0]]||'').slice(0,10) + '<br/>' +
     hmPeriod + '日涨跌: <b style="color:' + (r>=0?TC.positive:TC.negative) + '">' + r + '%</b>';
   }
  },
  series: [{
   type: 'heatmap', data: hd,
   label: { show: true, fontSize: 10, fontFamily: 'Consolas,monospace', color: TC.textHeading,
    textShadowColor: 'rgba(0,0,0,0.7)', textShadowBlur: 1.5,
    formatter: function(p) { return (p.value[2] * 100).toFixed(1); } },
   emphasis: { itemStyle: { shadowBlur: 6, borderColor: '#fff', borderWidth: 1.5 }, label: { show: true, color: '#fff', textShadowColor: 'rgba(0,0,0,0.8)', textShadowBlur: 2 } }
  }]
 });

 // Date label row
 var dateRow = $id('hm-date-row');
 // Match chart total width exactly so scrollLeft sync is pixel-perfect
 // Chart = GRID_L + nCols*CELL + GRID_R. Under border-box, padding-left:2 is inside this width.
 dateRow.style.width = (HM_GRID_L + nCols * HM_CELL + HM_GRID_R) + 'px';
 var html = '';
 for (var ci = 0; ci < nCols; ci++) {
  var d = visDates[ci];
  var cls = 'hm-date-cell';
  if (hmSortDate === d) cls += hmSortDir === 1 ? ' sort-desc' : ' sort-asc';
  html += '<span class="' + cls + '" data-date="' + d + '">' + d.slice(5) + '</span>';
 }
 dateRow.innerHTML = html;
 dateRow.onclick = function(e) {
  var cell = e.target.closest('.hm-date-cell');
  if (!cell) return;
  var d = cell.dataset.date;
  if (hmSortDate === d) hmSortDir = (hmSortDir + 1) % 3;
  else { hmSortDate = d; hmSortDir = 1; }
  updateHmSortUI();
  renderHeatmap();
 };

 // Force resize after container becomes visible (fixes white cells on first view)
 setTimeout(function() { if (hmChart) hmChart.resize(); }, 120);

 window._visDates = visDates;
 // Double rAF ensures layout is complete before reading clientWidth
 var _nc = nCols, _nr = nRows;
 requestAnimationFrame(function() {
  requestAnimationFrame(function() { initHmSliders(_nc, _nr); });
 });
}

function initHmSliders(nCols, nRows) {
 var vp = $id('hm-viewport');
 var cdom = $id('hm-chart');
 var dw = $id('hm-date-wrap');
 var etfColWrap = $id('hm-etf-labels-wrap');
 var hTrack = $id('hm-hslider-track');
 var hWin = $id('hm-hslider-win');
 var vd = window._visDates || [];

 // Mutable state (refreshed on resize)
 var st = {}; // { tw, chartW, vpW, maxSX, w }
 function refreshDims() {
  // Use getBoundingClientRect for synchronous layout; derive tw from vpW (they share same left/right margins)
  st.vpW = vp.getBoundingClientRect().width;
  st.chartW = HM_GRID_L + nCols * HM_CELL + HM_GRID_R;
  st.maxSX = Math.max(0, st.chartW - st.vpW);
  // Track is 8px narrower than viewport: parent padding 4px×2, track has no horizontal margin
  st.tw = Math.max(1, st.vpW - 8);
  var hRatio = Math.min(1, st.vpW / Math.max(1, st.chartW));
  st.w = Math.max(8, hRatio * st.tw); // minimum 8px for usability
  hWin.style.display = '';
  // Keep handle within bounds after resize
  var curLeft = parseFloat(hWin.style.left) || 0;
  var maxLeft = Math.max(0, st.tw - st.w);
  if (curLeft > maxLeft) { hWin.style.left = maxLeft + 'px'; }
 }

 function hToView() {
  var left = parseFloat(hWin.style.left) || 0;
  var pct = st.tw > st.w ? left / (st.tw - st.w) : 0;
  vp.scrollLeft = Math.round(pct * st.maxSX);
  dw.scrollLeft = vp.scrollLeft;
 }
 function hFromView() {
  var pct = st.maxSX > 0 ? vp.scrollLeft / st.maxSX : 0;
  hWin.style.left = Math.round(pct * (st.tw - st.w)) + 'px';
  dw.scrollLeft = vp.scrollLeft;
 }
 var vd = window._visDates || [];

 // Initial setup — start at rightmost (latest dates)
 refreshDims();
 hWin.style.width = st.w + 'px';
 hWin.style.left = Math.max(0, st.tw - st.w) + 'px';
 hToView(); // sync viewport scroll to slider position

 // ResizeObserver on viewport (handles tab switch, layout change, window resize — more reliable than window.resize)
 if (window._hmResizeObserver) window._hmResizeObserver.disconnect();
 window._hmResizeObserver = new ResizeObserver(function() {
  refreshDims();
  if (st.w > 0) {
   hWin.style.width = st.w + 'px';
   var maxLeft = Math.max(0, st.tw - st.w);
   var curLeft = parseFloat(hWin.style.left) || 0;
   if (curLeft > maxLeft) hWin.style.left = maxLeft + 'px';
   hToView();
  }
  refreshVDims();
  vWin.style.height = vst.vh + 'px';
  vFromView();
 });
 window._hmResizeObserver.observe(vp);

 var hDrag = false, hsx = 0, hsl = 0;
 hWin.onmousedown = function(e) { hDrag = true; hsx = e.clientX; hsl = parseFloat(hWin.style.left)||0; e.preventDefault(); };
 hTrack.onmousedown = function(e) {
  if (e.target === hWin) return;
  var nl = e.clientX - hTrack.getBoundingClientRect().left - st.w/2;
  nl = Math.max(0, Math.min(nl, st.tw - st.w));
  hWin.style.left = nl + 'px'; hToView();
 };
 document.addEventListener('mousemove', function(e) {
  if (!hDrag) return;
  var nl = hsl + (e.clientX - hsx);
  nl = Math.max(0, Math.min(nl, st.tw - st.w));
  hWin.style.left = nl + 'px'; hToView();
 });
 document.addEventListener('mouseup', function() { hDrag = false; });

 // -- V slider --
 var vTrack = $id('hm-vslider-track');
 var vWin = $id('hm-vslider-win');

 var vst = {}; // { th, vh, maxSY }
 function refreshVDims() {
  vst.th = vTrack.getBoundingClientRect().height;
  var vpHeight = vp.getBoundingClientRect().height;
  var chartHeight = nRows * HM_CELL + HM_GRID_B;
  var vRatio = Math.min(1, vpHeight / Math.max(1, chartHeight));
  vst.vh = Math.max(24, vRatio * vst.th);
  vst.maxSY = Math.max(0, chartHeight - vpHeight);
  if (vRatio >= 1) { vWin.style.display = 'none'; }
  else { vWin.style.display = ''; }
  var curTop = parseFloat(vWin.style.top) || 0;
  var maxTop = Math.max(0, vst.th - vst.vh);
  if (curTop > maxTop) { vWin.style.top = maxTop + 'px'; }
 }

 function vToView() {
  var top = parseFloat(vWin.style.top) || 0;
  var pct = vst.th > vst.vh ? top / (vst.th - vst.vh) : 0;
  vp.scrollTop = Math.round(pct * vst.maxSY);
  etfColWrap.scrollTop = vp.scrollTop;
 }
 function vFromView() {
  if (vst.maxSY <= 0) return;
  var pct = Math.min(1, vp.scrollTop / vst.maxSY);
  vWin.style.top = Math.round(pct * (vst.th - vst.vh)) + 'px';
  etfColWrap.scrollTop = vp.scrollTop;
 }

 // Initial
 refreshVDims();
 if (vst.vh > 0) { vWin.style.height = vst.vh + 'px'; vWin.style.top = '0px'; }

 // V slider resize handled by ResizeObserver in initHmSliders (above)

 var vDrag = false, vsy = 0, vstt = 0;
 vWin.onmousedown = function(e) { vDrag = true; vsy = e.clientY; vstt = parseFloat(vWin.style.top)||0; e.preventDefault(); };
 vTrack.onmousedown = function(e) {
  if (e.target === vWin) return;
  var nt = e.clientY - vTrack.getBoundingClientRect().top - vst.vh/2;
  nt = Math.max(0, Math.min(nt, vst.th - vst.vh));
  vWin.style.top = nt + 'px'; vToView();
 };
 document.addEventListener('mousemove', function(e) {
  if (!vDrag) return;
  var nt = vstt + (e.clientY - vsy);
  nt = Math.max(0, Math.min(nt, vst.th - vst.vh));
  vWin.style.top = nt + 'px'; vToView();
 });
 document.addEventListener('mouseup', function() { vDrag = false; });

 // Wheel on viewport = scroll
 vp.addEventListener('wheel', function(e) {
  e.preventDefault();
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) { vp.scrollLeft += e.deltaX; }
  else { vp.scrollTop += e.deltaY; }
  etfColWrap.scrollTop = vp.scrollTop;
  hFromView(); vFromView();
 }, { passive: false });

 // Sync scroll → sliders + ETF column
 vp.addEventListener('scroll', function() { etfColWrap.scrollTop = vp.scrollTop; hFromView(); vFromView(); }, { passive: true });

 updateHmHLabels();
}

function switchHeatmapPeriod(p) {
 hmPeriod = p;
 $id('hm-btn-5').classList.toggle('active', p === 5);
 $id('hm-btn-20').classList.toggle('active', p === 20);
 hmSortDate = null; hmSortDir = 0;
 updateHmSortUI();
 $id('hm-loading').style.display = '';
 // Re-fetch data for new lookback
 fetch('/api/heatmap_data?lookback=' + hmPeriod)
  .then(function(r) { return r.json(); })
  .then(function(data) {
   hmAllDates = data.dates || [];
   var retByCode = {};
   (data.etfs || []).forEach(function(e) { retByCode[e.code] = e.returns; });
   hmETFList.forEach(function(e) { e._returns = retByCode[e.code] || []; });
   $id('hm-loading').style.display = 'none';
   renderHeatmap();
  });
}

function resetHeatmapSort() { hmSortDate = null; hmSortDir = 0; updateHmSortUI(); renderHeatmap(); }

function updateHmHLabels() {}
function updateHmSortUI() {
 var el = $id('hm-sort-ind');
 if (hmSortDir === 0) { el.style.display = 'none'; return; }
 el.style.display = '';
 $id('hm-sort-d').textContent = (hmSortDate||'').slice(0,10);
 $id('hm-sort-a').textContent = hmSortDir === 1 ? '↓ 降序' : '↑ 升序';
}

// Lazy-init guide charts that start open
setTimeout(function() { renderF1ZOHChart(); }, 300);
// Default to results view on page load (overridden by URL autorun)
setTimeout(function() { initHeatmap(); }, 500);

// ══════════════════════════════════════════════════════════════
// Data Management Panel (数据管理)
// ══════════════════════════════════════════════════════════════
var dmAllData = null;
var dmSelected = {};
var dmLastClick = null;
var dmFilter = 'all';
var dmFreq = 'daily';
var dmField = 'close';
var dmSectorFilter = '';
var dmInited = false;
var dmLoading = false;
var dmDragState = null;
var DM_CELL_W = 64;

// ── Toast (参考 资产评审台 实现) ──
var _dmToastTimer;
// ── Themed confirm dialog (replaces native confirm()) ──
function dmConfirm(msg) {
 return new Promise(function(resolve) {
  var overlay = $id('dm-confirm-overlay');
  $id('dm-confirm-msg').textContent = msg;
  overlay.style.display = 'flex';
  function cleanup() {
   overlay.style.display = 'none';
   $id('dm-confirm-yes').removeEventListener('click', onYes);
   $id('dm-confirm-no').removeEventListener('click', onNo);
  }
  function onYes() { cleanup(); resolve(true); }
  function onNo() { cleanup(); resolve(false); }
  $id('dm-confirm-yes').addEventListener('click', onYes);
  $id('dm-confirm-no').addEventListener('click', onNo);
 });
}

function dmToast(msg, type) {
 var t = $id('dm-toast');
 if (!t) return;
 t.textContent = msg;
 t.className = 'show ' + (type || '');
 clearTimeout(_dmToastTimer);
 _dmToastTimer = setTimeout(function() { t.className = ''; }, 2800);
}

// ── Init ──
function initDataMgmt() {
 if (dmLoading) return;
 // Auto-reload if data is stale (>5 min) or never loaded
 var now = Date.now();
 if (dmInited && dmAllData && (now - (dmAllData._ts || 0)) < 300000) {
  $id('dm-loading').style.display = 'none';
  return;
 }
 // Pre-fill end date to today on initial load
 if (!dmInited) {
  var today = new Date().toISOString().slice(0, 10);
  $id('dm-date-end').value = today;
  dmLoadSplitStatus();
 }
 dmLoadMatrix();
}

// ── Cache mode ──
var dmCacheData = null;

// ── Split status ──
var dmSplitStatus = {};
var dmSplitPopupCode = null;

function dmShowSplitActions(code, el) {
 var popup = $id('dm-split-popup');
 if (!popup) return;
 if (dmSplitPopupCode === code) { popup.style.display = 'none'; dmSplitPopupCode = null; return; }
 dmSplitPopupCode = code;
 var sp = dmSplitStatus[code] || {};
 var rect = el.getBoundingClientRect();
 var sp = dmSplitStatus[code] || {};
 var label = '修复拆股数据' + (sp.ratio ? ' (1:' + sp.ratio + ')' : '');
 popup.innerHTML = '<div class=\"dm-split-btn\" data-code=\"' + code + '\">' + label + '</div>';
 popup.style.display = 'block';
 popup.style.left = Math.min(rect.right + 4, window.innerWidth - 160) + 'px';
 popup.style.top = Math.min(rect.top, window.innerHeight - 80) + 'px';
}

async function dmFullRefetchCode(code) {
 $id('dm-split-popup').style.display = 'none'; dmSplitPopupCode = null;
 var sp = dmSplitStatus[code] || {};
 var ok = await dmConfirm('确定修复 ' + code + ' 的拆股数据吗？\n\n将按 1:' + (sp.ratio||'?') + ' 比例调整历史价格并写入CSV。');
 if (!ok) return;
 dmToast('正在修复拆股数据...', '');
 _showProgress('修复拆股');
 try {
  var resp = await fetch('/api/data_full_refetch', {
   method: 'POST', headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({codes: [code], verify_split: true})
  });
  var data = await resp.json();
  _hideProgress();
  var r = (data.results || {})[code] || {};
  dmToast('全量重拉完成: ' + (r.rows || 0) + ' 行', r.error ? 'warning' : 'success');
  dmReloadData([code]);
  dmLoadSplitStatus();
 } catch(e) { _hideProgress(); dmToast('失败: ' + e.message, 'error'); }
}

// Delegate: ⚠ split warning + button click (capture phase to beat row onclick)
document.addEventListener('click', function(e) {
 var warn = e.target.closest('.dm-split-warn');
 if (warn) { e.stopPropagation(); e.preventDefault(); dmShowSplitActions(warn.dataset.code, warn); return; }
 var btn = e.target.closest('.dm-split-btn');
 if (btn) { e.stopPropagation(); e.preventDefault(); dmFullRefetchCode(btn.dataset.code); }
}, true);

// Click outside to close popup
document.addEventListener('click', function(e) {
 if (dmSplitPopupCode && !e.target.closest('#dm-split-popup') && !e.target.closest('.dm-etf-label')) {
  $id('dm-split-popup').style.display = 'none'; dmSplitPopupCode = null;
 }
});

function dmLoadSplitStatus() {
 fetch('/api/split_status')
  .then(function(r) { return r.json(); })
  .then(function(data) {
   dmSplitStatus = data;
   // Re-render ETF labels to show split icons
   if (dmAllData && dmFreq !== 'factor') dmRenderMatrix();
  })
  .catch(function() {});
}

async function dmFullRefetch() {
 var keys = Object.keys(dmSelected);
 var codes = [];
 keys.forEach(function(k) { var c = k.split('|')[0]; if (codes.indexOf(c) < 0) codes.push(c); });
 if (!codes.length) return;

 var hasSplit = codes.some(function(c) { return dmSplitStatus[c] && dmSplitStatus[c].status === 'pending_repair'; });
 var msg = '确定全量重拉 ' + codes.length + ' 支 ETF 的全部历史数据吗？';
 if (hasSplit) msg += '\n\n检测到拆股标记，重拉后将自动验证数据连续性。';
 var ok = await dmConfirm(msg);
 if (!ok) return;

 dmToast('正在全量重拉...', '');
 _showProgress('全量重拉');
 try {
  var resp = await fetch('/api/data_full_refetch', {
   method: 'POST', headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({codes: codes, verify_split: hasSplit})
  });
  var data = await resp.json();
  _hideProgress();
  var totalRows = 0, allVerified = true;
  if (data.results) {
   Object.keys(data.results).forEach(function(c) {
    if (data.results[c].error) allVerified = false;
    else totalRows += (data.results[c].rows || 0);
   });
  }
  dmToast('全量重拉完成: ' + totalRows + ' 行' + (allVerified ? '' : ' (部分失败)'), allVerified ? 'success' : 'warning');
  dmReloadData(Object.keys(data.results || {}));
  dmLoadSplitStatus();
 } catch(e) { _hideProgress(); dmToast('全量重拉失败: ' + e.message, 'error'); }
}

function dmLoadCacheStatus() {
 dmLoading = true;
 $id('dm-loading').style.display = 'flex';

 fetch('/api/factor_cache_status')
  .then(function(r) { return r.json(); })
  .then(function(data) {
   dmAllData = { etfs: data.etfs, dates: ['status'], cells: {} };
   data.etfs.forEach(function(e) {
    var cs = data.caches[e.code] || {};
    dmAllData.cells[e.code] = { 'status': { status: cs.status || 'missing', file_count: cs.file_count || 0 } };
   });
   dmAllData._ts = Date.now();
   dmLoading = false;
   dmInited = true;
   $id('dm-loading').style.display = 'none';
   populateSectorFilter();
   dmRenderMatrix();
   dmUpdateStatusBar();
  })
  .catch(function(e) {
   dmLoading = false;
   $id('dm-loading').textContent = '加载失败: ' + e.message;
   dmToast('缓存状态加载失败: ' + e.message, 'error');
  });
}

async function dmDeleteCache() {
 var keys = Object.keys(dmSelected);
 var codes = [];
 keys.forEach(function(k) { var c = k.split('|')[0]; if (codes.indexOf(c) < 0) codes.push(c); });
 if (!codes.length) return;
 var ok = await dmConfirm('确定删除 ' + codes.length + ' 支 ETF 的因子缓存吗？');
 if (!ok) return;

 dmToast('正在删除缓存...', '');
 _showProgress('删除缓存');
 try {
  var resp = await fetch('/api/factor_cache_delete', {
   method: 'POST', headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({codes: codes})
  });
  var data = await resp.json();
  _hideProgress();
  dmToast('已删除 ' + data.deleted + ' 个缓存文件', 'success');
  dmLoadCacheStatus();
 } catch(e) { _hideProgress(); dmToast('删除失败: ' + e.message, 'error'); }
}

async function dmRebuildCache() {
 var keys = Object.keys(dmSelected);
 var codes = [];
 keys.forEach(function(k) { var c = k.split('|')[0]; if (codes.indexOf(c) < 0) codes.push(c); });
 if (!codes.length) codes = null; // null = all

 dmToast('正在重建因子缓存...', '');
 _showProgress('重建缓存');
 try {
  var resp = await fetch('/api/factor_cache_rebuild', {
   method: 'POST', headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({codes: codes, mode: 'all'})
  });
  var data = await resp.json();
  _hideProgress();
  dmToast('重建完成: ' + (data.rebuilt || 0) + ' 支 ETF', 'success');
  dmLoadCacheStatus();
 } catch(e) { _hideProgress(); dmToast('重建失败: ' + e.message, 'error'); }
}

function dmLoadMatrix() {
 if (dmLoading) return;
 dmLoading = true;
 $id('dm-loading').style.display = 'flex';

 var start = $id('dm-date-start').value;
 var end = $id('dm-date-end').value;
 var params = [];
 if (start) params.push('start=' + encodeURIComponent(start));
 if (end) params.push('end=' + encodeURIComponent(end));
 var apiFreq = dmFreq === 'factor' ? dmField : dmFreq;
 var apiField = dmFreq === 'factor' ? 'close' : dmField;
 params.push('freq=' + apiFreq);
 params.push('field=' + apiField);
 var qs = params.length ? '?' + params.join('&') : '';

 fetch('/api/data_matrix' + qs)
  .then(function(r) { return r.json(); })
  .then(function(data) {
   dmAllData = data;
   dmAllData._ts = Date.now();
   dmLoading = false;
   dmInited = true;
   $id('dm-loading').style.display = 'none';
   populateSectorFilter();
   dmRenderMatrix();
   dmUpdateStatusBar();
   if (!start) {
    // Set default date range from API response
    var dates = data.dates || [];
    if (dates.length) {
     $id('dm-date-start').value = dates[0];
     $id('dm-date-end').value = dates[dates.length - 1];
    }
   }
  })
  .catch(function(e) {
   dmLoading = false;
   $id('dm-loading').textContent = '加载失败: ' + e.message;
   dmToast('数据加载失败: ' + e.message, 'error');
  });
}

function dmReloadData(codes) {
 dmSelected = {};
 dmLastClick = null;
 dmDragState = null;
 dmLoading = true;
 $id('dm-loading').style.display = 'flex';
 var params = [];
 var start = $id('dm-date-start').value;
 var end = $id('dm-date-end').value;
 if (start) params.push('start=' + encodeURIComponent(start));
 if (end) params.push('end=' + encodeURIComponent(end));
 params.push('freq=' + (dmFreq === 'factor' ? dmField : dmFreq));
 params.push('field=' + (dmFreq === 'factor' ? 'close' : dmField));
 var hasFilter = codes && codes.length;
 if (hasFilter) params.push('codes=' + encodeURIComponent(codes.join(',')));
 fetch('/api/data_matrix?' + params.join('&'))
  .then(function(r) { return r.json(); })
  .then(function(data) {
   dmLoading = false;
   $id('dm-loading').style.display = 'none';
   if (hasFilter && dmAllData) {
    // Merge partial response into existing data
    dmAllData._ts = Date.now();
    dmAllData.summary = data.summary;
    // Update only affected ETF entries and cells
    (data.etfs || []).forEach(function(e) {
     var found = false;
     for (var i = 0; i < dmAllData.etfs.length; i++) {
      if (dmAllData.etfs[i].code === e.code) { found = true; break; }
     }
    });
    // Merge cells
    var newCells = data.cells || {};
    Object.keys(newCells).forEach(function(code) {
     dmAllData.cells[code] = newCells[code];
    });
    dmRenderMatrix();
    dmUpdateStatusBar();
    dmLoadSplitStatus();
   } else {
    dmAllData = data; dmAllData._ts = Date.now();
    if (!dmInited) dmInited = true;
    populateSectorFilter();
    dmRenderMatrix();
    dmUpdateStatusBar();
    dmLoadSplitStatus();
   }
  })
  .catch(function(e) {
   dmLoading = false;
   $id('dm-loading').textContent = '刷新失败: ' + e.message;
  });
}

// ── Date range ──
function dmSetRange(days) {
 document.querySelectorAll('[id^="dm-rng-"]').forEach(function(b) { b.classList.remove('active'); });
 var btn = $id('dm-rng-' + days); if (btn) btn.classList.add('active');
 var today = new Date();
 var endStr = today.toISOString().slice(0, 10);
 var start = new Date(today);
 if (days > 0) {
  start.setDate(start.getDate() - Math.ceil(days * 1.4)); // trading days ~70% of calendar days
  $id('dm-date-start').value = start.toISOString().slice(0, 10);
 } else {
  $id('dm-date-start').value = '2020-01-01';
 }
 $id('dm-date-end').value = endStr;
 dmSelected = {};
 dmLoadMatrix();
}

// ── Filters ──
function dmToggleFilter(type) {
 dmFilter = (dmFilter === type) ? 'all' : type;
 ['missing','intraday','anomaly'].forEach(function(t) {
  var btn = $id('dm-btn-' + t);
  if (btn) btn.classList.toggle('active', dmFilter === t);
 });
 dmSelected = {};
 dmRenderMatrix();
 dmUpdateStatusBar();
}

function populateSectorFilter() {
 var sel = $id('dm-sector-filter');
 if (!sel || !dmAllData) return;
 var sectors = [];
 (dmAllData.etfs || []).forEach(function(e) {
  if (e.sector && sectors.indexOf(e.sector) < 0) sectors.push(e.sector);
 });
 sel.innerHTML = '<option value="">全部板块</option>' +
  sectors.map(function(s) { return '<option value="' + s + '">' + s + '</option>'; }).join('');
}

// ── Freq / Field toggles ──
function dmSetFreq(freq) {
 dmFreq = freq;
 ['daily','weekly','factor'].forEach(function(f) {
  var btn = $id('dm-freq-' + f); if (btn) btn.classList.toggle('active', freq === f);
 });
 var isFactor = (freq === 'factor');
 $id('dm-fields-data').style.display = isFactor ? 'none' : '';
 $id('dm-fields-factor').style.display = isFactor ? '' : 'none';
 ['dm-btn-missing','dm-btn-intraday','dm-btn-anomaly'].forEach(function(id) {
  var el = $id(id); if (el) el.style.display = isFactor ? 'none' : '';
 });
 dmSelected = {};
 if (isFactor) {
  // Default to F7 when entering factor mode
  var activeF = document.querySelector('#dm-fields-factor .dm-filter-btn.active');
  dmField = activeF ? activeF.textContent.toLowerCase() : 'f7';
 } else {
  var activeD = document.querySelector('#dm-fields-data .dm-filter-btn.active');
  dmField = activeD ? (activeD.id === 'dm-field-close' ? 'close' : 'amount') : 'close';
 }
 dmLoadMatrix();
}
function dmSetField(field) {
 dmField = field;
 // Update active states in whichever group is visible
 var isFactor = dmFreq === 'factor';
 if (isFactor) {
  ['f1','f3','f7'].forEach(function(f) {
   var b = $id('dm-field-' + f); if (b) b.classList.toggle('active', field === f);
  });
 } else {
  ['close','volume','amount'].forEach(function(f) {
   var b = $id('dm-field-' + f); if (b) b.classList.toggle('active', field === f);
  });
 }
 dmSelected = {};
 dmLoadMatrix();
}
function dmFilterBySector() {
 dmSectorFilter = $id('dm-sector-filter').value;
 dmSelected = {};
 dmRenderMatrix();
 dmUpdateStatusBar();
}

// ── Rendering ──
function dmRenderMatrix() {
 if (!dmAllData) return;
 var etfs = dmAllData.etfs;
 var dates = dmAllData.dates;
 var cells = dmAllData.cells;

 // Sector color palette (matching heatmap)
 var SEC_COLOR = {
  '科技': TC.accent, '新能源': TC.greenLight, '制造': TC.cyan,
  '资源周期': TC.warning, '医药': TC.negative, '金融': '#eab308',
  '传统': '#78716c', '消费': TC.orange, '平台经济': '#6366f1',
  '海外科技': TC.purple, '另类': '#c084fc', '基准': '#9ca3af'
 };

 // Build visible ETF list with applied filters
 var visibleEtfs = [];
 for (var ei = 0; ei < etfs.length; ei++) {
  var code = etfs[ei].code;
  if (dmSectorFilter && etfs[ei].sector !== dmSectorFilter) continue;
  visibleEtfs.push({idx: ei, code: code, etf: etfs[ei]});
 }

 // ETF labels column (sector dot + name + code, uniform 24px rows — no sector headers)
 var etfLabelsEl = $id('dm-etf-labels');
 var etfHtml = '';
 var visibleCodes = [];
 for (var ri = 0; ri < visibleEtfs.length; ri++) {
  var ve = visibleEtfs[ri];
  var e = ve.etf;
  visibleCodes.push(ve.code);
  var sc = SEC_COLOR[e.sector] || TC.textMuted;
  var rowSel = dmIsRowSelected(ve.code, dates) ? ' row-selected' : '';
  etfHtml += '<div class="dm-etf-label' + rowSel + '" data-code="' + ve.code + '" title="' + e.name + ' | ' + e.sector + '">' +
   '<span class="dm-sec-dot" style="background:' + sc + ';" title="' + (e.sector||'') + '"></span>' +
   '<span class="dm-etf-code">' + ve.code + '</span>' +
   '<span class="dm-etf-name">' + (e.name || ve.code) + '</span>';
   // Split indicator (clickable)
   var sp = dmSplitStatus[ve.code];
   if (sp && sp.status === 'pending_repair') {
    etfHtml += '<span class="dm-split-warn" data-code="' + ve.code + '" title="拆股待修复: ' + (sp.symptom || sp.ex_date) + '">⚠</span>';
   }
   etfHtml += '</div>';
 }
 etfLabelsEl.innerHTML = etfHtml || '<div style="text-align:center;color:var(--text-muted);padding:20px;">无匹配ETF</div>';

 // Date headers
 var dateRowEl = $id('dm-date-row');
 var totalW = dates.length * DM_CELL_W;
 dateRowEl.style.width = totalW + 'px';
 var dateHtml = '';
 for (var di = 0; di < dates.length; di++) {
  var d = dates[di];
  var colSel = dmIsColSelected(d, visibleCodes) ? ' col-selected' : '';
  dateHtml += '<span class="dm-date-cell' + colSel + '" data-date="' + d + '">' + d.slice(5) + '</span>';
 }
 dateRowEl.innerHTML = dateHtml;

 // Grid body (uniform rows, no sector separators)
 // Compute value range for gradient (volume / amount modes only)
 var allVals = []; var vMin = 0, vMax = 1;
 if (dmField === 'amount') {
  for (var ri = 0; ri < visibleEtfs.length; ri++) {
   var code = visibleEtfs[ri].code;
   for (var di = 0; di < dates.length; di++) {
    var d = dates[di];
    var cell = (cells[code] && cells[code][d]) || {};
    if (cell.value != null && cell.status === 'csv') allVals.push(cell.value);
   }
  }
  if (allVals.length) { vMin = Math.min.apply(null, allVals); vMax = Math.max.apply(null, allVals); }
 }
 // Gradient: linear RGB interpolation
 function dmGrad(t, lo, hi) {
  t = Math.max(0, Math.min(1, t));
  var r = Math.round(lo[0] + t * (hi[0] - lo[0]));
  var g = Math.round(lo[1] + t * (hi[1] - lo[1]));
  var b = Math.round(lo[2] + t * (hi[2] - lo[2]));
  return 'rgb(' + r + ',' + g + ',' + b + ')';
 }
 var gradLo, gradHi;
 if (dmField === 'amount') { gradLo = [26, 10, 48]; gradHi = [90, 42, 138]; }

 var gridBody = $id('dm-grid-body');
 gridBody.style.width = totalW + 'px';
 var gridHtml = '';
 for (var ri = 0; ri < visibleEtfs.length; ri++) {
  var ve = visibleEtfs[ri];
  var code = ve.code;
  gridHtml += '<div style="display:flex;">';
  for (var di = 0; di < dates.length; di++) {
   var d = dates[di];
   var cell = (cells[code] && cells[code][d]) || {status: 'missing'};
   // Apply filter
   if (dmFilter === 'missing' && cell.status !== 'missing') { gridHtml += '<div style="width:'+DM_CELL_W+'px;flex-shrink:0;"></div>'; continue; }
   if (dmFilter === 'intraday' && cell.status !== 'intraday') { gridHtml += '<div style="width:'+DM_CELL_W+'px;flex-shrink:0;"></div>'; continue; }
   if (dmFilter === 'anomaly' && !cell.anomaly) { gridHtml += '<div style="width:'+DM_CELL_W+'px;flex-shrink:0;"></div>'; continue; }

   var selClass = dmSelected[code + '|' + d] ? ' selected' : '';
   var statusClass = cell.status;
   if (cell.anomaly) statusClass += ' anomaly-' + cell.anomaly;

   // Cell text: format based on field / cache mode
   var val = cell.value;
   var cellText = '';
   if (cell.status === 'csv' || cell.status === 'intraday') {
    if (val != null) {
     if (dmField === 'close' || dmFreq === 'factor') {
      cellText = val.toFixed(3);
     } else if (dmField === 'amount') {
      if (val >= 1e8) cellText = (val / 1e8).toFixed(2) + '亿';
      else cellText = (val / 1e4).toFixed(0) + '万';
     }
    }
   }

   // Gradient background for volume/amount CSV cells
   var inlineStyle = '';
   if (dmField === 'amount' && cell.status === 'csv' && val != null && vMax > vMin) {
    var t = (val - vMin) / (vMax - vMin);
    inlineStyle = ' style="background:' + dmGrad(t, gradLo, gradHi) + ' !important;"';
   }

   var title = code + ' ' + (ve.etf.name||'') + ' | ' + d + ' | ' + cell.status;
   if (cell.anomalyLabel) title += ' | ' + cell.anomalyLabel;
   if (cell.time) title += ' | ' + cell.time;
   gridHtml += '<div class="dm-cell ' + statusClass + selClass + '"' + inlineStyle +
    ' data-code="' + code + '" data-date="' + d +
    '" title="' + title + '">' + cellText + '</div>';
  }
  gridHtml += '</div>';
 }
 gridBody.innerHTML = gridHtml || '<div style="text-align:center;color:var(--text-muted);padding:40px;">无匹配数据</div>';

 // Sector legend
 var legendEl = $id('dm-sec-legend');
 if (legendEl) {
  var seenSectors = [];
  for (var ri = 0; ri < visibleEtfs.length; ri++) {
   var s = visibleEtfs[ri].etf.sector || '';
   if (s && seenSectors.indexOf(s) < 0) seenSectors.push(s);
  }
  legendEl.innerHTML = seenSectors.map(function(s) {
   return '<span data-dm-sector="' + s + '" style="cursor:pointer;margin-right:10px;font-size:10px;color:var(--text-muted);">' +
    '<span class="dm-sec-dot" style="background:' + (SEC_COLOR[s]||TC.textMuted) + ';vertical-align:middle;"></span>' + s + '</span>';
  }).join('');
 }

 // Sync scrolling + horizontal slider
 var vp = $id('dm-grid-viewport');
 var etfWrap = $id('dm-etf-labels-wrap');
 var dateWrap = $id('dm-date-wrap');
 var hTrack = $id('dm-hslider-track');
 var hWin = $id('dm-hslider-win');

 if (vp) {
  vp.onscroll = function() {
   if (etfWrap) etfWrap.scrollTop = vp.scrollTop;
   if (dateWrap) dateWrap.scrollLeft = vp.scrollLeft;
   dmFromView();
  };
  // Mouse wheel → vertical scroll
  vp.addEventListener('wheel', function(e) {
   vp.scrollTop += e.deltaY;
   if (etfWrap) etfWrap.scrollTop = vp.scrollTop;
   if (window.dmVFromView) window.dmVFromView();
   e.preventDefault();
  }, {passive: false});
  // Also scroll via wheel on ETF labels column
  if (etfWrap) {
   etfWrap.addEventListener('wheel', function(e) {
    vp.scrollTop += e.deltaY;
    etfWrap.scrollTop = vp.scrollTop;
    if (window.dmVFromView) window.dmVFromView();
    e.preventDefault();
   }, {passive: false});
  }
 }

 // Slider logic (matching heatmap hm-hslider)
 if (hTrack && hWin && vp) {
  var dmSlider = {};
  function dmRefreshSlider() {
   dmSlider.vpW = vp.getBoundingClientRect().width;
   dmSlider.tw = Math.max(1, hTrack.getBoundingClientRect().width);
   dmSlider.maxSX = Math.max(0, totalW - dmSlider.vpW);
   var hRatio = Math.min(1, dmSlider.vpW / Math.max(1, totalW));
   dmSlider.w = Math.max(20, hRatio * dmSlider.tw);
   hWin.style.width = dmSlider.w + 'px';
   hWin.style.display = dmSlider.maxSX > 0 ? '' : 'none';
   // Keep handle within bounds
   var curLeft = parseFloat(hWin.style.left) || 0;
   var maxLeft = Math.max(0, dmSlider.tw - dmSlider.w);
   if (curLeft > maxLeft || isNaN(curLeft)) hWin.style.left = maxLeft + 'px';
  }
  window.dmFromView = function() {
   var pct = dmSlider.maxSX > 0 ? vp.scrollLeft / dmSlider.maxSX : 0;
   hWin.style.left = Math.round(pct * (dmSlider.tw - dmSlider.w)) + 'px';
  };
  function dmHToView() {
   var left = parseFloat(hWin.style.left) || 0;
   var pct = dmSlider.tw > dmSlider.w ? left / (dmSlider.tw - dmSlider.w) : 0;
   vp.scrollLeft = Math.round(pct * dmSlider.maxSX);
   if (dateWrap) dateWrap.scrollLeft = vp.scrollLeft;
  }

  // Slider drag
  var dmDragH = null;
  hWin.addEventListener('mousedown', function(e) {
   dmDragH = { startX: e.clientX, startLeft: parseFloat(hWin.style.left) || 0 };
   e.preventDefault();
  });
  document.addEventListener('mousemove', function(e) {
   if (!dmDragH) return;
   var dx = e.clientX - dmDragH.startX;
   var newLeft = Math.max(0, Math.min(dmSlider.tw - dmSlider.w, dmDragH.startLeft + dx));
   hWin.style.left = newLeft + 'px';
   dmHToView();
  });
  document.addEventListener('mouseup', function() { dmDragH = null; });

  // Also allow clicking on track to jump
  hTrack.addEventListener('mousedown', function(e) {
   if (e.target === hWin) return; // let handle drag handle itself
   var rect = hTrack.getBoundingClientRect();
   var pct = (e.clientX - rect.left - dmSlider.w / 2) / (dmSlider.tw - dmSlider.w);
   pct = Math.max(0, Math.min(1, pct));
   hWin.style.left = Math.round(pct * (dmSlider.tw - dmSlider.w)) + 'px';
   dmHToView();
  });

  dmRefreshSlider();
  window.addEventListener('resize', function() { dmRefreshSlider(); dmHToView(); });
  // Start at rightmost
  setTimeout(function() {
   dmRefreshSlider();
   hWin.style.left = Math.max(0, dmSlider.tw - dmSlider.w) + 'px';
   dmHToView();
  }, 100);

  // Vertical slider (matching heatmap hm-vslider)
  var vTrack = $id('dm-vslider-track');
  var vWin = $id('dm-vslider-win');
  if (vTrack && vWin && vp) {
   var vst = {};
   function dmRefreshVSlider() {
    vst.th = vTrack.getBoundingClientRect().height;
    var vpHeight = vp.getBoundingClientRect().height;
    var chartHeight = visibleEtfs.length * 30; // 30px per row
    var vRatio = Math.min(1, vpHeight / Math.max(1, chartHeight));
    vst.vh = Math.max(24, vRatio * vst.th);
    vst.maxSY = Math.max(0, chartHeight - vpHeight);
    if (vRatio >= 1) { vWin.style.display = 'none'; }
    else { vWin.style.display = ''; vWin.style.height = vst.vh + 'px'; }
    var curTop = parseFloat(vWin.style.top) || 0;
    var maxTop = Math.max(0, vst.th - vst.vh);
    if (curTop > maxTop || isNaN(curTop)) vWin.style.top = maxTop + 'px';
   }
   function dmVToView() {
    var top = parseFloat(vWin.style.top) || 0;
    var pct = vst.th > vst.vh ? top / (vst.th - vst.vh) : 0;
    vp.scrollTop = Math.round(pct * vst.maxSY);
    if (etfWrap) etfWrap.scrollTop = vp.scrollTop;
   }
   window.dmVFromView = function() {
    if (vst.maxSY <= 0) return;
    var pct = Math.min(1, vp.scrollTop / vst.maxSY);
    vWin.style.top = Math.round(pct * (vst.th - vst.vh)) + 'px';
    if (etfWrap) etfWrap.scrollTop = vp.scrollTop;
   };

   dmRefreshVSlider();
   vWin.style.top = '0px';

   // Hook into existing scroll handler
   var _prevVpScroll = vp.onscroll;
   vp.onscroll = function() {
    if (_prevVpScroll) _prevVpScroll();
    if (window.dmVFromView) window.dmVFromView();
   };

   // V slider drag
   var vDrag = false, vDragStartY = 0, vDragStartTop = 0;
   vWin.addEventListener('mousedown', function(e) {
    vDrag = true; vDragStartY = e.clientY; vDragStartTop = parseFloat(vWin.style.top) || 0;
    e.preventDefault(); e.stopPropagation();
   });
   vTrack.addEventListener('mousedown', function(e) {
    if (e.target === vWin) return;
    var nt = e.clientY - vTrack.getBoundingClientRect().top - vst.vh / 2;
    nt = Math.max(0, Math.min(nt, vst.th - vst.vh));
    vWin.style.top = nt + 'px'; dmVToView();
   });
   // Add to global mousemove/mouseup handlers
   var _prevDocMove = document.onmousemove;
   var _prevDocUp = document.onmouseup;
   document.addEventListener('mousemove', function(e) {
    if (!vDrag) return;
    var nt = vDragStartTop + (e.clientY - vDragStartY);
    nt = Math.max(0, Math.min(nt, vst.th - vst.vh));
    vWin.style.top = nt + 'px'; dmVToView();
   });
   document.addEventListener('mouseup', function() { vDrag = false; });

   // Resize
   window.addEventListener('resize', function() {
    dmRefreshVSlider();
    var maxTop = Math.max(0, vst.th - vst.vh);
    var curTop = parseFloat(vWin.style.top) || 0;
    if (curTop > maxTop) vWin.style.top = maxTop + 'px';
    dmVToView();
   });
  }
 }

 dmUpdateButtonStates();
}

// ── Selection ──
function dmCellMouseDown(e, code, date) {
 e.preventDefault();
 dmDragState = {startCode: code, startDate: date};
 dmSelected = {};
 dmSelected[code + '|' + date] = true;
 dmLastClick = {code: code, date: date};
 dmUpdateSelection();
}

// Global mouse handlers for drag-select
document.addEventListener('mousemove', function(e) {
 if (!dmDragState) return;
 var el = document.elementFromPoint(e.clientX, e.clientY);
 if (!el || !el.classList.contains('dm-cell')) return;
 var code = el.dataset.code;
 var date = el.dataset.date;
 if (!code || !date) return;
 if (code === dmDragState.startCode && date === dmDragState.startDate) return;
 dmSelectRange(dmDragState.startCode, dmDragState.startDate, code, date);
});

document.addEventListener('mouseup', function() {
 if (dmDragState) {
  dmDragState = null;
  dmUpdateStatusBar();
 }
});

function dmSelectRange(code1, date1, code2, date2) {
 if (!dmAllData) return;
 var dates = dmAllData.dates;
 var etfs = dmAllData.etfs;

 // Build ordered code list from visible ETFs
 var orderedCodes = [];
 for (var i = 0; i < etfs.length; i++) {
  if (!dmSectorFilter || etfs[i].sector === dmSectorFilter) {
   orderedCodes.push(etfs[i].code);
  }
 }

 var idx1 = orderedCodes.indexOf(code1);
 var idx2 = orderedCodes.indexOf(code2);
 if (idx1 < 0 || idx2 < 0) return;
 var rowMin = Math.min(idx1, idx2), rowMax = Math.max(idx1, idx2);

 var didx1 = dates.indexOf(date1);
 var didx2 = dates.indexOf(date2);
 if (didx1 < 0 || didx2 < 0) return;
 var colMin = Math.min(didx1, didx2), colMax = Math.max(didx1, didx2);

 dmSelected = {};
 for (var ri = rowMin; ri <= rowMax; ri++) {
  for (var ci = colMin; ci <= colMax; ci++) {
   dmSelected[orderedCodes[ri] + '|' + dates[ci]] = true;
  }
 }
 dmUpdateSelection();
}

function dmSelectRow(code) {
 if (!dmAllData) return;
 var dates = dmAllData.dates;
 var allSelected = true;
 for (var i = 0; i < dates.length; i++) {
  if (!dmSelected[code + '|' + dates[i]]) { allSelected = false; break; }
 }
 if (allSelected) {
  for (var i = 0; i < dates.length; i++) delete dmSelected[code + '|' + dates[i]];
 } else {
  for (var i = 0; i < dates.length; i++) dmSelected[code + '|' + dates[i]] = true;
 }
 dmUpdateSelection();
 dmUpdateStatusBar();
}

function dmSelectCol(date) {
 if (!dmAllData) return;
 var etfs = dmAllData.etfs;
 // Select ALL ETFs for this date, not just visible ones
 var allSelected = etfs.length > 0;
 for (var i = 0; i < etfs.length; i++) {
  if (!dmSelected[etfs[i].code + '|' + date]) { allSelected = false; break; }
 }
 if (allSelected) {
  for (var i = 0; i < etfs.length; i++) delete dmSelected[etfs[i].code + '|' + date];
 } else {
  for (var i = 0; i < etfs.length; i++) dmSelected[etfs[i].code + '|' + date] = true;
 }
 dmUpdateSelection();
 dmUpdateStatusBar();
}

function dmSelectSector(sector) {
 if (!dmAllData) return;
 var dates = dmAllData.dates;
 var etfs = dmAllData.etfs;
 var sectorCodes = [];
 for (var i = 0; i < etfs.length; i++) {
  if (etfs[i].sector === sector) sectorCodes.push(etfs[i].code);
 }
 var allSelected = sectorCodes.length > 0;
 for (var i = 0; i < sectorCodes.length; i++) {
  for (var j = 0; j < dates.length; j++) {
   if (!dmSelected[sectorCodes[i] + '|' + dates[j]]) { allSelected = false; break; }
  }
  if (!allSelected) break;
 }
 if (allSelected) {
  for (var i = 0; i < sectorCodes.length; i++)
   for (var j = 0; j < dates.length; j++)
    delete dmSelected[sectorCodes[i] + '|' + dates[j]];
 } else {
  for (var i = 0; i < sectorCodes.length; i++)
   for (var j = 0; j < dates.length; j++)
    dmSelected[sectorCodes[i] + '|' + dates[j]] = true;
 }
 dmUpdateSelection();
 dmUpdateStatusBar();
}

function dmIsRowSelected(code, dates) {
 if (!dmAllData) return false;
 var dts = dates || dmAllData.dates;
 for (var i = 0; i < dts.length; i++) {
  if (dmSelected[code + '|' + dts[i]]) return true;
 }
 return false;
}

function dmIsColSelected(date, codes) {
 if (!dmAllData) return false;
 var cs = codes || [];
 if (!cs.length) {
  var etfs = dmAllData.etfs;
  for (var i = 0; i < etfs.length; i++) cs.push(etfs[i].code);
 }
 for (var i = 0; i < cs.length; i++) {
  if (dmSelected[cs[i] + '|' + date]) return true;
 }
 return false;
}

function dmUpdateSelection() {
 document.querySelectorAll('.dm-cell').forEach(function(el) {
  var key = el.dataset.code + '|' + el.dataset.date;
  el.classList.toggle('selected', !!dmSelected[key]);
 });
 document.querySelectorAll('.dm-etf-label').forEach(function(el) {
  el.classList.toggle('row-selected', dmIsRowSelected(el.dataset.code));
 });
 document.querySelectorAll('.dm-date-cell').forEach(function(el) {
  el.classList.toggle('col-selected', dmIsColSelected(el.dataset.date));
 });
 dmUpdateButtonStates();
}

function dmUpdateButtonStates() {
 var n = Object.keys(dmSelected).length;
 var delBtn = $id('dm-btn-delete'); if (delBtn) delBtn.disabled = n === 0;
 var refBtn = $id('dm-btn-refetch'); if (refBtn) refBtn.disabled = n === 0;
 var selSpan = $id('dm-stat-selected'); if (selSpan) selSpan.textContent = n;
}

// ── Status bar ──
function dmUpdateStatusBar() {
 if (!dmAllData || !dmAllData.summary) return;
 var s = dmAllData.summary;
 $id('dm-stat-total').textContent = s.totalCells || 0;
 $id('dm-stat-csv').textContent = s.csvCount || 0;
 $id('dm-stat-intraday').textContent = s.intradayCount || 0;
 $id('dm-stat-missing').textContent = s.missingCount || 0;
 $id('dm-stat-anomaly').textContent = s.anomalyCount || 0;
 $id('dm-stat-halted').textContent = s.haltedCount || 0;
}

// ── Operations ──
// Check if all visible dates are selected for each code → expand to full history
function dmExpandFullRows() {
 var result = {};
 var totalDates = dmAllData.dates.length;
 var countByCode = {};
 var keys = Object.keys(dmSelected);
 for (var i = 0; i < keys.length; i++) {
  var code = keys[i].split('|')[0];
  countByCode[code] = (countByCode[code] || 0) + 1;
 }
 Object.keys(countByCode).forEach(function(code) {
  if (countByCode[code] >= totalDates) result[code] = true;
 });
 return result;
}

async function dmDeleteSelected() {
 if (dmFreq === 'factor') { dmDeleteCache(); return; }
 var keys = Object.keys(dmSelected);
 if (!keys.length) return;
 var ok = await dmConfirm('确定删除 ' + keys.length + ' 条选中的数据吗？此操作不可逆。');
 if (!ok) return;

 // Group consecutive dates per code into start/end ranges
 var byCode = {};
 keys.forEach(function(k) {
  var parts = k.split('|');
  var code = parts[0], date = parts[1];
  if (!byCode[code]) byCode[code] = [];
  byCode[code].push(date);
 });
 var fullRow = dmExpandFullRows();
 var ops = [];
 Object.keys(byCode).forEach(function(code) {
  var dates = byCode[code].sort();
  if (fullRow[code]) { ops.push({code: code, start: '1900-01-01', end: '2099-12-31'}); return; }
  var segStart = dates[0], segEnd = dates[0];
  for (var i = 1; i < dates.length; i++) {
   if (dates[i] <= segEnd) { segEnd = dates[i]; continue; }
   var prevIdx = dmAllData.dates.indexOf(segEnd);
   var curIdx = dmAllData.dates.indexOf(dates[i]);
   if (curIdx === prevIdx + 1) { segEnd = dates[i]; }
   else { ops.push({code: code, start: segStart, end: segEnd}); segStart = dates[i]; segEnd = dates[i]; }
  }
  ops.push({code: code, start: segStart, end: segEnd});
 });

 _showProgress('删除数据');
 try {
  var resp = await fetch('/api/data_delete', {
   method: 'POST', headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({operations: ops, freq: dmFreq})
  });
  var data = await resp.json();
  _hideProgress();
  if (data.ok && !(data.errors && data.errors.length)) {
   dmToast('已删除 ' + data.deleted + ' 条数据', 'success');
  } else if (data.errors && data.errors.length) {
   dmToast('删除: ' + data.deleted + ' 条, ' + data.errors.length + ' 个问题 — ' + data.errors[0], 'warning');
  } else {
   dmToast('已删除 ' + data.deleted + ' 条数据', 'success');
  }
  dmReloadData(Object.keys(byCode));
 } catch(e) {
  _hideProgress();
  dmToast('删除失败: ' + e.message, 'error');
 }
}

async function dmRefetchSelected() {
 if (dmFreq === 'factor') { dmDeleteCache(); return; }
 var keys = Object.keys(dmSelected);
 if (!keys.length) return;

 // Group by code, find min/max date
 var byCode = {};
 keys.forEach(function(k) {
  var parts = k.split('|');
  var code = parts[0], date = parts[1];
  if (!byCode[code]) byCode[code] = {min: date, max: date};
  else {
   if (date < byCode[code].min) byCode[code].min = date;
   if (date > byCode[code].max) byCode[code].max = date;
  }
 });
 var fullRow = dmExpandFullRows();
 var ops = Object.keys(byCode).map(function(code) {
  if (fullRow[code]) return {code: code, start: '1900-01-01', end: '2099-12-31'};
  return {code: code, start: byCode[code].min, end: byCode[code].max};
 });

 var isWeekly = dmFreq === 'weekly';
 dmToast('正在' + (isWeekly ? '重建周线' : '强制更新') + ' ' + ops.length + ' 支 ETF...', '');
 _showProgress(isWeekly ? '重建周线' : '强制更新');
 try {
  var resp = await fetch('/api/data_refetch', {
   method: 'POST', headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({operations: ops, freq: dmFreq})
  });
  var data = await resp.json();
  _hideProgress();
  var totalRows = 0, hasErr = false;
  if (data.results) {
   Object.keys(data.results).forEach(function(c) {
    if (data.results[c].error) hasErr = true;
    else totalRows += (data.results[c].rows || 0);
   });
  }
  if (isWeekly) {
   dmToast('周线重建完成: ' + totalRows + ' 周' + (hasErr ? ' (部分失败)' : ''), hasErr ? 'warning' : 'success');
  } else if (totalRows === 0 && !hasErr) {
   dmToast('日线数据已是最新，无需更新', 'info');
  } else {
   dmToast('更新完成: ' + totalRows + ' 行' + (hasErr ? ' (部分失败)' : ''), hasErr ? 'warning' : 'success');
  }
  dmReloadData(Object.keys(byCode));
 } catch(e) {
  _hideProgress();
  dmToast('拉取失败: ' + e.message, 'error');
 }
}

async function dmFillGaps() {
 if (dmFreq === 'factor') { dmRebuildCache(); return; }
 var codes = null;
 var startDate = null, endDate = null;

 // If cells are selected, use their code+date range; otherwise use current filter / full range
 var selKeys = Object.keys(dmSelected);
 if (selKeys.length) {
  var codeSet = {};
  selKeys.forEach(function(k) {
   var parts = k.split('|');
   codeSet[parts[0]] = true;
   if (!startDate || parts[1] < startDate) startDate = parts[1];
   if (!endDate || parts[1] > endDate) endDate = parts[1];
  });
  codes = Object.keys(codeSet);
 } else if (dmSectorFilter) {
  codes = dmAllData.etfs.filter(function(e) { return e.sector === dmSectorFilter; }).map(function(e) { return e.code; });
 }
 if (!startDate) startDate = dmAllData.dates[0];
 if (!endDate) endDate = dmAllData.dates[dmAllData.dates.length - 1];

 dmToast('正在检测缺失数据 (' + (codes ? codes.length + '支' : '全部') + ' ETF)...', '');
 _showProgress('补全空缺');
 try {
  var resp = await fetch('/api/data_fill_gaps', {
   method: 'POST', headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({codes: codes, start: startDate, end: endDate, freq: dmFreq})
  });
  var data = await resp.json();
  _hideProgress();
  if (dmFreq === 'weekly') {
   dmToast('周线补全完成: 填充 ' + (data.filled || 0) + ' 周', 'success');
  } else {
   dmToast('补全完成: 填充 ' + (data.filled || 0) + ' 个缺失格', 'success');
  }
  dmReloadData(codes || null);
 } catch(e) {
  _hideProgress();
  dmToast('补充失败: ' + e.message, 'error');
 }
}


/* ── REQ-112: Central event delegation (replaces all inline onclick/onchange/onmousedown) ── */

function toggleDebugPill() {
  var chk = $id('chk-debug');
  chk.checked = !chk.checked;
  $id('toggle-debug').classList.toggle('on', chk.checked);
}

document.addEventListener('click', function(e) {
  /* ── Static handlers with data-action ── */
  var el = e.target.closest('[data-action]');
  if (el) {
    var a = el.dataset.action;
    switch (a) {
      /* No-param */
      case 'toggleUniverse': toggleUniverse(); break;
      case 'universeSelectAll': universeSelectAll(); break;
      case 'universeSelectNone': universeSelectNone(); break;
      case 'universeSelectInverse': universeSelectInverse(); break;
      case 'saveUniverse': saveUniverse(); break;
      case 'runBacktest': runBacktest(); break;
      case 'refreshData': refreshData(); break;
      case 'refreshMetadata': refreshMetadata(); break;
      case 'saveYAML': saveYAML(); break;
      case 'undoSave': undoSave(); break;
      case 'flipMetricsPage': flipMetricsPage(); break;
      case 'flipSortinoSharpe': flipSortinoSharpe(); break;
      case 'resetHeatmapSort': resetHeatmapSort(); break;
      case 'dmLoadMatrix': dmLoadMatrix(); break;
      case 'dmDeleteSelected': dmDeleteSelected(); break;
      case 'dmRefetchSelected': dmRefetchSelected(); break;
      case 'dmFillGaps': dmFillGaps(); break;
      case 'toggleGroupLevel': toggleGroupLevel(); break;
      case 'toggleShortcuts': toggleShortcuts(); break;
      case '_sectorSelectNone': _sectorSelectNone(); break;
      case '_sectorInvert': _sectorInvert(); break;
      case 'sectorFilterClear': sectorFilter.clear(); renderTunerSnapshot(tunerSnapshotIdx); break;
      case 'toggleDebugPill': toggleDebugPill(); break;
      case 'toggleShortcutsOverlay': if (e.target.id === 'shortcuts-overlay') toggleShortcuts(); break;
      /* With params */
      case 'toggleF1Day': toggleF1Day(parseInt(el.dataset.bit)); break;
      case 'setF1ActiveDays': setF1ActiveDays(parseInt(el.dataset.val)); break;
      case 'setConfType': setConfType(el.dataset.val); break;
      case 'setMaDirConfirm': setMaDirConfirm(el.dataset.val === 'true'); break;
      case 'setFreq': setFreq(el.dataset.val); break;
      case 'setPeriodSpan': setPeriodSpan(parseInt(el.dataset.val)); break;
      case 'nudgeDate': nudgeDate(el.dataset.which, parseInt(el.dataset.days)); break;
      case 'switchRightView': switchRightView(el.dataset.view); break;
      case 'switchKlineFreq': switchKlineFreq(el.dataset.freq); break;
      case 'switchKlineView': switchKlineView(el.dataset.view); break;
      case 'toggleSnapRaw': toggleSnapRaw(); break;
      case 'switchHeatmapPeriod': switchHeatmapPeriod(parseInt(el.dataset.days)); break;
      case 'dmSetRange': dmSetRange(parseInt(el.dataset.days)); break;
      case 'dmSetFreq': dmSetFreq(el.dataset.freq); break;
      case 'dmSetField': dmSetField(el.dataset.field); break;
      case 'jumpToDistDate': jumpToDistDate(el.dataset.which); break;
      case 'onSnapSort': onSnapSort(el.dataset.col); break;
      case 'toggleGuide': toggleGuide(el.dataset.id); break;
    }
    return;
  }

  /* ── Dynamic JS-generated handlers (class + data-* selectors, no data-action) ── */
  var pr = e.target.closest('[data-param-key]');
  if (pr) { focusParamControl(pr.dataset.paramKey); return; }
  var sr = e.target.closest('.snap-row');
  if (sr) { onSnapshotRowClick(sr.dataset.code); return; }
  var ts = e.target.closest('[data-sector-legend]');
  if (ts) { toggleSector(ts.dataset.sectorLegend); return; }
  var dl = e.target.closest('.dm-etf-label');
  if (dl) { dmSelectRow(dl.dataset.code); return; }
  var dc = e.target.closest('.dm-date-cell');
  if (dc) { dmSelectCol(dc.dataset.date); return; }
  var ds = e.target.closest('[data-dm-sector]');
  if (ds) { dmSelectSector(ds.dataset.dmSector); return; }
});

/* mousedown delegation for data management matrix cells + dual-range track */
document.addEventListener('mousedown', function(e) {
  var cell = e.target.closest('.dm-cell');
  if (cell) { dmCellMouseDown(e, cell.dataset.code, cell.dataset.date); return; }
  var track = e.target.closest('[data-action="preventTrackJump"]');
  if (track) { preventTrackJump(e); }
});

/* change delegation for select elements */
document.addEventListener('change', function(e) {
  var el = e.target.closest('[data-action]');
  if (!el) return;
  if (el.dataset.action === 'dmFilterBySector') dmFilterBySector();
});

// ── Hook into global refresh flow: auto-reload DM after refreshData ──
var _origRefreshData = refreshData;
refreshData = async function() {
 var result = await _origRefreshData();
 // If DM view is active, reload data after global refresh
 if ($id('right-view-datamgmt') && $id('right-view-datamgmt').classList.contains('active')) {
  dmInited = false;
  setTimeout(function() { initDataMgmt(); }, 500); // small delay for cache to update
 }
 return result;
};
