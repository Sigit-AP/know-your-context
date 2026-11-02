// Interfaces with abstract and default methods.
public interface Shape : Object {
    public abstract double area();

    public virtual string describe() {
        return @"area=$(area())";
    }
}

public class Circle : Object, Shape {
    public double radius { get; set; default = 1.0; }

    public double area() {
        return 3.14159 * radius * radius;
    }
}
