// Closures, IIFEs and nested functions.
function makeCounter(start) {
  let count = start;
  function step(amount = 1) {
    count += amount;
    return count;
  }
  function reset() {
    count = start;
  }
  return { step, reset };
}

const id = (function () {
  let seed = 0;
  return () => (seed += 1);
})();
