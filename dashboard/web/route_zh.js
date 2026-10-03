(function(root){
'use strict';
const data=typeof module!=='undefined'&&module.exports?require('./route_zh_data.js'):root.routeZhData;
const normalize=id=>String(id).replace(/[^a-z0-9]/gi,'').toUpperCase();
const lookup=Object.create(null);
for(const group of Object.values(data.names))for(const [id,name]of Object.entries(group))lookup[normalize(id)]=name;
// Native ending dialogue uses an event key for the Wiki's Architect monster.
lookup[normalize('THE_ARCHITECT')]=data.names.monsters.ARCHITECT;
const fixed={Monster:'普通战斗',Elite:'精英',Boss:'首领',Shop:'商店',RestSite:'营地',Treasure:'宝箱',Unknown:'未知房间',Ancient:'先古之民',Map:'地图',Combat:'战斗',Event:'事件',
 MerchantCardEntry:'卡牌',MerchantRelicEntry:'遗物',MerchantPotionEntry:'药水',MerchantCardRemovalEntry:'移除卡牌',
 HEAL:'休息回血',SMITH:'升级卡牌',PROCEED:'继续',LEAVE:'离开',SKIP:'跳过',
 card_skip:'跳过选卡',buy_removal:'购买移除卡牌服务',rewards_skip:'离开奖励',treasure_skip:'跳过宝箱',
 Upgrade:'升级',Remove:'移除',Transform:'变化',Obtain:'获得',Exhaust:'消耗',Discard:'弃牌',Retain:'保留'};
for(const [id,name]of Object.entries(fixed))lookup[normalize(id)]=name;
function name(id){return lookup[normalize(id)]||String(id??'—')}
function entity(id){const raw=String(id??'—'),zh=name(raw);return zh===raw?raw:`${zh}（${raw}）`}
function event(key){
 const raw=String(key??'—');
 if(Object.hasOwn(data.event_options,raw))return `${name(raw.split('.')[0])} · ${data.event_options[raw]}`;
 const parts=raw.split('.');
 if(parts[1]==='pages'&&parts[3]==='options')return `${name(parts[0])} · ${name(parts.slice(4).join('.'))}`;
 if(parts[1]==='dialogue')return `${name(parts[0])} · 对话 ${Number(parts[2])+1}`;
 return name(raw);
}
const prefixes={card:'卡牌',relic:'遗物',potion:'药水',remove:'移除',upgrade:'升级',transform:'变化',obtain:'获得',duplicate:'复制',rest:'营地',use_potion:'使用药水'};
function label(input){
 const raw=String(input??''),colon=raw.indexOf(':'),prefix=raw.slice(0,colon),id=raw.slice(colon+1);
 if(prefix==='event')return `${event(id)}（${id}）`;
 if(prefix==='go'){const [room,act]=id.split('@a');return `前往${name(room)}${act===undefined?'':` · 第 ${Number(act)+1} 幕`}`}
 if(Object.hasOwn(prefixes,prefix))return `${prefixes[prefix]}：${entity(id)}`;
 return name(raw);
}
// Translate presentation strings only; raw actions, evidence and exports stay intact.
function title(input){
 return String(input??'').replace(/(?:event|card|relic|potion|remove|upgrade|transform|obtain|duplicate|rest|use_potion):[A-Za-z][A-Za-z0-9_.]*|go:[A-Za-z]+@a\d+|[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*/g,token=>{
  if(token.includes(':'))return label(token);
  if(token.includes('.pages.')||token.includes('.dialogue.')){const zh=event(token);return zh===token?token:`${zh}（${token}）`}
  return entity(token);
 });
}
const api={data,name,entity,event,label,title};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.routeZh=api;
})(typeof globalThis!=='undefined'?globalThis:this);
