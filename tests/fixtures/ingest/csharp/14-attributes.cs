// Custom attributes applied to types and members.
[AttributeUsage(AttributeTargets.Class | AttributeTargets.Method)]
public sealed class TagAttribute : Attribute
{
    public TagAttribute(string name)
    {
        Name = name;
    }

    public string Name { get; }
}

[Tag("service")]
public class Service
{
    [Tag("entry")]
    public void Handle()
    {
    }
}
