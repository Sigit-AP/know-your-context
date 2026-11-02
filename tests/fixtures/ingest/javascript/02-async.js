// Async functions, await and promise combinators.
async function fetchOne(url) {
  return Promise.resolve(url);
}

async function fetchAll(urls) {
  const out = [];
  for (const url of urls) {
    out.push(await fetchOne(url));
  }
  return out;
}

async function main() {
  return Promise.all([fetchAll(["a", "b"])]);
}
