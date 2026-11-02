// Generic classes and constraints.
public class Box<T> where T : notnull
{
    private readonly T _value;

    public Box(T value)
    {
        _value = value;
    }

    public T Unwrap() => _value;
}

public static class Maybe
{
    public static T? FirstOrDefault<T>(IEnumerable<T> items) where T : class
    {
        foreach (var item in items) return item;
        return null;
    }
}
