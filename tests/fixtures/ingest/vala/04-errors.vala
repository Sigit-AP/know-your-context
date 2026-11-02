// Error domains, throws and try/catch.
public class ConfigLookup : Object {
    public string read_config(HashTable<string, string> map, string key) throws Error {
        string? value = map.lookup(key);
        if (value == null) {
            throw new IOError.FAILED(@"missing config key: $key");
        }
        return value;
    }

    public string? read_safe(HashTable<string, string> map, string key) {
        try {
            return read_config(map, key);
        } catch (Error e) {
            return null;
        }
    }
}
