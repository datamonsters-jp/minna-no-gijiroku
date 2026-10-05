const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('pages_src/budget.html','utf8');
const handlers = {};
const tooltip = {style:{display:'block'}};
const code = source.slice(source.indexOf('function hideTooltip()'),source.indexOf('function render()'));
const context = vm.createContext({tooltip, pinned:'rev0', document:{addEventListener(type, fn, opts){(handlers[type]??=[]).push({fn,opts});}}});
vm.runInContext(code,context);
assert.equal(handlers.click.length,1);
assert.equal(handlers.click[0].opts,undefined);
for(let i=0;i<4;i++){
 context.pinned='rev0'; tooltip.style.display='block'; handlers.click[0].fn();
 assert.equal(tooltip.style.display,'none'); assert.equal(context.pinned,null);
}
context.pinned='rev0'; tooltip.style.display='block';
handlers.keydown[0].fn({key:'Enter'}); assert.equal(tooltip.style.display,'block');
handlers.keydown[0].fn({key:'Escape'}); assert.equal(tooltip.style.display,'none'); assert.equal(context.pinned,null);
assert.match(source,/function render\(\) \{\s*if \(!BUDGET\) return;\s*hideTooltip\(\);/);
assert(!source.includes('{ once: true }'));
console.log('Tooltip repeated outside-click, Escape, non-Escape and resize-reset checks passed');
