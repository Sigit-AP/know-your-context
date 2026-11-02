// Properties, indexers and events.
public class Registry
{
    private readonly List<string> _items = new();

    public int Count => _items.Count;

    public string this[int index] => _items[index];

    public event EventHandler? Changed;

    public void Add(string item)
    {
        _items.Add(item);
        Changed?.Invoke(this, EventArgs.Empty);
    }
}
