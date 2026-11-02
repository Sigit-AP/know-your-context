// Call expressions that park unresolved refs.
function helper(value) {
  return value * 2;
}

function run(items) {
  const doubled = items.map(helper);
  console.log(doubled.length);
  return doubled.reduce(add, 0);
}

function add(a, b) {
  return a + b;
}
