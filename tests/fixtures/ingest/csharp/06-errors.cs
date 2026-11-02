// try/catch/finally, throw expressions, custom exceptions.
public class ConfigException : Exception
{
    public ConfigException(string key) : base($"missing config key: {key}")
    {
        Key = key;
    }

    public string Key { get; }
}

public class Config
{
    public string Read(IDictionary<string, string> map, string key)
    {
        try
        {
            return map[key] ?? throw new ConfigException(key);
        }
        catch (KeyNotFoundException)
        {
            throw new ConfigException(key);
        }
        finally
        {
            _ = map.Count;
        }
    }
}
