// Nullable reference types and pattern matching.
#nullable enable

public class Lookup
{
    public string? Find(string? key)
    {
        if (key is null) return null;
        return key switch
        {
            "" => null,
            var k when k.Length > 3 => k,
            _ => null,
        };
    }

    public bool TryGet(string key, out string? value)
    {
        value = Find(key);
        return value is not null;
    }
}
