// async/await with Task and IAsyncEnumerable.
using System.Collections.Generic;
using System.Threading.Tasks;

public class Fetcher
{
    public async Task<string> FetchOneAsync(string url)
    {
        await Task.Delay(1);
        return url;
    }

    public async Task<IReadOnlyList<string>> FetchAllAsync(IReadOnlyList<string> urls)
    {
        var results = new List<string>();
        foreach (var url in urls)
        {
            results.Add(await FetchOneAsync(url));
        }
        return results;
    }
}
