// Destructuring declarations and defaults.
const pair = [1, 2];
const [first, second] = pair;

const config = { host: "localhost", port: 8080 };
const { host, port = 80 } = config;

function describe({ host, port }) {
  return `${host}:${port}`;
}
