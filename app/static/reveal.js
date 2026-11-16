(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ManaReveal=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  function findPairing(rounds,entrants,roundNumber,playerId){
    const players=new Map((entrants||[]).map(player=>[String(player.id),player]));
    const player=players.get(String(playerId));
    const round=(rounds||[]).find(item=>String(item.round)===String(roundNumber));
    if(!player||!round)return {kind:'unavailable'};
    const match=(round.matches||[]).find(item=>String(item.p1)===String(playerId)||String(item.p2)===String(playerId));
    if(!match)return {kind:'unpaired',player:player.name,round:round.round};
    const common={player:player.name,round:round.round,table:match.table??null};
    if(match.void||match.result==='void')return {kind:'void',...common};
    if(match.bye)return {kind:'bye',...common};
    const opponentId=String(match.p1)===String(playerId)?match.p2:match.p1;
    const opponent=opponentId==null?null:players.get(String(opponentId));
    if(!opponent)return {kind:'pending',...common};
    return {kind:'match',...common,opponent:opponent.name};
  }
  return {findPairing};
});
