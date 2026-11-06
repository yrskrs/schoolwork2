// Optional dialogs share one first load, keep arguments, and retry failed downloads.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const scripts = [], notices = [], busy = new Set();
const window = {schoolDialogQueue: [{url:'/guide.js', names:['openGuide']}]};
const context = vm.createContext({window, Map, Error, setTimeout(){}, document:{
  activeElement:{setAttribute(n){busy.add(n);},removeAttribute(n){busy.delete(n);}},
  createElement(){return {style:{},setAttribute(){},remove(){this.removed=true;}};},
  head:{appendChild(script){scripts.push(script);}},
  body:{appendChild(notice){notices.push(notice);}},
}});
const source = fs.readFileSync('static/js/main.js','utf8').split('/**\n * SchoolNet')[0];
vm.runInContext(source,context);
(async () => {
  assert.equal(scripts.length,0,'Registering a dialog must not download it');
  const calls=[];
  const first=window.openGuide('teacher');
  const second=window.openGuide('student');
  assert.equal(scripts.length,1,'Concurrent clicks share one script');
  window.openGuide=(...args)=>calls.push(args);
  scripts[0].onload();
  await Promise.all([first,second]);
  assert.deepEqual(calls,[['teacher'],['student']]);
  assert.equal(busy.size,0);
  window.schoolDialogQueue.push({url:'/other.js',names:['openOther']});
  const failed=window.openOther();scripts[1].onerror();await failed;
  assert.equal(notices.length,1);
  const retry=window.openOther(7);assert.equal(scripts.length,3);
  window.openOther=n=>calls.push([n]);scripts[2].onload();await retry;
  assert.deepEqual(calls.at(-1),[7]);
  window.registerLazyDialog('/guide.js',['openGuide']);
  window.openGuide('again');assert.equal(scripts.length,3);
  console.log('PASS: no eager fetch, shared concurrent load, arguments preserved, retry and loaded opener retained.');
})().catch(e=>{console.error(e);process.exitCode=1;});
