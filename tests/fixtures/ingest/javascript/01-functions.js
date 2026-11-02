// Basic function declarations and expressions.
function add(a, b) {
  return a + b;
}

function greet(name, greeting = "hello") {
  return `${greeting}, ${name}`;
}

function variadic(first, ...rest) {
  return [first, rest];
}
