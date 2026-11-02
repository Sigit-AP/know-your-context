// Call expressions parking unresolved refs.
function helper(value: number): number {
  return value * 2;
}

function run(items: number[]): number {
  const doubled = items.map(helper);
  return doubled.reduce((a, b) => a + b, 0);
}
