// Interfaces with default members and generics.
public interface IShape
{
    double Area();
    string Name { get; }
}

public interface IRepository<T>
{
    T? Find(int id);
    void Save(T entity);
}

public class Circle : IShape
{
    public double Radius { get; init; }
    public string Name => "circle";
    public double Area() => 3.14159 * Radius * Radius;
}
