// LINQ and extension-method call chains.
using System.Linq;

public class Stats
{
    public int TotalEven(IEnumerable<int> values)
    {
        return items_where().Sum();
    }

    private IEnumerable<int> items_where() => Enumerable.Range(1, 10).Where(x => x % 2 == 0);
}
