// Typed function declarations.
function add(a: number, b: number): number {
  return a + b;
}

function greet(name: string, greeting: string = "hello"): string {
  return `${greeting}, ${name}`;
}

function variadic(first: number, ...rest: number[]): number[] {
  return [first, ...rest];
}
