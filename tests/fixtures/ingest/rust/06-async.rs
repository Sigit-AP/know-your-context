// Async functions and futures.
pub async fn fetch_one(url: &str) -> String {
    url.to_string()
}

pub async fn fetch_all(urls: &[&str]) -> Vec<String> {
    let mut out = Vec::new();
    for url in urls {
        out.push(fetch_one(url).await);
    }
    out
}

pub async fn run() -> usize {
    fetch_all(&["a", "b"]).await.len()
}
