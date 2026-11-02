// Async typed functions.
async function fetchOne(url: string): Promise<string> {
  return Promise.resolve(url);
}

async function fetchAll(urls: string[]): Promise<string[]> {
  const out: string[] = [];
  for (const url of urls) {
    out.push(await fetchOne(url));
  }
  return out;
}

async function main(): Promise<void> {
  await fetchAll(["a"]);
}
