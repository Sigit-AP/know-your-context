// File-scoped namespaces and nested classes.
namespace Demo.Deep;

public class Outer
{
    public class Inner
    {
        public int Value() => 1;
    }

    public Inner Make() => new Inner();
}
