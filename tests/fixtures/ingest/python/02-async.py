"""Async functions and await expressions."""

import asyncio


async def fetch_one(url):
    await asyncio.sleep(0)
    return url


async def fetch_all(urls):
    results = [await fetch_one(u) for u in urls]
    return results


async def main():
    return await asyncio.gather(fetch_all(["a", "b"]))
